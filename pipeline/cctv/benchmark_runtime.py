"""Frozen, resumable loopback HTTP retrieval benchmark without loading CLIP."""

from __future__ import annotations

import argparse
import hashlib
import math
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.events import checksum, protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest, relative_video
from pipeline.cctv.search import CctvSearch
from pipeline.cctv.traffic import Contract

ROOT = Path(__file__).resolve().parents[2]
SERVER_CODE = ("app/cctv.py", "pipeline/cctv/clip.py", "pipeline/cctv/search.py")


class QueryCase(Contract):
    """Unlabelled workload item; an image must be an indexed relative JPEG."""

    query_id: str
    kind: Literal["text", "image"]
    text: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    frame: str | None = None

    @model_validator(mode="after")
    def consistent(self):
        identity(self.query_id, "query_id")
        if self.kind == "text":
            if self.text is None or not self.text.strip() or self.frame is not None:
                raise ValueError("Text case requires only nonblank text")
        elif self.text is not None or self.frame is None:
            raise ValueError("Image case requires only an indexed frame")
        else:
            relative_video(self.frame)
        return self


class Workload(Contract):
    """Bound requests and warmup counts; no invented relevance labels."""

    schema_version: Literal[1] = 1
    top_k: Annotated[int, Field(ge=1, le=50)] = 12
    warmup: Annotated[int, Field(ge=1, le=5)] = 2
    repeats: Annotated[int, Field(ge=2, le=30)] = 10
    cases: Annotated[tuple[QueryCase, ...], Field(min_length=1, max_length=24)]

    @model_validator(mode="after")
    def unique(self):
        if len({c.query_id.casefold() for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate query_id")
        return self


def loopback_url(url: str) -> str:
    """Confine processed image uploads to the explicitly selected local server."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise ValueError("Use an explicit loopback HTTP host and port")
    return url.rstrip("/")


def statistics(values: list[float]) -> dict:
    """Linear interpolated empirical quantiles; no SLA or quality inference."""
    ordered = sorted(values)
    if any(not math.isfinite(v) or v < 0 for v in ordered):
        raise ValueError("Invalid latency sample")
    if not ordered:
        return dict(count=0, p50_ms=None, p95_ms=None, mean_ms=None)

    def quantile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lo, hi = math.floor(position), math.ceil(position)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)

    return dict(
        count=len(ordered),
        p50_ms=quantile(0.5),
        p95_ms=quantile(0.95),
        mean_ms=sum(ordered) / len(ordered),
    )


def verify_results(payload: dict, catalog: dict, expected: int) -> list[dict]:
    """Check ranked IDs, source locators and finite cosine scores against SQLite."""
    results = payload.get("results")
    if (
        not isinstance(results, list)
        or len(results) != expected
        or payload.get("total") != expected
    ):
        raise ValueError("Result count mismatch")
    seen, previous = set(), float("inf")
    for row in results:
        original = catalog.get(row.get("id"))
        if original is None or row["id"] in seen:
            raise ValueError("Unknown or duplicate result ID")
        seen.add(row["id"])
        for key in ("file", "camera_id", "video_id", "timestamp_sec", "frame_idx"):
            if row.get(key) != original[key]:
                raise ValueError("Result locator mismatch")
        score = row.get("score")
        if (
            type(score) not in (float, int)
            or not math.isfinite(score)
            or not -1.0001 <= score <= 1.0001
        ):
            raise ValueError("Invalid cosine score")
        if score > previous + 1e-6:
            raise ValueError("Results not ranked by cosine score")
        previous = score
    return results


def check_server(
    client: httpx.Client, expected: dict, session: str | None = None
) -> dict:
    """Bind requests to startup-validated index, model, code and one model lifetime."""
    response = client.get("/ready")
    response.raise_for_status()
    ready = response.json()
    info = ready.get("benchmark_identity")
    if (
        not ready.get("ready")
        or not isinstance(info, dict)
        or not info.get("session_id")
    ):
        raise ValueError("Server needs benchmark identity; restart the demo server")
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Server identity mismatch: {key}")
    if session is not None and info["session_id"] != session:
        raise ValueError("Server restarted; use a new benchmark output directory")
    return ready


def measure(
    client: httpx.Client,
    case: QueryCase,
    payload: bytes | None,
    top_k: int,
    catalog: dict,
    clock=time.perf_counter,
) -> dict:
    """Time complete HTTP response, then validate JSON outside the timed interval."""
    started = clock()
    response = None
    elapsed = None
    try:
        if case.kind == "text":
            response = client.get(
                "/cctv/search", params={"query": case.text, "top_k": top_k}
            )
        else:
            response = client.post(
                "/cctv/search/image",
                params={"top_k": top_k},
                content=payload,
                headers={"Content-Type": "image/jpeg"},
            )
        elapsed = (clock() - started) * 1000
        response.raise_for_status()
        rows = verify_results(response.json(), catalog, min(top_k, len(catalog)))
        return dict(latency_ms=elapsed, passed=True, results=rows)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if elapsed is None:
            elapsed = (clock() - started) * 1000
        return dict(
            latency_ms=elapsed,
            passed=False,
            http_status=response.status_code if response is not None else None,
            error=f"{type(exc).__name__}: {exc}"[:1000],
        )


def run_benchmark(
    manifest_path: Path,
    workload_path: Path,
    index_dir: Path,
    output: Path,
    url: str,
    *,
    transport=None,
) -> dict:
    """Freeze inputs, checkpoint whole cases and publish descriptive local latency."""
    url = loopback_url(url)
    index_dir = index_dir.resolve()
    early = {
        str(p.resolve()): digest(p)
        for p in (
            manifest_path,
            workload_path,
            index_dir / "index_state.json",
            *[
                index_dir / name
                for name in read_json(index_dir / "index_state.json")["artifacts"]
            ],
        )
    }
    manifest = load_manifest(manifest_path)
    workload = Workload.model_validate_json(workload_path.read_text(encoding="utf-8"))
    search = CctvSearch(index_dir)
    with closing(search.connect()) as conn:
        import sqlite3

        conn.row_factory = sqlite3.Row
        catalog = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT f.id,f.file,c.camera_id,c.video_id,c.timestamp_sec,c.frame_idx "
                "FROM frames f JOIN cctv_frames c ON c.id=f.id"
            )
        }
    sources = {v.video_id: v.camera_id for v in manifest.videos}
    if len(catalog) != search.index.ntotal or any(
        sources.get(r["video_id"]) != r["camera_id"] for r in catalog.values()
    ):
        raise ValueError("Index source scope mismatch")
    root = Path(search.manifest["keyframes_root"]).resolve()
    files = [
        manifest.path,
        workload_path.resolve(),
        index_dir.resolve() / "index_state.json",
        *[
            index_dir.resolve() / name
            for name in read_json(index_dir.resolve() / "index_state.json")["artifacts"]
        ],
        *[ROOT / name for name in SERVER_CODE],
        Path(__file__).resolve(),
    ]
    images = {}
    indexed = {row["file"] for row in catalog.values()}
    for case in workload.cases:
        if case.kind == "image":
            if case.frame not in indexed:
                raise ValueError("Query image not registered in index")
            target = (root / case.frame).absolute()
            reject_link(target)
            if (
                not target.is_relative_to(root)
                or target.stat().st_size > 8 * 1024 * 1024
            ):
                raise ValueError("Unsafe or oversized query image")
            files.append(target)
            images[case.query_id] = target.read_bytes()
    for path in files:
        reject_link(path)
    hashes = {str(p): digest(p) for p in files}
    if any(hashes.get(p) != sha for p, sha in early.items()):
        raise ValueError("Frozen benchmark input changed while reading")
    for case in workload.cases:
        if (
            case.kind == "image"
            and hashlib.sha256(images[case.query_id]).hexdigest()
            != hashes[str(root / case.frame)]
        ):
            raise ValueError("Query image changed while reading")

    def unchanged() -> None:
        if any(digest(Path(p)) != sha for p, sha in hashes.items()):
            raise ValueError("Frozen benchmark input changed")

    signature = dict(
        pipeline="cctv-runtime-benchmark-v1",
        inputs=hashes,
        url=url,
        protocol="serial-per-case-warmup-http-complete-response-linear-quantiles",
    )
    output = protect_output(
        output, [manifest.raw_root, root, index_dir.resolve(), *files]
    )
    state_path, report_path = (
        output / "runtime_state.json",
        output / "runtime_report.json",
    )
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in (".ingest.lock", "runtime_state.json", "runtime_report.json"):
        reject_link(output / name)
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(
                signature=signature,
                status="processing",
                cases={},
                cases_sha256=checksum({}),
            )
        )
        if state["signature"] != signature:
            raise ValueError("Input/recipe changed; use a new output directory")
        if checksum(state["cases"]) != state["cases_sha256"]:
            raise ValueError("Case checkpoint changed")
        unchanged()
        if state["status"] == "complete":
            if digest(report_path) != state["report_sha256"]:
                raise ValueError("Runtime report changed")
            return dict(read_json(report_path), resumed=True)
        expected = dict(
            index_state_sha256=digest(index_dir / "index_state.json"),
            code_sha256={name: hashes[str(ROOT / name)] for name in SERVER_CODE},
        )
        with httpx.Client(
            base_url=url,
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            ready = check_server(client, expected, state.get("session_id"))
            info = ready["benchmark_identity"]
            for field in ("clip_model", "revision", "model_sha256"):
                if info["model_recipe"].get(field) != search.manifest["recipe"].get(
                    field
                ):
                    raise ValueError("Serving model differs from frozen index")
            state.setdefault("session_id", info["session_id"])
            state.setdefault("started_utc", datetime.now(timezone.utc).isoformat())
            save_json(state_path, state)
            for case in tqdm(workload.cases, desc="HTTP benchmark cases"):
                if case.query_id in state["cases"]:
                    continue
                check_server(client, expected, state["session_id"])
                warmups = [
                    measure(
                        client, case, images.get(case.query_id), workload.top_k, catalog
                    )
                    for _ in range(workload.warmup)
                ]
                samples = [
                    measure(
                        client, case, images.get(case.query_id), workload.top_k, catalog
                    )
                    for _ in range(workload.repeats)
                ]
                check_server(client, expected, state["session_id"])
                unchanged()
                state["cases"][case.query_id] = dict(
                    kind=case.kind, warmups=warmups, samples=samples
                )
                state["cases_sha256"] = checksum(state["cases"])
                save_json(state_path, state)
            after = check_server(client, expected, state["session_id"])
        unchanged()
        by_kind = {}
        errors = sum(
            not sample["passed"]
            for value in state["cases"].values()
            for sample in value["warmups"] + value["samples"]
        )
        for kind in ("text", "image"):
            samples = [
                s
                for c in state["cases"].values()
                if c["kind"] == kind
                for s in c["samples"]
            ]
            by_kind[kind] = dict(
                statistics([s["latency_ms"] for s in samples if s["passed"]]),
                attempted=len(samples),
                failed=sum(not s["passed"] for s in samples),
            )
        report = dict(
            schema_version=1,
            signature=signature,
            status="PASSED_TECHNICAL_BENCHMARK" if errors == 0 else "FAILED_REQUESTS",
            started_utc=state["started_utc"],
            finished_utc=datetime.now(timezone.utc).isoformat(),
            server=info,
            model_memory_after=after.get("model_memory", {}),
            memory_scope="server model lifetime peaks; not request-specific or total GPU usage",
            workload=workload.model_dump(mode="json"),
            by_kind=by_kind,
            by_query={
                key: statistics(
                    [s["latency_ms"] for s in value["samples"] if s["passed"]]
                )
                for key, value in state["cases"].items()
            },
            request_errors_including_warmup=errors,
            cases=state["cases"],
            measurement_scope="client HTTP response wall time including upload, preprocessing, GPU, FAISS, SQLite and JSON transfer; excludes image file read, validation, cold start and warmup",
            isolation="single serial benchmark client; other server clients are not prevented",
            semantic_retrieval_metrics=None,
            relevance_labels_available=False,
            operational_ready=False,
        )
        save_json(report_path, report)
        save_json(
            state_path,
            dict(state, status="complete", report_sha256=digest(report_path)),
        )
        return dict(report, resumed=False)


def main() -> int:
    """Benchmark the already running local demo; the client never loads a model."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "workload", "index-dir", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    try:
        report = run_benchmark(
            args.manifest, args.workload, args.index_dir, args.output, args.url
        )
        import json

        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "status",
                        "by_kind",
                        "request_errors_including_warmup",
                        "resumed",
                    )
                },
                indent=2,
            )
        )
        return 0 if report["status"] == "PASSED_TECHNICAL_BENCHMARK" else 1
    except (ValueError, OSError, KeyError, RuntimeError, httpx.HTTPError) as exc:
        parser.exit(1, f"Runtime benchmark failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
