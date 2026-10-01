"""Temporal hypotheses and v1/v2 review compatibility using synthetic evidence."""

import json
import unittest
from unittest.mock import patch

import test_cctv_congestion as fixture_module
from pipeline.cctv import incidents
from pipeline.cctv.event_review import list_events, submit_review
from pipeline.cctv.events import canonical, checksum
from pipeline.cctv.incident_fixtures import evaluate_fixtures, fixture
from pipeline.cctv.incidents import (
    IncidentConfig,
    IncidentEvent,
    load_incidents,
    process_incidents,
    screen_video,
)
from pipeline.cctv.ingest import digest, save_json


class RuleTests(unittest.TestCase):
    def test_sampled_ordinals_do_not_reset_every_observation(self):
        frames, roi, config = fixture("moving_stop")
        sparse = []
        for frame in frames:
            value = frame.model_dump(mode="json")
            value["locator"]["frame_idx"] *= 12
            for observation in value["observations"]:
                observation["locator"]["frame_idx"] *= 12
            sparse.append(incidents.TrackFrame.model_validate_json(json.dumps(value)))
        self.assertTrue(
            screen_video(sparse, roi, config, "0" * 64, sample_interval=0.5)
        )
        self.assertFalse(screen_video(sparse, roi, config, "0" * 64))

    def screen(self, name):
        frames, roi, config = fixture(name)
        return screen_video(frames, roi, config, "0" * 64, synthetic=True)

    def test_ten_fixtures_match_and_confounder_is_explicit(self):
        report = evaluate_fixtures()
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["cases"]), 10)
        confounder = next(r for r in report["cases"] if r["known_non_emergency"])
        self.assertEqual(confounder["actual_rule_candidates"], 1)
        self.assertFalse(report["real_event_accuracy_measured"])

    def test_positive_candidate_pts_scope_and_only_one_per_stop(self):
        records = self.screen("moving_stop")
        self.assertEqual(len(records), 1)
        event = records[0]
        self.assertEqual(
            (event.schema_version, event.event_type), (2, "suspected_road_incident")
        )
        self.assertEqual((event.track_id, event.segment_id), (1, "seg-000000"))
        self.assertAlmostEqual(event.moving_sec, 1.2)
        self.assertAlmostEqual(event.stationary_sec, 2.0)
        self.assertEqual([e.locator.frame_idx for e in event.evidence], [0, 8])
        self.assertTrue(event.synthetic_event and event.demo_only)
        self.assertFalse(event.operational_ready)

    def test_unknown_motion_and_gaps_reset(self):
        for name in (
            "already_stationary",
            "keeps_moving",
            "short_stop",
            "missing_track",
            "segment_reset",
            "timestamp_gap",
            "outside_roi",
            "weak_detection",
        ):
            with self.subTest(name=name):
                self.assertEqual(self.screen(name), [])

    def test_unordered_pts_and_mixed_sources_rejected(self):
        frames, roi, config = fixture("moving_stop")
        with self.assertRaisesRegex(ValueError, "Unordered"):
            screen_video([frames[1], frames[0]], roi, config, "0" * 64)
        value = frames[2].model_dump(mode="json")
        value["run_id"] = "other-run"
        for observation in value["observations"]:
            observation["run_id"] = "other-run"
        frames[2] = incidents.TrackFrame.model_validate_json(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "Mixed source"):
            screen_video(frames, roi, config, "0" * 64)

    def test_configuration_overlap_and_nan_rejected(self):
        _, _, config = fixture("moving_stop")
        for update in (
            dict(stationary_max_body_lengths_per_sec=0.5),
            dict(max_gap_sec=float("nan")),
            dict(box_area_min=0.0),
        ):
            with self.assertRaises(ValueError):
                IncidentConfig.model_validate_json(
                    json.dumps(dict(config.model_dump(mode="json"), **update))
                )

    def test_box_scale_jump_resets_evidence(self):
        frames, roi, config = fixture("moving_stop")
        for n in range(5, len(frames)):
            value = frames[n].model_dump(mode="json")
            value["observations"][0]["detection"]["bbox"][2] += 0.3
            frames[n] = incidents.TrackFrame.model_validate_json(json.dumps(value))
        self.assertEqual(screen_video(frames, roi, config, "0" * 64), [])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.WindowPipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = self.fixture.fixture
        self.config = self.source.root / "incidents.json"
        _, _, config = fixture("moving_stop")
        save_json(self.config, config.model_dump(mode="json"))
        self.output = self.source.root / "incidents"

    def run_pipeline(self, output=None, draft=True):
        return process_incidents(
            self.source.manifest,
            self.source.traffic,
            self.config,
            self.source.output,
            output or self.output,
            draft,
        )

    def snapshot(self, directory):
        return {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in directory.iterdir()
            if p.is_file() and p.name != ".ingest.lock"
        }

    def test_pending_roi_gate_and_empty_publication_resume(self):
        before = self.snapshot(self.source.output)
        with self.assertRaisesRegex(ValueError, "Reviewed ROI"):
            self.run_pipeline(draft=False)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_pipeline()["events"], 0)
        baseline = self.snapshot(self.output)
        self.assertTrue(self.run_pipeline()["resumed"])
        self.assertEqual(baseline, self.snapshot(self.output))
        self.assertEqual(before, self.snapshot(self.source.output))

    def test_raw_foreign_and_corrupt_input_preserved(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_pipeline(self.source.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.run_pipeline()
        self.assertEqual((self.output / "keep.txt").read_text(), "keep")
        (self.source.output / "frames.jsonl").write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_pipeline(self.source.root / "other")

    def test_invalid_frame_fails_closed_and_logs_then_recovers(self):
        with patch.object(
            incidents.TrackFrame,
            "model_validate_json",
            side_effect=ValueError("invalid frame"),
        ):
            with self.assertRaisesRegex(ValueError, "videos failed"):
                self.run_pipeline()
        self.assertFalse((self.output / "events.jsonl").exists())
        self.assertIn("frame_error", (self.output / "incidents.log").read_text())
        self.run_pipeline()
        self.assertNotIn(
            "errors", json.loads((self.output / "event_state.json").read_text())
        )

    def test_changed_config_and_corrupt_events_block_resume(self):
        self.run_pipeline()
        ledger = self.output / "events.jsonl"
        ledger.write_text("changed")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_pipeline()
        config = json.loads(self.config.read_text())
        config["stationary_duration_sec"] = 4.0
        save_json(self.config, config)
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            self.run_pipeline()

    def test_publication_interrupt_skips_completed_video(self):
        save = incidents.save_json

        def interrupt(path, value):
            if path.name == "event_manifest.json":
                raise KeyboardInterrupt()
            return save(path, value)

        with patch.object(incidents, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pipeline()
        with patch.object(
            incidents, "screen_video", side_effect=AssertionError("must skip")
        ):
            self.assertEqual(self.run_pipeline()["events"], 0)

    def test_v2_synthetic_package_review_keeps_demo_scope(self):
        self.run_pipeline()
        report = json.loads((self.output / "event_manifest.json").read_text())
        frames, roi, config = fixture("moving_stop")
        event = screen_video(
            frames, roi, config, checksum(report["signature"]), synthetic=True
        )[0]
        ledger = self.output / "events.jsonl"
        ledger.write_text(canonical(event.model_dump(mode="json")) + "\n")
        report["events"] = 1
        save_json(self.output / "event_manifest.json", report)
        state = json.loads((self.output / "event_state.json").read_text())
        state["artifacts"] = {
            name: digest(self.output / name)
            for name in ("events.jsonl", "event_manifest.json")
        }
        save_json(self.output / "event_state.json", state)
        self.assertIsInstance(load_incidents(self.output)[1][0], IncidentEvent)
        reviews = self.source.root / "reviews"
        record = submit_review(
            self.output,
            reviews,
            event.event_id,
            "confirmed",
            "synthetic-tester",
            "Synthetic fixture only.",
            0,
        )
        self.assertEqual(record.scope, "demo_review")
        self.assertEqual(list_events(self.output, reviews)[0]["revision"], 1)


if __name__ == "__main__":
    unittest.main()
