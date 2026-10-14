"""Verify native dependencies and FP16 CUDA operations without datasets or models."""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Callable


def verify(require_cuda: bool) -> dict[str, object]:
    """Exercise actual image, vector, database and GPU operations, isolating failures."""
    checks: dict[str, dict[str, object]] = {}
    report: dict[str, object] = {"checks": checks, "retrieval_baseline": "NOT_TESTED_NO_DATA"}

    def check(name: str, operation: Callable[[], object]) -> None:
        try:
            checks[name] = {"passed": True, "details": operation()}
        except Exception as exc:
            checks[name] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}

    def image_and_vector() -> dict[str, object]:
        import cv2
        import faiss
        import numpy as np

        image = np.full((32, 32, 3), 127, dtype=np.uint8)
        success, encoded = cv2.imencode(".jpg", image)
        assert success, "JPEG encoding failed"
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        assert decoded is not None and decoded.shape == image.shape
        vectors = np.eye(4, dtype=np.float32)
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(4))
        index.add_with_ids(vectors, np.array([10, 20, 30, 40], dtype=np.int64))
        scores, ids = index.search(vectors[1:2], 1)
        assert ids[0, 0] == 20 and abs(float(scores[0, 0]) - 1.0) < 1e-6
        return {"opencv": cv2.__version__, "faiss": faiss.__version__, "numpy": np.__version__}

    def sqlite_fts() -> str:
        import sqlite3

        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE words USING fts5(text)")
            connection.execute("INSERT INTO words VALUES ('traffic congestion')")
            count = connection.execute("SELECT count(*) FROM words WHERE words MATCH 'traffic'").fetchone()[0]
            assert count == 1
        return sqlite3.sqlite_version

    def model_interfaces() -> list[str]:
        return ["CLIPModel"]
    def cuda_fp16() -> dict[str, object]:
        import torch
        from torchvision.ops import nms

        # The detector relies on torchvision operators, not just an import.
        boxes = torch.tensor([[0., 0., 10., 10.], [1., 1., 9., 9.]])
        assert nms(boxes, torch.tensor([0.9, 0.8]), 0.5).tolist() == [0]
        available = torch.cuda.is_available()
        if not available:
            if require_cuda:
                raise RuntimeError("CUDA is required but unavailable")
            return {"cuda_available": False, "fp16": "SKIPPED"}
        a = b = product = None
        try:
            torch.cuda.reset_peak_memory_stats()
            with torch.inference_mode():
                a = torch.ones((256, 256), device="cuda", dtype=torch.float32)
                b = torch.eye(256, device="cuda", dtype=torch.float32)
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    product = a @ b
                torch.cuda.synchronize()
                assert product.dtype == torch.float16 and torch.isfinite(product).all().item()
                assert torch.allclose(product.float(), a)
                gpu_boxes = boxes.cuda()
                gpu_scores = torch.tensor([0.9, 0.8], device="cuda")
                assert nms(gpu_boxes, gpu_scores, 0.5).cpu().tolist() == [0]
                del gpu_boxes, gpu_scores
            return {
                "torch": torch.__version__, "cuda_build": torch.version.cuda,
                "device": torch.cuda.get_device_name(0), "fp16": "PASSED",
                "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 2),
            }
        finally:
            del a, b, product
            gc.collect()
            torch.cuda.empty_cache()

    def imports() -> list[str]:
        names = ["fastapi", "uvicorn", "redis", "httpx", "PIL", "tqdm"]
        for name in names:
            importlib.import_module(name)
        return names

    def media_tools() -> dict[str, str]:
        found: dict[str, str] = {}
        for name in ("ffmpeg", "ffprobe"):
            executable = shutil.which(name)
            if executable is None:
                raise RuntimeError(f"Missing tool: {name}")
            found[name] = executable
        return found

    check("image_vector", image_and_vector)
    check("sqlite_fts5", sqlite_fts)
    check("model_interfaces_without_weights", model_interfaces)
    check("cuda_fp16_and_nms", cuda_fp16)
    check("runtime_imports", imports)
    check("media_tools", media_tools)
    report["passed"] = all(item["passed"] for item in checks.values())
    return report


def main() -> int:
    """Save a diagnostic report in the dedicated project output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    try:
        report = verify(args.require_cuda)
        content = json.dumps(report, ensure_ascii=False, indent=2)
        destination = Path(__file__).resolve().parents[1] / "outputs" / "preflight"
        destination.mkdir(parents=True, exist_ok=True)
        temporary = destination / "runtime-checks.json.tmp"
        temporary.write_text(content + "\n", encoding="utf-8")
        temporary.replace(destination / "runtime-checks.json")
        print(content)
        return 0 if report["passed"] else 1
    except OSError as exc:
        print(f"Cannot write runtime report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())