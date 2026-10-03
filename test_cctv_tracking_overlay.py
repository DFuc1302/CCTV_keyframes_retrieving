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

    def run_overlay(self, reader=fixtures.fake_reader, stitched_dir=None):
        with patch.object(module, "decoded_frames", side_effect=reader):
            return module.run(
                self.manifest, self.tracking, self.overlay, stitched_dir=stitched_dir
            )

    def test_h264_native_pts_and_noop_resume(self):
        result = self.run_overlay()
        self.assertEqual(
            result["id_scope"], "run_id/camera_id/video_id/segment_id/track_id"
        )
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

    def stitch_fixture(self):
        from pipeline.cctv.stitch import stitch_tracks

        directory = self.root / "stitched"
        stitch_tracks(self.tracking, directory)
        return directory

    def test_stitched_h264_pts_resume_and_local_scope_default(self):
        stitched = self.stitch_fixture()
        result = self.run_overlay(stitched_dir=stitched)
        self.assertEqual(result["id_scope"], "derived_global_track_id/geometric_hypothesis")
        self.assertTrue(result["videos"][0]["pts_verified"])
        with av.open(str(self.overlay / "N001-V001.mp4")) as source:
            self.assertEqual(source.streams.video[0].codec_context.name, "h264")
            self.assertEqual(
                [round(float(f.pts * f.time_base), 6) for f in source.decode(video=0)],
                [0.0, 0.4, 0.8, 1.2, 1.6, 2.0],
            )
        with patch.object(module, "decoded_frames", side_effect=AssertionError("resume")):
            self.assertEqual(
                module.run(self.manifest, self.tracking, self.overlay,
                           stitched_dir=stitched)["skipped"], 1
            )
        with self.assertRaisesRegex(ValueError, "Inputs/recipe changed"):
            self.run_overlay()

    def test_stitched_tamper_and_output_ownership(self):
        stitched = self.stitch_fixture()
        with self.assertRaises(ValueError):
            module.run(self.manifest, self.tracking, stitched / "overlay",
                       stitched_dir=stitched)
        self.run_overlay(stitched_dir=stitched)
        (stitched / "stitched_mapping.json").write_text("{}")
        with patch.object(module, "decoded_frames", side_effect=AssertionError("tamper")):
            with self.assertRaises(ValueError):
                self.run_overlay(stitched_dir=stitched)

    def test_stitched_code_change_invalidates_overlay_resume(self):
        from pathlib import Path
        from pipeline.cctv import stitch

        stitched = self.stitch_fixture()
        self.run_overlay(stitched_dir=stitched)
        code = Path(stitch.__file__).resolve()
        real_digest = module.digest

        def changed_digest(path):
            return "0" * 64 if Path(path).resolve() == code else real_digest(path)

        with patch.object(module, "digest", side_effect=changed_digest):
            with self.assertRaisesRegex(ValueError, "Inputs/recipe changed"):
                self.run_overlay(stitched_dir=stitched)

    def test_missing_stitched_identity_does_not_fall_back(self):
        stitched = self.stitch_fixture()
        with patch("pipeline.cctv.stitch.load_mapping", return_value={}):
            with self.assertRaisesRegex(ValueError, "clips failed"):
                self.run_overlay(stitched_dir=stitched)
        self.assertFalse((self.overlay / "N001-V001.mp4").exists())

    def test_stitched_mapping_is_bound_to_tracking_publication(self):
        stitched = self.stitch_fixture()
        state_path = self.tracking / "track_state.json"
        state = json.loads(state_path.read_text())
        state["run_id"] = "another-run"
        save_json(state_path, state)
        publication_path = self.tracking / "tracking_manifest.json"
        publication = json.loads(publication_path.read_text())
        publication["run_id"] = "another-run"
        save_json(publication_path, publication)
        state["publication"]["tracking_manifest.json"] = digest(publication_path)
        save_json(state_path, state)
        with self.assertRaises(ValueError):
            self.run_overlay(stitched_dir=stitched)

    def test_stitched_overlay_rejects_source_pts_mismatch(self):
        stitched = self.stitch_fixture()

        def bad_reader(*args):
            for frame in fixtures.fake_reader(*args):
                frame.source_pts += 1
                yield frame

        with self.assertRaisesRegex(ValueError, "clips failed"):
            self.run_overlay(bad_reader, stitched_dir=stitched)
        self.assertFalse((self.overlay / "N001-V001.mp4").exists())

    def test_stitched_labels_colors_and_banner_use_global_identity(self):
        from PIL import Image
        from pipeline.cctv.track import TrackFrame

        with closing(sqlite3.connect(self.tracking / "tracks.db")) as conn:
            payload = conn.execute(
                "SELECT payload FROM frames ORDER BY frame_idx LIMIT 1"
            ).fetchone()[0]
        record = TrackFrame.model_validate_json(payload)
        mapping = {
            (record.run_id, record.locator.camera_id, record.locator.video_id,
             record.segment_id, observation.track_id): 101
            for observation in record.observations
        }
        self.assertTrue(record.observations)
        with Image.new("RGB", (640, 480)) as image:
            with patch.object(module.ImageDraw, "Draw") as draw_factory:
                with module.draw_overlay(image, record, 640, mapping):
                    pass
        calls = draw_factory.return_value.text.call_args_list
        identity_calls = [c for c in calls if c.args[1].startswith("GLOBAL 101 ")]
        self.assertEqual(len(identity_calls), len(record.observations))
        self.assertEqual(
            identity_calls[0].kwargs["fill"],
            (64 + 101 * 67 % 192, 64 + 101 * 101 % 192, 64 + 101 * 37 % 192),
        )
        self.assertTrue(any("GEOMETRIC STITCHED" in c.args[1] for c in calls))
        self.assertTrue(any("NOT VALIDATED RE-ID" in c.args[1] for c in calls))


if __name__ == "__main__":
    unittest.main()
