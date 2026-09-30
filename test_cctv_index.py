"""Verify real FAISS/SQLite publication and interruption with a tiny test encoder."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from pipeline.cctv.index import build_index
from pipeline.cctv.ingest import digest, images_digest
from pipeline.cctv.search import CctvSearch


class TinyEncoder:
    """Deterministic RGB vectors; tests storage semantics, not CLIP quality."""

    dim = 3

    def __init__(self, path: Path, device: str) -> None:
        self.calls = 0

    def images(self, images: list) -> np.ndarray:
        self.calls += 1
        return np.stack(
            [np.asarray(image, dtype=np.float32).mean(axis=(0, 1)) for image in images]
        )

    def memory(self) -> dict:
        return {}

    def close(self) -> None:
        pass


class CCTVIndexTests(unittest.TestCase):
    """All inputs are synthetic and temporary; no project data/model is loaded."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        (self.raw / "source.mov").write_bytes(b"synthetic source reference")
        self.source = self.root / "processed"
        self.folder = self.source / "keyframes" / "N001-V001"
        self.folder.mkdir(parents=True)
        maps = self.source / "map-keyframes"
        maps.mkdir()
        for n, color in enumerate(
            ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)), 1
        ):
            Image.new("RGB", (16, 12), color).save(self.folder / f"{n:03d}.jpg")
        mapping = maps / "N001-V001.csv"
        mapping.write_text(
            "n,pts_time,fps,frame_idx\n1,0,25,0\n2,0.52,25,13\n3,1,25,25\n4,1.52,25,38\n",
            encoding="utf-8",
        )
        checkpoint = {
            "pipeline": "cctv-ingest-v1",
            "videos": {
                "N001-V001": {
                    "status": "complete",
                    "artifacts": {
                        "map_sha256": digest(mapping),
                        "images_sha256": images_digest(self.folder),
                    },
                }
            },
        }
        dataset = {
            "schema_version": 1,
            "raw_root": str(self.raw),
            "run_counts": {},
            "videos": [
                {
                    "video_id": "N001-V001",
                    "camera_id": "N001",
                    "path": "source.mov",
                    "status": "complete",
                }
            ],
        }
        (self.source / "ingest_state.json").write_text(
            json.dumps(checkpoint), encoding="utf-8"
        )
        (self.source / "dataset_manifest.json").write_text(
            json.dumps(dataset), encoding="utf-8"
        )
        self.output, self.model = self.root / "index", self.root / "model"
        self.recipe = {
            "device": "cpu",
            "clip_model": "test-only",
            "revision": "fixture",
        }

    def build(self, factory=TinyEncoder, output: Path | None = None) -> dict:
        return build_index(
            self.source,
            output or self.output,
            self.recipe,
            self.model,
            batch_size=2,
            encoder_factory=factory,
        )

    def test_round_trip_preserves_camera_timestamp_and_inputs(self) -> None:
        before = {p: digest(p) for p in self.source.rglob("*") if p.is_file()}
        result = self.build()
        self.assertEqual(result["count"], 4)
        search = CctvSearch(self.output)
        matches = search.query(search.stored_vector("N001-V001/002.jpg"), 2)
        self.assertEqual(matches[0]["file"], "N001-V001/002.jpg")
        self.assertEqual(matches[0]["camera_id"], "N001")
        self.assertEqual(matches[0]["timestamp_sec"], 0.52)
        self.assertEqual(matches[0]["frame_idx"], 13)
        self.assertAlmostEqual(matches[0]["score"], 1, places=5)
        self.assertEqual(before, {p: digest(p) for p in before})
        with closing(
            sqlite3.connect((self.output / "meta.db").as_uri() + "?mode=ro", uri=True)
        ) as conn:
            self.assertEqual(
                conn.execute("SELECT ocr_status FROM frames LIMIT 1").fetchone()[0],
                "skipped",
            )

    def test_resume_never_loads_encoder_or_rewrites_artifacts(self) -> None:
        self.build()
        state = json.loads((self.output / "index_state.json").read_text())
        before = {
            rel: ((self.output / rel).stat().st_mtime_ns, digest(self.output / rel))
            for rel in state["artifacts"]
        }

        def forbidden(*args):
            raise AssertionError("Resume should not load any model")

        result = self.build(forbidden)
        self.assertEqual(
            result, {"done": 0, "skipped": 4, "error": 0, "model_loaded": False}
        )
        self.assertEqual(
            before,
            {
                rel: ((self.output / rel).stat().st_mtime_ns, digest(self.output / rel))
                for rel in before
            },
        )

    def test_interrupt_commits_only_finished_batch_then_resumes(self) -> None:
        class InterruptEncoder(TinyEncoder):
            def images(self, images):
                if self.calls == 1:
                    raise KeyboardInterrupt
                return super().images(images)

        with self.assertRaises(KeyboardInterrupt):
            self.build(InterruptEncoder)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            CctvSearch(self.output)
        result = self.build()
        self.assertEqual((result["done"], result["skipped"]), (2, 2))
        self.assertEqual(CctvSearch(self.output).index.ntotal, 4)

    def test_changed_input_and_recipe_do_not_overwrite_index(self) -> None:
        self.build()
        artifact = self.output / "faiss_index" / "frames.faiss"
        checksum = digest(artifact)
        self.recipe["revision"] = "changed"
        with self.assertRaisesRegex(ValueError, "NEW"):
            self.build()
        self.recipe["revision"] = "fixture"
        with (self.folder / "001.jpg").open("ab") as handle:
            handle.write(b"modified")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.build()
        self.assertEqual(digest(artifact), checksum)

    def test_corrupted_vector_or_published_index_is_rejected(self) -> None:
        self.build()
        with closing(sqlite3.connect(self.output / "meta.db")) as conn:
            with conn:
                conn.execute(
                    "UPDATE cctv_vectors SET vector=? WHERE id=(SELECT id FROM cctv_vectors LIMIT 1)",
                    (bytes(12),),
                )
        with self.assertRaisesRegex(ValueError, "checkpoint vector"):
            self.build()
        with self.assertRaisesRegex(ValueError, "checksum"):
            CctvSearch(self.output)

    def test_frame_failure_isolated_and_retry_only_encodes_missing_frame(self) -> None:
        original = Image.open

        def fail_one(path, *args, **kwargs):
            if Path(path).name == "003.jpg":
                raise OSError("synthetic decode failure")
            return original(path, *args, **kwargs)

        with patch("pipeline.cctv.index.Image.open", side_effect=fail_one):
            result = self.build()
        self.assertEqual((result["done"], result["error"]), (3, 1))
        self.assertFalse((self.output / "index_manifest.json").exists())
        retry = self.build()
        self.assertEqual((retry["done"], retry["skipped"]), (1, 3))

    def test_oom_splits_batch_and_produces_valid_index(self) -> None:
        import torch

        class OomEncoder(TinyEncoder):
            def images(self, images):
                if len(images) > 1:
                    raise torch.cuda.OutOfMemoryError("synthetic OOM")
                return super().images(images)

        self.assertEqual(self.build(OomEncoder)["count"], 4)

    def test_interrupted_publication_rebuilds_without_model(self) -> None:
        from pipeline.cctv.ingest import save_json

        def interrupt(path, payload):
            if path.name == "index_manifest.json":
                raise KeyboardInterrupt
            return save_json(path, payload)

        with patch("pipeline.cctv.index.save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.build()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            CctvSearch(self.output)

        def forbidden(*args):
            raise AssertionError("Committed vectors should not be encoded again")

        result = self.build(forbidden)
        self.assertEqual((result["done"], result["skipped"]), (0, 4))
        self.assertEqual(CctvSearch(self.output).index.ntotal, 4)

    def test_unowned_output_and_raw_overlap_preserve_foreign_files(self) -> None:
        self.output.mkdir()
        foreign = self.output / "note.txt"
        foreign.write_text("keep")
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.build()
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.build(output=self.raw / "generated")
        self.assertEqual(foreign.read_text(), "keep")
        self.assertFalse((self.raw / "generated").exists())


if __name__ == "__main__":
    unittest.main()
