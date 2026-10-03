"""Integration verifier detects metadata, media and review/cap failures."""

import copy
import sqlite3
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

from scripts.verify_cctv_demo import SERVER_CODE, run, verify_demo


class DemoAcceptanceTests(unittest.TestCase):
    """HTTP fixtures only; no model, raw footage or existing review journal."""

    def setUp(self):
        original = dict(
            id="frame-1",
            file="N001-V001/001.jpg",
            camera_id="N001",
            video_id="N001-V001",
            timestamp_sec=0.0,
            frame_idx=0,
            faiss_id=1,
        )
        self.catalog = {original["id"]: original}
        row = dict(
            original,
            score=0.5,
            image_url="/cctv/image/1",
            video_url="/cctv/video/N001-V001",
            video_seek_sec=0.0,
        )
        self.image = BytesIO()
        Image.new("RGB", (2, 2)).save(self.image, format="JPEG")
        source = dict(
            video_id="N001-V001",
            camera_id="N001",
            window_count=1,
            roi_image_url="/cctv/traffic/roi/N001-V001",
        )
        demand = dict(event_id="event-1", review_status="needs_more_evidence")
        proposal = dict(
            dry_run=True,
            fictional_resource_data=True,
            operational_ready=False,
            read_only=True,
            assigned_officers=0,
            dispatchable_officer_limit=2,
            available_officers_remaining=4,
            required_reserve_officers=2,
            reserve_met=True,
            assignments=[],
            unmet_demands=[],
        )
        self.payloads = {
            "/ready": dict(
                ready=True,
                frames=1,
                benchmark_identity=dict(session_id="fixture-session"),
            ),
            "/cctv/search": dict(results=[row], total=1),
            "/cctv/search/image": dict(results=[row], total=1),
            "/cctv/traffic": dict(
                available=True, operational_ready=False, videos=[source], windows=1
            ),
            "/cctv/traffic/windows/N001-V001": dict(
                total=1,
                rows=[dict(window=dict(video_id="N001-V001", camera_id="N001"))],
            ),
            "/cctv/events": dict(
                read_only=True,
                operational_ready=False,
                packages=[
                    dict(package_id=p, available=True, total=int(p == "demo"))
                    for p in ("incidents", "congestion", "demo")
                ],
            ),
            "/cctv/events/incidents": dict(total=0, rows=[]),
            "/cctv/events/congestion": dict(total=0, rows=[]),
            "/cctv/events/demo": dict(
                total=1,
                rows=[
                    dict(event_id="event-1", status="needs_more_evidence", evidence=[])
                ],
            ),
            "/cctv/dispatch": dict(
                read_only=True,
                packages=[
                    dict(package_id=p, available=True)
                    for p in ("simulation", "events_empty", "review_gate")
                ],
            ),
            "/cctv/dispatch/simulation": copy.deepcopy(proposal),
            "/cctv/dispatch/events_empty": copy.deepcopy(proposal),
            "/cctv/dispatch/review_gate": dict(proposal, unmet_demands=[demand]),
        }
        self.video_status = 206
        self.requests = []

    def verify(self, profile="legacy", expected_dispatch=None):
        def handle(request):
            self.requests.append(str(request.url))
            path = request.url.path
            if path == "/":
                return httpx.Response(
                    200,
                    text='<div id="workflow-panel"></div>'
                    + "".join(
                        f'<div id="{p}-workspace"></div>'
                        for p in ("search", "traffic", "events", "dispatch")
                    ),
                )
            if path.startswith("/static/"):
                return httpx.Response(200, text="fixture asset")
            if path in ("/cctv/image/1", "/cctv/traffic/roi/N001-V001"):
                return httpx.Response(200, content=self.image.getvalue())
            if path == "/cctv/video/N001-V001":
                return httpx.Response(
                    self.video_status,
                    content=b"0" * 32,
                    headers={"Content-Range": "bytes 0-31/100"},
                )
            if request.method == "POST" and path == "/cctv/dispatch/simulation":
                return httpx.Response(405)
            if path not in self.payloads:
                raise AssertionError("Unexpected endpoint " + path)
            return httpx.Response(200, json=self.payloads[path])

        with httpx.Client(
            base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(handle)
        ) as client:
            return verify_demo(
                client,
                self.catalog,
                profile=profile,
                expected_dispatch=expected_dispatch,
            )

    def test_workflow_profile_requires_exact_current_packages(self):
        self.payloads["/cctv/dispatch"]["packages"] = [
            dict(package_id="simulation", available=True)
        ]
        self.assertTrue(self.verify("workflow", {"simulation"})["passed"])
        with self.assertRaisesRegex(ValueError, "missing/stale"):
            self.verify()
        self.payloads["/cctv/dispatch"]["packages"][0]["available"] = False
        with self.assertRaisesRegex(ValueError, "missing/stale"):
            self.verify("workflow", {"simulation"})

    def test_complete_http_flow(self):
        report = self.verify()
        self.assertTrue(report["passed"])
        self.assertFalse(report["operational_ready"])
        self.assertEqual(report["checks"]["traffic_windows"], 1)
        self.assertTrue(any("/cctv/search/image" in url for url in self.requests))

    def test_wrong_locator_rejected(self):
        self.payloads["/cctv/search"]["results"][0]["frame_idx"] = 500
        with self.assertRaisesRegex(ValueError, "locator"):
            self.verify()

    def test_external_asset_url_rejected_before_fetch(self):
        self.payloads["/cctv/search"]["results"][0]["image_url"] = (
            "https://example.com/image.jpg"
        )
        with self.assertRaisesRegex(ValueError, "asset ID"):
            self.verify()
        self.assertFalse(any("example.com" in url for url in self.requests))

    def test_missing_video_range_rejected(self):
        self.video_status = 200
        with self.assertRaisesRegex(ValueError, "byte range"):
            self.verify()

    def test_unavailable_stage_and_unsafe_budget_rejected(self):
        self.payloads["/cctv/dispatch"]["packages"][0]["available"] = False
        with self.assertRaisesRegex(ValueError, "missing/stale"):
            self.verify()
        self.payloads["/cctv/dispatch"]["packages"][0]["available"] = True
        self.payloads["/cctv/dispatch/simulation"]["assigned_officers"] = 3
        with self.assertRaisesRegex(ValueError, "cap/reserve"):
            self.verify()

    def test_live_review_mismatch_rejected(self):
        self.payloads["/cctv/dispatch/review_gate"]["unmet_demands"][0][
            "review_status"
        ] = "confirmed"
        with self.assertRaisesRegex(ValueError, "live review"):
            self.verify()

    def test_rebuilt_bundle_without_journals_or_historical_benchmark(self):
        """Absent reviews are valid inputs, but their creation invalidates the run."""
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            manifest = root / "configs/demo.json"
            index = root / "outputs/index"
            for name in (
                "video-preview",
                "tracking-clips",
                "traffic-draft-v1",
                "roi-draft-previews",
                "events-v1",
                "events-demo-v1",
                "incidents-draft-v1",
                "dispatch-simulation-v2",
                "dispatch-events-empty-v2",
                "dispatch-review-gate-v2",
            ):
                (root / "outputs/demo-data" / name).mkdir(parents=True)
            for path in (
                manifest,
                index / "index_state.json",
                root / "outputs/model-cache/clip.json",
                root / "outputs/demo-data-review/dispatch-event-request.json",
                *(root / name for name in SERVER_CODE),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            def connect():
                conn = sqlite3.connect(":memory:")
                conn.execute("CREATE TABLE frames (id, file, faiss_id)")
                conn.execute(
                    "CREATE TABLE cctv_frames (id, camera_id, video_id, timestamp_sec, frame_idx)"
                )
                return conn

            journal = root / "outputs/demo-data/events-demo-reviews-v1"
            with (
                patch("scripts.verify_cctv_demo.ROOT", root),
                patch(
                    "scripts.verify_cctv_demo.load_manifest",
                    return_value=SimpleNamespace(
                        path=manifest, raw_root=root / "raw", videos=[]
                    ),
                ),
                patch("scripts.verify_cctv_demo.CctvSearch") as search,
                patch("scripts.verify_cctv_demo.check_server"),
                patch(
                    "scripts.verify_cctv_demo.verify_demo",
                    return_value={"passed": True},
                ) as verify,
            ):
                search.return_value.connect.side_effect = connect
                report = run(
                    manifest,
                    index,
                    root / "outputs/acceptance",
                    "http://127.0.0.1:8080",
                )
                self.assertTrue(report["passed"])
                self.assertEqual(len(report["absent_review_folders"]), 3)
                self.assertFalse(journal.exists())
                self.assertFalse(
                    (root / "outputs/demo-data/runtime-benchmark-v1").exists()
                )

                def create_journal(*args):
                    journal.mkdir()
                    return {"passed": True}

                verify.side_effect = create_journal
                with self.assertRaisesRegex(ValueError, "journal appeared"):
                    run(
                        manifest,
                        index,
                        root / "outputs/changed",
                        "http://127.0.0.1:8080",
                    )
                self.assertFalse((root / "outputs/changed").exists())


if __name__ == "__main__":
    unittest.main()
