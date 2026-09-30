"""Independent brute-force oracle and safety/review-bound allocation regressions."""

import copy
import itertools
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_cctv_events as event_fixture
import test_cctv_resources as resource_fixture
from pipeline.cctv import dispatch
from pipeline.cctv.dispatch import EventScenario, Scenario, allocate, propose_dispatch
from pipeline.cctv.event_review import submit_review
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.resources import ResourceConfig


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = resource_fixture.ResourceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.payload = copy.deepcopy(self.fixture.payload)
        self.demand_path = self.root / "demands.json"
        self.output = self.root / "dispatch"
        self.scenario = json.loads(
            (Path(__file__).parent / "configs/cctv.demands.simulation.json").read_text()
        )
        save_json(self.demand_path, self.scenario)

    def config(self):
        return ResourceConfig.model_validate_json(json.dumps(self.payload))

    def demands(self):
        return [
            dict(
                d.model_dump(mode="json"), synthetic_demand=True, source_demo_only=True
            )
            for d in Scenario.model_validate_json(json.dumps(self.scenario)).demands
        ]

    def solve(self):
        return allocate(self.config(), self.demands())

    def run_pipeline(self, output=None):
        return propose_dispatch(
            self.fixture.manifest,
            self.fixture.config,
            self.demand_path,
            output or self.output,
        )

    def test_shared_cap_reserve_and_pending_priority(self):
        result = self.solve()
        self.assertEqual(result["assigned_officers"], 2)
        self.assertEqual(result["available_officers_remaining"], 2)
        self.assertTrue(result["reserve_met"])
        self.assertEqual(result["assignments"][0]["demand_id"], "SIM-TRAFFIC-A")
        self.assertEqual(result["assignments"][0]["units"], ["SIM-TEAM-A"])
        unmet = {d["demand_id"]: d for d in result["unmet_demands"]}
        self.assertIn("review_not_confirmed", unmet["SIM-PENDING-B"]["reasons"])
        self.assertIn(
            "shared_staff_cap_or_competing_demand", unmet["SIM-SCENE-B"]["reasons"]
        )
        self.assertFalse(result["operational_ready"])

    def test_multi_team_demand_is_whole_and_each_team_used_once(self):
        self.payload["policy"]["reserve_officers"] = 0
        self.scenario["demands"] = [dict(self.scenario["demands"][1], min_officers=4)]
        result = self.solve()
        self.assertEqual(result["assigned_officers"], 4)
        self.assertEqual(
            result["assignments"][0]["units"], ["SIM-TEAM-A", "SIM-TEAM-B"]
        )
        self.scenario["demands"].append(
            dict(self.scenario["demands"][0], demand_id="SECOND")
        )
        result = self.solve()
        self.assertEqual(len(result["assignments"]), 1)
        assigned = [u for a in result["assignments"] for u in a["units"]]
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_exact_solver_avoids_greedy_skill_trap(self):
        self.payload["policy"]["reserve_officers"] = 0
        self.scenario["demands"] = [
            dict(
                self.scenario["demands"][1],
                demand_id="A-SCENE",
                camera_id="N001",
                priority=5,
            ),
            dict(self.scenario["demands"][0], demand_id="B-TRAFFIC", priority=5),
        ]
        result = self.solve()
        assignments = {a["demand_id"]: a["units"] for a in result["assignments"]}
        self.assertEqual(
            assignments, {"A-SCENE": ["SIM-TEAM-B"], "B-TRAFFIC": ["SIM-TEAM-A"]}
        )
        self.assertEqual(result["fulfilled_by_priority"], [2, 0, 0, 0, 0])

    def test_missing_skill_busy_zone_and_travel_deadline_reasons(self):
        self.scenario["demands"] = [
            dict(self.scenario["demands"][0], camera_id="N002", max_travel_sec=50.0)
        ]
        result = self.solve()
        row = result["unmet_demands"][0]
        self.assertIn("travel_deadline_exceeded", row["unit_reasons"]["SIM-TEAM-A"])
        self.assertIn("required_skill_missing", row["unit_reasons"]["SIM-TEAM-B"])
        self.assertIn("unit_not_available", row["unit_reasons"]["SIM-TEAM-C"])
        self.assertIn("zone_not_allowed", row["unit_reasons"]["SIM-TEAM-C"])
        self.payload["travel_times"] = []
        row = self.solve()["unmet_demands"][0]
        self.assertIn("travel_time_missing", row["unit_reasons"]["SIM-TEAM-A"])

    def test_unknown_camera_mapping_and_all_review_states_blocked(self):
        self.scenario["demands"] = self.scenario["demands"][:1]
        for status in ("pending_review", "rejected", "needs_more_evidence"):
            self.scenario["demands"][0]["review_status"] = status
            self.assertEqual(self.solve()["assignments"], [])
        self.scenario["demands"][0]["review_status"] = "confirmed"
        self.payload["camera_bindings"][0].update(zone_id=None, status="unknown")
        self.assertIn(
            "camera_mapping_unknown", self.solve()["unmet_demands"][0]["reasons"]
        )

    def test_independent_oracle_matches_objective_for_small_scenarios(self):
        self.payload["policy"].update(reserve_officers=0, max_dispatch_officers=4)
        self.scenario["demands"] = self.scenario["demands"][:2]
        for minimum in (1, 2, 3, 4):
            self.scenario["demands"][1]["min_officers"] = minimum
            demands = self.demands()
            config = self.config()
            best = None
            for destinations in itertools.product((-1, 0, 1), repeat=len(config.units)):
                total_staff = 0
                cost = 0.0
                staffed = [0, 0]
                valid = True
                for unit, target in zip(config.units, destinations):
                    if target < 0:
                        continue
                    demand = demands[target]
                    zone = next(
                        b.zone_id
                        for b in config.camera_bindings
                        if b.camera_id == demand["camera_id"]
                    )
                    travel = (
                        0.0
                        if unit.home_zone == zone
                        else next(
                            (
                                e.seconds
                                for e in config.travel_times
                                if (e.from_zone, e.to_zone) == (unit.home_zone, zone)
                            ),
                            None,
                        )
                    )
                    if (
                        unit.status != "available"
                        or not set(demand["required_skills"]) <= set(unit.skills)
                        or zone not in unit.allowed_zones
                        or travel is None
                        or travel > demand["max_travel_sec"]
                    ):
                        valid = False
                        break
                    total_staff += unit.officers
                    staffed[target] += unit.officers
                    cost += travel
                if (
                    not valid
                    or total_staff > 4
                    or any(0 < n < d["min_officers"] for n, d in zip(staffed, demands))
                ):
                    continue
                counts = tuple(
                    sum(
                        n >= d["min_officers"]
                        for n, d in zip(staffed, demands)
                        if d["priority"] == priority
                    )
                    for priority in (5, 4, 3, 2, 1)
                )
                score = (counts, -cost, -total_staff)
                best = score if best is None or score > best else best
            result = self.solve()
            self.assertEqual(
                (
                    tuple(result["fulfilled_by_priority"]),
                    -result["total_simulated_travel_sec"],
                    -result["assigned_officers"],
                ),
                best,
            )

    def test_deterministic_input_order_and_empty_demand(self):
        before = self.solve()
        self.scenario["demands"].reverse()
        self.payload["units"].reverse()
        self.assertEqual(before, self.solve())
        self.scenario["demands"] = []
        self.assertEqual(self.solve()["assigned_officers"], 0)

    def test_search_and_scenario_limits_fail_without_partial_proposal(self):
        with patch.object(dispatch, "MAX_SEARCH_STEPS", 0):
            with self.assertRaisesRegex(ValueError, "search limit"):
                self.run_pipeline()
        self.assertFalse((self.output / "dispatch_report.json").exists())
        value = copy.deepcopy(self.scenario)
        value["demands"] = [
            dict(value["demands"][0], demand_id=f"D-{i}") for i in range(13)
        ]
        with self.assertRaises(ValueError):
            Scenario.model_validate_json(json.dumps(value))

    def test_invalid_schema_and_duplicate_demands(self):
        for update in (
            dict(priority=True),
            dict(min_officers=0),
            dict(required_skills=[]),
            dict(max_travel_sec=float("nan")),
        ):
            value = copy.deepcopy(self.scenario)
            value["demands"][0].update(update)
            with self.assertRaises(ValueError):
                Scenario.model_validate_json(json.dumps(value))
        self.scenario["demands"].append(copy.deepcopy(self.scenario["demands"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate demand"):
            self.solve()
        spoof = dict(
            schema_version=1,
            mode="simulation",
            kind="event_package",
            demands=[
                dict(
                    event_id="event-fake",
                    review_status="confirmed",
                    demand_id="D",
                    min_officers=1,
                    required_skills=["scene_assessment"],
                    priority=1,
                    max_travel_sec=10.0,
                )
            ],
        )
        with self.assertRaises(ValueError):
            EventScenario.model_validate_json(json.dumps(spoof))

    def test_resume_preserves_inputs_and_skips_solver(self):
        raw = (
            digest(self.fixture.raw / "video.mov"),
            (self.fixture.raw / "video.mov").stat().st_mtime_ns,
        )
        self.run_pipeline()
        paths = [
            self.output / "dispatch_state.json",
            self.output / "dispatch_report.json",
        ]
        before = [(digest(p), p.stat().st_mtime_ns) for p in paths]
        with patch.object(
            dispatch, "allocate", side_effect=AssertionError("must skip")
        ):
            self.assertTrue(self.run_pipeline()["resumed"])
        self.assertEqual(before, [(digest(p), p.stat().st_mtime_ns) for p in paths])
        self.assertEqual(
            raw,
            (
                digest(self.fixture.raw / "video.mov"),
                (self.fixture.raw / "video.mov").stat().st_mtime_ns,
            ),
        )

    def test_raw_foreign_corruption_and_recipe_change_preserved(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_pipeline(self.fixture.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.run_pipeline()
        self.output = self.root / "owned"
        self.run_pipeline()
        report = self.output / "dispatch_report.json"
        report.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_pipeline()
        self.scenario["demands"][0]["priority"] = 1
        save_json(self.demand_path, self.scenario)
        with self.assertRaisesRegex(ValueError, "recipe/review changed"):
            self.run_pipeline()

    def test_publication_interruption_recovers(self):
        save = dispatch.save_json

        def interrupt(path, value):
            if path.name == "dispatch_report.json":
                raise KeyboardInterrupt()
            return save(path, value)

        with patch.object(dispatch, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pipeline()
        self.assertFalse(self.run_pipeline()["resumed"])

    def test_input_change_during_solver_blocks_publication(self):
        solver = dispatch.allocate

        def mutate(config, demands):
            result = solver(config, demands)
            self.fixture.config.write_text("changed during solve")
            return result

        with patch.object(dispatch, "allocate", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during allocation"):
                self.run_pipeline()
        self.assertFalse((self.output / "dispatch_report.json").exists())

    def test_event_journal_controls_status_and_invalidates_cached_proposal(self):
        fixture = event_fixture.EventTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        event_id = fixture.demo_id()
        self.payload["camera_bindings"] = self.payload["camera_bindings"][:1]
        resource_path = fixture.source.root / "resources.json"
        save_json(resource_path, self.payload)
        request = dict(
            schema_version=1,
            mode="simulation",
            kind="event_package",
            demands=[
                dict(
                    demand_id="EVENT-D",
                    event_id=event_id,
                    min_officers=2,
                    required_skills=["scene_assessment"],
                    priority=4,
                    max_travel_sec=180.0,
                )
            ],
        )
        demand_path = fixture.source.root / "request.json"
        save_json(demand_path, request)
        output = fixture.source.root / "proposal"

        def run(target):
            return propose_dispatch(
                fixture.source.manifest,
                resource_path,
                demand_path,
                target,
                fixture.output,
                fixture.reviews,
            )

        pending = run(output)
        self.assertEqual(pending["assignments"], [])
        self.assertIn("review_not_confirmed", pending["unmet_demands"][0]["reasons"])
        self.assertFalse(fixture.reviews.exists())
        submit_review(
            fixture.output,
            fixture.reviews,
            event_id,
            "confirmed",
            "synthetic-tester",
            "Synthetic fixture only.",
            0,
        )
        with self.assertRaisesRegex(ValueError, "review changed"):
            run(output)
        confirmed = run(fixture.source.root / "confirmed-proposal")
        self.assertEqual(len(confirmed["assignments"]), 1)
        self.assertTrue(confirmed["assignments"][0]["synthetic_demand"])
        self.assertTrue(confirmed["assignments"][0]["source_demo_only"])
        self.assertFalse(confirmed["operational_ready"])


if __name__ == "__main__":
    unittest.main()
