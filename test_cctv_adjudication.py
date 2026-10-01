"""Adjudication gates verified with synthetic notes, never real demo labels."""

import json
import unittest

import test_cctv_review_notes as fixtures
from pipeline.cctv.adjudication import run
from pipeline.cctv.benchmark_events import AnnotationSet
from pipeline.cctv.ingest import digest, save_json


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        fixtures.ReviewerNotesTests.setUp(self)
        self.decisions = self.work / "decisions.json"

    def prepare(self, complete=False):
        if complete:
            fixtures.ReviewerNotesTests.complete(self)
        save_json(self.a, self.left)
        save_json(self.b, self.right)
        self.package = self.root / "adjudication-template"
        run(self.manifest, self.labels, self.a, self.b, self.package)
        self.payload = json.loads(
            (self.package / "adjudication.pending.json").read_text()
        )

    def reviewed(self):
        self.payload["adjudicator"] = "fixture-adjudicator"
        self.payload["intervals"][0].update(
            status="reviewed",
            decided_at="2026-10-01T10:01:00+07:00",
            reason="Synthetic resolution only",
            reviewers_accepted_final=True,
            blind_to_predictions=True,
        )

    def export(self):
        save_json(self.decisions, self.payload)
        return run(
            self.manifest,
            self.labels,
            self.a,
            self.b,
            self.root / "export",
            self.decisions,
            fixture_mode=True,
        )

    def test_pending_export_and_stable_resume(self):
        self.prepare()
        self.assertTrue(
            run(self.manifest, self.labels, self.a, self.b, self.package)["resumed"]
        )
        result = self.export()
        self.assertEqual(result["reviewed_intervals"], 0)
        labels = AnnotationSet.model_validate_json(
            (self.root / "export/annotations.json").read_text()
        )
        self.assertEqual(labels.intervals[0].review_status, "pending")
        self.assertEqual(labels.intervals[0].events, ())
        before = {
            p.name: (digest(p), p.stat().st_mtime_ns)
            for p in (self.root / "export").glob("*.json")
        }
        self.assertTrue(
            run(
                self.manifest,
                self.labels,
                self.a,
                self.b,
                self.root / "export",
                self.decisions,
                fixture_mode=True,
            )["resumed"]
        )
        self.assertEqual(
            before,
            {
                p.name: (digest(p), p.stat().st_mtime_ns)
                for p in (self.root / "export").glob("*.json")
            },
        )

    def test_complete_agreement_stays_pending_until_decision(self):
        self.prepare(complete=True)
        self.assertEqual(self.export()["reviewed_intervals"], 0)

    def test_explicit_positive_and_negative_export_legacy_schema(self):
        self.prepare(complete=True)
        self.reviewed()
        self.payload["intervals"][0]["events"] = [
            dict(
                event_id="FIXTURE-E1", label="road_incident", start_sec=2.0, end_sec=8.0
            )
        ]
        result = self.export()
        self.assertEqual(result["reviewed_intervals"], 1)
        self.assertFalse(result["independent_ground_truth_exported"])
        labels = AnnotationSet.model_validate_json(
            (self.root / "export/annotations.json").read_text()
        )
        self.assertEqual(labels.mode, "synthetic")
        self.assertEqual(labels.intervals[0].reviewed_by, ("fixture-A", "fixture-B"))
        self.assertEqual(labels.intervals[0].events[0].label, "road_incident")

    def test_explicit_negative(self):
        self.prepare(complete=True)
        self.reviewed()
        self.export()
        labels = json.loads((self.root / "export/annotations.json").read_text())
        self.assertEqual(labels["intervals"][0]["review_status"], "reviewed")
        self.assertEqual(labels["intervals"][0]["events"], [])

    def test_unknown_preserves_unscored_coverage(self):
        self.prepare()
        self.payload["adjudicator"] = "fixture-adjudicator"
        self.payload["intervals"][0].update(
            status="unknown",
            reason="Needs longer video",
            decided_at="2026-10-01T10:01:00+07:00",
        )
        self.assertEqual(self.export()["reviewed_intervals"], 0)
        labels = json.loads((self.root / "export/annotations.json").read_text())
        self.assertEqual(labels["intervals"][0]["review_status"], "unknown")

    def test_pending_or_exposed_notes_cannot_export_reviewed(self):
        self.prepare()
        self.reviewed()
        with self.assertRaisesRegex(ValueError, "complete blind"):
            self.export()
        fixtures.ReviewerNotesTests.complete(self)
        self.left["intervals"][0]["blind_to_predictions"] = False
        save_json(self.a, self.left)
        save_json(self.b, self.right)
        self.payload["reviewer_notes_sha256"] = [digest(self.a), digest(self.b)]
        with self.assertRaisesRegex(ValueError, "complete blind"):
            self.export()

    def test_acceptance_identity_time_and_bounds_gates(self):
        self.prepare(complete=True)
        self.reviewed()
        row = self.payload["intervals"][0]
        row["reviewers_accepted_final"] = False
        with self.assertRaisesRegex(ValueError, "acceptance"):
            self.export()
        row["reviewers_accepted_final"] = True
        row["blind_to_predictions"] = False
        with self.assertRaisesRegex(ValueError, "blind adjudication"):
            self.export()
        row["blind_to_predictions"] = True
        self.payload["adjudicator"] = None
        with self.assertRaisesRegex(ValueError, "named adjudicator"):
            self.export()
        self.payload["adjudicator"] = "fixture-adjudicator"
        row["decided_at"] = "2026-10-01T09:59:00+07:00"
        with self.assertRaisesRegex(ValueError, "predates"):
            self.export()
        row["decided_at"] = "2026-10-01T10:01:00"
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.export()
        row["decided_at"] += "+07:00"
        row["events"] = [
            dict(event_id="E1", label="accident", start_sec=9.0, end_sec=11.0)
        ]
        with self.assertRaisesRegex(ValueError, "outside"):
            self.export()

    def test_changed_notes_and_wrong_scope_rejected(self):
        self.prepare()
        self.a.write_text(self.a.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError, "different manifest/template/ordered notes"
        ):
            self.export()
        self.payload["reviewer_notes_sha256"][0] = digest(self.a)
        self.payload["intervals"][0]["video_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "cover"):
            self.export()

    def test_human_edits_and_input_changes_preserved(self):
        self.prepare()
        self.export()
        target = self.root / "export/annotations.json"
        target.write_text("human edit", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Edited output"):
            self.export()
        self.assertEqual(target.read_text(), "human edit")
        self.payload["adjudicator"] = "fixture-name"
        with self.assertRaisesRegex(ValueError, "Inputs/recipe changed"):
            self.export()

    def test_foreign_raw_and_config_output_rejected(self):
        self.prepare()
        for output in (self.config / "generated", self.root / "raw/generated"):
            with self.assertRaises(ValueError):
                run(self.manifest, self.labels, self.a, self.b, output)
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            run(self.manifest, self.labels, self.a, self.b, foreign)
        self.assertEqual((foreign / "keep.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
