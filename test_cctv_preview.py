"""Verify MP4 preview timestamps, read-only inputs and publication resume."""

import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_cctv_index as index_tests
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.preview import prepare_previews, probe


@unittest.skipUnless(shutil.which("ffmpeg"), "Requires FFmpeg")
class CCTVPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = index_tests.CCTVIndexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.raw = fixture.raw / "source.mov"
        # Overwrite only this test's temporary synthetic placeholder.
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=25",
                "-t",
                "1.6",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-output_ts_offset",
                "0.2",
                str(self.raw),
            ],
            capture_output=True,
            check=True,
        )
        fixture.build()
        self.output = fixture.root / "previews"

    def run_preview(self) -> dict:
        return prepare_previews(self.fixture.output, self.output)

    def test_preview_first_pts_resume_and_source_unchanged(self) -> None:
        before = (digest(self.raw), self.raw.stat().st_mtime_ns)
        self.assertEqual(self.run_preview(), {"done": 1, "skipped": 0, "error": 0})
        path = self.output / "N001-V001.mp4"
        self.assertEqual(probe(path)["codec"], "h264")
        self.assertAlmostEqual(probe(path)["first_pts"], 0, places=3)
        emitted = (digest(path), path.stat().st_mtime_ns)
        self.assertEqual(self.run_preview(), {"done": 0, "skipped": 1, "error": 0})
        self.assertEqual(emitted, (digest(path), path.stat().st_mtime_ns))
        self.assertEqual(before, (digest(self.raw), self.raw.stat().st_mtime_ns))

    def test_corrupt_preview_is_preserved_and_not_replaced(self) -> None:
        self.run_preview()
        path = self.output / "N001-V001.mp4"
        with path.open("ab") as handle:
            handle.write(b"corrupt")
        before = digest(path)
        self.assertEqual(self.run_preview()["error"], 1)
        self.assertEqual(digest(path), before)

    def test_interrupted_publish_resumes_matching_owned_mp4(self) -> None:
        def interrupt(path, payload):
            if (
                payload.get("videos", {}).get("N001-V001", {}).get("status")
                == "complete"
            ):
                raise KeyboardInterrupt
            return save_json(path, payload)

        with patch("pipeline.cctv.preview.save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_preview()
        path = self.output / "N001-V001.mp4"
        before = (digest(path), path.stat().st_mtime_ns)
        self.assertEqual(self.run_preview(), {"done": 0, "skipped": 1, "error": 0})
        self.assertEqual(before, (digest(path), path.stat().st_mtime_ns))
        state = json.loads((self.output / "preview_state.json").read_text())
        self.assertEqual(state["videos"]["N001-V001"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
