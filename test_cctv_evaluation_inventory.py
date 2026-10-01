"""Read-only metadata inventory, pending forms, failed-video retry and resume."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pipeline.cctv.benchmark_events import SplitManifest
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.traffic import TrafficConfig
from scripts.inventory_cctv_evaluation import inventory


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "Requires FFmpeg"
)
class EvaluationInventoryTests(unittest.TestCase):
    def test_pending_resume_retry_and_protection(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            raw = root / "raw"
            raw.mkdir()
            video = raw / "source.mov"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=25",
                    "-t",
                    "0.4",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    str(video),
                ],
                check=True,
                capture_output=True,
            )
            manifest = root / "manifest.json"
            save_json(
                manifest,
                dict(
                    schema_version=1,
                    raw_root=str(raw),
                    cameras=[dict(camera_id="CAM")],
                    videos=[dict(video_id="VID", camera_id="CAM", path="source.mov")],
                ),
            )
            source_before = (digest(video), video.stat().st_mtime_ns)
            output = root / "inventory"
            with patch(
                "scripts.inventory_cctv_evaluation.probe",
                side_effect=ValueError("bad probe"),
            ):
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    inventory(manifest, output)
            self.assertFalse((output / "inventory.json").exists())
            self.assertFalse(inventory(manifest, output)["resumed"])
            rows = json.loads((output / "inventory.json").read_text())["videos"]
            self.assertEqual(rows[0]["width"], 160)
            self.assertEqual(rows[0]["nominal_fps"], "25")
            self.assertIsNone(
                json.loads((output / "content.pending.json").read_text())["videos"][0][
                    "content_tags"
                ]
            )
            SplitManifest.model_validate_json(
                (output / "splits.pending.json").read_text()
            )
            TrafficConfig.model_validate_json((output / "roi.pending.json").read_text())
            artifacts = list(output.glob("*.json"))
            before = [(digest(p), p.stat().st_mtime_ns) for p in artifacts]
            with patch(
                "scripts.inventory_cctv_evaluation.probe",
                side_effect=AssertionError("must skip"),
            ):
                self.assertTrue(inventory(manifest, output)["resumed"])
            self.assertEqual(
                before, [(digest(p), p.stat().st_mtime_ns) for p in artifacts]
            )
            self.assertEqual(source_before, (digest(video), video.stat().st_mtime_ns))
            with self.assertRaisesRegex(ValueError, "overlaps"):
                inventory(manifest, raw / "outputs")
            (output / "content.pending.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "publication changed"):
                inventory(manifest, output)


if __name__ == "__main__":
    unittest.main()
