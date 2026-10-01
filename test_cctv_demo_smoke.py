"""Owned server lifecycle and busy-port guards, without loading model weights."""

import os
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import run_cctv_demo_smoke as module


class DemoSmokeTests(unittest.TestCase):
    def setUp(self):
        base = module.ROOT / "outputs/tests"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "report"
        self.server = MagicMock()
        self.server.should_exit = False

        def serve(sockets):
            self.assertEqual(len(sockets), 1)
            while not self.server.should_exit:
                time.sleep(0.005)

        self.server.run.side_effect = serve
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.factory = patch.object(
            module.uvicorn, "Server", return_value=self.server
        ).start()
        self.reserve = patch.object(
            module, "reserve_port", return_value=self.listener
        ).start()
        self.ready = patch.object(module, "wait_ready").start()
        self.verify = patch.object(
            module, "verify_demo", return_value=dict(passed=True, protected_files=100)
        ).start()
        self.addCleanup(patch.stopall)

    def test_owned_server_stops_and_environment_restores(self):
        with patch.dict(os.environ, {"CCTV_DEVICE": "original"}):
            result = module.run(self.output)
            self.assertEqual(os.environ["CCTV_DEVICE"], "original")
        self.assertTrue(result["passed"] and result["owned_server_stopped"])
        self.assertTrue(self.server.should_exit)
        self.assertEqual(self.listener.fileno(), -1)
        self.factory.assert_called_once()

    def test_verification_failure_stops_owned_server(self):
        self.verify.side_effect = ValueError("bad provenance")
        with self.assertRaisesRegex(ValueError, "provenance"):
            module.run(self.output)
        self.assertTrue(self.server.should_exit)
        self.assertEqual(self.listener.fileno(), -1)

    def test_readiness_failure_stops_without_verifying(self):
        self.ready.side_effect = RuntimeError("not ready")
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            module.run(self.output)
        self.verify.assert_not_called()
        self.assertTrue(self.server.should_exit)

    def test_existing_server_is_not_started_or_stopped(self):
        result = module.run(self.output, existing_server=True)
        self.assertFalse(result["managed_server"] or result["owned_server_stopped"])
        self.factory.assert_not_called()
        self.reserve.assert_not_called()
        self.assertFalse(self.server.should_exit)

    def test_existing_report_preserved_before_model_loading(self):
        self.output.mkdir()
        target = self.output / "human.txt"
        target.write_text("keep")
        with self.assertRaisesRegex(ValueError, "NEW empty"):
            module.run(self.output)
        self.factory.assert_not_called()
        self.assertEqual(target.read_text(), "keep")

    def test_busy_port_rejected_before_server_factory(self):
        self.reserve.side_effect = RuntimeError("CLIP was not loaded")
        with self.assertRaisesRegex(RuntimeError, "CLIP was not loaded"):
            module.run(self.output)
        self.factory.assert_not_called()

    def test_worker_startup_failure_propagates_and_closes_socket(self):
        self.server.run.side_effect = RuntimeError("model absent")
        self.ready.side_effect = lambda url, timeout, worker, errors: worker.join(1)
        with self.assertRaisesRegex(RuntimeError, "model absent"):
            module.run(self.output)
        self.assertEqual(self.listener.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
