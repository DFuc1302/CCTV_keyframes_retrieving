"""Synthetic reviewer fixtures: validation never creates independent ground truth."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.review_notes import ReviewerNotes, run


class ReviewerNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "configs"
        self.config.mkdir()
        self.manifest = self.config / "manifest.json"
        save_json(
            self.manifest,
            dict(
                schema_version=1,
                raw_root="../raw",
                cameras=[dict(camera_id="C1", name="Fixture")],
                videos=[dict(video_id="V1", camera_id="C1", path="fixture.mov")],
            ),
        )
        self.labels = self.config / "pending.json"
        save_json(
            self.labels,
            dict(
                schema_version=1,
                mode="independent",
                source_manifest_sha256=digest(self.manifest),
                intervals=[
                    dict(
                        camera_id="C1",
                        video_id="V1",
                        start_sec=0.0,
                        end_sec=10.0,
                        review_status="pending",
                        reviewed_by=[],
                        reviewed_at=None,
                        blind_to_predictions=False,
                        events=[],
                    )
                ],
            ),
        )
        self.blank = self.root / "templates"
        run(self.manifest, self.labels, self.blank)
        self.work = self.root / "working"
        self.work.mkdir()
        self.a, self.b = self.work / "a.json", self.work / "b.json"
        self.left = json.loads((self.blank / "reviewer-a.pending.json").read_text())
        self.right = json.loads((self.blank / "reviewer-b.pending.json").read_text())

    def check(self):
        save_json(self.a, self.left)
        save_json(self.b, self.right)
        output = self.root / "checked"
        result = run(self.manifest, self.labels, output, self.a, self.b)
        report = json.loads((output / "comparison_report.json").read_text())
        return result, report

    def complete(self):
        for data, name in ((self.left, "fixture-A"), (self.right, "fixture-B")):
            data["reviewer"] = name
            data["intervals"][0].update(
                status="complete",
                reason="Synthetic test only",
                recorded_at="2026-10-01T10:00:00+07:00",
                blind_to_predictions=True,
                full_interval_watched=True,
                ontology_reviewed=True,
            )

    def test_pending_and_noop_resume(self):
        before = {
            p.name: (digest(p), p.stat().st_mtime_ns) for p in self.blank.glob("*.json")
        }
        self.assertTrue(run(self.manifest, self.labels, self.blank)["resumed"])
        self.assertEqual(
            before,
            {
                p.name: (digest(p), p.stat().st_mtime_ns)
                for p in self.blank.glob("*.json")
            },
        )
        result, report = self.check()
        self.assertEqual(result["counts"]["PENDING_REVIEW"], 1)
        self.assertFalse(report["ground_truth_published"])
        self.assertEqual(report["reviewed_intervals"], 0)
        self.assertTrue(
            run(self.manifest, self.labels, self.root / "checked", self.a, self.b)[
                "resumed"
            ]
        )

    def test_agreement_ignores_local_ids_but_needs_adjudication(self):
        self.complete()
        for data, event_id in ((self.left, "A1"), (self.right, "B1")):
            data["intervals"][0]["events"] = [
                dict(event_id=event_id, label="congestion", start_sec=2.0, end_sec=8.0)
            ]
        _, report = self.check()
        self.assertEqual(report["counts"]["AGREEMENT_REQUIRES_ADJUDICATION"], 1)
        self.assertFalse(report["ground_truth_published"])

    def test_disagreement_including_duplicate_span_counts(self):
        self.complete()
        event = dict(event_id="A1", label="accident", start_sec=2.0, end_sec=8.0)
        self.left["intervals"][0]["events"] = [event]
        self.right["intervals"][0]["events"] = [
            dict(event, event_id="B1"),
            dict(event, event_id="B2"),
        ]
        _, report = self.check()
        self.assertEqual(report["counts"]["DISAGREEMENT_REQUIRES_ADJUDICATION"], 1)

    def test_unknown_requires_context(self):
        self.complete()
        self.left["intervals"][0]["status"] = "unknown"
        _, report = self.check()
        self.assertEqual(report["counts"]["UNKNOWN_NEEDS_CONTEXT"], 1)

    def test_exposure_is_ineligible(self):
        self.complete()
        self.left["intervals"][0]["blind_to_predictions"] = False
        _, report = self.check()
        self.assertEqual(report["counts"]["INELIGIBLE_NOT_BLIND"], 1)

    def test_identity_aliases_and_same_slots_rejected(self):
        self.complete()
        self.left["reviewer"], self.right["reviewer"] = "Alice", "ＡＬＩＣＥ"
        with self.assertRaisesRegex(ValueError, "identities"):
            self.check()
        self.right["reviewer"] = "Bob"
        self.right["slot_id"] = self.left["slot_id"].upper()
        with self.assertRaisesRegex(ValueError, "slots"):
            self.check()

    def test_scope_and_provenance_rejected(self):
        self.left["intervals"][0]["camera_id"] = "C2"
        with self.assertRaisesRegex(ValueError, "cover"):
            self.check()
        self.left["intervals"][0]["camera_id"] = "C1"
        self.left["annotation_template_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source/template"):
            self.check()

    def test_invalid_observation_contracts(self):
        self.complete()
        for change in (
            dict(recorded_at="2026-10-01T10:00:00"),
            dict(full_interval_watched=False),
            dict(ontology_reviewed=False),
            dict(reason=""),
            dict(
                events=[
                    dict(event_id="E1", label="accident", start_sec=9.0, end_sec=11.0)
                ]
            ),
        ):
            with self.subTest(change=change):
                data = copy.deepcopy(self.left)
                data["intervals"][0].update(change)
                with self.assertRaises(ValueError):
                    ReviewerNotes.model_validate_json(json.dumps(data))

    def test_duplicate_coverage_and_missing_reviewer_rejected(self):
        data = copy.deepcopy(self.left)
        data["intervals"].append(copy.deepcopy(data["intervals"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate note"):
            ReviewerNotes.model_validate_json(json.dumps(data))
        self.complete()
        self.left["reviewer"] = None
        with self.assertRaisesRegex(ValueError, "actual reviewer"):
            self.check()

    def test_preserve_edited_outputs_and_changed_inputs(self):
        target = self.blank / "reviewer-a.pending.json"
        target.write_text("human edit", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Edited output"):
            run(self.manifest, self.labels, self.blank)
        self.assertEqual(target.read_text(), "human edit")
        self.labels.write_text(self.labels.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Input/recipe changed"):
            run(self.manifest, self.labels, self.blank)

    def test_foreign_and_source_outputs_preserved(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "user.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign output"):
            run(self.manifest, self.labels, foreign)
        with self.assertRaises(ValueError):
            run(self.manifest, self.labels, self.config / "generated")
        with self.assertRaises(ValueError):
            run(self.manifest, self.labels, self.root / "raw" / "generated")
        self.assertEqual((foreign / "user.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
