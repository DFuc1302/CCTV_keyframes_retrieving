"""Read-only optional overlay routes, source offsets and publication invalidation."""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_cctv_api as api_fixture
import test_cctv_track as track_fixture
import test_cctv_traffic_api as traffic_fixture
from app.cctv import create_app
from pipeline.cctv import tracking_overlay
from pipeline.cctv.ingest import digest, save_json


class OverlayApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = traffic_fixture.TrafficApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.track = self.fixture.track
        self.output = self.track.root / "overlay"
        with patch.object(
            tracking_overlay, "decoded_frames", side_effect=track_fixture.fake_reader
        ):
            tracking_overlay.run(self.track.manifest, self.track.output, self.output)

    def client(self, output=None):
        api = self.fixture.api
        app = create_app(
            api.fixture.output,
            api.fixture.root / "model.json",
            api.preview,
            "cpu",
            encoder_factory=api_fixture.ApiEncoder,
            traffic_dir=self.fixture.metrics.output,
            tracking_dir=self.track.output,
            roi_preview_dir=self.fixture.roi,
            overlay_dir=output or self.output,
            overlay_manifest=self.track.manifest,
        )
        return api.stack.enter_context(TestClient(app))

    def test_offset_range_and_readonly_keep_raw_preview_separate(self):
        before = {
            p: (digest(p), p.stat().st_mtime_ns)
            for p in self.output.iterdir()
            if p.name != ".ingest.lock"
        }
        client = self.client()
        status = client.get("/cctv/traffic/overlays").json()
        self.assertTrue(status["available"] and status["read_only"])
        self.assertFalse(status["operational_ready"] or status["roi_approved"])
        row = client.get("/cctv/traffic/windows/N001-V001?offset=1&limit=1").json()[
            "rows"
        ][0]
        overlay = row["tracking_overlay"]
        self.assertAlmostEqual(overlay["video_seek_sec"], 1.2)
        self.assertAlmostEqual(row["evidence"]["video_seek_sec"], 1.45)
        response = client.get(overlay["video_url"], headers={"Range": "bytes=0-15"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 16)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertTrue(response.headers["content-range"].startswith("bytes 0-15/"))
        self.assertEqual(client.post(overlay["video_url"], json={}).status_code, 405)
        self.assertEqual(client.get("/cctv/traffic/overlay/unknown").status_code, 404)
        self.assertNotIn(str(self.track.root), json.dumps(status))
        self.assertEqual(before, {p: (digest(p), p.stat().st_mtime_ns) for p in before})
        self.assertEqual(api_fixture.ApiEncoder.loaded, 1)

    def test_missing_overlay_does_not_disable_traffic_or_retrieval(self):
        client = self.client(self.track.root / "missing")
        self.assertFalse(client.get("/cctv/traffic/overlays").json()["available"])
        self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)
        self.assertTrue(client.get("/cctv/traffic").json()["available"])
        row = client.get("/cctv/traffic/windows/N001-V001").json()["rows"][0]
        self.assertIsNone(row["tracking_overlay"])
        self.assertEqual(client.get("/cctv/traffic/overlay/N001-V001").status_code, 503)

    def test_live_clip_edit_invalidates_overlay_only(self):
        client = self.client()
        (self.output / "N001-V001.mp4").write_bytes(b"changed")
        self.assertEqual(client.get("/cctv/traffic/overlay/N001-V001").status_code, 503)
        self.assertTrue(client.get("/cctv/traffic").json()["available"])
        self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)

    def test_wrong_offset_rejected_at_start(self):
        path = self.output / "overlay_state.json"
        state = json.loads(path.read_text())
        state["videos"]["N001-V001"]["playback_to_source_offset_sec"] = 0.5
        save_json(path, state)
        self.assertFalse(
            self.client().get("/cctv/traffic/overlays").json()["available"]
        )

    def test_unexpected_input_path_is_rejected_without_reading_it(self):
        path = self.output / "overlay_state.json"
        state = json.loads(path.read_text())
        state["signature"]["inputs"][str(self.track.root / "secret.txt")] = "0" * 64
        save_json(path, state)
        client = self.client()
        self.assertFalse(client.get("/cctv/traffic/overlays").json()["available"])

    def test_false_validation_flags_rejected(self):
        path = self.output / "overlay_state.json"
        state = json.loads(path.read_text())
        state["videos"]["N001-V001"]["roi_approved"] = True
        save_json(path, state)
        self.assertFalse(
            self.client().get("/cctv/traffic/overlays").json()["available"]
        )

    def test_checkpoint_live_edit_invalidates_snapshot(self):
        client = self.client()
        path = self.output / "overlay_state.json"
        path.write_text(path.read_text() + "\n", encoding="utf-8")
        self.assertFalse(client.get("/cctv/traffic/overlays").json()["available"])


if __name__ == "__main__":
    unittest.main()
