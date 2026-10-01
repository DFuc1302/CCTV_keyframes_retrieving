"""Independent geometry/time expectations and CPU pipeline safety checks."""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_cctv_track as fixture_module
from pipeline.cctv.congestion import (
    CameraThresholds,
    MetricsConfig,
    RasterROI,
    process_windows,
    summarize_video,
)
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import CameraROI


def frame(index, stamp=None, boxes=None, segment="seg-000000"):
    """Explicit PTS in milliseconds and normalized synthetic vehicle boxes."""
    stamp = index * 0.4 if stamp is None else stamp
    locator = {
        "camera_id": "N001",
        "video_id": "N001-V001",
        "frame_idx": index,
        "timestamp_sec": stamp,
    }
    boxes = [(1, [0.1, 0.1, 0.6, 0.6])] if boxes is None else boxes
    value = {
        "schema_version": 1,
        "kind": "track_frame",
        "run_id": "run-test",
        "segment_id": segment,
        "locator": locator,
        "width": 100,
        "height": 100,
        "source_pts": round(stamp * 1000),
        "origin_pts": 0,
        "time_base": [1, 1000],
        "observations": [
            {
                "schema_version": 1,
                "kind": "track",
                "run_id": "run-test",
                "segment_id": segment,
                "track_id": tid,
                "locator": locator,
                "detection": {"label": "car", "confidence": 0.9, "bbox": box},
            }
            for tid, box in boxes
        ],
    }
    return TrackFrame.model_validate_json(json.dumps(value))


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.roi = CameraROI.model_validate_json(
            json.dumps(
                {
                    "camera_id": "N001",
                    "status": "pending",
                    "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
                }
            )
        )
        self.config = MetricsConfig.model_validate_json(
            json.dumps(
                {
                    "schema_version": 1,
                    "window_sec": 1.0,
                    "mask_width": 100,
                    "max_gap_sec": 0.5,
                    "min_coverage": 0.9,
                    "min_motion_pairs": 1,
                    "cameras": [
                        {
                            "camera_id": "N001",
                            "occupancy_min": 0.1,
                            "vehicle_count_min": 1.0,
                            "motion_max_px_per_sec": 5.0,
                            "persistence_sec": 2.0,
                        }
                    ],
                }
            )
        )
        self.threshold = self.config.cameras[0]

    def summarize(self, frames, end=2.4):
        return summarize_video(
            frames, self.roi, self.config, self.threshold, 0.0, end, True
        )

    def test_sampled_frames_keep_source_ordinals(self):
        rows = [frame(n * 12, stamp=n * 0.5) for n in range(6)]
        windows = summarize_video(
            rows, self.roi, self.config, self.threshold, 0, 3, True, 0.5
        )
        self.assertGreater(len(windows), 0)
        with self.assertRaises(ValueError):
            self.summarize(rows, end=3)

    def test_union_occupancy_does_not_double_count_overlapping_boxes(self):
        raster = RasterROI(self.roi, 100, 100, 100)
        count, occupancy, _ = raster.measure(
            frame(0, boxes=[(1, [0.1, 0.1, 0.6, 0.6]), (2, [0.1, 0.1, 0.6, 0.6])])
        )
        self.assertEqual(count, 2)
        self.assertAlmostEqual(occupancy, 0.25)

    def test_bottom_anchor_inclusion_and_box_roi_intersection(self):
        roi = self.roi.model_copy(
            update={"polygon": ((0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0))}
        )
        count, occupancy, _ = RasterROI(roi, 100, 100, 100).measure(
            frame(0, boxes=[(1, [0.4, 0.1, 0.8, 0.6])])
        )
        self.assertEqual(count, 0)
        self.assertGreater(occupancy, 0)

    def test_pts_weighted_mean_not_unweighted_frame_average(self):
        frames = [
            frame(0, 0.0),
            frame(1, 0.1, []),
            frame(2, 0.5, []),
            frame(3, 1.0, []),
        ]
        rows = self.summarize(frames, end=1.1)
        self.assertAlmostEqual(rows[0]["coverage"], 1.0)
        self.assertAlmostEqual(rows[0]["window"]["mean_vehicle_count"], 0.1)
        self.assertAlmostEqual(rows[0]["window"]["mean_occupancy"], 0.025)
        self.assertEqual(rows[0]["window"]["sample_count"], 3)

    def test_stationary_persistence_and_partial_window_unknown(self):
        rows = self.summarize(frame(i) for i in range(6))
        self.assertEqual(
            [r["status"] for r in rows],
            ["slow_dense", "congestion_candidate", "insufficient_evidence"],
        )
        self.assertEqual(rows[1]["persistent_sec"], 2.0)
        self.assertEqual(rows[0]["window"]["median_motion_px_per_sec"], 0.0)
        self.assertEqual(rows[0]["window"]["kind"], "draft_window")
        self.assertFalse(rows[1]["thresholds_calibrated"])

    def test_real_motion_uses_original_pixels_and_dt(self):
        rows = self.summarize(
            frame(i, boxes=[(1, [0.1 + i * 0.04, 0.1, 0.6 + i * 0.04, 0.6])])
            for i in range(6)
        )
        self.assertAlmostEqual(rows[0]["window"]["median_motion_px_per_sec"], 10.0)
        self.assertEqual(rows[0]["status"], "no_candidate")

    def test_segment_reset_never_connects_integer_id(self):
        rows = self.summarize(frame(i, segment=f"seg-{i:06d}") for i in range(6))
        self.assertTrue(all(r["motion_pairs"] == 0 for r in rows))
        self.assertIsNone(rows[0]["window"]["median_motion_px_per_sec"])
        self.assertEqual(rows[0]["status"], "insufficient_evidence")

    def test_pts_gap_is_unknown_and_breaks_persistence(self):
        rows = self.summarize(
            [frame(0, 0.0), frame(1, 0.4), frame(2, 1.8), frame(3, 2.0)]
        )
        self.assertLess(rows[0]["coverage"], 0.9)
        self.assertEqual(rows[0]["status"], "insufficient_evidence")
        self.assertEqual(rows[1]["persistent_sec"], 0.0)

    def test_nonconsecutive_frame_or_mixed_video_rejected(self):
        with self.assertRaisesRegex(ValueError, "Reviewed ROI"):
            summarize_video(
                [frame(0)], self.roi, self.config, self.threshold, 0.0, 1.0, False
            )
        with self.assertRaisesRegex(ValueError, "invalid frames"):
            self.summarize([frame(0), frame(2)])
        with self.assertRaises(ValueError):
            CameraThresholds.model_validate_json(
                json.dumps(
                    {
                        "camera_id": "N001",
                        "occupancy_min": 1.1,
                        "vehicle_count_min": 1.0,
                        "motion_max_px_per_sec": 1.0,
                        "persistence_sec": 2.0,
                    }
                )
            )


class WindowPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.TrackingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_track()
        self.metrics = self.fixture.manifest.parent / "metrics.json"
        config = WindowTests()
        config.setUp()
        save_json(self.metrics, config.config.model_dump(mode="json"))
        save_json(
            self.fixture.traffic,
            {"schema_version": 1, "cameras": [config.roi.model_dump(mode="json")]},
        )
        self.output = self.fixture.root / "windows"

    def run_windows(self, draft=True, output=None):
        return process_windows(
            self.fixture.manifest,
            self.fixture.traffic,
            self.metrics,
            self.fixture.output,
            output or self.output,
            draft,
        )

    def test_pending_roi_gates_before_writing_and_draft_resume_preserves_inputs(self):
        before = {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in self.fixture.output.iterdir()
            if p.is_file()
        }
        with self.assertRaisesRegex(ValueError, "Reviewed ROI"):
            self.run_windows(False)
        self.assertFalse(self.output.exists())
        result = self.run_windows()
        self.assertEqual(
            (result["videos_done"], result["windows"], result["error"]), (1, 3, 0)
        )
        artifacts = {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in self.output.iterdir()
            if p.name != ".ingest.lock"
        }
        self.assertEqual(self.run_windows()["videos_skipped"], 1)
        self.assertEqual(
            artifacts,
            {
                p.name: (digest(p), p.stat().st_mtime_ns)
                for p in self.output.iterdir()
                if p.name != ".ingest.lock"
            },
        )
        self.assertEqual(
            before,
            {
                p.name: (digest(p), p.stat().st_mtime_ns)
                for p in self.fixture.output.iterdir()
                if p.is_file()
            },
        )
        report = json.loads((self.output / "window_manifest.json").read_text())
        self.assertFalse(report["roi_ready"])
        self.assertFalse(report["operational_ready"])

    def test_reviewed_roi_emits_legacy_window_schema(self):
        config = json.loads(self.fixture.traffic.read_text())
        config["cameras"][0].update(
            status="approved",
            reviewed_by="synthetic-test-reviewer",
            reviewed_at="2026-10-01T00:00:00+07:00",
        )
        save_json(self.fixture.traffic, config)
        self.run_windows(False)
        rows = [
            json.loads(line)
            for line in (self.output / "windows.jsonl").read_text().splitlines()
        ]
        self.assertTrue(
            all(r["kind"] == "window" and r["roi_status"] == "approved" for r in rows)
        )

    def test_changed_recipe_or_corrupt_output_is_preserved(self):
        self.run_windows()
        path = self.output / "signals.jsonl"
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_windows()
        self.assertEqual(path.read_text(), "changed")
        self._change_recipe()
        with self.assertRaisesRegex(ValueError, "Input/recipe changed"):
            self.run_windows()

    def _change_recipe(self):
        value = json.loads(self.metrics.read_text())
        value["mask_width"] = 64
        save_json(self.metrics, value)
        return True

    def test_raw_overlap_and_foreign_output_preserved(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_windows(output=self.fixture.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_windows()
        self.assertEqual((self.output / "keep.txt").read_text(), "keep")

    def test_video_checkpoint_replays_after_interruption(self):
        from pipeline.cctv import congestion

        real_save = congestion.save_json

        def interrupt(path, value):
            if path.name == "window_state.json" and "errors" in value:
                raise KeyboardInterrupt()
            return real_save(path, value)

        with patch.object(congestion, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_windows()
        with closing(sqlite3.connect(self.output / "windows.db")) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM videos").fetchone()[0], 1
            )
        self.assertFalse((self.output / "windows.jsonl").exists())
        self.assertEqual(self.run_windows()["videos_skipped"], 1)

    def test_tracking_corruption_blocks_output_before_calculation(self):
        path = self.fixture.output / "frames.jsonl"
        path.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "Tracking input changed"):
            self.run_windows()
        self.assertFalse(self.output.exists())

    def test_invalid_frame_logs_failure_and_does_not_publish(self):
        from pipeline.cctv import congestion

        with patch.object(
            congestion.TrackFrame,
            "model_validate_json",
            side_effect=ValueError("bad record"),
        ):
            self.assertEqual(self.run_windows()["error"], 1)
        self.assertFalse((self.output / "windows.jsonl").exists())
        self.assertIn("frame_error", (self.output / "windows.log").read_text())
        self.assertEqual(self.run_windows()["videos_done"], 1)

    def test_publication_interruption_resumes_completed_video(self):
        real_replace = Path.replace

        def interrupted(path, target):
            if path.name == ".windows.jsonl.tmp":
                raise OSError("simulated publication failure")
            return real_replace(path, target)

        with patch.object(Path, "replace", interrupted):
            with self.assertRaisesRegex(OSError, "publication failure"):
                self.run_windows()
        self.assertEqual(
            json.loads((self.output / "window_state.json").read_text())["status"],
            "publishing",
        )
        self.assertEqual(self.run_windows()["videos_skipped"], 1)

    def test_checkpoint_corruption_preserved_before_resume(self):
        from pipeline.cctv import congestion

        real_save = congestion.save_json

        def interrupted(path, value):
            if "errors" in value:
                raise KeyboardInterrupt()
            return real_save(path, value)

        with patch.object(congestion, "save_json", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_windows()
        with closing(sqlite3.connect(self.output / "windows.db")) as conn:
            with conn:
                conn.execute("UPDATE videos SET payload='[]'")
        with self.assertRaisesRegex(ValueError, "Checkpoint video corrupted"):
            self.run_windows()

    def test_roi_preview_resume_without_mutating_tracking(self):
        from scripts.preview_cctv_roi import prepare

        output = self.fixture.root / "roi-previews"
        result = prepare(
            self.fixture.manifest, self.fixture.traffic, self.fixture.output, output
        )
        self.assertEqual((result["done"], result["error"]), (1, 0))
        path = output / "N001-V001.png"
        original = (digest(path), path.stat().st_mtime_ns)
        self.assertEqual(
            prepare(
                self.fixture.manifest, self.fixture.traffic, self.fixture.output, output
            )["skipped"],
            1,
        )
        self.assertEqual(original, (digest(path), path.stat().st_mtime_ns))


if __name__ == "__main__":
    unittest.main()
