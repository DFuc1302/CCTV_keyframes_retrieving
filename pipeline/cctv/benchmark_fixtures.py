"""Fictional event metrics fixtures, isolated from actual CCTV annotations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.cctv.benchmark_events import (
    AnnotationSet,
    Prediction,
    SplitManifest,
    evaluate_records,
)
from pipeline.cctv.events import protect_output
from pipeline.cctv.ingest import reject_link, save_json
from pipeline.cctv.manifest import load_manifest


def fixture() -> tuple[AnnotationSet, SplitManifest, list[Prediction]]:
    """Two truths, a duplicate, one false alarm and one unreviewed prediction."""
    labels = AnnotationSet.model_validate_json(
        json.dumps(
            dict(
                schema_version=1,
                mode="synthetic",
                source_manifest_sha256="0" * 64,
                intervals=[
                    dict(
                        camera_id="SIM-CAM",
                        video_id="SIM-VIDEO",
                        start_sec=0.0,
                        end_sec=10.0,
                        review_status="reviewed",
                        reviewed_by=["fixture-a", "fixture-b"],
                        reviewed_at="2026-10-01T00:00:00Z",
                        blind_to_predictions=True,
                        events=[
                            dict(
                                event_id="GT-A",
                                label="road_incident",
                                start_sec=2.0,
                                end_sec=4.0,
                            ),
                            dict(
                                event_id="GT-B",
                                label="road_incident",
                                start_sec=6.0,
                                end_sec=8.0,
                            ),
                        ],
                    )
                ],
            )
        )
    )
    splits = SplitManifest.model_validate_json(
        json.dumps(
            dict(
                schema_version=1,
                mode="synthetic",
                source_manifest_sha256="0" * 64,
                camera_holdout=False,
                videos=[
                    dict(
                        video_id="SIM-VIDEO",
                        recording_group_id="SIM-SESSION",
                        grouping_status="synthetic",
                        split="test",
                    )
                ],
            )
        )
    )
    predictions = []
    for event_id, start, end, trigger in (
        ("P-001", 2.0, 6.0, 5.0),
        ("P-002", 2.0, 6.0, 5.5),
        ("P-003", 9.0, 10.0, 9.5),
        ("P-004", 10.0, 12.0, 11.0),
    ):
        predictions.append(
            Prediction.model_validate_json(
                json.dumps(
                    dict(
                        event_id=event_id,
                        camera_id="SIM-CAM",
                        video_id="SIM-VIDEO",
                        label="road_incident",
                        start_sec=start,
                        end_sec=end,
                        trigger_source_sec=trigger,
                        synthetic_event=True,
                        demo_only=True,
                    )
                )
            )
        )
    return labels, splits, predictions


def fixture_report() -> dict:
    """Check known counts/formulas, never estimate real footage performance."""
    labels, splits, predictions = fixture()
    result = evaluate_records(labels, splits, predictions)
    scores = result["metrics"]
    passed = (
        (scores["tp"], scores["fp"], scores["fn"]) == (1, 2, 1)
        and scores["precision"] == 1 / 3
        and scores["recall"] == 0.5
        and scores["f1"] == 0.4
    )
    return dict(
        synthetic_only=True, real_accuracy_measured=False, passed=passed, result=result
    )


def main() -> int:
    """Write an idempotent synthetic calculation report outside read-only inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, require_files=False)
    output = protect_output(
        args.output,
        [manifest.path, manifest.raw_root, Path(__file__).resolve().parents[1]],
    )
    if output.exists() and any(
        p.name != "fixture-report.json" for p in output.iterdir()
    ):
        raise ValueError("Foreign fixture output directory")
    output.mkdir(parents=True, exist_ok=True)
    report = fixture_report()
    path = output / "fixture-report.json"
    reject_link(path)
    if not path.exists() or json.loads(path.read_text(encoding="utf-8")) != report:
        save_json(path, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
