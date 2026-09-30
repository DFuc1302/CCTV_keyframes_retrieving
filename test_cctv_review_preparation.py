"""Pending-only review preparation with clean inputs and immutable resume."""

import json
import unittest
from unittest.mock import patch

from PIL import Image

import test_cctv_index as fixture_module
from pipeline.cctv.ingest import digest, save_json
from scripts.prepare_cctv_review import prepare


class ReviewPreparationTests(unittest.TestCase):
    """Use tiny temporary images and a fake preview, never CCTV/model weights."""

    def setUp(self):
        self.fixture = fixture_module.CCTVIndexTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.build()
        root = self.fixture.root
        self.manifest = root / "review-manifest.json"
        save_json(
            self.manifest,
            dict(
                schema_version=1,
                raw_root=str(self.fixture.raw),
                cameras=[dict(camera_id="N001")],
                videos=[
                    dict(camera_id="N001", video_id="N001-V001", path="source.mov")
                ],
            ),
        )
        sha = digest(self.manifest)
        self.annotations, self.splits, self.rois = (
            root / n for n in ("annotations.json", "splits.json", "rois.json")
        )
        save_json(
            self.annotations,
            dict(
                schema_version=1,
                mode="independent",
                source_manifest_sha256=sha,
                intervals=[
                    dict(
                        camera_id="N001",
                        video_id="N001-V001",
                        start_sec=0.0,
                        end_sec=2.0,
                        review_status="pending",
                        reviewed_by=[],
                        reviewed_at=None,
                        blind_to_predictions=False,
                        events=[],
                    )
                ],
            ),
        )
        save_json(
            self.splits,
            dict(
                schema_version=1,
                mode="independent",
                source_manifest_sha256=sha,
                camera_holdout=False,
                videos=[
                    dict(
                        video_id="N001-V001",
                        recording_group_id="UNVERIFIED",
                        grouping_status="unverified",
                        split="unassigned",
                    )
                ],
            ),
        )
        save_json(
            self.rois,
            dict(
                schema_version=1,
                cameras=[
                    dict(
                        camera_id="N001",
                        status="pending",
                        polygon=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                    )
                ],
            ),
        )
        self.previews = root / "previews"
        self.previews.mkdir()
        video = self.previews / "N001-V001.mp4"
        video.write_bytes(b"synthetic preview only")
        save_json(
            self.previews / "preview_state.json",
            dict(
                pipeline="cctv-preview-v1",
                videos={
                    "N001-V001": dict(
                        status="complete",
                        file=video.name,
                        signature=dict(
                            source=str(self.fixture.raw / "source.mov"),
                            sha256=digest(self.fixture.raw / "source.mov"),
                        ),
                        output_sha256=digest(video),
                        duration_sec=3.0,
                        seek_offset_sec=0.25,
                    )
                },
            ),
        )
        # Inputs in a dedicated subtree let protected parent overlap checks stay meaningful.
        self.inputs = root / "inputs"
        self.inputs.mkdir()
        for attr in ("manifest", "annotations", "splits", "rois"):
            original = getattr(self, attr)
            target = self.inputs / original.name
            original.replace(target)
            setattr(self, attr, target)
        # Moving does not change the manifest bytes, hence the original SHA remains valid.
        self.output = root / "review-output"

    def run_bundle(self, output=None):
        return prepare(
            self.manifest,
            self.annotations,
            self.splits,
            self.rois,
            self.fixture.output,
            self.previews,
            output or self.output,
        )

    def footprint(self):
        return {
            str(p.relative_to(self.output)): (digest(p), p.stat().st_mtime_ns)
            for p in self.output.rglob("*")
            if p.is_file() and p.name != ".ingest.lock"
        }

    def test_pending_forms_clean_frames_and_source_offset(self):
        result = self.run_bundle()
        self.assertTrue(result["passed"])
        self.assertEqual(result["samples"], 4)
        self.assertEqual(result["reviewed_intervals"], 0)
        self.assertEqual(
            (self.output / "forms/annotations.pending.json").read_bytes(),
            self.annotations.read_bytes(),
        )
        page = (self.output / "labeling/index.html").read_text(encoding="utf-8")
        self.assertNotIn("roi/", page)
        self.assertNotIn("/cctv/events", page)
        self.assertIn('data-seek="0.25"', page)
        report = json.loads((self.output / "review_manifest.json").read_text())
        for k, sample in enumerate(report["intervals"][0]["samples"], 1):
            original = self.fixture.folder / f"{k:03d}.jpg"
            self.assertEqual(digest(self.output / sample["path"]), digest(original))
        self.assertFalse(report["split_ready"])
        self.assertFalse(report["operational_ready"])

    def test_resume_preserves_all_hashes_mtimes_and_skips_render(self):
        self.run_bundle()
        before = self.footprint()
        with patch(
            "scripts.prepare_cctv_review.Image.open",
            side_effect=AssertionError("skip render"),
        ):
            self.assertEqual(self.run_bundle()["skipped"], 1)
        self.assertEqual(before, self.footprint())

    def test_edited_or_missing_bundle_artifact_is_preserved(self):
        self.run_bundle()
        page = self.output / "labeling/index.html"
        original = page.read_bytes()
        page.write_bytes(b"human edit")
        with self.assertRaisesRegex(ValueError, "Edited bundle"):
            self.run_bundle()
        self.assertEqual(page.read_bytes(), b"human edit")
        page.write_bytes(original)
        page.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.run_bundle()

    def test_frame_error_is_logged_and_resume_can_finish(self):
        original = Image.open
        calls = 0

        def flaky(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient decode failure")
            return original(*args, **kwargs)

        with patch("scripts.prepare_cctv_review.Image.open", side_effect=flaky):
            result = self.run_bundle()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertFalse((self.output / "forms/annotations.pending.json").exists())
        self.assertTrue(self.run_bundle()["passed"])

    def test_source_or_template_change_rejected(self):
        self.run_bundle()
        self.annotations.write_bytes(self.annotations.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "Inputs/recipe changed"):
            self.run_bundle()
        source = self.fixture.raw / "source.mov"
        source.write_bytes(b"changed synthetic source")
        with self.assertRaisesRegex(ValueError, "Source/preview"):
            self.run_bundle(self.fixture.root / "new-output")

    def test_foreign_and_raw_output_are_preserved(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.run_bundle(self.fixture.raw / "out")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_bundle()
        self.assertEqual((self.output / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
