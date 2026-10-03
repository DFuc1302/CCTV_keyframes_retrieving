"""Unit and integration tests for protocol-bounded 27+9 threshold calibration."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pipeline.cctv.benchmark_events import (
    AnnotationSet,
    Prediction,
    SplitManifest,
)
from pipeline.cctv.calibrate_thresholds import (
    BASELINE_CONGESTION,
    BASELINE_INCIDENT,
    CONGESTION_MOTIONS,
    CONGESTION_OCCUPANCIES,
    CONGESTION_PERSISTENCES,
    INCIDENT_DURATIONS,
    INCIDENT_STATIONARIES,
    calibrate_and_evaluate,
    relative_distance_congestion,
    relative_distance_incident,
)
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.track import TrackFrame


class CalibrationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_grid_recipe_counts_and_baselines(self):
        self.assertEqual(len(CONGESTION_OCCUPANCIES), 3)
        self.assertEqual(len(CONGESTION_MOTIONS), 3)
        self.assertEqual(len(CONGESTION_PERSISTENCES), 3)
        self.assertEqual(
            len(CONGESTION_OCCUPANCIES) * len(CONGESTION_MOTIONS) * len(CONGESTION_PERSISTENCES),
            27,
        )
        self.assertEqual(len(INCIDENT_STATIONARIES), 3)
        self.assertEqual(len(INCIDENT_DURATIONS), 3)
        self.assertEqual(len(INCIDENT_STATIONARIES) * len(INCIDENT_DURATIONS), 9)

        # Baseline distance is zero
        dist_cong = relative_distance_congestion(
            BASELINE_CONGESTION["occupancy_min"],
            BASELINE_CONGESTION["motion_max_px_per_sec"],
            BASELINE_CONGESTION["persistence_sec"],
        )
        self.assertAlmostEqual(dist_cong, 0.0)

        dist_inc = relative_distance_incident(
            BASELINE_INCIDENT["stationary_max_body_lengths_per_sec"],
            BASELINE_INCIDENT["stationary_duration_sec"],
        )
        self.assertAlmostEqual(dist_inc, 0.0)

    def _create_synthetic_environment(self):
        manifest_path = self.root / "manifest.json"
        roi_path = self.root / "roi.json"
        annotation_path = self.root / "annotations.json"
        split_path = self.root / "splits.json"
        tracking_dir = self.root / "tracking"
        output_dir = self.root / "output"
        tracking_dir.mkdir(parents=True, exist_ok=True)

        manifest_data = {
            "schema_version": 1,
            "raw_root": str(self.root / "raw"),
            "cameras": [
                {"camera_id": "CAM-01", "name": "Camera 1"},
                {"camera_id": "CAM-02", "name": "Camera 2"},
            ],
            "videos": [
                {"video_id": "VID-DEV", "camera_id": "CAM-01", "path": "VID-DEV.mp4", "duration_sec": 30.0},
                {"video_id": "VID-TEST", "camera_id": "CAM-02", "path": "VID-TEST.mp4", "duration_sec": 30.0},
            ],
        }
        save_json(manifest_path, manifest_data)
        manifest_sha = digest(manifest_path)

        roi_data = {
            "schema_version": 1,
            "cameras": [
                {
                    "camera_id": "CAM-01",
                    "status": "approved",
                    "reviewed_by": "Reviewer-1",
                    "reviewed_at": "2026-10-01T00:00:00Z",
                    "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                },
                {
                    "camera_id": "CAM-02",
                    "status": "approved",
                    "reviewed_by": "Reviewer-1",
                    "reviewed_at": "2026-10-01T00:00:00Z",
                    "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                },
            ],
        }
        save_json(roi_path, roi_data)

        split_data = {
            "schema_version": 1,
            "mode": "synthetic",
            "source_manifest_sha256": manifest_sha,
            "camera_holdout": False,
            "videos": [
                {"video_id": "VID-DEV", "recording_group_id": "GRP-1", "grouping_status": "synthetic", "split": "development"},
                {"video_id": "VID-TEST", "recording_group_id": "GRP-2", "grouping_status": "synthetic", "split": "test"},
            ],
        }
        save_json(split_path, split_data)

        annotation_data = {
            "schema_version": 1,
            "mode": "synthetic",
            "source_manifest_sha256": manifest_sha,
            "intervals": [
                {
                    "camera_id": "CAM-01",
                    "video_id": "VID-DEV",
                    "start_sec": 0.0,
                    "end_sec": 20.0,
                    "review_status": "reviewed",
                    "reviewed_by": ["rev-a", "rev-b"],
                    "reviewed_at": "2026-10-01T00:00:00Z",
                    "blind_to_predictions": True,
                    "events": [
                        {
                            "event_id": "TRUTH-DEV-INC",
                            "label": "road_incident",
                            "start_sec": 2.0,
                            "end_sec": 6.0,
                        }
                    ],
                },
                {
                    "camera_id": "CAM-02",
                    "video_id": "VID-TEST",
                    "start_sec": 0.0,
                    "end_sec": 20.0,
                    "review_status": "reviewed",
                    "reviewed_by": ["rev-a", "rev-b"],
                    "reviewed_at": "2026-10-01T00:00:00Z",
                    "blind_to_predictions": True,
                    "events": [
                        {
                            "event_id": "TRUTH-TEST-INC",
                            "label": "road_incident",
                            "start_sec": 3.0,
                            "end_sec": 7.0,
                        }
                    ],
                },
            ],
        }
        save_json(annotation_path, annotation_data)

        # Build synthetic frames simulating a vehicle that moves then stops
        # at t=2.0s to 5.0s in both videos
        frames_cache = {"VID-DEV": [], "VID-TEST": []}
        run = "run-test"
        for vid, cam in [("VID-DEV", "CAM-01"), ("VID-TEST", "CAM-02")]:
            video_frames = []
            for i, t in enumerate([float(x) * 0.5 for x in range(30)]):
                sid = "seg-000000"
                locator = {
                    "camera_id": cam,
                    "video_id": vid,
                    "timestamp_sec": t,
                    "frame_idx": i,
                }
                if t < 2.0:
                    x = 0.2 + t * 0.15
                    y = 0.5
                else:
                    x = 0.5
                    y = 0.5
                w, h = 0.1, 0.1
                observations = [
                    {
                        "schema_version": 1,
                        "kind": "track",
                        "run_id": run,
                        "segment_id": sid,
                        "track_id": 1,
                        "locator": locator,
                        "detection": {
                            "label": "car",
                            "confidence": 0.8,
                            "bbox": [x - w / 2, y - h, x + w / 2, y],
                        },
                    }
                ]
                frame_dict = {
                    "schema_version": 1,
                    "kind": "track_frame",
                    "run_id": run,
                    "segment_id": sid,
                    "locator": locator,
                    "source_pts": 1000 + round(t * 1000),
                    "origin_pts": 1000,
                    "time_base": [1, 1000],
                    "width": 1000,
                    "height": 1000,
                    "observations": observations,
                }
                video_frames.append(TrackFrame.model_validate_json(json.dumps(frame_dict)))
            frames_cache[vid] = video_frames
        return manifest_path, tracking_dir, roi_path, annotation_path, split_path, output_dir, frames_cache

    def test_calibrate_and_evaluate_synthetic_end_to_end(self):
        (
            manifest_path,
            tracking_dir,
            roi_path,
            annotation_path,
            split_path,
            output_dir,
            frames_cache,
        ) = self._create_synthetic_environment()

        report = calibrate_and_evaluate(
            manifest_path=manifest_path,
            tracking_dir=tracking_dir,
            roi_path=roi_path,
            annotation_path=annotation_path,
            split_path=split_path,
            output_dir=output_dir,
            allow_draft_roi=True,
            frames_cache=frames_cache,
        )

        self.assertEqual(report["status"], "CALIBRATION_COMPLETE")
        self.assertFalse(report["operational_ready"])
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(len(report["congestion_sweep"]), 27)
        self.assertEqual(len(report["incident_sweep"]), 9)

        # Check structure of selected recipes
        sel_cong = report["selected_congestion"]
        self.assertIn("recipe_id", sel_cong)
        self.assertIn("parameters", sel_cong)
        self.assertIn("selection_reason", sel_cong)

        sel_inc = report["selected_incident"]
        self.assertIn("recipe_id", sel_inc)
        self.assertIn("parameters", sel_inc)
        self.assertIn("selection_reason", sel_inc)

        # Check test split evaluation across IoU thresholds
        test_eval = report["test_evaluation"]
        self.assertIn("iou_0_1", test_eval)
        self.assertIn("iou_0_3", test_eval)
        self.assertIn("iou_0_5", test_eval)
        for iou_key in ("iou_0_1", "iou_0_3", "iou_0_5"):
            entry = test_eval[iou_key]
            self.assertEqual(entry["split"], "test")
            self.assertIn("metrics", entry)
            self.assertIn("by_camera", entry)
            self.assertIn("by_label", entry)

        # Test state file
        state_file = output_dir / "calibration_state.json"
        self.assertTrue(state_file.exists())
        state = json.loads(state_file.read_text())
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["report_sha256"], digest(output_dir / "calibration_report.json"))

        # Re-running returns cached report without errors
        resumed = calibrate_and_evaluate(
            manifest_path=manifest_path,
            tracking_dir=tracking_dir,
            roi_path=roi_path,
            annotation_path=annotation_path,
            split_path=split_path,
            output_dir=output_dir,
            allow_draft_roi=True,
            frames_cache=frames_cache,
        )
        self.assertEqual(resumed["calibrated_at"], report["calibrated_at"])

    def test_pending_intervals_yield_not_evaluated_safely(self):
        (
            manifest_path,
            tracking_dir,
            roi_path,
            annotation_path,
            split_path,
            output_dir,
            frames_cache,
        ) = self._create_synthetic_environment()

        # Overwrite annotations with pending review status (no reviewed intervals)
        manifest_sha = digest(manifest_path)
        pending_annotation_data = {
            "schema_version": 1,
            "mode": "synthetic",
            "source_manifest_sha256": manifest_sha,
            "intervals": [
                {
                    "camera_id": "CAM-01",
                    "video_id": "VID-DEV",
                    "start_sec": 0.0,
                    "end_sec": 20.0,
                    "review_status": "pending",
                    "reviewed_by": [],
                    "reviewed_at": None,
                    "blind_to_predictions": False,
                    "events": [],
                },
                {
                    "camera_id": "CAM-02",
                    "video_id": "VID-TEST",
                    "start_sec": 0.0,
                    "end_sec": 20.0,
                    "review_status": "pending",
                    "reviewed_by": [],
                    "reviewed_at": None,
                    "blind_to_predictions": False,
                    "events": [],
                },
            ],
        }
        save_json(annotation_path, pending_annotation_data)

        report = calibrate_and_evaluate(
            manifest_path=manifest_path,
            tracking_dir=tracking_dir,
            roi_path=roi_path,
            annotation_path=annotation_path,
            split_path=split_path,
            output_dir=output_dir,
            allow_draft_roi=True,
            frames_cache=frames_cache,
        )

        self.assertEqual(report["status"], "CALIBRATION_COMPLETE")
        # All sweeps should have f1=None and fallback to baseline
        self.assertIn("baseline_retained", report["selected_congestion"]["selection_reason"])
        self.assertIn("baseline_retained", report["selected_incident"]["selection_reason"])

        # Test evaluation status must be NOT_EVALUATED_NO_REVIEWED_INTERVALS
        for iou_key in ("iou_0_1", "iou_0_3", "iou_0_5"):
            entry = report["test_evaluation"][iou_key]
            self.assertEqual(entry["status"], "NOT_EVALUATED_NO_REVIEWED_INTERVALS")
            self.assertIsNone(entry["metrics"])

    def test_tie_breaking_order(self):
        from pipeline.cctv.calibrate_thresholds import select_best_recipe

        keys = ("param_a", "param_b")
        # Case 1: Higher F1 wins regardless of FP/h or distance
        sweep = [
            {"recipe_id": "r1", "param_a": 1, "param_b": 1, "f1": 0.80, "fp_per_hour": 100.0, "relative_distance": 2.0},
            {"recipe_id": "r2", "param_a": 2, "param_b": 2, "f1": 0.85, "fp_per_hour": 500.0, "relative_distance": 5.0},
        ]
        best, reason = select_best_recipe(sweep, keys)
        self.assertEqual(best["recipe_id"], "r2")
        self.assertIn("0.8500", reason)

        # Case 2: Tied F1 -> lower FP/h wins
        sweep = [
            {"recipe_id": "r1", "param_a": 1, "param_b": 1, "f1": 0.80, "fp_per_hour": 50.0, "relative_distance": 2.0},
            {"recipe_id": "r2", "param_a": 2, "param_b": 2, "f1": 0.80, "fp_per_hour": 30.0, "relative_distance": 5.0},
        ]
        best, _ = select_best_recipe(sweep, keys)
        self.assertEqual(best["recipe_id"], "r2")

        # Case 3: Tied F1 and tied FP/h -> smaller relative distance wins
        sweep = [
            {"recipe_id": "r1", "param_a": 1, "param_b": 1, "f1": 0.80, "fp_per_hour": 30.0, "relative_distance": 2.0},
            {"recipe_id": "r2", "param_a": 2, "param_b": 2, "f1": 0.80, "fp_per_hour": 30.0, "relative_distance": 0.5},
        ]
        best, _ = select_best_recipe(sweep, keys)
        self.assertEqual(best["recipe_id"], "r2")

        # Case 4: Tied F1, tied FP/h, tied distance -> ascending parameter tuple wins
        sweep = [
            {"recipe_id": "r2", "param_a": 2, "param_b": 1, "f1": 0.80, "fp_per_hour": 30.0, "relative_distance": 0.5},
            {"recipe_id": "r1", "param_a": 1, "param_b": 2, "f1": 0.80, "fp_per_hour": 30.0, "relative_distance": 0.5},
        ]
        best, _ = select_best_recipe(sweep, keys)
        self.assertEqual(best["recipe_id"], "r1")

        # Case 5: All F1 is None -> baseline retained (minimum relative distance)
        sweep = [
            {"recipe_id": "r2", "param_a": 2, "param_b": 2, "f1": None, "fp_per_hour": 0.0, "relative_distance": 1.5},
            {"recipe_id": "r_base", "param_a": 1, "param_b": 1, "f1": None, "fp_per_hour": 0.0, "relative_distance": 0.0},
        ]
        best, reason = select_best_recipe(sweep, keys)
        self.assertEqual(best["recipe_id"], "r_base")
        self.assertEqual(reason, "baseline_retained_no_defined_f1")

    def test_tampered_output_fails_closed(self):
        (
            manifest_path,
            tracking_dir,
            roi_path,
            annotation_path,
            split_path,
            output_dir,
            frames_cache,
        ) = self._create_synthetic_environment()

        calibrate_and_evaluate(
            manifest_path=manifest_path,
            tracking_dir=tracking_dir,
            roi_path=roi_path,
            annotation_path=annotation_path,
            split_path=split_path,
            output_dir=output_dir,
            allow_draft_roi=True,
            frames_cache=frames_cache,
        )

        # Tamper report file
        report_file = output_dir / "calibration_report.json"
        tampered = json.loads(report_file.read_text())
        tampered["status"] = "TAMPERED"
        save_json(report_file, tampered)

        # Rerunning should reject or regenerate because digest mismatches state
        resumed = calibrate_and_evaluate(
            manifest_path=manifest_path,
            tracking_dir=tracking_dir,
            roi_path=roi_path,
            annotation_path=annotation_path,
            split_path=split_path,
            output_dir=output_dir,
            allow_draft_roi=True,
            frames_cache=frames_cache,
        )
        self.assertEqual(resumed["status"], "CALIBRATION_COMPLETE")
if __name__ == "__main__":
    unittest.main()
