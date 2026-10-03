"""Exercise real spawned CLIP inference with an isolated, tiny CPU checkpoint."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.cctv.clip import ClipEncoder
from pipeline.cctv.clip_process import ProcessClipEncoder


def make_tiny_clip(path: Path) -> None:
    """Write model and processor files locally; never fetch a pretrained asset."""
    import torch
    from transformers import (
        CLIPConfig,
        CLIPImageProcessor,
        CLIPModel,
        CLIPProcessor,
        CLIPTextConfig,
        CLIPTokenizer,
        CLIPVisionConfig,
    )

    path.mkdir()
    tokens = ["<|startoftext|>", "<|endoftext|>"]
    letters = "abcdefghijklmnopqrstuvwxyz"
    tokens.extend(letters)
    tokens.extend(f"{letter}</w>" for letter in letters)
    vocab = {token: index for index, token in enumerate(tokens)}
    vocab_file = path / "vocab.json"
    merges_file = path / "merges.txt"
    vocab_file.write_text(json.dumps(vocab), encoding="utf-8")
    merges_file.write_text("#version: 0.2\n", encoding="utf-8")
    tokenizer = CLIPTokenizer(
        vocab_file=str(vocab_file),
        merges_file=str(merges_file),
        model_max_length=32,
    )
    processor = CLIPProcessor(
        tokenizer=tokenizer,
        image_processor=CLIPImageProcessor(
            size={"shortest_edge": 16},
            crop_size={"height": 16, "width": 16},
        ),
    )
    processor.save_pretrained(path)
    config = CLIPConfig(
        text_config=CLIPTextConfig(
            vocab_size=len(vocab),
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=1,
            num_attention_heads=4,
            max_position_embeddings=32,
            bos_token_id=vocab["<|startoftext|>"],
            eos_token_id=vocab["<|endoftext|>"],
            pad_token_id=vocab["<|endoftext|>"],
        ).to_dict(),
        vision_config=CLIPVisionConfig(
            hidden_size=32,
            intermediate_size=64,
            num_hidden_layers=1,
            num_attention_heads=4,
            image_size=16,
            patch_size=4,
        ).to_dict(),
        projection_dim=16,
    )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(173)
        model = CLIPModel(config).eval()
        model.save_pretrained(path)


class CCTVClipProcessTests(unittest.TestCase):
    """Compare actual model projections, request ownership, and worker lifetime."""

    @classmethod
    def setUpClass(cls) -> None:
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.checkpoint = cls.root / "tiny-clip"
        make_tiny_clip(cls.checkpoint)
        cls.prompts = ("red car", "blue bus", "green truck", "a white bicycle")
        cls.image_batches = (
            [Image.new("RGB", (23, 17), (230, 30, 10))],
            [
                Image.new("RGB", (17, 23), (10, 40, 220)),
                Image.fromarray(
                    np.arange(18 * 24 * 3, dtype=np.uint8).reshape(18, 24, 3)
                ),
                Image.new("RGB", (32, 16), (20, 210, 60)),
            ],
        )
        direct = ClipEncoder(cls.checkpoint, device="cpu")
        try:
            cls.expected_text = [direct.text(prompt) for prompt in cls.prompts]
            cls.expected_images = [direct.images(batch) for batch in cls.image_batches]
            cls.dimension = direct.dim
        finally:
            direct.close()

    def encoder(self) -> ProcessClipEncoder:
        encoder = ProcessClipEncoder(self.checkpoint, device="cpu")
        self.addCleanup(encoder.close)
        return encoder

    def assert_vectors(self, actual: np.ndarray, expected: np.ndarray) -> None:
        self.assertEqual(actual.dtype, np.dtype(np.float32))
        self.assertEqual(actual.shape, expected.shape)
        self.assertEqual(actual.shape[1], self.dimension)
        self.assertTrue(np.isfinite(actual).all())
        np.testing.assert_allclose(
            np.linalg.norm(actual, axis=1), 1.0, rtol=1e-5, atol=1e-6
        )
        np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-6)

    def test_text_and_image_projections_match_direct_encoder(self) -> None:
        encoder = self.encoder()
        self.assertEqual(encoder.dim, self.dimension)
        for prompt, expected in zip(self.prompts, self.expected_text):
            with self.subTest(prompt=prompt):
                self.assert_vectors(encoder.text(prompt), expected)
        for batch, expected in zip(self.image_batches, self.expected_images):
            with self.subTest(images=len(batch)):
                self.assert_vectors(encoder.images(batch), expected)

    def test_close_waits_for_owned_worker_and_rejects_requests(self) -> None:
        encoder = self.encoder()
        worker = encoder._process
        self.assertIsNotNone(worker.pid)
        self.assertNotEqual(worker.pid, multiprocessing.current_process().pid)
        self.assertTrue(worker.is_alive())
        self.assert_vectors(encoder.text(self.prompts[0]), self.expected_text[0])
        encoder.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(worker.exitcode, 0)
        encoder.close()
        self.assertFalse(worker.is_alive())
        for request in (
            lambda: encoder.text(self.prompts[0]),
            lambda: encoder.images(self.image_batches[0]),
            encoder.memory,
        ):
            with self.subTest(request=request), self.assertRaises(RuntimeError):
                request()

    def test_concurrent_requests_keep_their_own_vectors(self) -> None:
        encoder = self.encoder()
        requests = [
            ("text", prompt, expected)
            for prompt, expected in zip(self.prompts, self.expected_text)
        ] + [
            ("images", batch, expected)
            for batch, expected in zip(self.image_batches, self.expected_images)
        ]
        barrier = threading.Barrier(len(requests), timeout=30)

        def query(request: tuple) -> np.ndarray:
            kind, value, _ = request
            barrier.wait()
            return getattr(encoder, kind)(value)

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            results = list(pool.map(query, requests))
        for actual, (_, _, expected) in zip(results, requests):
            self.assert_vectors(actual, expected)

    def test_python_request_error_does_not_break_next_query(self) -> None:
        encoder = self.encoder()
        with self.assertRaises(RuntimeError):
            encoder.images([object()])
        self.assert_vectors(encoder.text(self.prompts[1]), self.expected_text[1])
        self.assert_vectors(encoder.images(self.image_batches[1]), self.expected_images[1])

    def test_unexpected_worker_exit_fails_and_can_be_closed(self) -> None:
        encoder = self.encoder()
        worker = encoder._process
        worker.terminate()
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive())
        with self.assertRaises(RuntimeError):
            encoder.text(self.prompts[0])
        encoder.close()
        encoder.close()
        self.assertFalse(worker.is_alive())
        self.assertIsNotNone(worker.exitcode)

    def test_invalid_checkpoint_leaves_no_owned_child(self) -> None:
        invalid = self.root / "invalid-clip"
        invalid.mkdir()
        previous = set(multiprocessing.active_children())
        try:
            with self.assertRaises(RuntimeError):
                ProcessClipEncoder(invalid, device="cpu")
            self.assertEqual(set(multiprocessing.active_children()) - previous, set())
        finally:
            for child in set(multiprocessing.active_children()) - previous:
                child.terminate()
                child.join(timeout=30)


if __name__ == "__main__":
    unittest.main()
