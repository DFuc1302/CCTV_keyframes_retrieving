"""Exercise traffic contracts without weights, GPU or changes to real footage."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.traffic import CameraROI, TrafficConfig, load_config, validate_record


class TrafficTests(unittest.TestCase):
    def setUp(self) -> None:
        """Use the demo identity contract only; no media is opened."""
        root = Path(__file__).resolve().parent
        self.manifest = load_manifest(
            root / "configs/cctv.demo.json", require_files=False
        )
        self.config = load_config(
            root / "configs/cctv.traffic.demo.json", self.manifest
        )
        self.frame = {
            "schema_version": 1,
            "kind": "detections",
            "run_id": "run-001",
            "locator": {
                "camera_id": "N001",
                "video_id": "N001-V001",
                "timestamp_sec": 249.519,
                "frame_idx": 6238,
            },
            "width": 1920,
            "height": 1080,
            "detections": [
                {"label": "motorcycle", "confidence": 0.8, "bbox": [0.1, 0.2, 0.4, 0.7]}
            ],
        }

    def parse(self, item):
        return validate_record(json.dumps(item), self.manifest, self.config)

    def test_demo_pending_is_valid_but_not_approved(self):
        self.assertEqual(
            [c.status for c in self.config.cameras], ["pending", "pending"]
        )
        self.assertTrue(all(c.polygon is None for c in self.config.cameras))
        self.assertEqual(self.parse(self.frame).locator.frame_idx, 6238)
        self.frame["detections"] = []
        self.assertEqual(self.parse(self.frame).detections, ())

    def test_polygon_topology_and_bounds(self):
        invalid = [
            [[0, 0], [1, 0], [1, 1], [0, 0]],
            [[0, 0], [1, 0], [0.5, 0], [0, 1]],
            [[0, 0], [1, 1], [0, 1], [0.8, 0]],
            [[0, 0], [1.1, 0], [0, 1]],
            [[0, 0], [0.5, 0.5], [1, 1]],
        ]
        for polygon in invalid:
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                CameraROI.model_validate_json(
                    json.dumps({"camera_id": "N001", "polygon": polygon})
                )
        polygon = [[0, 0], [1, 0], [1, 1], [0.5, 0.5], [0, 1]]
        for vertices in (polygon, list(reversed(polygon))):
            self.assertIsNotNone(
                CameraROI.model_validate_json(
                    json.dumps({"camera_id": "N001", "polygon": vertices})
                ).polygon
            )

    def test_approval_requires_human_and_timezone(self):
        roi = {
            "camera_id": "N001",
            "status": "approved",
            "polygon": [[0, 0], [1, 0], [0, 1]],
        }
        with self.assertRaises(ValueError):
            CameraROI.model_validate_json(json.dumps(roi))
        roi.update(reviewed_by="reviewer", reviewed_at="2026-09-30T12:00:00")
        with self.assertRaises(ValueError):
            CameraROI.model_validate_json(json.dumps(roi))
        roi["reviewed_at"] += "+07:00"
        self.assertEqual(
            CameraROI.model_validate_json(json.dumps(roi)).status, "approved"
        )
        roi["status"] = "pending"
        with self.assertRaises(ValueError):
            CameraROI.model_validate_json(json.dumps(roi))

    def test_camera_coverage_and_locator_consistency(self):
        for cameras in (["N001"], ["N001", "n001"], ["N001", "N003"]):
            config = TrafficConfig.model_validate_json(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cameras": [{"camera_id": c} for c in cameras],
                    }
                )
            )
            with self.assertRaises(ValueError):
                config.bind(self.manifest)
        for camera, video in (("N002", "N001-V001"), ("N001", "unknown")):
            self.frame["locator"].update(camera_id=camera, video_id=video)
            with self.assertRaises(ValueError):
                self.parse(self.frame)

    def test_invalid_scalars_boxes_and_unknown_fields(self):
        cases = [
            ("schema_version", True),
            ("width", True),
            ("width", 0),
            ("extra", 1),
        ]
        for key, value in cases:
            item = {**self.frame, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(item)
        for box in ([0.5, 0, 0.2, 1], [0, 0, 1, 0], [0, -0.1, 1, 1]):
            self.frame["detections"][0]["bbox"] = box
            with self.assertRaises(ValueError):
                self.parse(self.frame)
        self.frame["locator"]["timestamp_sec"] = float("nan")
        with self.assertRaises(ValueError):
            self.parse(self.frame)

    def test_track_ids_are_scoped_across_video_and_reset(self):
        item = {
            "schema_version": 1,
            "kind": "track",
            "run_id": "run-001",
            "segment_id": "segment-1",
            "track_id": 1,
            "locator": self.frame["locator"],
            "detection": self.frame["detections"][0],
        }
        first = self.parse(item)
        item["segment_id"] = "segment-2"
        second = self.parse(item)
        item["locator"] = {**item["locator"], "video_id": "N001-V002"}
        third = self.parse(item)
        self.assertEqual(len({first.track_key, second.track_key, third.track_key}), 3)

    def test_window_requires_approved_roi_and_valid_interval(self):
        item = {
            "schema_version": 1,
            "kind": "window",
            "run_id": "run-001",
            "camera_id": "N001",
            "video_id": "N001-V001",
            "start_sec": 0.0,
            "end_sec": 10.0,
            "sample_count": 20,
            "mean_vehicle_count": 2.5,
            "mean_occupancy": 0.4,
            "median_motion_px_per_sec": None,
            "roi_status": "approved",
        }
        with self.assertRaisesRegex(ValueError, "reviewed"):
            self.parse(item)
        camera = CameraROI.model_validate_json(
            json.dumps(
                {
                    "camera_id": "N001",
                    "status": "approved",
                    "polygon": [[0, 0], [1, 0], [0, 1]],
                    "reviewed_by": "test fixture",
                    "reviewed_at": "2026-09-30T12:00:00+07:00",
                }
            )
        )
        self.config = TrafficConfig(
            schema_version=1, cameras=(camera, self.config.cameras[1])
        )
        self.assertIsNone(self.parse(item).median_motion_px_per_sec)
        for key, value in (
            ("end_sec", 0.0),
            ("sample_count", 0),
            ("mean_occupancy", 1.1),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse({**item, key: value})

    def test_unsupported_record_or_version_rejected(self):
        with self.assertRaises(ValueError):
            self.parse({**self.frame, "kind": "accident"})
        with self.assertRaises(ValidationError):
            self.parse({**self.frame, "schema_version": 2})


if __name__ == "__main__":
    unittest.main()
