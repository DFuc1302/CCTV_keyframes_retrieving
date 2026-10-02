"""Validate input protection and resume with tiny synthetic videos, not raw CCTV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline.cctv.ingest import digest, output_lease, run_ingestion, validate_artifacts
from pipeline.cctv.manifest import ManifestError, load_manifest


@unittest.skipUnless(shutil.which("ffmpeg"), "Requires FFmpeg for synthetic media")
class CCTVIngestionTests(unittest.TestCase):
    """Run the inherited extractor and inspect real output maps and JPEGs."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "synthetic inputs"
        self.raw.mkdir()
        self.video = self.raw / "đường phố sample.avi"
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=160x90:rate=10", "-t", "1.6", "-an", "-c:v", "mjpeg",
            "-threads", "1", "-q:v", "3", str(self.video),
        ], capture_output=True, check=True, timeout=30)
        self.path, self.output = self.root / "manifest.json", self.root / "processed"
        self.payload = {
            "schema_version": 1, "raw_root": str(self.raw),
            "cameras": [{"camera_id": "CAM_01"}],
            "videos": [{"video_id": "ROAD_A", "camera_id": "CAM_01", "path": self.video.name}],
        }
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def run_pipeline(self) -> dict[str, int]:
        return run_ingestion(self.path, self.output)

    def test_legacy_map_timestamps_and_read_only_input(self) -> None:
        before = (digest(self.video), self.video.stat().st_mtime_ns)
        self.assertEqual(self.run_pipeline(), {"done": 1, "skipped": 0, "error": 0})
        mapping = self.output / "map-keyframes" / "ROAD_A.csv"
        with mapping.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ["n", "pts_time", "fps", "frame_idx"])
            rows = list(reader)
        self.assertEqual([float(r["pts_time"]) for r in rows], [0., 0.5, 1., 1.5])
        self.assertEqual([int(r["frame_idx"]) for r in rows], [0, 5, 10, 15])
        self.assertEqual(before, (digest(self.video), self.video.stat().st_mtime_ns))
        self.assertFalse(json.loads((self.output / "dataset_manifest.json").read_text(encoding="utf-8"))["serving_ready"])

    def test_rerun_skips_without_rewriting_images(self) -> None:
        self.run_pipeline()
        image = self.output / "keyframes" / "ROAD_A" / "001.jpg"
        before = (digest(image), image.stat().st_mtime_ns)
        self.assertEqual(self.run_pipeline(), {"done": 0, "skipped": 1, "error": 0})
        self.assertEqual(before, (digest(image), image.stat().st_mtime_ns))

    def test_resume_after_interrupted_publication(self) -> None:
        self.run_pipeline()
        state_path = self.output / "ingest_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["videos"]["ROAD_A"]["status"] = "running"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        (self.output / "map-keyframes" / "ROAD_A.csv").unlink()
        image = self.output / "keyframes" / "ROAD_A" / "001.jpg"
        before = (digest(image), image.stat().st_mtime_ns)
        self.assertEqual(self.run_pipeline()["done"], 1)
        self.assertEqual(before, (digest(image), image.stat().st_mtime_ns))
        validate_artifacts(image.parent, self.output / "map-keyframes" / "ROAD_A.csv")

    def test_modified_input_or_recipe_preserves_published_outputs(self) -> None:
        self.run_pipeline()
        image = self.output / "keyframes" / "ROAD_A" / "001.jpg"
        original = digest(image)
        self.assertEqual(run_ingestion(self.path, self.output, device="cuda")["error"], 1)
        self.assertEqual(digest(image), original)
        with self.video.open("ab") as handle:
            handle.write(b"changed synthetic input")
        self.assertEqual(self.run_pipeline()["error"], 1)
        self.assertEqual(digest(image), original)

    def test_decode_interruption_leaves_resumable_checkpoint_and_releases_lock(self) -> None:
        with patch("pipeline.cctv.ingest.legacy.extract_one", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pipeline()
        state = json.loads((self.output / "ingest_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["videos"]["ROAD_A"]["status"], "running")
        self.assertEqual(self.run_pipeline()["done"], 1)

    def test_corrupt_frame_is_detected_and_preserved(self) -> None:
        self.run_pipeline()
        image = self.output / "keyframes" / "ROAD_A" / "001.jpg"
        image.write_bytes(b"damaged generated frame")
        self.assertEqual(self.run_pipeline()["error"], 1)
        self.assertEqual(image.read_bytes(), b"damaged generated frame")
        self.assertIn("frame_error", (self.output / "logs" / "ingest.log").read_text(encoding="utf-8"))

    def test_bad_video_does_not_block_next_video(self) -> None:
        (self.raw / "broken.avi").write_bytes(b"invalid synthetic video")
        self.payload["videos"].insert(0, {"video_id": "BROKEN", "camera_id": "CAM_01", "path": "broken.avi"})
        self.write()
        self.assertEqual(self.run_pipeline(), {"done": 1, "skipped": 0, "error": 1})
        state = json.loads((self.output / "ingest_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["videos"]["ROAD_A"]["status"], "complete")
        self.assertEqual(state["videos"]["BROKEN"]["status"], "error")

    def test_input_output_overlap_and_empty_dataset_rejected(self) -> None:
        for output in (self.raw, self.raw / "generated", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                run_ingestion(self.path, output)
        self.payload["videos"] = []
        self.write()
        with self.assertRaisesRegex(ManifestError, "No videos"):
            self.run_pipeline()
        self.assertFalse(self.output.exists())

    def test_foreign_output_is_not_adopted_even_after_second_attempt(self) -> None:
        frames = self.output / "keyframes" / "ROAD_A"
        frames.mkdir(parents=True)
        foreign = frames / "001.jpg"
        foreign.write_bytes(b"original foreign asset")
        for _ in range(2):
            self.assertEqual(self.run_pipeline()["error"], 1)
        self.assertEqual(foreign.read_bytes(), b"original foreign asset")

    def test_active_output_lock_blocks_second_writer(self) -> None:
        self.output.mkdir()
        with output_lease(self.output):
            with self.assertRaisesRegex(RuntimeError, "Another ingestion"):
                self.run_pipeline()

    def test_process_exit_releases_output_lock(self) -> None:
        self.output.mkdir()
        child = (
            "import os,sys; from pathlib import Path; "
            "from pipeline.cctv.ingest import output_lease; "
            "lease=output_lease(Path(sys.argv[1])); lease.__enter__(); os._exit(0)"
        )
        subprocess.run(
            [sys.executable, "-c", child, str(self.output)],
            cwd=Path(__file__).parent, check=True, timeout=30,
        )
        self.assertEqual(self.run_pipeline(), {"done": 1, "skipped": 0, "error": 0})


if __name__ == "__main__":
    unittest.main()
