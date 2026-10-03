"""Explicit synthetic trajectories for rule behavior, not real accident evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.cctv.events import protect_output
from pipeline.cctv.incidents import IncidentConfig, screen_video
from pipeline.cctv.ingest import reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import CameraROI

CASES = {
    "moving_stop": 1,
    "red_light_stop_confounder": 1,
    "already_stationary": 0,
    "keeps_moving": 0,
    "short_stop": 0,
    "missing_track": 0,
    "segment_reset": 0,
    "timestamp_gap": 0,
    "outside_roi": 0,
    "weak_detection": 0,
    "collective_stop": 0,
    "collective_stop_filter_disabled": 3,
    "queue_departure": 1,
    "queue_last_neighbor_departure": 1,
    "queue_lingering_neighbor": 0,
    "far_stop": 3,
    "opposite_direction_stop": 3,
    "joining_queue": 0,
    "joining_opposite_queue": 1,
    "stationary_neighbors_without_travel": 1,
    "one_queue_neighbor": 2,
}


def fixture(name: str) -> tuple[list[TrackFrame], CameraROI, IncidentConfig]:
    """Create a named, small trajectory with rational PTS and normalized boxes."""
    if name not in CASES:
        raise ValueError("Unknown fixture")
    config = IncidentConfig.model_validate_json(
        json.dumps(
            dict(
                schema_version=1,
                moving_min_body_lengths_per_sec=0.3,
                stationary_max_body_lengths_per_sec=0.04,
                moving_duration_sec=1.0,
                stationary_duration_sec=2.0,
                max_gap_sec=0.5,
                confidence_min=0.4,
                box_area_min=0.0002,
                max_box_scale_ratio=1.5,
                queue_filter_enabled=name != "collective_stop_filter_disabled",
                neighbor_radius_diagonals=3.0,
                min_queue_neighbors=2,
                stop_sync_window_sec=2.0,
            )
        )
    )
    roi = CameraROI.model_validate_json(
        json.dumps(
            dict(
                camera_id="N001",
                status="pending",
                polygon=[[0, 0], [1, 0], [1, 1], [0, 1]],
            )
        )
    )
    if name == "outside_roi":
        roi = CameraROI.model_validate_json(
            json.dumps(
                dict(
                    camera_id="N001",
                    status="pending",
                    polygon=[[0.6, 0], [1, 0], [1, 1], [0.6, 1]],
                )
            )
        )
    if name in QUEUE_CASES:
        return _queue_frames(name), roi, config
    frames = []
    for n in range(14):
        x = 0.1 + 0.03 * min(n, 3)
        if name == "already_stationary":
            x = 0.1
        elif name == "keeps_moving" or (name == "short_stop" and n >= 6):
            x = 0.1 + 0.03 * n
        stamp = n * 0.4 + (2.0 if name == "timestamp_gap" and n >= 5 else 0.0)
        locator = dict(
            camera_id="N001", video_id="N001-V001", frame_idx=n, timestamp_sec=stamp
        )
        segment = "seg-000001" if name == "segment_reset" and n >= 5 else "seg-000000"
        observations = [
            dict(
                schema_version=1,
                kind="track",
                run_id="synthetic-run",
                segment_id=segment,
                track_id=1,
                locator=locator,
                detection=dict(
                    label="car",
                    confidence=0.2 if name == "weak_detection" else 0.9,
                    bbox=[x, 0.4, x + 0.08, 0.48],
                ),
            )
        ]
        if name == "missing_track" and n == 5:
            observations = []
        frames.append(
            TrackFrame.model_validate_json(
                json.dumps(
                    dict(
                        schema_version=1,
                        kind="track_frame",
                        run_id="synthetic-run",
                        segment_id=segment,
                        locator=locator,
                        source_pts=round(stamp * 1000),
                        origin_pts=0,
                        time_base=[1, 1000],
                        width=1000,
                        height=1000,
                        observations=observations,
                    )
                )
            )
        )
    return frames, roi, config


QUEUE_CASES = {
    "collective_stop",
    "collective_stop_filter_disabled",
    "queue_departure",
    "queue_last_neighbor_departure",
    "queue_lingering_neighbor",
    "far_stop",
    "opposite_direction_stop",
    "joining_queue",
    "joining_opposite_queue",
    "stationary_neighbors_without_travel",
    "one_queue_neighbor",
}


def _queue_frames(name: str) -> list[TrackFrame]:
    """Nearby vehicles share a stop, except where a named confounder changes it."""
    joining = name in {"joining_queue", "joining_opposite_queue"}
    count = 4 if joining else (2 if name == "one_queue_neighbor" else 3)
    frames = []
    for n in range(49):
        stamp = n * 0.25
        locator = dict(
            camera_id="N001", video_id="N001-V001", frame_idx=n, timestamp_sec=stamp
        )
        observations = []
        for track_id in range(1, count + 1):
            if track_id != 1:
                if name == "queue_departure" and n >= 16:
                    continue
                if name in {"queue_last_neighbor_departure", "queue_lingering_neighbor"}:
                    if track_id == 2 and n >= 16:
                        continue
                    if name == "queue_last_neighbor_departure" and n >= 24:
                        continue
            offset = 0.12 * (track_id - 1)
            x = 0.1 + offset + 0.03 * min(n, 4)
            if name == "far_stop":
                offset = (0.0, 0.4, 0.65)[track_id - 1]
                x = 0.1 + offset + 0.03 * min(n, 4)
            elif name == "opposite_direction_stop" and track_id != 1:
                x = 0.22 + offset + 0.03 * (4 - min(n, 4))
            elif name == "stationary_neighbors_without_travel" and track_id != 1:
                x = 0.22 + offset
            elif joining:
                if track_id == 1:
                    x = 0.1 + 0.015 * min(n, 16)
                elif name == "joining_opposite_queue":
                    x = 0.34 + offset + 0.03 * (4 - min(n, 4))
                else:
                    x = 0.34 + offset - 0.03 * (4 - min(n, 4))
            observations.append(
                dict(
                    schema_version=1,
                    kind="track",
                    run_id="synthetic-run",
                    segment_id="seg-000000",
                    track_id=track_id,
                    locator=locator,
                    detection=dict(
                        label="car", confidence=0.9, bbox=[x, 0.4, x + 0.08, 0.48]
                    ),
                )
            )
        frames.append(
            TrackFrame.model_validate_json(
                json.dumps(
                    dict(
                        schema_version=1,
                        kind="track_frame",
                        run_id="synthetic-run",
                        segment_id="seg-000000",
                        locator=locator,
                        source_pts=n * 250,
                        origin_pts=0,
                        time_base=[1, 1000],
                        width=1000,
                        height=1000,
                        observations=observations,
                    )
                )
            )
        )
    return frames


def evaluate_fixtures() -> dict:
    """Report exact logical expectations and a known traffic-light false alarm."""
    rows = []
    for name, expected in CASES.items():
        frames, roi, config = fixture(name)
        records = screen_video(frames, roi, config, "0" * 64, synthetic=True)
        rows.append(
            dict(
                case=name,
                expected_rule_candidates=expected,
                actual_rule_candidates=len(records),
                passed=len(records) == expected,
                synthetic=True,
                known_non_emergency=name == "red_light_stop_confounder",
            )
        )
    return dict(
        synthetic_only=True,
        operational_ready=False,
        real_event_accuracy_measured=False,
        cases=rows,
        passed=all(r["passed"] for r in rows),
        limitation="Red-light stop triggers the same rule; this is screening, not accident recognition.",
    )


def main() -> int:
    """Write a standalone fixture report without touching CCTV inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path("configs/cctv.demo.json"))
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, require_files=False)
    output = protect_output(
        args.output,
        [manifest.raw_root, manifest.path, Path(__file__).resolve().parents[1]],
    )
    if output.exists() and any(
        p.name != "fixture-report.json" for p in output.iterdir()
    ):
        raise ValueError("Foreign fixture output directory")
    output.mkdir(parents=True, exist_ok=True)
    report = evaluate_fixtures()
    path = output / "fixture-report.json"
    reject_link(path)
    if not path.exists() or json.loads(path.read_text(encoding="utf-8")) != report:
        save_json(path, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
