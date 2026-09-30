"""Exercise CCTV HTTP/readiness/assets with synthetic data and one test encoder."""

import json
import unittest
from contextlib import ExitStack
from io import BytesIO
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import test_cctv_index as index_tests
from app.cctv import create_app
from pipeline.cctv.ingest import digest
from test_cctv_index import TinyEncoder


class ApiEncoder(TinyEncoder):
    """Readiness/text vector matches the synthetic red frame; never loads CLIP."""

    loaded = 0
    closed = 0

    def __init__(self, *args) -> None:
        super().__init__(*args)
        type(self).loaded += 1

    def text(self, query: str) -> np.ndarray:
        return np.asarray([[1, 0, 0]], dtype=np.float32)

    def close(self) -> None:
        type(self).closed += 1


class CCTVApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = index_tests.CCTVIndexTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        fixture.recipe["model_sha256"] = "synthetic-model"
        fixture.build()
        self.preview = fixture.root / "previews"
        self.preview.mkdir()
        video = self.preview / "N001-V001.mp4"
        video.write_bytes(b"0123456789abcdef")
        source = fixture.raw / "source.mov"
        payload = {
            "pipeline": "cctv-preview-v1",
            "videos": {
                "N001-V001": {
                    "status": "complete",
                    "file": video.name,
                    "signature": {"source": str(source), "sha256": digest(source)},
                    "output_sha256": digest(video),
                    "seek_offset_sec": 0,
                }
            },
        }
        (self.preview / "preview_state.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch("app.cctv.model_recipe", return_value=(fixture.recipe, fixture.model))
        )
        ApiEncoder.loaded = ApiEncoder.closed = 0

    def client(self) -> TestClient:
        app = create_app(
            self.fixture.output,
            self.fixture.root / "model.json",
            self.preview,
            "cpu",
            encoder_factory=ApiEncoder,
        )
        return self.stack.enter_context(TestClient(app))

    def test_ready_text_and_image_share_one_model_and_assets_are_read_only(
        self,
    ) -> None:
        before = digest(self.fixture.output / "meta.db")
        client = self.client()
        self.assertEqual(client.get("/ready").status_code, 200)
        benchmark = client.get("/ready").json()["benchmark_identity"]
        self.assertEqual(
            benchmark["index_state_sha256"],
            digest(self.fixture.output / "index_state.json"),
        )
        self.assertEqual(benchmark["device"], "cpu")
        self.assertEqual(
            benchmark["session_id"],
            client.get("/ready").json()["benchmark_identity"]["session_id"],
        )
        result = client.get("/cctv/search", params={"query": "red traffic", "top_k": 2})
        self.assertEqual(result.status_code, 200)
        first = result.json()["results"][0]
        self.assertEqual(first["camera_id"], "N001")
        self.assertNotIn("source_video", first)
        self.assertEqual(client.get(first["image_url"]).status_code, 200)
        with BytesIO() as stream:
            Image.new("RGB", (16, 12), (255, 0, 0)).save(stream, format="PNG")
            response = client.post("/cctv/search/image", content=stream.getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["file"], "N001-V001/001.jpg")
        self.assertEqual(ApiEncoder.loaded, 1)
        self.assertEqual(digest(self.fixture.output / "meta.db"), before)
        self.stack.close()
        self.assertEqual(ApiEncoder.closed, 1)

    def test_video_supports_ranges_and_unregistered_assets_are_denied(self) -> None:
        client = self.client()
        response = client.get("/cctv/video/N001-V001", headers={"Range": "bytes=2-5"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")
        self.assertEqual(client.get("/cctv/video/unknown").status_code, 404)
        self.assertEqual(client.get("/cctv/image/123").status_code, 404)
        self.assertEqual(client.get("/static/../cctv.py").status_code, 404)

    def test_invalid_image_query_and_upload_limit(self) -> None:
        client = self.client()
        self.assertEqual(
            client.get("/cctv/search", params={"query": " ", "top_k": 2}).status_code,
            422,
        )
        self.assertEqual(
            client.get("/cctv/search", params={"query": "x", "top_k": 0}).status_code,
            422,
        )
        self.assertEqual(
            client.post("/cctv/search/image", content=b"not an image").status_code, 422
        )
        with patch("app.cctv.MAX_UPLOAD_BYTES", 8):
            self.assertEqual(
                client.post("/cctv/search/image", content=b"123456789").status_code, 413
            )

    def test_bad_index_is_unready_and_does_not_load_model(self) -> None:
        path = self.fixture.output / "faiss_index" / "frames.faiss"
        with path.open("ab") as handle:
            handle.write(b"changed")
        client = self.client()
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/ready").status_code, 503)
        self.assertEqual(
            client.get("/cctv/search", params={"query": "road"}).status_code, 503
        )
        self.assertEqual(ApiEncoder.loaded, 0)

    def test_ui_assets_and_missing_preview_fail_closed(self) -> None:
        (self.preview / "N001-V001.mp4").unlink()
        client = self.client()
        self.assertEqual(client.get("/ready").status_code, 503)
        self.assertEqual(ApiEncoder.loaded, 0)
        self.assertIn("Tìm đúng khoảnh khắc", client.get("/").text)
        self.assertEqual(client.get("/static/app.js").status_code, 200)
        self.assertEqual(client.get("/static/style.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
