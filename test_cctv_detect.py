"""Verify real transactional detection outputs with a synthetic CPU detector."""

from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

import test_cctv_index as index_tests
from pipeline.cctv.detect import detect_frames
from pipeline.cctv.ingest import digest, save_json


class FakeDetector:
    """Produce boxes without weights; these tests do not measure detection quality."""

    loads = 0
    closes = 0

    def __init__(self, *args):
        FakeDetector.loads += 1
        self.calls = 0

    def predict(self, images):
        self.calls += 1
        return [
            [{"label": "car", "confidence": 0.75, "bbox": [0.1, 0.1, 0.7, 0.8]}]
            for _ in images
        ]

    def memory(self):
        return {}

    def close(self):
        FakeDetector.closes += 1


class DetectionTests(unittest.TestCase):
    def setUp(self):
        """Reuse tiny ingestion artifacts without importing another TestCase by name."""
        fixture = index_tests.CCTVIndexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.source = fixture.root, fixture.source
        dataset_path = self.source / "dataset_manifest.json"
        dataset = json.loads(dataset_path.read_text())
        dataset["cameras"] = [{"camera_id": "N001"}]
        save_json(dataset_path, dataset)
        config_dir, cache_dir = self.root / "configs", self.root / "cache"
        config_dir.mkdir()
        cache_dir.mkdir()
        self.config = config_dir / "traffic.json"
        save_json(
            self.config,
            {
                "schema_version": 1,
                "cameras": [{"camera_id": "N001", "status": "pending"}],
            },
        )
        weights = cache_dir / "fake.pt"
        weights.write_bytes(
            b"synthetic detector fixture - never loaded as torch weights"
        )
        self.model = cache_dir / "model.json"
        save_json(
            self.model,
            {
                "model": "fake",
                "revision": "test",
                "path": str(weights),
                "sha256": digest(weights),
            },
        )
        self.output = self.root / "detections"
        FakeDetector.loads = FakeDetector.closes = 0

    def run_detect(self, factory=FakeDetector, **kwargs):
        return detect_frames(
            self.source,
            self.output,
            self.model,
            self.config,
            device="cpu",
            detector_factory=factory,
            **kwargs,
        )

    def test_complete_resume_preserves_input_and_published_bytes(self):
        original = {
            p: (digest(p), p.stat().st_mtime_ns)
            for p in self.source.rglob("*")
            if p.is_file()
        }
        result = self.run_detect()
        self.assertEqual((result["done"], result["error"]), (4, 0))
        records = [
            json.loads(line)
            for line in (self.output / "detections.jsonl").read_text().splitlines()
        ]
        self.assertEqual([r["locator"]["frame_idx"] for r in records], [0, 13, 25, 38])
        self.assertTrue((self.output / "previews/N001-V001.jpg").is_file())
        published = {
            p: (digest(p), p.stat().st_mtime_ns)
            for p in self.output.rglob("*")
            if p.is_file() and p.suffix != ".lock"
        }
        result = self.run_detect()
        self.assertEqual((result["skipped"], result["model_loaded"]), (4, False))
        self.assertEqual((FakeDetector.loads, FakeDetector.closes), (1, 1))
        for path, expected in {**original, **published}.items():
            self.assertEqual((digest(path), path.stat().st_mtime_ns), expected)

    def test_interrupt_keeps_committed_frames_and_closes_model(self):
        class Interrupted(FakeDetector):
            def predict(self, images):
                if self.calls:
                    raise KeyboardInterrupt()
                return super().predict(images)

        with self.assertRaises(KeyboardInterrupt):
            self.run_detect(Interrupted)
        with sqlite3.connect(self.output / "detections.db") as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM frames").fetchone()[0], 2
            )
        result = self.run_detect()
        self.assertEqual((result["done"], result["skipped"]), (2, 2))
        self.assertEqual(FakeDetector.closes, 2)

    def test_bad_frame_record_does_not_discard_other_frames(self):
        class InvalidBox(FakeDetector):
            def predict(self, images):
                rows = super().predict(images)
                for image, detections in zip(images, rows):
                    if (
                        image.getpixel((0, 0))[1] > 200
                        and image.getpixel((0, 0))[0] < 10
                    ):
                        detections[0]["bbox"] = [0.7, 0.1, 0.2, 0.9]
                return rows

        result = self.run_detect(InvalidBox)
        self.assertEqual((result["done"], result["error"]), (3, 1))
        self.assertFalse((self.output / "detections.jsonl").exists())
        result = self.run_detect()
        self.assertEqual(
            (result["done"], result["skipped"], result["error"]), (1, 3, 0)
        )

    def test_failed_batch_splits_to_bounded_single_images(self):
        class SplitDetector(FakeDetector):
            def predict(self, images):
                if len(images) > 1:
                    raise RuntimeError("synthetic batch failure")
                return super().predict(images)

        self.assertEqual(self.run_detect(SplitDetector)["done"], 4)

    def test_changed_recipe_and_unowned_output_preserved(self):
        self.run_detect()
        checksum = digest(self.output / "detections.db")
        with self.assertRaisesRegex(ValueError, "NEW output"):
            self.run_detect(stride=2)
        self.assertEqual(digest(self.output / "detections.db"), checksum)
        self.output = self.root / "foreign"
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("do not change")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_detect()
        self.assertEqual(marker.read_text(), "do not change")

    def test_corrupt_publication_is_preserved_not_rebuilt(self):
        self.run_detect()
        target = self.output / "detections.jsonl"
        target.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_detect()
        self.assertEqual(target.read_text(), "damaged")
        self.assertEqual(FakeDetector.loads, 1)

    def test_publication_interruption_recovers_without_model(self):
        def interrupt_complete(path, value):
            if path.name == "detect_state.json" and value.get("status") == "complete":
                raise KeyboardInterrupt()
            save_json(path, value)

        with patch("pipeline.cctv.detect.save_json", side_effect=interrupt_complete):
            with self.assertRaises(KeyboardInterrupt):
                self.run_detect()
        self.assertEqual(
            json.loads((self.output / "detect_state.json").read_text())["status"],
            "publishing",
        )
        result = self.run_detect()
        self.assertEqual((result["skipped"], result["model_loaded"]), (4, False))
        self.assertEqual(FakeDetector.loads, 1)

    def test_protected_source_and_config_reject_output_overlap(self):
        for target in (
            self.source / "derived",
            self.root / "raw/derived",
            self.config.parent / "derived",
        ):
            self.output = target
            with self.assertRaisesRegex(ValueError, "overlaps protected"):
                self.run_detect()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
