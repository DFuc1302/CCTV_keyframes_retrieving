"""Portable archive boundaries, dependency rebasing and committed segment recovery."""

import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pipeline.cctv.cloud import (
    pack,
    unpack,
    tracking_checkpoint,
    import_result,
    code_identity,
    run_job,
)
from pipeline.cctv.ingest import digest, save_json
from app.cctv_workflow import Workflow
import test_cctv_track as fixtures
from pipeline.cctv.track import track_videos


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_code_identity_ignores_checkout_newlines_not_code_changes(self):
        windows = self.root / "windows"
        linux = self.root / "linux"
        for root in (windows, linux):
            (root / "app").mkdir(parents=True)
        (windows / "app/main.py").write_bytes(b"value = 1\r\nprint(value)\r\n")
        (linux / "app/main.py").write_bytes(b"value = 1\nprint(value)\n")
        self.assertEqual(code_identity(windows), code_identity(linux))
        (linux / "app/main.py").write_bytes(b"value = 2\nprint(value)\n")
        self.assertNotEqual(code_identity(windows), code_identity(linux))

    def test_archive_checksums_nested_metadata_and_unsafe_entries(self):
        folder = self.root / "payload"
        (folder / "nested").mkdir(parents=True)
        save_json(folder / "nested/cloud.json", dict(value=1))
        archive = self.root / "job.zip"
        pack(folder, archive, dict(kind="test"))
        self.assertIn(
            "nested/cloud.json", unpack(archive, self.root / "valid")["files"]
        )
        for names in (("../escape",), ("C:/escape",), ("same", "SAME")):
            with zipfile.ZipFile(archive, "w") as output:
                for name in names:
                    output.writestr(name, "bad")
            with self.assertRaises(ValueError):
                unpack(archive, self.root / "unsafe")
        pack(folder, archive, dict(kind="test"))
        with zipfile.ZipFile(archive, "a") as output:
            output.writestr("extra", "uncovered")
        with self.assertRaisesRegex(ValueError, "coverage"):
            unpack(archive, self.root / "extra")


    def test_cloud_resume_skips_completed_stage_after_runtime_loss(self):
        repo = self.root / "repo"
        repo.mkdir()
        payload = self.root / "job-payload"
        (payload / "raw").mkdir(parents=True)
        (payload / "raw/VID.mp4").write_bytes(b"demo")
        archive = self.root / "job.zip"
        pack(
            payload,
            archive,
            dict(
                kind="cctv-cloud-job-v1",
                code=code_identity(repo),
                dataset_id="a" * 32,
                name="demo",
                preparation={},
                commit="b" * 40,
                videos=[
                    dict(
                        video_id="VID",
                        camera_id="CAM",
                        source="VID.mp4",
                        sha256=digest(payload / "raw/VID.mp4"),
                    )
                ],
            ),
        )
        calls = []

        def command(workflow, module, args, run):
            calls.append(module)
            if module == "second" and calls.count("second") == 1:
                raise ValueError("runtime lost")
            (run / (module + ".txt")).write_text("complete")

        with (
            patch("app.cctv_workflow.ROOT", repo),
            patch.object(
                Workflow,
                "stages",
                return_value=[("first", "first", []), ("second", "second", [])],
            ),
            patch.object(Workflow, "command", command),
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.get_device_name", return_value="fixture"),
        ):
            with self.assertRaisesRegex(ValueError, "runtime lost"):
                run_job(repo, archive, self.root / "drive")
            owned_run = repo / "outputs/ui-workflow" / ("a" * 32) / "run-colab"
            shutil.rmtree(owned_run)
            result = run_job(repo, archive, self.root / "drive")
            self.assertEqual(calls, ["first", "second", "second"])
            unpack(result, self.root / "result")

    def test_result_rebases_paths_sqlite_and_dependency_hashes(self):
        repo = self.root / "repo"
        repo.mkdir()
        raw = self.root / "raw"
        raw.mkdir()
        source = raw / "VID.mp4"
        source.write_bytes(b"read-only demo")
        original = (digest(source), source.stat().st_mtime_ns)
        payload = self.root / "payload"
        run = payload / "run"
        (run / "configs").mkdir(parents=True)
        (run / "search-index").mkdir()
        (run / "processed/keyframes").mkdir(parents=True)
        remote = "/content/repo/outputs/run"
        save_json(
            run / "configs/manifest.json",
            dict(
                schema_version=1,
                raw_root="/content/Demo_Data",
                cameras=[dict(camera_id="CAM")],
                videos=[dict(video_id="VID", camera_id="CAM", path="VID.mp4")],
            ),
        )
        save_json(
            run / "search-index/index_manifest.json",
            dict(keyframes_root=remote + "/processed/keyframes"),
        )
        database = run / "tracks.db"
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("CREATE TABLE metadata(signature TEXT)")
            conn.execute(
                "INSERT INTO metadata VALUES (?)",
                (
                    json.dumps(
                        dict(manifest_sha256=digest(run / "configs/manifest.json"))
                    ),
                ),
            )
            conn.execute("CREATE TABLE cctv_frames(source_video TEXT)")
            conn.execute(
                "INSERT INTO cctv_frames VALUES (?)", ("/content/Demo_Data/VID.mp4",)
            )
        save_json(
            run / "state.json",
            dict(
                db=digest(database),
                index=digest(run / "search-index/index_manifest.json"),
            ),
        )
        data = dict(
            preparation=dict(tracking_mode="sampled"),
            videos=[dict(video_id="VID", camera_id="CAM", source=str(source))],
        )
        archive = self.root / "result.zip"
        pack(
            payload,
            archive,
            dict(
                kind="cctv-cloud-result-v1",
                remote_root="/content/repo",
                remote_run=remote,
                code=code_identity(repo),
                preparation=data["preparation"],
                commit="a" * 40,
                origin_job_sha256="b" * 64,
                videos=[
                    dict(
                        video_id="VID",
                        camera_id="CAM",
                        source="VID.mp4",
                        sha256=digest(source),
                    )
                ],
            ),
        )
        destination = self.root / "imported"
        import_result(repo, archive, data, destination)
        state = json.loads((destination / "state.json").read_text())
        self.assertEqual(state["db"], digest(destination / "tracks.db"))
        self.assertEqual(
            state["index"], digest(destination / "search-index/index_manifest.json")
        )
        with closing(sqlite3.connect(destination / "tracks.db")) as conn:
            self.assertEqual(
                conn.execute("SELECT source_video FROM cctv_frames").fetchone()[0],
                str(source),
            )
            signature = json.loads(
                conn.execute("SELECT signature FROM metadata").fetchone()[0]
            )
        self.assertEqual(
            signature["manifest_sha256"], digest(destination / "configs/manifest.json")
        )
        self.assertEqual((digest(source), source.stat().st_mtime_ns), original)
        self.assertFalse(
            json.loads((destination / "cloud-provenance.json").read_text())[
                "cloud_quality_evaluated"
            ]
        )

    def test_preprocessing_proof_rejects_changes(self):
        workflow = Workflow(
            self.root / "datasets", lambda: None, lambda run: None, lambda: None, "cpu"
        )
        data = dict(id="a" * 32)
        folder = workflow.root / data["id"]
        folder.mkdir(parents=True)
        save_json(folder / "dataset.json", data)
        source = folder / "old"
        source.mkdir()
        for name in ("inventory", "processed", "search-index", "video-preview"):
            (source / name).mkdir()
            (source / name / "file").write_text("verified")
        save_json(
            folder / "preprocessing.json",
            dict(run=str(source), key={}, files=workflow.preprocessing_files(source)),
        )
        new = folder / "new"
        new.mkdir()
        with patch.object(workflow, "preprocessing_key", return_value={}):
            self.assertTrue(workflow.reuse_preprocessing(data, new))
            self.assertTrue(workflow.reuse_preprocessing(data, new))
            (source / "processed/file").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "changed"):
                workflow.reuse_preprocessing(data, new)


class SegmentRecoveryTests(unittest.TestCase):
    setUp = fixtures.TrackingTests.setUp

    def test_drive_snapshot_resumes_committed_segment(self):
        archive = self.root / "drive/tracking.zip"
        calls = []

        def checkpoint(conn, state):
            tracking_checkpoint(conn, state, archive)
            calls.append(True)
            raise KeyboardInterrupt()

        args = (self.manifest, self.traffic, self.model, self.tracker, self.output)
        options = dict(
            device="cpu",
            segment_seconds=1,
            backend_factory=fixtures.FakeTracker,
            reader_factory=fixtures.fake_reader,
        )
        with self.assertRaises(KeyboardInterrupt):
            track_videos(*args, checkpoint=checkpoint, **options)
        self.assertEqual(len(calls), 1)
        restored = self.root / "restored"
        unpack(archive, restored)
        shutil.rmtree(self.output)
        shutil.copytree(restored / "tracking", self.output)
        resumed = track_videos(*args, **options)
        self.assertEqual(resumed["error"], 0)
        self.assertGreater(resumed["frames_skipped"], 0)
        self.assertEqual(resumed["frames_done"] + resumed["frames_skipped"], 6)


if __name__ == "__main__":
    unittest.main()
