"""Read-only HTTP event evidence and journal/source binding with synthetic data."""

import json
import unittest

from fastapi.testclient import TestClient

import test_cctv_api as api_module
import test_cctv_events as event_module
from app.cctv import create_app
from pipeline.cctv.ingest import digest


class EventApiTests(unittest.TestCase):
    """Never load CLIP weights, use raw CCTV, or write existing review journals."""

    def setUp(self):
        self.api = api_module.CCTVApiTests()
        self.api.setUp()
        self.addCleanup(self.api.doCleanups)
        self.events = event_module.EventTests()
        self.events.setUp()
        self.addCleanup(self.events.doCleanups)
        self.events.run_events(True)
        self.api_source = self.api.fixture.raw / "source.mov"
        self.api_source.write_bytes(
            (self.events.source.raw / "source.mov").read_bytes()
        )
        state_path = self.api.preview / "preview_state.json"
        state = json.loads(state_path.read_text())
        state["videos"]["N001-V001"]["signature"]["sha256"] = digest(self.api_source)
        state["videos"]["N001-V001"]["seek_offset_sec"] = 0.25
        state_path.write_text(json.dumps(state))

    def client(self):
        app = create_app(
            self.api.fixture.output,
            self.api.fixture.root / "model.json",
            self.api.preview,
            "cpu",
            encoder_factory=api_module.ApiEncoder,
            event_packages={
                "demo": (self.events.output, self.events.reviews),
                "missing": (
                    self.events.source.root / "missing",
                    self.events.source.root / "missing-review",
                ),
            },
        )
        return self.api.stack.enter_context(TestClient(app))

    def test_catalog_evidence_and_missing_journal_are_read_only(self):
        before = self.events.snapshot(self.events.output)
        client = self.client()
        catalog = client.get("/cctv/events").json()
        self.assertTrue(catalog["available"])
        self.assertFalse(catalog["packages"][1]["available"])
        self.assertIsNone(catalog["packages"][1]["total"])
        result = client.get("/cctv/events/demo?limit=1").json()
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["status"], "pending_review")
        self.assertFalse(row["operational_ready"])
        self.assertNotIn("provenance_sha256", row)
        loc = row["evidence"][0]
        self.assertAlmostEqual(loc["video_seek_sec"], loc["timestamp_sec"] + 0.25)
        self.assertEqual(client.get(loc["video_url"]).status_code, 200)
        self.assertFalse(self.events.reviews.exists())
        self.assertEqual(before, self.events.snapshot(self.events.output))
        self.assertEqual(api_module.ApiEncoder.loaded, 1)

    def test_live_review_refresh_keeps_demo_flags_and_revision(self):
        key = next(
            row["event"]["event_id"]
            for row in event_module.list_events(self.events.output, self.events.reviews)
            if row["event"]["synthetic_event"]
        )
        client = self.client()
        self.events.review(key, "needs_more_evidence", 0)
        before = self.events.snapshot(self.events.reviews)
        row = next(
            r
            for r in client.get("/cctv/events/demo").json()["rows"]
            if r["event_id"] == key
        )
        self.assertEqual(row["status"], "needs_more_evidence")
        self.assertEqual(row["revision"], 1)
        self.assertTrue(row["synthetic_event"])
        self.assertEqual(row["review"]["scope"], "demo_review")
        self.assertEqual(before, self.events.snapshot(self.events.reviews))
        self.assertEqual(
            client.post(
                "/cctv/events/demo", json={"decision": "confirmed"}
            ).status_code,
            405,
        )

    def test_corrupt_event_or_review_does_not_disable_retrieval(self):
        client = self.client()
        (self.events.output / "events.jsonl").write_text("{}")
        self.assertEqual(client.get("/cctv/events/demo").status_code, 503)
        self.assertTrue(client.get("/ready").json()["ready"])
        self.assertEqual(client.get("/cctv/search?query=red&top_k=1").status_code, 200)

    def test_source_mismatch_bounds_unknown_and_no_path_leak(self):
        client = self.client()
        self.assertEqual(client.get("/cctv/events/unknown").status_code, 404)
        self.assertEqual(client.get("/cctv/events/demo?limit=51").status_code, 422)
        self.assertEqual(client.get("/cctv/events/demo?offset=-1").status_code, 422)
        self.assertEqual(client.get("/cctv/events/demo?offset=100").json()["rows"], [])
        client.app.state.cctv.videos["N001-V001"]["signature"]["sha256"] = "wrong"
        response = client.get("/cctv/events/demo")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.events.output), response.text)

    def test_corrupt_review_journal_is_unavailable(self):
        key = self.events.demo_id()
        self.events.review(key)
        client = self.client()
        (self.events.reviews / "reviews.db").write_bytes(b"not sqlite")
        self.assertFalse(client.get("/cctv/events").json()["available"])
        self.assertEqual(client.get("/cctv/events/demo").status_code, 503)


if __name__ == "__main__":
    unittest.main()
