"""T10 failure/retry, provenance and non-approval regressions."""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pipeline.cctv.ingest import digest, save_json
from scripts import verify_cctv_t10 as module


class T10Tests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "outputs/dataset/run"
        (self.run / "configs").mkdir(parents=True)
        for folder in ("scripts", "app", "pipeline/cctv", "static/cctv", "raw"):
            (self.root / folder).mkdir(parents=True)
        self.source = self.root / "raw/video.mp4"
        self.source.write_bytes(b"read-only source fixture")
        for name in module.TESTS:
            (self.root / (name + ".py")).write_text("# fixture", encoding="utf-8")
        (self.root / "scripts/inventory_cctv_evaluation.py").write_text("# fixture")
        save_json(
            self.run / "configs/manifest.json",
            dict(
                schema_version=1,
                raw_root=str(self.root / "raw"),
                cameras=[dict(camera_id="CAM")],
                videos=[dict(video_id="VID", camera_id="CAM", path="video.mp4")],
            ),
        )
        save_json(
            self.run / "configs/roi.json",
            dict(
                schema_version=1,
                cameras=[
                    dict(
                        camera_id="CAM",
                        status="pending",
                        polygon=[[0, 0], [1, 0], [1, 1], [0, 1]],
                    )
                ],
            ),
        )
        self.output = self.root / "outputs/dataset/t10"
        self.addCleanup(patch.stopall)
        patch.object(module, "ROOT", self.root).start()
        patch.object(module.platform, "platform", return_value="Windows fixture").start()
        self.inventory = patch.object(
            module, "inventory", return_value={"done": 1}
        ).start()
        self.cpu = patch.object(
            module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ).start()
        self.http = patch.object(
            module, "workflow_http", return_value={"passed": True}
        ).start()

    def verify(self):
        return module.verify(self.run, self.output, "http://127.0.0.1:8087")

    def test_failed_http_retry_skips_cpu_and_keeps_pending_and_source(self):
        before = (digest(self.source), self.source.stat().st_mtime_ns)
        self.http.side_effect = ValueError("stale journal")
        with self.assertRaisesRegex(ValueError, "stale journal"):
            self.verify()
        self.assertEqual(
            json.loads((self.output / "state.json").read_text())["status"], "failed"
        )
        self.http.side_effect = None
        result = self.verify()
        self.assertEqual(result["status"], "PASSED_DEMO_CHECKS")
        self.assertIsNone(result["quality_metrics"])
        self.assertEqual(result["m8"], "PENDING")
        self.assertEqual(result["second_machine"], "NOT_RUN")
        self.assertEqual(result["checks"]["roi"]["cameras"][0]["approval"], "pending")
        self.assertEqual(self.cpu.call_count, 1)
        self.assertEqual(self.inventory.call_count, 1)
        self.assertEqual(before, (digest(self.source), self.source.stat().st_mtime_ns))
        self.assertTrue(self.verify()["resumed"])
        self.assertEqual(self.http.call_count, 2)
        self.assertTrue((self.output / "report.md").exists())

    def test_changed_source_or_checkpoint_refuses_completed_resume(self):
        self.verify()
        fixture = self.output / "fixtures.json"
        fixture.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checkpoint changed"):
            self.verify()
        self.source.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "Inputs/code changed"):
            self.verify()
        self.assertEqual(self.cpu.call_count, 1)


if __name__ == "__main__":
    unittest.main()
