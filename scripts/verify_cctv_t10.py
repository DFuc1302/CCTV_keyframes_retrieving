"""Resumable, read-only T10 demo checks; never approve or label footage."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import httpx
from tqdm import tqdm

from pipeline.cctv.benchmark_fixtures import fixture_report
from pipeline.cctv.benchmark_runtime import SERVER_CODE, check_server, loopback_url
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.search import CctvSearch
from pipeline.cctv.traffic import load_config
from scripts.inventory_cctv_evaluation import inventory
from scripts.verify_cctv_demo import require, verify_demo

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_cctv_benchmark",
    "test_cctv_incidents",
    "test_cctv_dispatch",
    "test_cctv_workflow",
)


def freeze(paths: list[Path]) -> dict:
    """Capture content and timestamps, rejecting links before reading."""
    result = {}
    for path in tqdm(sorted(set(paths)), desc="Freeze T10 inputs", unit="file"):
        reject_link(path)
        result[str(path)] = [digest(path), path.stat().st_mtime_ns]
    return result


def workflow_http(run: Path, url: str) -> dict:
    """Verify the selected workflow runtime using the existing HTTP checks."""
    search = CctvSearch(run / "search-index")
    with closing(search.connect()) as conn:
        conn.row_factory = sqlite3.Row
        catalog = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT f.id,f.file,f.faiss_id,c.camera_id,c.video_id,c.timestamp_sec,c.frame_idx "
                "FROM frames f JOIN cctv_frames c ON c.id=f.id"
            )
        }
    expected = {"simulation"}
    if (run / "proposal-current.json").exists():
        expected.add("review_gate")
    with httpx.Client(
        base_url=loopback_url(url), timeout=60, trust_env=False
    ) as client:
        check_server(
            client,
            dict(
                index_state_sha256=digest(run / "search-index/index_state.json"),
                code_sha256={name: digest(ROOT / name) for name in SERVER_CODE},
            ),
        )
        return verify_demo(client, catalog, True, "workflow", expected)


def verify(run: Path, output: Path, url: str) -> dict:
    """Checkpoint demo checks and publish reports with pending quality gates."""
    run = run.absolute()
    manifest = load_manifest(run / "configs/manifest.json")
    output = protect_output(
        output,
        [
            run,
            manifest.raw_root,
            ROOT / "app",
            ROOT / "pipeline",
            ROOT / "scripts",
            ROOT / "static",
        ],
    )
    require(output.is_relative_to(ROOT / "outputs"), "T10 output must be below outputs")
    url = loopback_url(url)
    paths = [p for p in run.rglob("*") if p.is_file() and not p.name.endswith(".lock")]
    paths += [v.source for v in manifest.videos]
    paths += list((ROOT / "pipeline/cctv").glob("*.py"))
    paths += list((ROOT / "app").glob("cctv*.py"))
    paths += list((ROOT / "scripts").glob("verify_cctv*.py"))
    paths += [
        ROOT / "scripts/inventory_cctv_evaluation.py",
        *[ROOT / (name + ".py") for name in TESTS],
    ]
    paths += [p for p in (ROOT / "static/cctv").iterdir() if p.is_file()]
    signature = dict(
        inputs=freeze(paths), python=sys.version, url=url, tests=list(TESTS)
    )
    reject_link(output)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    reject_link(state_path)
    if not state_path.exists() and any(
        p.name != "console.log" for p in output.iterdir()
    ):
        raise ValueError("Foreign verification output; preserved")
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(signature=signature, done={}, status="running")
        )
        require(
            state["signature"] == signature,
            "Inputs/code changed; start a NEW verification",
        )
        for name, checksum in state["done"].items():
            require(
                digest(output / (name + ".json")) == checksum,
                "Verification checkpoint changed",
            )
        if state["status"] == "complete":
            require(
                digest(output / "report.json") == state["report_sha256"],
                "Report changed",
            )
            require(
                digest(output / "report.md") == state["markdown_sha256"],
                "Markdown changed",
            )
            return dict(read_json(output / "report.json"), resumed=True)
        state.update(status="running", error=None)
        save_json(state_path, state)

        def roi() -> dict:
            config = load_config(run / "configs/roi.json", manifest)
            require(
                all(c.polygon is not None for c in config.cameras),
                "ROI polygon missing",
            )
            return dict(
                valid=True,
                cameras=[
                    dict(camera_id=c.camera_id, approval=c.status)
                    for c in config.cameras
                ],
            )

        def fixtures() -> dict:
            result = fixture_report()
            require(result["passed"], "Synthetic evaluator expectation failed")
            return result

        def cpu_tests() -> dict:
            with (output / "cpu-tests.log").open("ab") as log:
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", "-v", *TESTS],
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                )
            require(
                result.returncode == 0, "CPU contract tests failed; see cpu-tests.log"
            )
            return dict(passed=True, modules=list(TESTS))

        steps = [
            ("inventory", lambda: inventory(manifest.path, output / "inventory")),
            ("roi", roi),
            ("fixtures", fixtures),
            ("cpu-tests", cpu_tests),
            ("http", lambda: workflow_http(run, url)),
        ]
        try:
            for name, operation in tqdm(steps, desc="T10 demo checks", unit="step"):
                state.update(stage=name, completed=len(state["done"]), total=len(steps))
                save_json(state_path, state)
                with (output / "progress.jsonl").open(
                    "a", encoding="utf-8"
                ) as progress:
                    progress.write(
                        json.dumps(
                            dict(
                                stage=name,
                                completed=len(state["done"]),
                                total=len(steps),
                            )
                        )
                        + "\n"
                    )
                if name not in state["done"]:
                    save_json(output / (name + ".json"), operation())
                    state["done"][name] = digest(output / (name + ".json"))
                    save_json(state_path, state)
            require(
                freeze(paths) == signature["inputs"],
                "Protected inputs changed during verification",
            )
            report = dict(
                schema_version=1,
                status="PASSED_DEMO_CHECKS",
                demo_only=True,
                operational_ready=False,
                real_accuracy_measured=False,
                quality_metrics=None,
                m8="PENDING",
                second_machine="NOT_RUN",
                verified_utc=datetime.now(timezone.utc).isoformat(),
                environment=dict(
                    platform=platform.platform(),
                    python=sys.version,
                    packages={
                        name: version(name)
                        for name in ("torch", "opencv-python", "numpy", "fastapi")
                    },
                ),
                checks={
                    name: read_json(output / (name + ".json")) for name, _ in steps
                },
                pending=dict(
                    T10_1="Content/groups review",
                    T10_2="Human ROI approval",
                    T10_3="Verified footage split",
                    T10_4="M8 pending",
                    T10_5="Independent calibration not run",
                    T10_6="Footage quality not evaluated",
                    T10_7="Second machine not run",
                ),
                protected_files=len(paths),
                source_run=str(run),
            )
            save_json(output / "report.json", report)
            markdown = "# T10 — kiểm chứng demo\n\nPASSED_DEMO_CHECKS; không đo accuracy CCTV.\n\n"
            markdown += "| Kiểm tra | Kết quả |\n| --- | --- |\n" + "".join(
                f"| {name} | PASS |\n" for name, _ in steps
            )
            markdown += "\n## Còn pending\n\n" + "".join(
                f"- {task}: {reason}\n" for task, reason in report["pending"].items()
            )
            markdown += "\nFixtures tổng hợp kiểm tra phép tính; quality_metrics=null. M8 PENDING; máy thứ hai NOT_RUN.\n"
            (output / "report.md").write_text(markdown, encoding="utf-8")
            state.update(
                status="complete",
                completed=len(steps),
                report_sha256=digest(output / "report.json"),
                markdown_sha256=digest(output / "report.md"),
            )
        except Exception as exc:
            state.update(status="failed", error=str(exc))
            raise
        finally:
            save_json(state_path, state)
        return dict(report, resumed=False)


def main() -> int:
    """Run checks against one existing loopback workflow server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        result = verify(args.run, args.output, args.url)
        print(json.dumps(dict(status=result["status"], resumed=result["resumed"])))
        return 0
    except (ValueError, OSError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f"T10 verification failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
