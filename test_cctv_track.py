"""Check tracking segments, native timestamps and replay without real YOLO weights."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.track import DecodedFrame, TrackFrame, decoded_frames, track_videos


class FakeTracker:
    """Stable one-object IDs with explicit segment resets; not a tracking benchmark."""

    loads = 0
    closes = 0

    def __init__(self, *args):
        FakeTracker.loads += 1
        self.resets = 0

    def reset(self):
        self.resets += 1

    def process(self, image):
        return [
            {
                "track_id": 1,
                "detection": {
                    "label": "car",
                    "confidence": 0.8,
                    "bbox": [0.1, 0.2, 0.6, 0.8],
                },
            }
        ]

    def memory(self):
        return {}

    def close(self):
        FakeTracker.closes += 1


def fake_reader(path, start, duration):
    """Six consecutive decoded ordinals with PTS, no inferred frame-rate timestamps."""
    for n in range(6):
        stamp = n * 0.4
        if stamp >= start and (duration is None or stamp < start + duration):
            yield DecodedFrame(
                n,
                stamp,
                100 + n * 400,
                100,
                (1, 1000),
                Image.new("RGB", (64, 48), (n, n, n)),
            )


class TrackingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        (self.raw / "source.mov").write_bytes(b"synthetic read-only raw source")
        config = self.root / "configs"
        config.mkdir()
        self.manifest = config / "manifest.json"
        save_json(
            self.manifest,
            {
                "schema_version": 1,
                "raw_root": str(self.raw),
                "cameras": [{"camera_id": "N001"}],
                "videos": [
                    {"video_id": "N001-V001", "camera_id": "N001", "path": "source.mov"}
                ],
            },
        )
        self.traffic = config / "traffic.json"
        save_json(
            self.traffic,
            {
                "schema_version": 1,
                "cameras": [{"camera_id": "N001", "status": "pending"}],
            },
        )
        self.tracker = config / "tracker.yaml"
        self.tracker.write_bytes(
            (Path(__file__).parent / "configs/cctv.bytetrack.yaml").read_bytes()
        )
        cache = self.root / "cache"
        cache.mkdir()
        weights = cache / "fake.pt"
        weights.write_bytes(b"fake weights never loaded by torch")
        self.model = cache / "model.json"
        save_json(
            self.model,
            {
                "model": "fake",
                "revision": "test",
                "path": str(weights),
                "sha256": digest(weights),
            },
        )
        self.output = self.root / "tracks"
        FakeTracker.loads = FakeTracker.closes = 0

    def run_track(self, factory=FakeTracker, reader=fake_reader, **kwargs):
        return track_videos(
            self.manifest,
            self.traffic,
            self.model,
            self.tracker,
            self.output,
            duration=2.4,
            segment_seconds=1.0,
            device="cpu",
            backend_factory=factory,
            reader_factory=reader,
            **kwargs,
        )

    def test_complete_segment_scope_empty_frames_and_resume(self):
        class EmptyFrame(FakeTracker):
            def process(self, image):
                return [] if image.getpixel((0, 0))[0] == 1 else super().process(image)

        source = (
            digest(self.raw / "source.mov"),
            (self.raw / "source.mov").stat().st_mtime_ns,
        )
        result = self.run_track(EmptyFrame)
        self.assertEqual(
            (result["segments_done"], result["frames_done"], result["error"]), (3, 6, 0)
        )
        records = [
            TrackFrame.model_validate_json(line)
            for line in (self.output / "frames.jsonl").read_text().splitlines()
        ]
        self.assertEqual([r.locator.frame_idx for r in records], list(range(6)))
        self.assertEqual(records[1].observations, ())
        keys = {r.observations[0].track_key for r in records if r.observations}
        self.assertEqual(len(keys), 3)
        before = {
            p: (digest(p), p.stat().st_mtime_ns)
            for p in self.output.rglob("*")
            if p.is_file() and p.suffix != ".lock"
        }
        result = self.run_track(EmptyFrame)
        self.assertEqual((result["frames_skipped"], result["model_loaded"]), (6, False))
        self.assertEqual((FakeTracker.loads, FakeTracker.closes), (1, 1))
        for path, expected in before.items():
            self.assertEqual((digest(path), path.stat().st_mtime_ns), expected)
        self.assertEqual(
            (
                digest(self.raw / "source.mov"),
                (self.raw / "source.mov").stat().st_mtime_ns,
            ),
            source,
        )

    def test_interrupted_segment_replays_without_duplicating_committed_one(self):
        class Interrupted(FakeTracker):
            def process(self, image):
                if self.resets == 2:
                    raise KeyboardInterrupt()
                return super().process(image)

        with self.assertRaises(KeyboardInterrupt):
            self.run_track(Interrupted)
        with closing(sqlite3.connect(self.output / "tracks.db")) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM frames").fetchone()[0], 3
            )
        result = self.run_track()
        self.assertEqual((result["frames_done"], result["frames_skipped"]), (3, 3))
        self.assertEqual(FakeTracker.closes, 2)

    def test_frame_failure_discards_segment_and_continues_next_segment(self):
        class BadFrame(FakeTracker):
            def process(self, image):
                if image.getpixel((0, 0))[0] == 3:
                    raise RuntimeError("synthetic frame failure")
                return super().process(image)

        result = self.run_track(BadFrame)
        self.assertEqual((result["frames_done"], result["error"]), (4, 1))
        self.assertFalse((self.output / "tracks.jsonl").exists())
        result = self.run_track()
        self.assertEqual(
            (result["frames_done"], result["frames_skipped"], result["error"]),
            (2, 4, 0),
        )

    def test_video_ids_isolate_reused_track_ids(self):
        data = json.loads(self.manifest.read_text())
        (self.raw / "other.mov").write_bytes(b"another synthetic input")
        data["videos"].append(
            {"video_id": "N001-V002", "camera_id": "N001", "path": "other.mov"}
        )
        save_json(self.manifest, data)
        result = self.run_track()
        self.assertEqual(result["frames_done"], 12)
        rows = [
            json.loads(line)
            for line in (self.output / "tracks.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            len(
                {
                    (r["locator"]["video_id"], r["segment_id"], r["track_id"])
                    for r in rows
                }
            ),
            6,
        )
        self.assertEqual(FakeTracker.loads, 1)

    def test_corrupt_artifacts_and_recipe_changes_are_preserved(self):
        self.run_track()
        with self.assertRaisesRegex(ValueError, "NEW output"):
            self.run_track(start=0.1)
        target = self.output / "tracks.jsonl"
        target.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_track()
        self.assertEqual(target.read_text(), "damaged")

    def test_publication_interruption_resumes_without_model(self):
        def interrupt(path, value):
            if path.name == "track_state.json" and value.get("status") == "complete":
                raise KeyboardInterrupt()
            save_json(path, value)

        with patch("pipeline.cctv.track.save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_track()
        result = self.run_track()
        self.assertEqual((result["frames_skipped"], result["model_loaded"]), (6, False))
        self.assertEqual(FakeTracker.loads, 1)

    def test_foreign_and_raw_output_are_not_touched(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("original")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.run_track()
        self.assertEqual(marker.read_text(), "original")
        self.output = self.raw / "derived"
        with self.assertRaisesRegex(ValueError, "overlaps protected"):
            self.run_track()
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg fixture generator required")
    def test_real_decoder_preserves_offset_vfr_pts_and_ordinals(self):
        path = self.raw / "vfr.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x48:r=8:d=1.5",
                "-vf",
                r"select=not(eq(n\,2)),setpts=PTS+0.25/TB",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-video_track_timescale",
                "1000",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        frames = list(decoded_frames(path, 0, None))
        try:
            self.assertEqual(len(frames), 11)
            self.assertEqual([f.frame_idx for f in frames], list(range(11)))
            self.assertAlmostEqual(frames[0].timestamp_sec, 0)
            self.assertAlmostEqual(frames[2].timestamp_sec, 0.375)
            self.assertGreater(frames[0].origin_pts, 0)
        finally:
            for frame in frames:
                frame.image.close()

    def test_frame_contract_rejects_fabricated_pts(self):
        self.run_track()
        first = json.loads((self.output / "frames.jsonl").read_text().splitlines()[0])
        first["source_pts"] += 100
        with self.assertRaisesRegex(ValueError, "source PTS"):
            TrackFrame.model_validate_json(json.dumps(first))

    def test_real_bytetrack_recovers_low_score_detection_without_new_id(self):
        """Exercise real CPU association, including the second low-confidence stage."""
        import numpy as np

        (self.root / "sdk-cache").mkdir()
        with patch.dict(
            "os.environ",
            {
                "YOLO_CONFIG_DIR": str(self.root / "sdk-cache"),
                "YOLO_AUTOINSTALL": "false",
            },
        ):
            from types import SimpleNamespace

            import yaml
            from ultralytics.engine.results import Boxes
            from ultralytics.trackers.byte_tracker import BYTETracker

            tracker = BYTETracker(
                SimpleNamespace(**yaml.safe_load(self.tracker.read_text()))
            )

            def boxes(x, score):
                return Boxes(
                    np.array([[x, 10, x + 20, 35, score, 2]], dtype=np.float32),
                    (48, 64),
                )

            first = tracker.update(boxes(10, 0.8))
            rescued = tracker.update(boxes(11, 0.2))
            self.assertEqual(len(rescued), 1)
            self.assertEqual(int(first[0][4]), int(rescued[0][4]))
            missing = tracker.update(
                Boxes(np.empty((0, 6), dtype=np.float32), (48, 64))
            )
            self.assertEqual(len(missing), 0)
            recovered = tracker.update(boxes(12, 0.8))
            self.assertEqual(int(first[0][4]), int(recovered[0][4]))

    def test_corrupt_partial_segment_blocks_resume_before_model_load(self):
        class Interrupted(FakeTracker):
            def process(self, image):
                if self.resets == 2:
                    raise KeyboardInterrupt()
                return super().process(image)

        with self.assertRaises(KeyboardInterrupt):
            self.run_track(Interrupted)
        with closing(sqlite3.connect(self.output / "tracks.db")) as conn:
            with conn:
                conn.execute("UPDATE frames SET payload='{}' WHERE frame_idx=0")
        with self.assertRaisesRegex(ValueError, "Corrupt segment"):
            self.run_track()
        self.assertEqual(FakeTracker.loads, 1)


if __name__ == "__main__":
    unittest.main()
