"""Integration verifier detects metadata, media and review/cap failures."""

import copy
import unittest
from io import BytesIO

import httpx
from PIL import Image

from scripts.verify_cctv_demo import verify_demo


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

    def verify(self):
        def handle(request):
            self.requests.append(str(request.url))
            path = request.url.path
            if path == "/":
                return httpx.Response(
                    200,
                    text="".join(
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
            return verify_demo(client, self.catalog)

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


if __name__ == "__main__":
    unittest.main()
