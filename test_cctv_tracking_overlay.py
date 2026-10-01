"""H264/PTS and immutable overlay output checks with synthetic tracking fixtures."""

import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import av

import test_cctv_track as fixtures
from pipeline.cctv import tracking_overlay as module
from pipeline.cctv.ingest import digest, save_json


class TrackingOverlayTests(unittest.TestCase):
    def setUp(self):
        fixtures.TrackingTests.setUp(self)
        fixtures.TrackingTests.run_track(self)
        self.tracking = self.output
        self.overlay = self.root / "overlay"

    def run_overlay(self, reader=fixtures.fake_reader):
        with patch.object(module, "decoded_frames", side_effect=reader):
            return module.run(self.manifest, self.tracking, self.overlay)

    def test_h264_native_pts_and_noop_resume(self):
        result = self.run_overlay()
        self.assertEqual(result["videos"][0]["frames"], 6)
        self.assertTrue(result["videos"][0]["pts_verified"])
        self.assertFalse(result["gpu_inference"] or result["operational_ready"])
        video = self.overlay / "N001-V001.mp4"
        with av.open(str(video)) as source:
            self.assertEqual(source.streams.video[0].codec_context.name, "h264")
            self.assertEqual(
                [round(float(f.pts * f.time_base), 6) for f in source.decode(video=0)],
                [0.0, 0.4, 0.8, 1.2, 1.6, 2.0],
            )
        before = {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in self.overlay.iterdir()
            if p.name != ".ingest.lock"
        }
        with patch.object(
            module, "decoded_frames", side_effect=AssertionError("No decode on resume")
        ):
            self.assertEqual(
                module.run(self.manifest, self.tracking, self.overlay)["skipped"], 1
            )
        self.assertEqual(
            before,
            {
                p.name: (digest(p), p.stat().st_mtime_ns)
                for p in self.overlay.iterdir()
                if p.name != ".ingest.lock"
            },
        )

    def test_raw_edit_rejected_before_decode(self):
        (self.raw / "source.mov").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Raw video checksum"):
            self.run_overlay()

    def test_ledger_changed_rejected_before_decode(self):
        (self.tracking / "frames.jsonl").write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_overlay()

    def test_source_pts_mismatch_fails_clip(self):
        def bad_reader(*args):
            for frame in fixtures.fake_reader(*args):
                frame.source_pts += 1
                yield frame

        with self.assertRaisesRegex(ValueError, "clips failed"):
            self.run_overlay(bad_reader)
        state = json.loads((self.overlay / "overlay_state.json").read_text())
        self.assertEqual(state["status"], "incomplete")
        self.assertEqual(state["videos"], {})
        self.assertFalse((self.overlay / "N001-V001.mp4").exists())

    def test_missing_decoded_frame_does_not_publish_partial(self):
        def missing_reader(*args):
            for frame in fixtures.fake_reader(*args):
                if frame.frame_idx == 5:
                    frame.image.close()
                else:
                    yield frame

        with self.assertRaisesRegex(ValueError, "clips failed"):
            self.run_overlay(missing_reader)
        self.assertFalse((self.overlay / "N001-V001.mp4").exists())

    def test_camera_mismatch_even_with_resealed_sqlite(self):
        database = self.tracking / "tracks.db"
        with closing(sqlite3.connect(database)) as conn, conn:
            data = json.loads(
                conn.execute("SELECT payload FROM frames WHERE frame_idx=0").fetchone()[
                    0
                ]
            )
            data["locator"]["camera_id"] = "N002"
            for row in data["observations"]:
                row["locator"]["camera_id"] = "N002"
            conn.execute(
                "UPDATE frames SET payload=? WHERE frame_idx=0", (json.dumps(data),)
            )
        state_path = self.tracking / "track_state.json"
        state = json.loads(state_path.read_text())
        state["publication"]["tracks.db"] = digest(database)
        save_json(state_path, state)
        with self.assertRaisesRegex(ValueError, "clips failed"):
            self.run_overlay()

    def test_edited_overlay_and_recipe_change_preserved(self):
        self.run_overlay()
        target = self.overlay / "N001-V001.mp4"
        target.write_bytes(b"human edit")
        with self.assertRaisesRegex(ValueError, "Edited overlay"):
            self.run_overlay()
        self.assertEqual(target.read_bytes(), b"human edit")
        with self.assertRaisesRegex(ValueError, "Inputs/recipe changed"):
            module.run(self.manifest, self.tracking, self.overlay, width=640)

    def test_source_foreign_output_and_width_guards(self):
        for folder in (
            self.raw / "generated",
            self.tracking / "generated",
            self.manifest.parent / "generated",
        ):
            with self.assertRaises(ValueError):
                module.run(self.manifest, self.tracking, folder)
        self.overlay.mkdir()
        (self.overlay / "human.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_overlay()
        with self.assertRaisesRegex(ValueError, "Width"):
            module.run(self.manifest, self.tracking, self.root / "other", width=321)
        self.assertEqual((self.overlay / "human.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
