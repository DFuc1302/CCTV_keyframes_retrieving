"""Workflow upload/recipe boundaries and serial worker failure/retry checks."""

import io
import json
import subprocess
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cctv_workflow import Workflow, install
from pipeline.cctv.ingest import save_json


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.calls = []
        self.workflow = Workflow(
            self.root / "datasets",
            lambda: self.calls.append("release"),
            lambda run: self.calls.append("activate"),
            lambda: self.calls.append("restore"),
            "cpu",
        )
        app = FastAPI()
        install(app, self.workflow)
        self.client = TestClient(app)
        self.origin = {"Origin": "http://testserver"}

    def create(self):
        response = self.client.post(
            "/cctv/workflow/datasets", json={"name": "Test"}, headers=self.origin
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def test_upload_prepare_stream_limits_and_origin(self):
        self.assertEqual(
            self.client.post(
                "/cctv/workflow/datasets", json={"name": "Bad"}
            ).status_code,
            403,
        )
        dataset = self.create()
        path = self.root / "source.mp4"
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
                "2",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        response = self.client.post(
            f"/cctv/workflow/{dataset}/upload?filename=video.mp4",
            content=path.read_bytes(),
            headers=self.origin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        video = response.json()["videos"][0]
        self.assertNotIn("source", video)
        self.assertEqual(
            self.client.get(
                f"/cctv/workflow/{dataset}/frame/{video['video_id']}"
            ).status_code,
            200,
        )
        payload = dict(
            cameras={video["video_id"]: "CAM_1"},
            rois={"CAM_1": [[0, 0], [1, 0], [1, 1], [0, 1]]},
            start_sec=0,
            duration_sec=1,
        )
        self.assertEqual(
            self.client.post(
                f"/cctv/workflow/{dataset}/prepare", json=payload, headers=self.origin
            ).status_code,
            200,
        )
        payload["duration_sec"] = 61
        self.assertEqual(
            self.client.post(
                f"/cctv/workflow/{dataset}/prepare", json=payload, headers=self.origin
            ).status_code,
            400,
        )
        with patch("app.cctv_workflow.LIMIT", 1):
            response = self.client.post(
                f"/cctv/workflow/{dataset}/upload?filename=too-big.mp4",
                content=b"12",
                headers=self.origin,
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.workflow.get(dataset)["videos"]), 1)
        self.assertFalse(
            list((self.workflow.folder(dataset) / "uploads").glob("*.partial"))
        )
        self.assertEqual(
            self.client.post(
                f"/cctv/workflow/{dataset}/upload?filename=../video.mp4",
                content=b"x",
                headers=self.origin,
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/cctv/workflow/{dataset}/prepare",
                content=b"x" * 65537,
                headers=self.origin,
            ).status_code,
            413,
        )

    def test_worker_retry_new_recipe_and_bundle(self):
        dataset = self.create()
        folder = self.workflow.folder(dataset)
        data = self.workflow.get(dataset)
        data["preparation"] = dict(start_sec=0, duration_sec=10)
        data.pop("job")
        data.pop("active")
        save_json(folder / "dataset.json", data)
        with (
            patch.object(self.workflow, "stages", return_value=[("Check", "sys", [])]),
            patch("app.cctv_workflow.subprocess.Popen") as process,
        ):
            process.return_value.wait.return_value = 1
            self.workflow.start(dataset)
            self.workflow.thread.join(5)
            self.assertEqual(self.workflow.get(dataset)["job"]["status"], "failed")
            self.assertEqual(self.calls, ["release", "restore"])
            first_run = self.workflow.get(dataset)["job"]["run"]
            process.return_value.wait.return_value = 0
            self.workflow.start(dataset, retry=True)
            self.workflow.thread.join(5)
            self.assertEqual(self.workflow.get(dataset)["job"]["status"], "complete")
            self.assertEqual(self.workflow.get(dataset)["run"], first_run)
        run = Path(first_run)
        save_json(run / "preparation.json", data["preparation"])
        with zipfile.ZipFile(io.BytesIO(self.workflow.bundle(dataset))) as archive:
            self.assertEqual(json.loads(archive.read("summary.json"))["m8"], "PENDING")
            self.assertFalse(any(name.endswith(".mp4") for name in archive.namelist()))
        # A running thread blocks all new jobs and recipe changes.
        with patch.object(self.workflow, "thread") as thread:
            thread.is_alive.return_value = True
            with self.assertRaisesRegex(ValueError, "Đang xử lý"):
                self.workflow.start(dataset)

    def test_shutdown_preserves_checkpoint_without_reloading_model(self):
        dataset = self.create()
        run = self.workflow.folder(dataset) / "run-test"
        run.mkdir()
        job = dict(run=str(run), snapshot={}, download=False)
        self.workflow.closing = True
        self.workflow.cancelled.set()
        with patch.object(self.workflow, "stages", return_value=[("Check", "sys", [])]):
            self.workflow.worker(dataset, job)
        self.assertEqual(self.workflow.get(dataset)["job"]["status"], "cancelled")
        self.assertEqual(self.calls, ["release"])


if __name__ == "__main__":
    unittest.main()
