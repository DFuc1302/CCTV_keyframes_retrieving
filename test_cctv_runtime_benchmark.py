"""CPU-only benchmark protocol checks with synthetic index and HTTP transport."""

import json
import unittest
from unittest.mock import patch

import httpx
import numpy as np

import test_cctv_index as index_tests
from pipeline.cctv.benchmark_runtime import (
    ROOT,
    SERVER_CODE,
    QueryCase,
    Workload,
    loopback_url,
    measure,
    run_benchmark,
    statistics,
    verify_results,
)
from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.search import CctvSearch


class RuntimeBenchmarkTests(unittest.TestCase):
    """Exercise failures and idempotence without real model/data/server."""

    def setUp(self):
        fixture = index_tests.CCTVIndexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.build()
        self.fixture = fixture
        self.manifest = fixture.root / "manifest.json"
        save_json(
            self.manifest,
            dict(
                schema_version=1,
                raw_root=str(fixture.raw),
                cameras=[dict(camera_id="N001")],
                videos=[
                    dict(video_id="N001-V001", camera_id="N001", path="source.mov")
                ],
            ),
        )
        self.workload = fixture.root / "workload.json"
        save_json(
            self.workload,
            dict(
                schema_version=1,
                warmup=1,
                repeats=2,
                top_k=2,
                cases=[
                    dict(query_id="TEXT", kind="text", text="red road"),
                    dict(query_id="IMAGE", kind="image", frame="N001-V001/001.jpg"),
                ],
            ),
        )
        self.output = fixture.root / "benchmark"
        self.session = "test-session-1"
        self.calls = 0
        self.search = CctvSearch(fixture.output)
        self.rows = self.search.query(np.asarray([[1, 0, 0]], dtype=np.float32), 2)
        self.catalog = {row["id"]: row for row in self.rows}
        self.transport = httpx.MockTransport(self.respond)
        self.fail = False
        self.bad_identity = False

    def respond(self, request):
        if request.url.path == "/ready":
            return httpx.Response(
                200,
                json=dict(
                    ready=True,
                    benchmark_identity=dict(
                        session_id=self.session,
                        index_state_sha256="wrong"
                        if self.bad_identity
                        else digest(self.fixture.output / "index_state.json"),
                        model_recipe=self.fixture.recipe,
                        code_sha256={name: digest(ROOT / name) for name in SERVER_CODE},
                    ),
                    model_memory={},
                ),
            )
        self.calls += 1
        if self.fail:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json=dict(results=self.rows, total=2))

    def benchmark(self, output=None):
        return run_benchmark(
            self.manifest,
            self.workload,
            self.fixture.output,
            output or self.output,
            "http://127.0.0.1:8080",
            transport=self.transport,
        )

    def test_percentile_interpolation_and_empty(self):
        actual = statistics([10, 30, 20, 40])
        self.assertEqual(actual["p50_ms"], 25)
        self.assertEqual(actual["p95_ms"], 38.5)
        self.assertEqual(statistics([])["p95_ms"], None)
        with self.assertRaises(ValueError):
            statistics([float("nan")])

    def test_schema_and_loopback_confinement(self):
        for url in (
            "https://127.0.0.1:8080",
            "http://example.com:8080",
            "http://localhost",
            "http://user@localhost:8080",
            "http://localhost:8080/path",
        ):
            with self.assertRaises(ValueError):
                loopback_url(url)
        self.assertEqual(loopback_url("http://[::1]:8080/"), "http://[::1]:8080")
        for changes in (
            dict(repeats=0),
            dict(warmup=0),
            dict(top_k=51),
            dict(cases=[dict(query_id="X", kind="image", frame="../bad.jpg")]),
        ):
            data = json.loads(self.workload.read_text())
            data.update(changes)
            with self.assertRaises(ValueError):
                Workload.model_validate_json(json.dumps(data))

    def test_result_validation_rejects_locator_score_and_duplicates(self):
        for field, value in (
            ("video_id", "other"),
            ("timestamp_sec", 999),
            ("score", float("nan")),
        ):
            rows = [dict(r) for r in self.rows]
            rows[0][field] = value
            with self.assertRaises(ValueError):
                verify_results(dict(results=rows, total=2), self.catalog, 2)
        with self.assertRaises(ValueError):
            verify_results(dict(results=[self.rows[0]] * 2, total=2), self.catalog, 2)

    def test_http_measurement_with_independent_clock(self):
        with httpx.Client(
            base_url="http://127.0.0.1:8080", transport=self.transport
        ) as client:
            case = QueryCase(query_id="X", kind="text", text="red")
            result = measure(
                client, case, None, 2, self.catalog, clock=iter([1, 1.125]).__next__
            )
        self.assertEqual(result["latency_ms"], 125)
        self.assertTrue(result["passed"])

    def test_frozen_report_and_no_network_resume(self):
        before = digest(self.fixture.output / "meta.db")
        report = self.benchmark()
        self.assertEqual(report["status"], "PASSED_TECHNICAL_BENCHMARK")
        self.assertEqual(report["by_kind"]["text"]["count"], 2)
        self.assertEqual(self.calls, 6)
        snapshot = {
            p: (digest(p), p.stat().st_mtime_ns) for p in self.output.glob("*.json")
        }
        self.bad_identity = True
        self.assertTrue(self.benchmark()["resumed"])
        self.assertEqual(self.calls, 6)
        self.assertEqual(
            snapshot, {p: (digest(p), p.stat().st_mtime_ns) for p in snapshot}
        )
        self.assertEqual(before, digest(self.fixture.output / "meta.db"))
        self.assertIsNone(report["semantic_retrieval_metrics"])

    def test_wrong_server_rejected_before_queries(self):
        self.bad_identity = True
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.benchmark()
        self.assertEqual(self.calls, 0)

    def test_timeout_is_reported_and_not_counted_as_successful_latency(self):
        self.fail = True
        report = self.benchmark()
        self.assertEqual(report["status"], "FAILED_REQUESTS")
        self.assertEqual(report["request_errors_including_warmup"], 6)
        self.assertEqual(report["by_kind"]["image"]["count"], 0)
        self.assertIsNone(report["by_kind"]["image"]["p50_ms"])

    def test_completed_case_resume_and_server_restart_guard(self):
        original = measure

        def interrupt(client, case, *args):
            if case.query_id == "IMAGE":
                raise KeyboardInterrupt()
            return original(client, case, *args)

        with patch("pipeline.cctv.benchmark_runtime.measure", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.benchmark()
        self.assertEqual(self.calls, 3)
        self.session = "restarted"
        with self.assertRaisesRegex(ValueError, "restarted"):
            self.benchmark()
        self.session = "test-session-1"
        self.assertEqual(self.benchmark()["status"], "PASSED_TECHNICAL_BENCHMARK")
        self.assertEqual(self.calls, 6)

    def test_config_mutation_and_report_corruption_fail_closed(self):
        self.benchmark()
        report_path = self.output / "runtime_report.json"
        report_path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "report changed"):
            self.benchmark()
        self.workload.write_text(self.workload.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Input/recipe changed"):
            self.benchmark()

    def test_output_overlap_and_unregistered_query_image_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.benchmark(self.fixture.raw / "generated")
        data = json.loads(self.workload.read_text())
        data["cases"][1]["frame"] = "N001-V001/999.jpg"
        save_json(self.workload, data)
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.benchmark()

    def test_input_mutation_during_http_does_not_publish_report(self):
        responder = self.respond

        def mutate(request):
            result = responder(request)
            if request.url.path == "/cctv/search":
                self.workload.write_text(self.workload.read_text() + "\n")
            return result

        self.transport = httpx.MockTransport(mutate)
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.benchmark()
        self.assertFalse((self.output / "runtime_report.json").exists())


if __name__ == "__main__":
    unittest.main()
