"""Verify handover publication with synthetic reports and no CCTV/model access."""

import tempfile
import unittest
from pathlib import Path

from pipeline.cctv.ingest import digest, save_json
from scripts.export_cctv_handover import export_handover


class HandoverTests(unittest.TestCase):
    """Do not turn absent independent labels or machine tests into passing claims."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        (self.raw / "test.mov").write_bytes(b"synthetic")
        self.manifest = self.root / "manifest.json"
        save_json(
            self.manifest,
            dict(
                schema_version=1,
                raw_root=str(self.raw),
                cameras=[dict(camera_id="CAM")],
                videos=[dict(video_id="VIDEO", camera_id="CAM", path="test.mov")],
            ),
        )
        signature = dict(inputs={str(self.manifest): digest(self.manifest)})
        self.runtime = dict(
            signature=signature,
            status="PASSED_TECHNICAL_BENCHMARK",
            by_kind={},
            request_errors_including_warmup=0,
            model_memory_after={},
            memory_scope="fixture",
            server={},
            semantic_retrieval_metrics=None,
        )
        self.event = dict(
            signature=signature,
            status="NOT_EVALUATED_NO_REVIEWED_INTERVALS",
            annotation_mode="independent",
            split_ready=False,
            results=[dict(metrics=None)],
        )
        self.dispatch = dict(
            mode="simulation",
            operational_ready=False,
            assigned_officers=2,
            dispatchable_officer_limit=2,
            required_reserve_officers=2,
            reserve_met=True,
            assignments=[{}],
            unmet_demands=[{}, {}],
        )
        self.dirs = [self.root / n for n in ("runtime", "events", "dispatch")]
        self.output = self.root / "handover"
        self.publish()

    def publish(self):
        for folder, state_name, report_name, report in zip(
            self.dirs,
            ("runtime_state.json", "benchmark_state.json", "dispatch_state.json"),
            ("runtime_report.json", "benchmark_report.json", "dispatch_report.json"),
            (self.runtime, self.event, self.dispatch),
        ):
            folder.mkdir(exist_ok=True)
            save_json(folder / report_name, report)
            save_json(
                folder / state_name,
                dict(
                    status="complete",
                    report_sha256=digest(folder / report_name),
                    signature=dict(manifest_sha256=digest(self.manifest)),
                ),
            )

    def export(self, output=None):
        return export_handover(self.manifest, *self.dirs, output or self.output)

    def test_export_preserves_pending_and_raw(self):
        before = digest(self.raw / "test.mov")
        report = self.export()
        self.assertEqual(report["second_machine_status"], "NOT_RUN")
        self.assertIsNone(report["runtime"]["semantic_retrieval_metrics"])
        self.assertIsNone(report["event_benchmark"]["results"][0]["metrics"])
        self.assertFalse(report["operational_ready"])
        self.assertEqual(before, digest(self.raw / "test.mov"))

    def test_resume_preserves_all_bytes_and_mtime(self):
        self.export()
        before = {
            p: (digest(p), p.stat().st_mtime_ns) for p in self.output.glob("*.json")
        }
        self.assertTrue(self.export()["resumed"])
        self.assertEqual(before, {p: (digest(p), p.stat().st_mtime_ns) for p in before})

    def test_corrupt_source_publication_rejected(self):
        (self.dirs[0] / "runtime_report.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "changed publication"):
            self.export()

    def test_source_manifest_binding_rejected(self):
        self.runtime["signature"] = dict(inputs={})
        self.publish()
        with self.assertRaisesRegex(ValueError, "source manifest mismatch"):
            self.export()

    def test_non_simulation_dispatch_rejected(self):
        self.dispatch["mode"] = "operational"
        self.publish()
        with self.assertRaisesRegex(ValueError, "Only simulation"):
            self.export()

    def test_changed_input_and_export_artifact_rejected(self):
        self.export()
        (self.output / "second-machine.pending.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.export()
        self.runtime["status"] = "FAILED_REQUESTS"
        self.publish()
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.export()

    def test_raw_overlap_and_foreign_output_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.export(self.raw / "output")
        self.output.mkdir()
        (self.output / "foreign.txt").write_text("preserve")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.export()


if __name__ == "__main__":
    unittest.main()
