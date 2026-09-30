"""Read-only proposal API: stale inputs, feasibility and review gates on fixtures."""

import copy
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_cctv_api as api_fixture
import test_cctv_dispatch as dispatch_fixture
import test_cctv_events as event_fixture
from app.cctv import create_app
from app.cctv_dispatch import DispatchPackage, DispatchStore
from pipeline.cctv.dispatch import propose_dispatch
from pipeline.cctv.ingest import digest, save_json


class DispatchApiTests(unittest.TestCase):
    """Fixtures only; never mutate real review journals or load model weights."""

    def setUp(self):
        self.fixture = dispatch_fixture.DispatchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_pipeline()
        self.store = DispatchStore(
            self.fixture.fixture.manifest,
            self.fixture.fixture.config,
            {
                "simulation": DispatchPackage(
                    self.fixture.output, self.fixture.demand_path
                )
            },
        )
        self.sources = {"N001-V001": {"camera_id": "N001"}}

    def client(self):
        fixture = api_fixture.CCTVApiTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        app = create_app(
            fixture.fixture.output,
            fixture.fixture.root / "model.json",
            fixture.preview,
            "cpu",
            encoder_factory=api_fixture.ApiEncoder,
            event_packages={},
            dispatch_store=self.store,
        )
        return fixture.stack.enter_context(TestClient(app))

    def reseal(self, report):
        path = self.fixture.output / "dispatch_report.json"
        save_json(path, report)
        state_path = self.fixture.output / "dispatch_state.json"
        state = json.loads(state_path.read_text())
        state["report_sha256"] = digest(path)
        save_json(state_path, state)

    def test_http_readonly_flags_staff_and_no_solver(self):
        before = self.fixture.fixture.snapshot(self.fixture.output)
        client = self.client()
        with patch(
            "pipeline.cctv.dispatch.allocate", side_effect=AssertionError("no solver")
        ):
            catalog = client.get("/cctv/dispatch").json()
            self.assertTrue(catalog["available"])
            report = client.get("/cctv/dispatch/simulation").json()
        self.assertEqual(report["assigned_officers"], 2)
        self.assertEqual(report["required_reserve_officers"], 2)
        self.assertTrue(report["read_only"] and report["dry_run"])
        self.assertFalse(report["operational_ready"])
        self.assertEqual(len(report["unmet_demands"]), 2)
        self.assertNotIn("signature", report)
        self.assertEqual(before, self.fixture.fixture.snapshot(self.fixture.output))
        self.assertEqual(client.post("/cctv/dispatch/simulation").status_code, 405)
        self.assertEqual(client.get("/cctv/dispatch/unknown").status_code, 404)

    def test_stale_resource_or_demands_unavailable_retrieval_survives(self):
        client = self.client()
        for path in (self.fixture.fixture.config, self.fixture.demand_path):
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            self.assertFalse(client.get("/cctv/dispatch").json()["available"])
            response = client.get("/cctv/dispatch/simulation")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(str(path), response.text)
            self.assertEqual(client.get("/cctv/search?query=road").status_code, 200)
            path.write_bytes(original)

    def test_checksum_corruption_and_source_scope_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "retrieval"):
            self.store.report("simulation", {"N001-V001": {"camera_id": "N002"}})
        path = self.fixture.output / "dispatch_report.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertFalse(self.store.catalog(self.sources)["available"])

    def test_resealed_infeasible_assignment_and_counter_rejected(self):
        report = json.loads((self.fixture.output / "dispatch_report.json").read_text())
        for modification in (
            "busy",
            "duplicate",
            "staff",
            "travel",
            "flags",
            "coverage",
            "review_reason",
        ):
            value = copy.deepcopy(report)
            if modification == "busy":
                value["assignments"][0]["units"] = ["SIM-TEAM-C"]
            elif modification == "duplicate":
                value["assignments"][0]["units"] *= 2
            elif modification == "staff":
                value["assigned_officers"] = 4
            elif modification == "travel":
                value["total_simulated_travel_sec"] = 90
            elif modification == "flags":
                value["operational_ready"] = True
            elif modification == "coverage":
                value["unmet_demands"].pop()
            else:
                value["unmet_demands"][0]["reasons"] = [
                    "shared_staff_cap_or_competing_demand"
                ]
            self.reseal(value)
            with self.subTest(modification=modification), self.assertRaises(ValueError):
                self.store.report("simulation", self.sources)

    def test_malformed_publication_is_unavailable(self):
        path = self.fixture.output / "dispatch_state.json"
        state = json.loads(path.read_text())
        for value in ([], dict(state, signature=[])):
            save_json(path, value)
            self.assertFalse(self.store.catalog(self.sources)["available"])

    def test_empty_case_distinct_from_missing_report(self):
        save_json(self.fixture.demand_path, dict(self.fixture.scenario, demands=[]))
        output = self.fixture.root / "empty-proposal"
        self.fixture.run_pipeline(output)
        self.store.packages = {
            "empty": DispatchPackage(output, self.fixture.demand_path),
            "missing": DispatchPackage(
                self.fixture.root / "missing", self.fixture.demand_path
            ),
        }
        catalog = self.store.catalog(self.sources)
        self.assertTrue(catalog["packages"][0]["available"])
        self.assertEqual(catalog["packages"][0]["assignments"], 0)
        self.assertFalse(catalog["packages"][1]["available"])
        self.assertIsNone(catalog["packages"][1]["assignments"])

    def event_store(self):
        fixture = event_fixture.EventTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        key = fixture.demo_id()
        resource_path, demand_path = (
            fixture.source.root / "resources.json",
            fixture.source.root / "request.json",
        )
        save_json(
            resource_path,
            dict(
                self.fixture.payload,
                camera_bindings=self.fixture.payload["camera_bindings"][:1],
            ),
        )
        save_json(
            demand_path,
            dict(
                schema_version=1,
                mode="simulation",
                kind="event_package",
                demands=[
                    dict(
                        demand_id="EVENT-D",
                        event_id=key,
                        min_officers=2,
                        required_skills=["scene_assessment"],
                        priority=4,
                        max_travel_sec=180.0,
                    )
                ],
            ),
        )
        output = fixture.source.root / "proposal"

        def publish(target):
            return propose_dispatch(
                fixture.source.manifest,
                resource_path,
                demand_path,
                target,
                fixture.output,
                fixture.reviews,
            )

        publish(output)
        store = DispatchStore(
            fixture.source.manifest,
            resource_path,
            {
                "events": DispatchPackage(
                    output, demand_path, fixture.output, fixture.reviews
                )
            },
        )
        sources = {
            "N001-V001": dict(
                camera_id="N001",
                sha256=digest(fixture.source.raw / "source.mov"),
                seek_offset_sec=0,
            )
        }
        return fixture, key, store, sources, publish

    def test_journal_appearance_and_revision_make_snapshot_stale(self):
        fixture, key, store, sources, publish = self.event_store()
        report = store.report("events", sources)
        self.assertEqual(report["assignments"], [])
        self.assertFalse(fixture.reviews.exists())
        fixture.review(key)
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            store.report("events", sources)
        output = fixture.source.root / "confirmed"
        publish(output)
        package = store.packages["events"]
        store.packages["events"] = DispatchPackage(
            output, package.demands, package.events, package.reviews
        )
        before = fixture.snapshot(fixture.reviews)
        report = store.report("events", sources)
        self.assertEqual(len(report["assignments"]), 1)
        self.assertTrue(report["assignments"][0]["synthetic_demand"])
        self.assertEqual(before, fixture.snapshot(fixture.reviews))
        fixture.review(key, decision="needs_more_evidence", revision=1)
        self.assertFalse(store.catalog(sources)["available"])

    def test_event_source_sha_and_corrupt_journal_rejected(self):
        fixture, key, store, sources, publish = self.event_store()
        altered = copy.deepcopy(sources)
        altered["N001-V001"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "retrieval"):
            store.report("events", altered)
        fixture.review(key)
        output = fixture.source.root / "confirmed"
        publish(output)
        package = store.packages["events"]
        store.packages["events"] = DispatchPackage(
            output, package.demands, package.events, package.reviews
        )
        (fixture.reviews / "reviews.db").write_bytes(b"not sqlite")
        self.assertFalse(store.catalog(sources)["available"])


if __name__ == "__main__":
    unittest.main()
