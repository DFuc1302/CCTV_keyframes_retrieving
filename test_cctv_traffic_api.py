"""Traffic endpoints use synthetic committed pipelines and the existing tiny CLIP."""

from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

import test_cctv_api as api_module
import test_cctv_congestion as metrics_module
from app.cctv import create_app
from app.cctv_traffic import Signal
from pipeline.cctv.ingest import digest
from scripts.preview_cctv_roi import prepare


class TrafficApiTests(unittest.TestCase):
    def setUp(self):
        self.api = api_module.CCTVApiTests()
        self.api.setUp()
        self.addCleanup(self.api.doCleanups)
        self.metrics = metrics_module.WindowPipelineTests()
        self.metrics.setUp()
        self.addCleanup(self.metrics.doCleanups)
        self.metrics.run_windows()
        self.track = self.metrics.fixture
        self.roi = self.track.root / "roi-previews"
        prepare(self.track.manifest, self.track.traffic, self.track.output, self.roi)
        # All video bytes are synthetic; give retrieval the same source identity.
        source = self.api.fixture.raw / "source.mov"
        source.write_bytes((self.track.raw / "source.mov").read_bytes())
        preview_state = self.api.preview / "preview_state.json"
        value = json.loads(preview_state.read_text())
        value["videos"]["N001-V001"]["signature"]["sha256"] = digest(source)
        value["videos"]["N001-V001"]["seek_offset_sec"] = 0.25
        preview_state.write_text(json.dumps(value))

    def client(self, traffic_dir=None):
        app = create_app(
            self.api.fixture.output,
            self.api.fixture.root / "model.json",
            self.api.preview,
            "cpu",
            encoder_factory=api_module.ApiEncoder,
            traffic_dir=traffic_dir or self.metrics.output,
            tracking_dir=self.track.output,
            roi_preview_dir=self.roi,
            trusted_origins=("http://testserver",),
        )
        return self.api.stack.enter_context(TestClient(app))

    def test_ready_paginated_windows_roi_and_video_seek_are_read_only(self):
        protected = [
            self.metrics.output,
            self.track.output,
            self.roi,
            self.api.fixture.output,
        ]
        before = {
            p: (digest(p), p.stat().st_mtime_ns)
            for folder in protected
            for p in folder.rglob("*")
            if p.is_file()
        }
        client = self.client()
        readiness = client.get("/ready").json()
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["capabilities"]["traffic"])
        status = client.get("/cctv/traffic").json()
        self.assertTrue(status["available"])
        self.assertTrue(status["demo_only"])
        self.assertFalse(status["roi_ready"])
        self.assertFalse(status["operational_ready"])
        result = client.get("/cctv/traffic/windows/N001-V001?offset=1&limit=1").json()
        self.assertEqual((result["total"], len(result["rows"])), (3, 1))
        row = result["rows"][0]
        self.assertEqual(row["window"]["start_sec"], 1.0)
        self.assertEqual(row["evidence"]["frame_idx"], 3)
        self.assertAlmostEqual(row["evidence"]["video_seek_sec"], 1.45)
        self.assertNotIn(str(self.track.root), json.dumps(result))
        self.assertEqual(
            client.get(result["video"]["roi_image_url"]).headers["content-type"],
            "image/png",
        )
        self.assertEqual(
            client.get(
                row["evidence"]["video_url"], headers={"Range": "bytes=2-5"}
            ).status_code,
            206,
        )
        self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)
        self.assertEqual(api_module.ApiEncoder.loaded, 1)
        self.assertEqual(before, {p: (digest(p), p.stat().st_mtime_ns) for p in before})

    def rewrite_window_ledger(self, mutate):
        path = self.metrics.output / "windows.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            mutate(row)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        state_path = self.metrics.output / "window_state.json"
        state = json.loads(state_path.read_text())
        state["artifacts"]["windows.jsonl"] = digest(path)
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def test_historical_windows_missing_metric_defaults_load_without_rewriting(self):
        def historical(row):
            row.pop("median_speed_kmh")
            row.pop("calibration_sha256")
        self.rewrite_window_ledger(historical)
        path = self.metrics.output / "windows.jsonl"
        before = (digest(path), path.stat().st_mtime_ns)
        client = self.client()
        self.assertTrue(client.get("/cctv/traffic").json()["available"])
        self.assertEqual(before, (digest(path), path.stat().st_mtime_ns))

    def test_window_normalization_preserves_unknown_field_and_value_rejection(self):
        self.rewrite_window_ledger(lambda row: row.update(unknown_metric=1))
        self.assertFalse(self.client().get("/cctv/traffic").json()["available"])

    def test_window_normalization_preserves_ledger_mismatch_rejection(self):
        self.rewrite_window_ledger(lambda row: row.update(median_speed_kmh=40))
        self.assertFalse(self.client().get("/cctv/traffic").json()["available"])

    def test_missing_traffic_does_not_disable_retrieval(self):
        client = self.client(self.track.root / "missing")
        self.assertEqual(client.get("/ready").status_code, 200)
        self.assertFalse(client.get("/cctv/traffic").json()["available"])
        self.assertEqual(client.get("/cctv/traffic/windows/N001-V001").status_code, 503)
        self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)
        self.assertEqual(api_module.ApiEncoder.loaded, 1)

    def test_corrupted_startup_artifact_is_unavailable(self):
        (self.metrics.output / "signals.jsonl").write_text("corrupt")
        client = self.client()
        self.assertFalse(client.get("/ready").json()["capabilities"]["traffic"])
        response = client.get("/cctv/traffic/windows/N001-V001")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.track.root), response.text)

    def test_source_provenance_mismatch_is_unavailable(self):
        source = self.api.fixture.raw / "source.mov"
        source.write_bytes(b"different synthetic source")
        path = self.api.preview / "preview_state.json"
        value = json.loads(path.read_text())
        value["videos"]["N001-V001"]["signature"]["sha256"] = digest(source)
        path.write_text(json.dumps(value))
        client = self.client()
        self.assertTrue(client.get("/ready").json()["ready"])
        self.assertFalse(client.get("/cctv/traffic").json()["available"])

    def test_roi_provenance_mismatch_is_unavailable(self):
        path = self.roi / "roi_preview_state.json"
        value = json.loads(path.read_text())
        value["signature"]["roi"] = "different"
        path.write_text(json.dumps(value))
        self.assertFalse(self.client().get("/cctv/traffic").json()["available"])

    def test_live_edit_invalidates_traffic_snapshot(self):
        client = self.client()
        self.assertTrue(client.get("/cctv/traffic").json()["available"])
        (self.roi / "N001-V001.png").write_bytes(b"changed after startup")
        self.assertEqual(client.get("/cctv/traffic/roi/N001-V001").status_code, 503)
        self.assertFalse(client.get("/ready").json()["traffic"]["available"])
        self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)

    def test_unknown_assets_and_invalid_pagination_are_denied(self):
        client = self.client()
        for url in (
            "/cctv/traffic/roi/unknown",
            "/cctv/traffic/windows/unknown",
            "/cctv/traffic/roi/%2e%2e%2fwindow_state.json",
        ):
            self.assertEqual(client.get(url).status_code, 404)
        for query in ("limit=0", "limit=501", "offset=-1"):
            self.assertEqual(
                client.get("/cctv/traffic/windows/N001-V001?" + query).status_code, 422
            )
        self.assertEqual(
            client.get("/cctv/traffic/windows/N001-V001?offset=100").json()["rows"], []
        )

    def test_signal_rejects_fake_approval_or_missing_motion(self):
        payload = json.loads(
            (self.metrics.output / "signals.jsonl").read_text().splitlines()[0]
        )
        payload["demo_only"] = False
        with self.assertRaises(ValueError):
            Signal.model_validate_json(json.dumps(payload))
        payload["demo_only"] = True
        payload.update(evidence_sufficient=True, status="no_candidate", motion_pairs=0)
        with self.assertRaises(ValueError):
            Signal.model_validate_json(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
