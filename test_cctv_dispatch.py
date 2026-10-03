"""Independent brute-force oracle and safety/review-bound allocation regressions."""

import builtins
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

    def competing_scenario(self, size):
        """Many interchangeable eligible choices, with scarce scene capability."""
        scarce = size // 2
        self.payload["units"] = [
            dict(
                self.payload["units"][0],
                unit_id=f"TEAM-{i:03d}",
                officers=1,
                home_zone="SIM-A",
                allowed_zones=["SIM-A", "SIM-B"],
                skills=(
                    ["traffic_control"]
                    if i < size - scarce
                    else ["traffic_control", "scene_assessment"]
                ),
            )
            for i in range(size)
        ]
        self.payload["policy"].update(
            staff_budget=size, max_dispatch_officers=size, reserve_officers=0
        )
        self.scenario["demands"] = [
            dict(
                self.scenario["demands"][0],
                demand_id=(
                    f"A-SCENE-{i:03d}"
                    if i < scarce
                    else f"B-TRAFFIC-{i - scarce:03d}"
                ),
                camera_id="N001",
                min_officers=1,
                priority=5,
                review_status="confirmed",
                required_skills=(
                    ["scene_assessment"] if i < scarce else ["traffic_control"]
                ),
            )
            for i in range(size)
        ]

    def assert_same_allocation(self, actual, expected):
        # Work counters and solver metadata differ; consumer-visible decisions do not.
        for key in (
            "assignments", "unmet_demands", "fulfilled_by_priority",
            "assigned_officers", "available_officers_remaining", "reserve_met",
            "dispatchable_officer_limit", "total_simulated_travel_sec",
            "mode", "dry_run", "fictional_resource_data", "operational_ready", "exact",
        ):
            with self.subTest(field=key):
                self.assertEqual(actual[key], expected[key])

    def assert_feasible(self, result):
        """Check selected teams directly against inputs, not solver internals."""
        config = self.config()
        units = {u.unit_id: u for u in config.units}
        demands = {d["demand_id"]: d for d in self.demands()}
        bindings = {b.camera_id: b.zone_id for b in config.camera_bindings}
        travel = {(e.from_zone, e.to_zone): e.seconds for e in config.travel_times}
        seen, staff, cost = set(), 0, 0.0
        counts = [0] * 5
        for assignment in result["assignments"]:
            demand = demands[assignment["demand_id"]]
            self.assertEqual(demand["review_status"], "confirmed")
            zone = bindings[demand["camera_id"]]
            officers, seconds = 0, 0.0
            for name in assignment["units"]:
                self.assertNotIn(name, seen)
                seen.add(name)
                unit = units[name]
                self.assertEqual(unit.status, "available")
                self.assertIn(zone, unit.allowed_zones)
                self.assertTrue(set(demand["required_skills"]) <= set(unit.skills))
                t = 0.0 if unit.home_zone == zone else travel[(unit.home_zone, zone)]
                self.assertLessEqual(t, demand["max_travel_sec"])
                officers += unit.officers
                seconds += t
            self.assertGreaterEqual(officers, demand["min_officers"])
            self.assertEqual(assignment["officers"], officers)
            self.assertEqual(assignment["total_simulated_travel_sec"], seconds)
            counts[5 - demand["priority"]] += 1
            staff += officers
            cost += seconds
        available = sum(u.officers for u in units.values() if u.status == "available")
        cap = min(
            config.policy.max_dispatch_officers,
            max(0, available - config.policy.reserve_officers),
        )
        self.assertLessEqual(staff, cap)
        self.assertEqual(result["assigned_officers"], staff)
        self.assertEqual(result["fulfilled_by_priority"], counts)
        self.assertEqual(result["total_simulated_travel_sec"], cost)
        self.assertEqual(result["available_officers_remaining"], available - staff)
        self.assertEqual(
            {a["demand_id"] for a in result["assignments"]}
            | {d["demand_id"] for d in result["unmet_demands"]},
            set(demands),
        )

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
        self.assert_same_allocation(before, self.solve())
        self.scenario["demands"] = []
        self.assertEqual(self.solve()["assigned_officers"], 0)

    def test_hundred_demand_boundary_and_oversize_fail_without_report(self):
        self.scenario["demands"] = [
            dict(self.scenario["demands"][0], demand_id=f"D-{i:03d}",
                 review_status="pending_review")
            for i in range(100)
        ]
        self.assertEqual(len(self.solve()["unmet_demands"]), 100)
        self.scenario["demands"].append(
            dict(self.scenario["demands"][0], demand_id="D-100")
        )
        with self.assertRaises(ValueError):
            Scenario.model_validate_json(json.dumps(self.scenario))
        save_json(self.demand_path, self.scenario)
        with self.assertRaises(ValueError):
            self.run_pipeline()
        self.assertFalse((self.output / "dispatch_report.json").exists())
        with self.assertRaisesRegex(ValueError, "100"):
            self.solve()

    def test_hundred_team_boundary_and_oversize_resource_rejected(self):
        self.competing_scenario(100)
        self.scenario["demands"] = [
            dict(self.scenario["demands"][-1], demand_id="ONLY")
        ]
        result = self.solve()
        self.assertEqual(result["assignments"][0]["units"], ["TEAM-000"])
        self.assertEqual(result["fulfilled_by_priority"], [1, 0, 0, 0, 0])
        self.payload["units"].append(dict(self.payload["units"][0], unit_id="TEAM-100"))
        with self.assertRaises(ValueError):
            self.config()

    def test_ten_team_ten_demand_legacy_oracle_parity(self):
        self.competing_scenario(10)
        result = self.solve()
        self.assert_same_allocation(
            result, dispatch._allocate_legacy(self.config(), self.demands())
        )
        self.assertEqual(result["fulfilled_by_priority"], [10, 0, 0, 0, 0])
        self.assert_feasible(result)

    def test_one_high_priority_demand_beats_two_low_priority_demands(self):
        self.competing_scenario(3)
        for unit in self.payload["units"]:
            unit["skills"] = ["traffic_control"]
        self.payload["policy"].update(max_dispatch_officers=2)
        template = self.scenario["demands"][-1]
        self.scenario["demands"] = [
            dict(template, demand_id="HIGH", min_officers=2, priority=5),
            dict(template, demand_id="LOW-A", min_officers=1, priority=1),
            dict(template, demand_id="LOW-B", min_officers=1, priority=1),
        ]
        result = self.solve()
        self.assertEqual(result["fulfilled_by_priority"], [1, 0, 0, 0, 0])
        self.assertEqual(result["assignments"][0]["demand_id"], "HIGH")
        self.assertEqual(result["assignments"][0]["units"], ["TEAM-000", "TEAM-001"])
        self.assertEqual({d["demand_id"] for d in result["unmet_demands"]},
                         {"LOW-A", "LOW-B"})
        self.assert_same_allocation(
            result, dispatch._allocate_legacy(self.config(), self.demands())
        )
        self.assert_feasible(result)

    def test_varied_small_legacy_oracle_parity(self):
        original_payload, original_scenario = copy.deepcopy(self.payload), copy.deepcopy(self.scenario)
        variants = (
            ("scarce", {"reserve_officers": 0}, 4, 5, 120.0),
            ("reserve", {"reserve_officers": 2}, 2, 5, 120.0),
            ("cap", {"reserve_officers": 0, "max_dispatch_officers": 3}, 2, 4, 120.0),
            ("priority", {"reserve_officers": 0}, 2, 1, 120.0),
            ("travel", {"reserve_officers": 0}, 2, 4, 119.0),
            ("tie", {"reserve_officers": 0}, 2, 4, 120.0),
        )
        for name, policy, minimum, priority, deadline in variants:
            with self.subTest(case=name):
                self.payload, self.scenario = copy.deepcopy(original_payload), copy.deepcopy(original_scenario)
                self.payload["policy"].update(policy)
                self.scenario["demands"] = self.scenario["demands"][:2]
                self.scenario["demands"][1].update(
                    min_officers=minimum, priority=priority, max_travel_sec=deadline
                )
                if name == "tie":
                    self.payload["units"][1].update(
                        home_zone="SIM-A",
                        skills=["traffic_control", "scene_assessment"],
                    )
                    self.scenario["demands"][1]["camera_id"] = "N001"
                result = self.solve()
                self.assert_same_allocation(
                    result, dispatch._allocate_legacy(self.config(), self.demands())
                )
                self.assert_feasible(result)

    def test_twenty_five_competing_teams_deterministic_optimum(self):
        self.competing_scenario(25)
        result = self.solve()
        self.assertTrue(result["exact"])
        self.assertEqual(result["fulfilled_by_priority"], [25, 0, 0, 0, 0])
        self.assertEqual(result["assigned_officers"], 25)
        self.assertEqual(result["total_simulated_travel_sec"], 0.0)
        expected = {
            **{f"A-SCENE-{i:03d}": [f"TEAM-{13 + i:03d}"] for i in range(12)},
            **{f"B-TRAFFIC-{i:03d}": [f"TEAM-{i:03d}"] for i in range(13)},
        }
        self.assertEqual(
            {a["demand_id"]: a["units"] for a in result["assignments"]}, expected
        )
        self.assert_feasible(result)
        self.payload["units"].reverse()
        self.scenario["demands"].reverse()
        self.assert_same_allocation(result, self.solve())

    def test_mask_tie_break_distinguishes_bits_above_float_precision(self):
        self.competing_scenario(60)
        for i, unit in enumerate(self.payload["units"]):
            unit.update(status="available" if i in (0, 1, 54) else "busy",
                        skills=["traffic_control"])
        self.payload["units"][54]["officers"] = 20
        self.payload["policy"].update(
            staff_budget=100, reserve_officers=1, max_dispatch_officers=21
        )
        self.scenario["demands"] = [
            dict(self.scenario["demands"][-1], demand_id="MASK", min_officers=21)
        ]
        result = self.solve()
        # 2**54 + 1 and 2**54 + 2 cannot be ordered reliably as float objectives.
        self.assertEqual(result["assignments"][0]["units"], ["TEAM-000", "TEAM-054"])
        self.assert_feasible(result)
        self.assertTrue(result["reserve_met"])

    def test_reserve_never_splits_a_whole_team(self):
        self.payload["policy"].update(reserve_officers=3)
        self.scenario["demands"] = [
            dict(self.scenario["demands"][0], min_officers=1)
        ]
        result = self.solve()
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["available_officers_remaining"], 4)
        self.assertTrue(result["reserve_met"])
        self.payload["units"][0]["status"] = "busy"
        result = self.solve()
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["dispatchable_officer_limit"], 0)
        self.assertFalse(result["reserve_met"])

    def test_tiny_travel_difference_survives_officer_and_mask_stages(self):
        self.payload["units"] = self.payload["units"][:2]
        self.payload["policy"].update(reserve_officers=0, max_dispatch_officers=3)
        self.payload["units"][0].update(
            home_zone="SIM-A", officers=1, skills=["traffic_control"]
        )
        self.payload["units"][1].update(
            home_zone="SIM-B", skills=["traffic_control"]
        )
        self.scenario["demands"] = [
            dict(self.scenario["demands"][0], camera_id="N002", min_officers=1)
        ]
        for seconds, cheap_staff in itertools.product((1e-6, 1e-10, 1e-13), (1, 2)):
            with self.subTest(seconds=seconds, cheap_staff=cheap_staff):
                self.payload["travel_times"] = [
                    dict(from_zone="SIM-A", to_zone="SIM-B", seconds=seconds)
                ]
                self.payload["units"][1]["officers"] = cheap_staff
                result = self.solve()
                self.assertEqual(result["assignments"][0]["units"], ["SIM-TEAM-B"])
                self.assertEqual(result["total_simulated_travel_sec"], 0.0)
                self.assertEqual(result["assigned_officers"], cheap_staff)
                self.assert_feasible(result)

    def precision_scenario(self, size):
        self.competing_scenario(size)
        self.payload["zones"].append(dict(zone_id="SIM-C", name="Third fictional zone"))
        for i, unit in enumerate(self.payload["units"]):
            unit.update(
                home_zone="SIM-A" if i == 0 else "SIM-C",
                allowed_zones=["SIM-A", "SIM-B", "SIM-C"],
                skills=["traffic_control"],
            )
        self.payload["travel_times"] = [
            dict(from_zone="SIM-A", to_zone="SIM-B", seconds=120.00000000001),
            dict(from_zone="SIM-C", to_zone="SIM-B", seconds=120.0),
        ]
        self.scenario["demands"] = [
            dict(self.scenario["demands"][-1], demand_id="PRECISE", camera_id="N002",
                 max_travel_sec=121.0)
        ]

    def test_unsupported_travel_scale_warns_small_or_rejects_large(self):
        original_payload = copy.deepcopy(self.payload)
        original_scenario = copy.deepcopy(self.scenario)
        self.precision_scenario(3)
        expected = dispatch._allocate_legacy(self.config(), self.demands())
        with self.assertWarnsRegex(RuntimeWarning, "precision|range"):
            self.assert_same_allocation(self.solve(), expected)
        self.assertEqual(expected["assignments"][0]["units"], ["TEAM-001"])
        self.payload, self.scenario = original_payload, original_scenario
        self.precision_scenario(13)
        with self.assertRaisesRegex(ValueError, "precision|range"):
            self.solve()

    def test_scipy_missing_warns_and_falls_back_only_for_small_scenarios(self):
        self.payload["policy"]["reserve_officers"] = 0
        expected = dispatch._allocate_legacy(self.config(), self.demands())
        original_import = builtins.__import__

        def missing_scipy(name, *args, **kwargs):
            if name == "scipy" or name.startswith("scipy."):
                raise ImportError("SciPy unavailable in isolated fixture")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=missing_scipy):
            with self.assertWarnsRegex(RuntimeWarning, "SciPy|scipy"):
                self.assert_same_allocation(self.solve(), expected)
            self.competing_scenario(13)
            with self.assertRaisesRegex(ValueError, "SciPy|scipy"):
                self.solve()
            self.payload["units"] = self.payload["units"][:3]
            with self.assertRaisesRegex(ValueError, "SciPy|scipy"):
                self.solve()

    def test_uncertified_backend_result_never_publishes_partial_proposal(self):
        from scipy.optimize import milp

        for status, gap, success in (
            (1, 0.0, True), (2, 0.0, True), (3, 0.0, True), (4, 0.0, True),
            (0, 0.01, True), (0, float("nan"), True), (0, float("inf"), True),
            (0, 0.0, False),
        ):
            def uncertified(*args, **kwargs):
                result = milp(*args, **kwargs)
                result.status = status
                result.mip_gap = gap
                result.success = success
                return result

            with self.subTest(status=status, gap=gap, success=success):
                with patch("scipy.optimize.milp", side_effect=uncertified):
                    with self.assertRaises(ValueError):
                        self.run_pipeline()
                self.assertFalse((self.output / "dispatch_report.json").exists())

    def test_late_stage_failure_or_invalid_solution_blocks_publication(self):
        from scipy.optimize import milp

        stages = self.solve()["solver_stages"]
        self.assertGreater(stages, 1)
        for failure in ("positive_gap", "infeasible_assignment"):
            calls = 0

            def invalidate_last_stage(*args, **kwargs):
                nonlocal calls
                calls += 1
                result = milp(*args, **kwargs)
                if calls == stages:
                    if failure == "positive_gap":
                        result.mip_gap = 0.1
                    else:
                        result.x[:] = 0.0
                return result

            with self.subTest(failure=failure):
                with patch("scipy.optimize.milp", side_effect=invalidate_last_stage):
                    with self.assertRaises(ValueError):
                        self.run_pipeline()
                self.assertFalse((self.output / "dispatch_report.json").exists())

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
