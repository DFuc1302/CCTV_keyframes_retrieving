"""Independent event evidence, immutable source and review-history regressions."""

from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import test_cctv_congestion as fixture_module
from pipeline.cctv import events as module
from pipeline.cctv.event_review import list_events, submit_review
from pipeline.cctv.events import Event, build_events, load_events
from pipeline.cctv.ingest import digest, save_json


class EventTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.WindowPipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_windows()
        self.source = self.fixture.fixture
        self.output = self.source.root / "events"
        self.reviews = self.source.root / "reviews"

    def run_events(self, demo=False, output=None):
        return build_events(
            self.source.manifest,
            self.fixture.output,
            self.source.output,
            output or self.output,
            "N001-V001" if demo else None,
            0.0,
            2.0,
        )

    def snapshot(self, folder):
        return {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in folder.iterdir()
            if p.is_file() and p.name != ".ingest.lock"
        }

    def demo_id(self):
        self.run_events(True)
        return next(
            e.event_id for e in load_events(self.output)[1] if e.synthetic_event
        )

    def review(self, event_id, decision="confirmed", revision=0, **kwargs):
        return submit_review(
            self.output,
            self.reviews,
            event_id,
            decision,
            kwargs.get("reviewer", "synthetic-test-reviewer"),
            kwargs.get("reason", "Synthetic workflow test only."),
            revision,
            kwargs.get("reviewed_at", "2026-10-01T00:00:00+07:00"),
        )

    def test_candidate_onset_includes_earlier_slow_dense_window(self):
        self.assertEqual(self.run_events()["events"], 1)
        report, records = load_events(self.output)
        event = records[0]
        self.assertEqual((event.start_sec, event.end_sec), (0.0, 2.0))
        self.assertEqual([e.locator.frame_idx for e in event.evidence], [0, 4])
        self.assertEqual([e.locator.timestamp_sec for e in event.evidence], [0.0, 1.6])
        self.assertFalse(event.synthetic_event)
        self.assertTrue(event.demo_only)
        self.assertFalse(report["operational_ready"])
        self.assertEqual(event.initial_status, "pending_review")

    def test_zero_candidate_package_is_valid(self):
        ledger = self.fixture.output / "signals.jsonl"
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            if row["evidence_sufficient"]:
                row.update(status="no_candidate", persistent_sec=0.0)
        ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
        state_path = self.fixture.output / "window_state.json"
        state = json.loads(state_path.read_text())
        state["artifacts"]["signals.jsonl"] = digest(ledger)
        save_json(state_path, state)
        self.assertEqual(self.run_events()["events"], 0)
        self.assertEqual(load_events(self.output)[1], [])
        self.assertEqual(list_events(self.output, self.reviews), [])
        self.assertFalse(self.reviews.exists())

    def test_synthetic_case_keeps_real_reference_frame_and_demo_label(self):
        self.run_events(True)
        records = load_events(self.output)[1]
        self.assertEqual(len(records), 2)
        event = next(e for e in records if e.synthetic_event)
        self.assertEqual(event.event_type, "demo_case")
        self.assertEqual(event.basis, "synthetic_fixture")
        self.assertTrue(event.demo_only)
        self.assertEqual(event.evidence[0].locator.video_id, "N001-V001")

    def test_resume_preserves_publication_and_all_inputs(self):
        raw, tracking, windows = map(
            self.snapshot, (self.source.raw, self.source.output, self.fixture.output)
        )
        self.run_events(True)
        before = self.snapshot(self.output)
        self.assertTrue(self.run_events(True)["resumed"])
        self.assertEqual(before, self.snapshot(self.output))
        self.assertEqual(raw, self.snapshot(self.source.raw))
        self.assertEqual(tracking, self.snapshot(self.source.output))
        self.assertEqual(windows, self.snapshot(self.fixture.output))

    def test_changed_recipe_and_corrupt_publication_are_preserved(self):
        self.run_events(True)
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            self.run_events(False)
        path = self.output / "events.jsonl"
        path.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_events(True)
        self.assertEqual(path.read_text(), "corrupt")

    def test_raw_overlap_and_foreign_output_blocked(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_events(output=self.source.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            self.run_events()
        self.assertEqual((self.output / "keep.txt").read_text(), "keep")

    def test_corrupt_tracking_blocks_before_output(self):
        (self.source.output / "tracks.db").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Input artifact changed"):
            self.run_events()
        self.assertFalse(self.output.exists())

    def test_failed_evidence_does_not_publish_and_can_resume(self):
        with patch.object(
            module, "evidence_frames", side_effect=ValueError("frame_error")
        ):
            with self.assertRaisesRegex(ValueError, "videos failed"):
                self.run_events()
        self.assertFalse((self.output / "events.jsonl").exists())
        self.assertIn("frame_error", (self.output / "events.log").read_text())
        self.assertEqual(self.run_events()["events"], 1)

    def test_interrupted_publication_resumes_video_checkpoint(self):
        save = module.save_json

        def interrupt(path, value):
            if path.name == "event_manifest.json":
                raise KeyboardInterrupt()
            return save(path, value)

        with patch.object(module, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_events()
        with patch.object(
            module, "evidence_frames", side_effect=AssertionError("should skip")
        ):
            self.assertEqual(self.run_events()["events"], 1)

    def test_event_contract_rejects_forged_id_scope_and_incident_label(self):
        self.run_events(True)
        event = load_events(self.output)[1][-1].model_dump(mode="json")
        for updates in (
            dict(event_id="event-forged"),
            dict(event_type="suspected_accident"),
            dict(demo_only=False),
            dict(end_sec=0.0),
        ):
            with self.assertRaises(ValueError):
                Event.model_validate_json(json.dumps(dict(event, **updates)))
        event["evidence"][0]["locator"]["video_id"] = "N002-V001"
        with self.assertRaises(ValueError):
            Event.model_validate_json(json.dumps(event))

    def test_list_missing_journal_does_not_write(self):
        self.run_events()
        rows = list_events(self.output, self.reviews)
        self.assertEqual(
            (rows[0]["status"], rows[0]["revision"]), ("pending_review", 0)
        )
        self.assertFalse(self.reviews.exists())

    def test_demo_confirmation_stays_demo_and_preserves_originals(self):
        event_id = self.demo_id()
        before = self.snapshot(self.output)
        record = self.review(event_id)
        self.assertEqual(record.scope, "demo_review")
        self.assertEqual(before, self.snapshot(self.output))
        row = next(
            r
            for r in list_events(self.output, self.reviews)
            if r["event"]["event_id"] == event_id
        )
        self.assertTrue(row["event"]["synthetic_event"])
        self.assertFalse(row["event"]["operational_ready"])
        self.assertEqual((row["status"], row["revision"]), ("confirmed", 1))

    def test_stale_revision_and_final_reversal_rejected(self):
        event_id = self.demo_id()
        self.review(event_id)
        with self.assertRaisesRegex(ValueError, "Stale revision"):
            self.review(event_id, revision=0)
        with self.assertRaisesRegex(ValueError, "Reopen"):
            self.review(event_id, "rejected", 1)
        self.review(event_id, "needs_more_evidence", 1)
        self.review(event_id, "rejected", 2)
        with closing(sqlite3.connect(self.reviews / "reviews.db")) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM reviews").fetchone()[0], 3
            )

    def test_review_requires_named_reason_and_timezone(self):
        event_id = self.demo_id()
        for kwargs in (
            dict(reviewer=" "),
            dict(reason=""),
            dict(reviewed_at="2026-10-01T00:00:00"),
        ):
            with self.assertRaises(ValueError):
                self.review(event_id, **kwargs)
        self.assertFalse(self.reviews.exists())

    def test_audit_tampering_and_wrong_package_blocked(self):
        event_id = self.demo_id()
        self.review(event_id)
        with closing(sqlite3.connect(self.reviews / "reviews.db")) as conn:
            conn.execute("UPDATE reviews SET sha256='tampered'")
            conn.commit()
        with self.assertRaisesRegex(ValueError, "audit chain"):
            list_events(self.output, self.reviews)
        with self.assertRaisesRegex(ValueError, "audit chain"):
            self.review(event_id, "needs_more_evidence", 1)
        other = self.source.root / "other-events"
        self.run_events(output=other)
        with self.assertRaisesRegex(ValueError, "another event package"):
            list_events(other, self.reviews)

    def test_review_cannot_write_to_source_or_event_directory(self):
        event_id = self.demo_id()
        for output in (
            self.source.raw,
            self.source.output,
            self.fixture.output,
            self.output,
        ):
            with self.assertRaisesRegex(ValueError, "overlaps"):
                submit_review(
                    self.output,
                    output,
                    event_id,
                    "confirmed",
                    "tester",
                    "Synthetic test.",
                    0,
                )


if __name__ == "__main__":
    unittest.main()
