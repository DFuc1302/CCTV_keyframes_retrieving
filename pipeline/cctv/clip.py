"""One local CLIP instance for bounded Windows image/text inference."""

from __future__ import annotations

import gc
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


class ClipEncoder:
    """Load a pinned local model once; never download weights during inference."""

    def __init__(self, path: Path, device: str = "cuda") -> None:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda")
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; explicitly choose --device cpu")
        self.device = device
        self.dtype = torch.float16 if device == "cuda" else torch.float32
        torch.set_num_threads(2)
        print(f"Loading local CLIP on {device} ({self.dtype})...", flush=True)
        self.processor = CLIPProcessor.from_pretrained(
            path, local_files_only=True, use_fast=False
        )
        self.model = (
            CLIPModel.from_pretrained(
                path,
                local_files_only=True,
                dtype=self.dtype,
            )
            .to(device)
            .eval()
        )
        self.dim = int(self.model.config.projection_dim)
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        print(f"CLIP ready: {self.dim} dimensions", flush=True)

    def _encode(self, inputs: dict[str, Any], kind: str) -> np.ndarray:
        """Project in FP16 on CUDA, normalize in FP32 for cosine inner product."""
        import torch

        moved = {
            key: value.to(device=self.device, dtype=self.dtype)
            if key == "pixel_values"
            else value.to(self.device)
            for key, value in inputs.items()
        }
        context = (
            torch.amp.autocast("cuda", dtype=torch.float16)
            if self.device == "cuda"
            else nullcontext()
        )
        with torch.inference_mode(), context:
            if kind == "image":
                pooled = self.model.vision_model(**moved).pooler_output
                features = self.model.visual_projection(pooled)
            else:
                pooled = self.model.text_model(**moved).pooler_output
                features = self.model.text_projection(pooled)
            features = torch.nn.functional.normalize(features.float(), dim=-1)
        result = features.cpu().numpy().astype(np.float32)
        del moved, features, pooled
        return result

    def images(self, images: list[Image.Image]) -> np.ndarray:
        """Encode already-decoded RGB PIL images in one bounded batch."""
        return self._encode(self.processor(images=images, return_tensors="pt"), "image")

    def text(self, query: str) -> np.ndarray:
        """Encode English text using the model's matching text projection."""
        return self._encode(
            self.processor(text=[query], return_tensors="pt", truncation=True), "text"
        )

    def memory(self) -> dict[str, float]:
        """Report observed CUDA peak allocations, not an estimated memory budget."""
        import torch

        if self.device == "cpu":
            return {}
        return {
            "peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "peak_reserved_mib": torch.cuda.max_memory_reserved() / 1024**2,
        }

    def close(self) -> None:
        """Release the only model before any later pipeline stage loads another."""
        import torch

        self.model = None
        self.processor = None
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()
