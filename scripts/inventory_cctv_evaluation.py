"""Inventory read-only evaluation footage; leave content and group decisions pending."""

from __future__ import annotations

import argparse
import json
import logging
import math
import subprocess
from pathlib import Path

import av
from tqdm import tqdm

from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.preview import probe

LOG = logging.getLogger(__name__)


def inventory(manifest_path: Path, output: Path) -> dict:
    """Checkpoint technical metadata, never infer labels, sessions or approval."""
    manifest = load_manifest(manifest_path)
    inputs = [
        manifest.path,
        Path(__file__).resolve(),
        *(v.source for v in manifest.videos),
    ]
    before = {
        str(p): dict(sha256=digest(p), mtime_ns=p.stat().st_mtime_ns) for p in inputs
    }
    output = protect_output(output, [manifest.raw_root, *inputs])
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "inventory_state.json"
    names = (
        "inventory.json",
        "content.pending.json",
        "splits.pending.json",
        "roi.pending.json",
    )
    for name in (*names, "inventory_state.json", ".ingest.lock"):
        reject_link(output / name)
    with output_lease(output):
        if not state_path.exists() and any(
            p.name != ".ingest.lock" for p in output.iterdir()
        ):
            raise ValueError(
                "Foreign output directory; preserve it and choose a new output"
            )
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(inputs=before, status="processing", videos={}, errors={})
        )
        if state["inputs"] != before:
            raise ValueError("Inputs changed; use a new output")
        if state["status"] == "complete":
            if any(digest(output / name) != state["artifacts"][name] for name in names):
                raise ValueError("Inventory publication changed")
            return dict(
                status="PREPARED_PENDING_HUMAN_REVIEW",
                videos=len(state["videos"]),
                resumed=True,
            )
        for video in tqdm(manifest.videos, desc="Evaluation inventory", unit="video"):
            if video.video_id in state["videos"]:
                continue
            try:
                metadata = probe(video.source)
                if (
                    not math.isfinite(metadata["duration"])
                    or metadata["duration"] <= 0
                    or not math.isfinite(metadata["first_pts"])
                ):
                    raise ValueError("Invalid source duration or first PTS")
                with av.open(str(video.source)) as container:
                    stream = container.streams.video[0]
                    metadata.update(
                        width=stream.width,
                        height=stream.height,
                        nominal_fps=str(stream.average_rate),
                        time_base=str(stream.time_base),
                    )
                source = before[str(video.source)]
                state["videos"][video.video_id] = dict(
                    video_id=video.video_id,
                    camera_id=video.camera_id,
                    source_path=str(video.source),
                    bytes=video.source.stat().st_size,
                    **source,
                    **metadata,
                    recording_start_time=video.start_time,
                )
                state["errors"].pop(video.video_id, None)
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
                av.FFmpegError,
            ) as exc:
                LOG.exception("Inventory failed: %s", video.video_id)
                state["errors"][video.video_id] = str(exc)
            save_json(state_path, state)
        if before != {
            str(p): dict(sha256=digest(p), mtime_ns=p.stat().st_mtime_ns)
            for p in inputs
        }:
            raise ValueError("Source changed during inventory")
        if state["errors"]:
            raise ValueError(f"Inventory incomplete: {state['errors']}")
        rows = [state["videos"][v.video_id] for v in manifest.videos]
        save_json(
            output / names[0],
            dict(
                videos=rows,
                total_duration_sec=sum(r["duration"] for r in rows),
                timestamp_scope="first_pts is source timestamp; annotation times are relative to first decoded frame",
            ),
        )
        save_json(
            output / names[1],
            dict(
                source_manifest_sha256=digest(manifest.path),
                videos=[
                    dict(
                        video_id=v.video_id,
                        source_sha256=before[str(v.source)]["sha256"],
                        review_status="pending",
                        reviewed_by=None,
                        reviewed_at=None,
                        recording_group_id=None,
                        group_evidence=None,
                        overlap_evidence=None,
                        camera_view_evidence=None,
                        location=None,
                        content_tags=None,
                        prediction_exposure="unknown",
                        observation_notes=None,
                    )
                    for v in manifest.videos
                ],
            ),
        )
        save_json(
            output / names[2],
            dict(
                schema_version=1,
                mode="independent",
                source_manifest_sha256=digest(manifest.path),
                camera_holdout=False,
                videos=[
                    dict(
                        video_id=v.video_id,
                        recording_group_id="UNVERIFIED",
                        grouping_status="unverified",
                        split="unassigned",
                    )
                    for v in manifest.videos
                ],
            ),
        )
        save_json(
            output / names[3],
            dict(
                schema_version=1,
                cameras=[
                    dict(
                        camera_id=c["camera_id"],
                        status="pending",
                        polygon=None,
                        reviewed_by=None,
                        reviewed_at=None,
                    )
                    for c in manifest.cameras
                ],
            ),
        )
        state.update(
            status="complete", artifacts={name: digest(output / name) for name in names}
        )
        save_json(state_path, state)
        return dict(
            status="PREPARED_PENDING_HUMAN_REVIEW", videos=len(rows), resumed=False
        )


def main() -> int:
    """Run with native Windows venv; failed videos remain resumable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        print(json.dumps(inventory(args.manifest, args.output), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, f"Evaluation inventory failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
