"""Simulation roster constraints, manifest binding and read-only publication."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.cctv import resources
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.resources import ResourceConfig, summarize, validate_resources


class ResourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        (self.raw / "video.mov").write_bytes(b"read-only synthetic source")
        self.manifest = self.root / "manifest.json"
        save_json(
            self.manifest,
            dict(
                schema_version=1,
                raw_root=str(self.raw),
                cameras=[dict(camera_id="N001"), dict(camera_id="N002")],
                videos=[dict(video_id="N001-V001", camera_id="N001", path="video.mov")],
            ),
        )
        self.payload = json.loads(
            (
                Path(__file__).parent / "configs/cctv.resources.simulation.json"
            ).read_text()
        )
        self.config = self.root / "resources.json"
        save_json(self.config, self.payload)
        self.output = self.root / "output"

    def parse(self, value=None):
        return ResourceConfig.model_validate_json(
            json.dumps(self.payload if value is None else value)
        )

    def run_report(self, output=None):
        return validate_resources(self.manifest, self.config, output or self.output)

    def snapshot(self, folder):
        return {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in folder.iterdir()
            if p.is_file() and p.name != ".ingest.lock"
        }

    def test_demo_budget_reserve_and_directed_travel(self):
        config = self.parse()
        config.bind(load_manifest(self.manifest))
        report = summarize(config)
        self.assertEqual(
            (
                report["roster_officers"],
                report["available_officers"],
                report["unavailable_officers"],
            ),
            (6, 4, 2),
        )
        self.assertEqual(report["dispatchable_officer_limit"], 2)
        self.assertTrue(report["simulation_ready"])
        self.assertFalse(report["operational_ready"])
        pairs = {
            (p["unit_id"], p["zone_id"]): p for p in report["unit_zone_constraints"]
        }
        self.assertEqual(pairs[("SIM-TEAM-A", "SIM-B")]["simulated_travel_sec"], 90.0)
        self.assertEqual(pairs[("SIM-TEAM-B", "SIM-A")]["simulated_travel_sec"], 120.0)
        self.assertEqual(pairs[("SIM-TEAM-A", "SIM-A")]["simulated_travel_sec"], 0.0)
        self.assertIn(
            "unit_not_available", pairs[("SIM-TEAM-C", "SIM-A")]["blocked_reasons"]
        )
        self.assertEqual(report["assignments"], [])

    def test_unknown_mapping_remains_null_and_not_ready(self):
        self.payload["camera_bindings"][1].update(zone_id=None, status="unknown")
        config = self.parse()
        config.bind(load_manifest(self.manifest))
        report = summarize(config)
        self.assertFalse(report["simulation_ready"])
        self.assertEqual(report["unknown_camera_mappings"], ["N002"])
        self.assertIsNone(config.camera_bindings[1].zone_id)

    def test_available_below_reserve_and_indivisible_team(self):
        self.payload["units"][0]["status"] = "busy"
        self.payload["policy"]["reserve_officers"] = 3
        report = summarize(self.parse())
        self.assertEqual(report["dispatchable_officer_limit"], 0)
        self.assertIn("available_staff_below_reserve", report["warnings"])
        self.payload["policy"]["reserve_officers"] = 1
        report = summarize(self.parse())
        self.assertEqual(report["dispatchable_officer_limit"], 1)
        self.assertFalse(report["simulation_ready"])
        self.assertTrue(
            all(
                not p["eligible_without_demand"]
                for p in report["unit_zone_constraints"]
            )
        )

    def test_missing_travel_never_inferred_from_reverse_edge(self):
        self.payload["travel_times"] = self.payload["travel_times"][:1]
        report = summarize(self.parse())
        pair = next(
            p
            for p in report["unit_zone_constraints"]
            if p["unit_id"] == "SIM-TEAM-B" and p["zone_id"] == "SIM-A"
        )
        self.assertIsNone(pair["simulated_travel_sec"])
        self.assertIn("travel_time_missing", pair["blocked_reasons"])

    def test_duplicate_and_unknown_references_rejected(self):
        original = copy.deepcopy(self.payload)
        mutations = [
            lambda p: p["zones"].append(dict(zone_id="sim-a", name="Duplicate")),
            lambda p: p["units"].append(copy.deepcopy(p["units"][0])),
            lambda p: p["camera_bindings"].append(
                copy.deepcopy(p["camera_bindings"][0])
            ),
            lambda p: p["travel_times"].append(copy.deepcopy(p["travel_times"][0])),
            lambda p: p["units"][0].update(home_zone="MISSING"),
            lambda p: p["camera_bindings"][0].update(zone_id="MISSING"),
            lambda p: p["travel_times"][0].update(to_zone="MISSING"),
        ]
        for mutation in mutations:
            value = copy.deepcopy(original)
            mutation(value)
            with self.assertRaises(ValueError):
                self.parse(value)

    def test_strict_counts_budget_mode_review_and_mapping(self):
        original = copy.deepcopy(self.payload)
        mutations = [
            lambda p: p["units"][0].update(officers=True),
            lambda p: p["units"][0].update(officers=0),
            lambda p: p["policy"].update(staff_budget=5),
            lambda p: p["policy"].update(max_dispatch_officers=7),
            lambda p: p["policy"].update(reserve_officers=7),
            lambda p: p["policy"].update(require_confirmed_events=False),
            lambda p: p["policy"].update(require_confirmed_events=1),
            lambda p: p.update(mode="operational"),
            lambda p: p["camera_bindings"][0].update(status="unknown"),
            lambda p: p["travel_times"][0].update(seconds=float("nan")),
            lambda p: p["travel_times"][0].update(to_zone="SIM-A"),
            lambda p: p["zones"][0].update(name=" "),
        ]
        for mutation in mutations:
            value = copy.deepcopy(original)
            mutation(value)
            with self.assertRaises(ValueError):
                self.parse(value)

    def test_manifest_coverage_and_canonical_case(self):
        for value in (
            self.payload["camera_bindings"][:1],
            [
                dict(camera_id="n001", zone_id="SIM-A", status="simulated"),
                self.payload["camera_bindings"][1],
            ],
        ):
            payload = dict(self.payload, camera_bindings=value)
            with self.assertRaisesRegex(ValueError, "canonical"):
                self.parse(payload).bind(load_manifest(self.manifest))

    def test_empty_roster_valid_but_not_ready(self):
        self.payload["units"] = []
        self.payload["policy"].update(
            staff_budget=0, max_dispatch_officers=0, reserve_officers=0
        )
        report = summarize(self.parse())
        self.assertFalse(report["simulation_ready"])
        self.assertEqual(report["dispatchable_officer_limit"], 0)

    def test_resume_preserves_raw_config_and_report_bytes(self):
        raw = self.snapshot(self.raw)
        config = (digest(self.config), self.config.stat().st_mtime_ns)
        self.assertFalse(self.run_report()["resumed"])
        before = self.snapshot(self.output)
        self.assertTrue(self.run_report()["resumed"])
        self.assertEqual(before, self.snapshot(self.output))
        self.assertEqual(raw, self.snapshot(self.raw))
        self.assertEqual(config, (digest(self.config), self.config.stat().st_mtime_ns))

    def test_raw_overlap_and_foreign_output_preserved(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_report(self.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_report()
        self.assertEqual((self.output / "keep.txt").read_text(), "keep")

    def test_invalid_manifest_binding_writes_nothing(self):
        self.payload["camera_bindings"] = self.payload["camera_bindings"][:1]
        save_json(self.config, self.payload)
        with self.assertRaisesRegex(ValueError, "canonical"):
            self.run_report()
        self.assertFalse(self.output.exists())

    def test_changed_config_and_corrupt_report_preserved(self):
        self.run_report()
        report = self.output / "resource_report.json"
        report.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_report()
        self.assertEqual(report.read_text(), "corrupt")
        self.payload["policy"]["reserve_officers"] = 1
        save_json(self.config, self.payload)
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            self.run_report()

    def test_interrupted_publication_recovers(self):
        save = resources.save_json

        def interrupt(path, value):
            if path.name == "resource_report.json":
                raise KeyboardInterrupt()
            return save(path, value)

        with patch.object(resources, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_report()
        self.assertFalse(self.run_report()["resumed"])
        self.assertTrue(self.run_report()["resumed"])


if __name__ == "__main__":
    unittest.main()
