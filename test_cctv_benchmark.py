"""Independent event metric expectations, matching oracle and label/split safety."""

import itertools
import json
import unittest
from unittest.mock import patch

import test_cctv_events as fixture_module
from pipeline.cctv import benchmark_events as module
from pipeline.cctv.benchmark_events import (
    AnnotationSet,
    Interval,
    Prediction,
    SplitManifest,
    evaluate_records,
    metrics,
    optimal_pairs,
    run_benchmark,
    temporal_iou,
)
from pipeline.cctv.benchmark_fixtures import fixture, fixture_report
from pipeline.cctv.ingest import digest, save_json


class MetricTests(unittest.TestCase):
    def test_synthetic_predictions_never_score_as_independent_footage(self):
        labels, splits, pred = fixture()
        label_data = labels.model_dump(mode="json")
        label_data["mode"] = "independent"
        split_data = splits.model_dump(mode="json")
        split_data["mode"] = "independent"
        split_data["videos"][0]["grouping_status"] = "verified"
        result = evaluate_records(
            AnnotationSet.model_validate_json(json.dumps(label_data)),
            SplitManifest.model_validate_json(json.dumps(split_data)),
            pred,
        )
        self.assertEqual(
            (result["metrics"]["tp"], result["metrics"]["fp"], result["metrics"]["fn"]),
            (0, 0, 2),
        )
        self.assertTrue(
            all(
                r["reason"] == "synthetic_prediction_in_independent_benchmark"
                for r in result["ignored_predictions"]
            )
        )

    def test_known_counts_duplicates_and_unknown_coverage(self):
        report = fixture_report()
        self.assertTrue(report["passed"])
        result = report["result"]
        self.assertEqual(
            (result["metrics"]["tp"], result["metrics"]["fp"], result["metrics"]["fn"]),
            (1, 2, 1),
        )
        self.assertEqual(result["metrics"]["reviewed_camera_seconds"], 10.0)
        self.assertEqual(result["metrics"]["false_alarms_per_camera_hour"], 720.0)
        self.assertEqual(result["matches"][0]["detection_delay_source_sec"], 3.0)
        self.assertEqual(result["ignored_predictions"][0]["event_id"], "P-004")
        self.assertEqual(result["by_label"]["road_incident"]["fp"], 2)

    def test_temporal_iou_hand_computed_and_threshold_inclusive(self):
        a = Interval(start_sec=0.0, end_sec=4.0)
        b = Interval(start_sec=2.0, end_sec=6.0)
        self.assertAlmostEqual(temporal_iou(a, b), 1 / 3)
        self.assertEqual(temporal_iou(a, Interval(start_sec=4.0, end_sec=5.0)), 0.0)
        labels, splits, pred = fixture()
        self.assertEqual(
            evaluate_records(labels, splits, pred, iou_threshold=0.5)["metrics"]["tp"],
            1,
        )
        self.assertEqual(
            evaluate_records(labels, splits, pred, iou_threshold=0.51)["metrics"]["tp"],
            0,
        )

    def test_matching_exact_weight_objective_and_greedy_trap(self):
        self.assertEqual(optimal_pairs([[0.9, 0.8], [0.85, 0.0]]), [(0, 1), (1, 0)])
        # Protocol optimizes total IoU, not cardinality before IoU.
        self.assertEqual(optimal_pairs([[0.99, 0.31], [0.31, 0.0]]), [(0, 0)])

    def test_independent_brute_matching_oracle(self):
        matrices = [
            [[0.5, 0.6, 0.0], [0.7, 0.0, 0.4], [0.0, 0.8, 0.9]],
            [[0.2, 0.9], [0.8, 0.7], [0.6, 0.0]],
        ]
        for matrix in matrices:
            best = 0.0
            for targets in itertools.product(
                range(-1, len(matrix[0])), repeat=len(matrix)
            ):
                assigned = [t for t in targets if t >= 0]
                if len(assigned) != len(set(assigned)) or any(
                    t >= 0 and matrix[i][t] <= 0 for i, t in enumerate(targets)
                ):
                    continue
                best = max(
                    best, sum(matrix[i][t] for i, t in enumerate(targets) if t >= 0)
                )
            self.assertAlmostEqual(
                sum(matrix[i][j] for i, j in optimal_pairs(matrix)), best
            )

    def test_undefined_denominators_are_null_not_perfect(self):
        result = metrics(0, 0, 0, 0.0)
        for key in ("precision", "recall", "f1", "false_alarms_per_camera_hour"):
            self.assertIsNone(result[key])
        self.assertEqual(metrics(0, 2, 0, 10.0)["precision"], 0.0)

    def test_pending_unknown_and_unassigned_not_negative(self):
        labels, splits, pred = fixture()
        for status in ("pending", "unknown"):
            value = labels.model_dump(mode="json")
            value["intervals"][0].update(
                review_status=status,
                events=[],
                reviewed_by=[],
                reviewed_at=None,
                blind_to_predictions=False,
            )
            report = evaluate_records(
                AnnotationSet.model_validate_json(json.dumps(value)), splits, pred
            )
            self.assertEqual(report["status"], "NOT_EVALUATED_NO_REVIEWED_INTERVALS")
            self.assertIsNone(report["metrics"])
        value = splits.model_dump(mode="json")
        value["videos"][0]["split"] = "unassigned"
        self.assertIsNone(
            evaluate_records(
                labels, SplitManifest.model_validate_json(json.dumps(value)), pred
            )["metrics"]
        )

    def test_wrong_type_is_fp_and_fn_not_a_match(self):
        labels, splits, pred = fixture()
        value = pred[0].model_dump(mode="json")
        value["label"] = "congestion"
        report = evaluate_records(
            labels, splits, [Prediction.model_validate_json(json.dumps(value))]
        )
        self.assertEqual(
            (report["metrics"]["tp"], report["metrics"]["fp"], report["metrics"]["fn"]),
            (0, 1, 2),
        )

    def test_schema_review_overlap_trigger_and_search_guards(self):
        labels, splits, pred = fixture()
        for update in (
            dict(reviewed_by=["a", "A"]),
            dict(blind_to_predictions=False),
            dict(reviewed_at="2026-10-01T00:00:00"),
        ):
            value = labels.model_dump(mode="json")
            value["intervals"][0].update(update)
            with self.assertRaises(ValueError):
                AnnotationSet.model_validate_json(json.dumps(value))
        value = labels.model_dump(mode="json")
        value["intervals"].append(value["intervals"][0])
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            AnnotationSet.model_validate_json(json.dumps(value))
        with self.assertRaises(ValueError):
            Prediction.model_validate_json(
                json.dumps(
                    dict(pred[0].model_dump(mode="json"), trigger_source_sec=100.0)
                )
            )
        with self.assertRaisesRegex(ValueError, "16 predictions"):
            optimal_pairs([[1.0] * 17])
        with self.assertRaises(ValueError):
            evaluate_records(labels, splits, pred, iou_threshold=float("nan"))

    def test_split_group_video_and_unverified_leakage(self):
        _, splits, _ = fixture()
        value = splits.model_dump(mode="json")
        value["videos"].append(
            dict(value["videos"][0], video_id="OTHER", split="development")
        )
        with self.assertRaisesRegex(ValueError, "group leakage"):
            SplitManifest.model_validate_json(json.dumps(value))
        value = splits.model_dump(mode="json")
        value["videos"].append(value["videos"][0])
        with self.assertRaisesRegex(ValueError, "Video occurs"):
            SplitManifest.model_validate_json(json.dumps(value))
        value = splits.model_dump(mode="json")
        value["mode"] = "independent"
        value["videos"][0]["grouping_status"] = "unverified"
        with self.assertRaisesRegex(ValueError, "unassigned"):
            SplitManifest.model_validate_json(json.dumps(value))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.EventTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_events()
        self.source = self.fixture.source
        sha = digest(self.source.manifest)
        self.annotation = self.source.root / "labels.json"
        self.splits = self.source.root / "splits.json"
        self.output = self.source.root / "benchmark"
        self.labels = dict(
            schema_version=1,
            mode="independent",
            source_manifest_sha256=sha,
            intervals=[
                dict(
                    camera_id="N001",
                    video_id="N001-V001",
                    start_sec=0.0,
                    end_sec=2.4,
                    review_status="pending",
                    reviewed_by=[],
                    reviewed_at=None,
                    blind_to_predictions=False,
                    events=[],
                )
            ],
        )
        self.split = dict(
            schema_version=1,
            mode="independent",
            source_manifest_sha256=sha,
            camera_holdout=False,
            videos=[
                dict(
                    video_id="N001-V001",
                    recording_group_id="UNVERIFIED",
                    grouping_status="unverified",
                    split="unassigned",
                )
            ],
        )
        save_json(self.annotation, self.labels)
        save_json(self.splits, self.split)

    def run_pipeline(self, output=None):
        return run_benchmark(
            self.source.manifest,
            self.annotation,
            self.splits,
            self.fixture.output,
            output or self.output,
        )

    def test_pending_report_no_score_and_resume_preserves(self):
        source_hash = digest(self.fixture.output / "events.jsonl")
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NOT_EVALUATED_NO_REVIEWED_INTERVALS")
        self.assertFalse(result["split_ready"])
        paths = [
            self.output / "benchmark_report.json",
            self.output / "benchmark_state.json",
        ]
        before = [(digest(p), p.stat().st_mtime_ns) for p in paths]
        with patch.object(
            module, "evaluate_records", side_effect=AssertionError("must skip")
        ):
            self.assertTrue(self.run_pipeline()["resumed"])
        self.assertEqual(before, [(digest(p), p.stat().st_mtime_ns) for p in paths])
        self.assertEqual(source_hash, digest(self.fixture.output / "events.jsonl"))

    def test_reviewed_negative_is_fp_with_measured_duration(self):
        self.labels["intervals"][0].update(
            review_status="reviewed",
            reviewed_by=["synthetic-a", "synthetic-b"],
            reviewed_at="2026-10-01T00:00:00Z",
            blind_to_predictions=True,
        )
        self.labels["mode"] = "synthetic"
        self.split["mode"] = "synthetic"
        self.split["videos"][0].update(grouping_status="synthetic", split="test")
        save_json(self.annotation, self.labels)
        save_json(self.splits, self.split)
        result = self.run_pipeline()["results"][1]
        self.assertEqual(
            (result["metrics"]["tp"], result["metrics"]["fp"], result["metrics"]["fn"]),
            (0, 1, 0),
        )
        self.assertEqual(result["metrics"]["reviewed_camera_seconds"], 2.4)
        self.assertEqual(result["status"], "EVALUATED_SYNTHETIC")

    def test_scope_provenance_and_canonical_split_rejected_before_output(self):
        self.labels["intervals"][0]["camera_id"] = "OTHER"
        save_json(self.annotation, self.labels)
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            self.run_pipeline()
        self.assertFalse(self.output.exists())
        self.labels["intervals"][0]["camera_id"] = "N001"
        self.labels["source_manifest_sha256"] = "0" * 64
        save_json(self.annotation, self.labels)
        with self.assertRaisesRegex(ValueError, "another manifest"):
            self.run_pipeline()

    def test_camera_holdout_leakage_and_raw_foreign_protection(self):
        value = json.loads(self.source.manifest.read_text())
        value["videos"].append(
            dict(video_id="SECOND", camera_id="N001", path="second.mov")
        )
        save_json(self.source.manifest, value)
        self.split.update(
            source_manifest_sha256=digest(self.source.manifest), camera_holdout=True
        )
        self.split["videos"][0].update(split="development", grouping_status="verified")
        self.split["videos"].append(
            dict(
                video_id="SECOND",
                recording_group_id="SECOND-SESSION",
                grouping_status="verified",
                split="test",
            )
        )
        from pipeline.cctv.manifest import load_manifest

        with self.assertRaisesRegex(ValueError, "Camera holdout leakage"):
            SplitManifest.model_validate_json(json.dumps(self.split)).bind(
                load_manifest(self.source.manifest, require_files=False)
            )

    def test_corruption_and_input_change_preserved(self):
        self.run_pipeline()
        report = self.output / "benchmark_report.json"
        report.write_text("corrupt")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_pipeline()
        self.labels["intervals"][0]["end_sec"] = 3.0
        save_json(self.annotation, self.labels)
        with self.assertRaisesRegex(ValueError, "recipe changed"):
            self.run_pipeline()

    def test_raw_foreign_and_publication_interruption(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.run_pipeline(self.source.raw / "generated")
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.run_pipeline()
        self.output = self.source.root / "recover"
        save = module.save_json

        def interrupt(path, value):
            if path.name == "benchmark_report.json":
                raise KeyboardInterrupt()
            return save(path, value)

        with patch.object(module, "save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pipeline()
        self.assertFalse(self.run_pipeline()["resumed"])


if __name__ == "__main__":
    unittest.main()
