"""Verify the complete local demo without inference models or review writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
from tqdm import tqdm

from pipeline.cctv.benchmark_runtime import (
    SERVER_CODE,
    check_server,
    loopback_url,
    verify_results,
)
from pipeline.cctv.events import protect_output
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.search import CctvSearch

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    """Raise real validation failures even when Python optimization is enabled."""
    if not condition:
        raise ValueError(message)


def verify_demo(
    client: httpx.Client, catalog: dict, require_overlays: bool = False
) -> dict:
    """Exercise retrieval/assets and three demo packages; fail on missing stages."""
    checks = {}

    def get(url: str) -> dict:
        response = client.get(url)
        response.raise_for_status()
        return response.json()

    ready = get("/ready")
    require(
        ready["ready"] and ready["frames"] == len(catalog),
        "Readiness/index count mismatch",
    )
    session = ready["benchmark_identity"]["session_id"]
    require(bool(session), "Missing server session")
    home = client.get("/")
    home.raise_for_status()
    for name in ("search", "traffic", "events", "dispatch"):
        require(f'id="{name}-workspace"' in home.text, "UI panel missing: " + name)
    for name in ("app.js", "traffic.js", "events.js", "dispatch.js", "style.css"):
        client.get("/static/" + name).raise_for_status()
    checks["ui_assets"] = "PASSED"

    rows = verify_results(
        get("/cctv/search?query=a%20road%20intersection&top_k=1"), catalog, 1
    )
    row = rows[0]
    require(
        row["image_url"] == f"/cctv/image/{catalog[row['id']]['faiss_id']}",
        "Wrong keyframe asset ID",
    )
    require(
        row["video_url"] == f"/cctv/video/{row['video_id']}", "Wrong video asset ID"
    )
    image = client.get(row["image_url"])
    image.raise_for_status()
    with Image.open(BytesIO(image.content)) as decoded:
        decoded.verify()
    response = client.post(
        "/cctv/search/image?top_k=1",
        content=image.content,
        headers={"Content-Type": "image/jpeg"},
    )
    response.raise_for_status()
    verify_results(response.json(), catalog, 1)
    checks["text_image_metadata"] = "PASSED"

    def video(video_id: str) -> None:
        response = client.get(
            "/cctv/video/" + video_id, headers={"Range": "bytes=0-31"}
        )
        require(
            response.status_code == 206 and len(response.content) == 32,
            "Video byte range unavailable",
        )
        require(
            response.headers.get("content-range", "").startswith("bytes 0-31/"),
            "Invalid video range",
        )

    for video_id in sorted({r["video_id"] for r in catalog.values()}):
        video(video_id)
    checks["video_ranges"] = "PASSED"

    traffic = get("/cctv/traffic")
    require(traffic["available"] and traffic["videos"], "Traffic stage unavailable")
    require(
        traffic["operational_ready"] is False,
        "Demo traffic cannot claim operational readiness",
    )
    count = 0
    for source in traffic["videos"]:
        result = get("/cctv/traffic/windows/" + source["video_id"] + "?limit=500")
        require(
            result["total"] == source["window_count"] == len(result["rows"]),
            "Traffic coverage mismatch",
        )
        require(
            all(
                r["window"]["video_id"] == source["video_id"]
                and r["window"]["camera_id"] == source["camera_id"]
                for r in result["rows"]
            ),
            "Traffic camera/video mismatch",
        )
        require(
            source["roi_image_url"] == "/cctv/traffic/roi/" + source["video_id"],
            "Wrong ROI asset ID",
        )
        response = client.get(source["roi_image_url"])
        response.raise_for_status()
        with Image.open(BytesIO(response.content)) as decoded:
            decoded.verify()
        count += result["total"]
    require(count == traffic["windows"], "Traffic total mismatch")
    checks["traffic_windows"] = count

    if require_overlays:
        overlays = get("/cctv/traffic/overlays")
        require(
            overlays["available"]
            and overlays["read_only"] is True
            and overlays["operational_ready"] is False
            and overlays["roi_approved"] is False,
            "Tracking overlays unavailable/unsafe",
        )
        overlay_rows = {row["video_id"]: row for row in overlays["videos"]}
        require(
            set(overlay_rows) == {row["video_id"] for row in traffic["videos"]},
            "Overlay coverage mismatch",
        )
        for vid, overlay in overlay_rows.items():
            require(
                overlay["video_url"] == f"/cctv/traffic/overlay/{vid}",
                "Wrong overlay asset URL",
            )
            response = client.get(overlay["video_url"], headers={"Range": "bytes=0-31"})
            require(
                response.status_code == 206
                and len(response.content) == 32
                and response.headers.get("content-range", "").startswith("bytes 0-31/"),
                "Overlay Range missing",
            )
            result = get(f"/cctv/traffic/windows/{vid}?limit=500")
            for row in result["rows"]:
                evidence = row["tracking_overlay"]
                require(
                    evidence is not None
                    and evidence["video_url"] == overlay["video_url"]
                    and abs(
                        evidence["video_seek_sec"]
                        - (
                            row["evidence"]["timestamp_sec"]
                            - overlay["playback_to_source_offset_sec"]
                        )
                    )
                    < 1e-8,
                    "Overlay evidence/offset mismatch",
                )
        checks["tracking_overlays"] = len(overlay_rows)

    events = get("/cctv/events")
    require(
        events["read_only"] is True and events["operational_ready"] is False,
        "Unsafe event flags",
    )
    packages = {p["package_id"]: p for p in events["packages"]}
    require(
        set(packages) == {"incidents", "congestion", "demo"}
        and all(p["available"] for p in packages.values()),
        "Demo event packages missing",
    )
    event_rows = {}
    for package_id, package in packages.items():
        result = get("/cctv/events/" + package_id + "?limit=50")
        require(
            result["total"] == package["total"] == len(result["rows"]),
            "Event coverage exceeds smoke limit or mismatch",
        )
        for event in result["rows"]:
            event_rows[event["event_id"]] = event
            for locator in event["evidence"]:
                require(
                    locator["video_url"] == "/cctv/video/" + event["video_id"],
                    "Wrong evidence video",
                )
                require(
                    locator["camera_id"] == event["camera_id"]
                    and locator["video_id"] == event["video_id"]
                    and locator["video_seek_sec"] >= 0,
                    "Evidence scope/seek mismatch",
                )
                video(locator["video_id"])
    checks["events"] = {k: p["total"] for k, p in packages.items()}

    dispatch = get("/cctv/dispatch")
    require(dispatch["read_only"] is True, "Dispatch must be read-only")
    packages = {p["package_id"]: p for p in dispatch["packages"]}
    require(
        set(packages) == {"simulation", "events_empty", "review_gate"}
        and all(p["available"] for p in packages.values()),
        "Demo dispatch packages missing/stale",
    )
    counts = {}
    for package_id in packages:
        report = get("/cctv/dispatch/" + package_id)
        require(
            report["dry_run"] is True
            and report["fictional_resource_data"] is True
            and report["operational_ready"] is False
            and report["read_only"] is True,
            "Unsafe dispatch flags",
        )
        require(
            report["assigned_officers"] <= report["dispatchable_officer_limit"]
            and report["available_officers_remaining"]
            >= report["required_reserve_officers"]
            and report["reserve_met"] is True,
            "Staff cap/reserve violated",
        )
        for demand in report["assignments"] + report["unmet_demands"]:
            if demand["event_id"]:
                require(
                    demand["review_status"] == event_rows[demand["event_id"]]["status"],
                    "Dispatch/live review mismatch",
                )
        require(
            all(r["review_status"] == "confirmed" for r in report["assignments"]),
            "Unconfirmed demand assigned",
        )
        counts[package_id] = dict(
            assignments=len(report["assignments"]), unmet=len(report["unmet_demands"])
        )
    checks["dispatch"] = counts
    require(
        client.post("/cctv/dispatch/simulation").status_code == 405,
        "Unexpected dispatch mutation endpoint",
    )
    require(
        get("/ready")["benchmark_identity"]["session_id"] == session,
        "Server restarted during checks",
    )
    return dict(
        checks=checks,
        server_session=session,
        passed=True,
        operational_ready=False,
        scope="technical HTTP integration only; no browser playback or semantic quality measurement",
    )


def run(
    manifest_path: Path,
    index: Path,
    output: Path,
    url: str,
    require_overlays: bool = False,
) -> dict:
    """Freeze existing demo files and refuse overwrites; each rerun uses a new output."""
    manifest = load_manifest(manifest_path)
    protected = [
        ROOT / "configs",
        index.absolute(),
        ROOT / "outputs/model-cache/clip.json",
    ]
    folders = [
        ROOT / "outputs/demo-data" / n
        for n in (
            "video-preview",
            "tracking-clips",
            "traffic-draft-v1",
            "roi-draft-previews",
            "events-v1",
            "events-demo-v1",
            "events-demo-reviews-v1",
            "incidents-draft-v1",
            "dispatch-simulation-v1",
            "dispatch-events-empty-v1",
            "dispatch-review-gate-v1",
            "runtime-benchmark-v1",
        )
    ]
    paths = [
        manifest.path,
        Path(__file__).resolve(),
        *[ROOT / name for name in SERVER_CODE],
        *ROOT.glob("app/cctv*.py"),
        *ROOT.glob("static/cctv/*"),
        *[v.source for v in manifest.videos],
    ]
    if require_overlays:
        folders.append(ROOT / "outputs/demo-data/tracking-overlay-v2")
    for folder in [*protected, *folders]:
        if folder.is_dir():
            paths.extend(
                p for p in folder.rglob("*") if p.is_file() and p.name != ".ingest.lock"
            )
        else:
            paths.append(folder)
    paths = sorted(set(paths))
    require(
        output.absolute().is_relative_to(ROOT / "outputs"),
        "Acceptance output must be below repository outputs",
    )
    output = protect_output(
        output,
        [
            manifest.raw_root,
            manifest.path,
            *protected,
            *folders,
            ROOT / "app",
            ROOT / "static",
            ROOT / "scripts",
        ],
    )
    require(
        not output.exists() or not any(output.iterdir()),
        "Choose an empty/new output; acceptance is a fresh run",
    )
    before = {
        str(p): (digest(p), p.stat().st_mtime_ns)
        for p in tqdm(paths, desc="Freeze demo inputs", unit="file")
    }
    search = CctvSearch(index)
    with closing(search.connect()) as conn:
        conn.row_factory = sqlite3.Row
        catalog = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT f.id,f.file,f.faiss_id,c.camera_id,c.video_id,c.timestamp_sec,c.frame_idx FROM frames f JOIN cctv_frames c ON c.id=f.id"
            )
        }
    with httpx.Client(
        base_url=loopback_url(url), timeout=60, trust_env=False, follow_redirects=False
    ) as client:
        check_server(
            client,
            dict(
                index_state_sha256=before[str(index.absolute() / "index_state.json")][
                    0
                ],
                code_sha256={name: before[str(ROOT / name)][0] for name in SERVER_CODE},
            ),
        )
        report = verify_demo(client, catalog, require_overlays)
    require(
        before == {str(p): (digest(p), p.stat().st_mtime_ns) for p in paths},
        "Protected demo inputs changed",
    )
    report.update(
        verified_utc=datetime.now(timezone.utc).isoformat(),
        protected_files=len(paths),
        inputs={p: sha for p, (sha, _) in before.items()},
    )
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "acceptance_report.json", report)
    return report


def main() -> int:
    """Run the Windows-ready local integration check against the existing server."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "index-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--require-overlays", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            args.manifest, args.index_dir, args.output, args.url, args.require_overlays
        )
        print(json.dumps({k: v for k, v in report.items() if k != "inputs"}, indent=2))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        httpx.HTTPError,
    ) as exc:
        parser.exit(1, f"Demo acceptance failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
