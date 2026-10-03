"""Read-only text/image retrieval from the CCTV index, with camera/time locators."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import faiss
import numpy as np
from PIL import Image

from app.query_hints import expand_bilingual_query

from pipeline.cctv.clip import ClipEncoder
from pipeline.cctv.index import PIPELINE, check_vector, model_recipe
from pipeline.cctv.ingest import digest


class CctvSearch:
    """Validate a complete publication and query SQLite strictly read-only."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        state = json.loads((self.root / "index_state.json").read_text(encoding="utf-8"))
        if (
            state.get("signature", {}).get("pipeline") != PIPELINE
            or state.get("status") != "complete"
        ):
            raise ValueError("Index is incomplete; resume indexing before searching")
        required = {
            "meta.db",
            "faiss_index/frames.faiss",
            "faiss_index/frames_meta.json",
            "index_manifest.json",
        }
        if set(state.get("artifacts", {})) != required:
            raise ValueError("Invalid published artifact inventory")
        for rel, checksum in state["artifacts"].items():
            if digest(self.root / rel) != checksum:
                raise ValueError(f"Artifact checksum mismatch: {rel}")
        self.manifest = json.loads(
            (self.root / "index_manifest.json").read_text(encoding="utf-8")
        )
        self.index = faiss.read_index(str(self.root / "faiss_index" / "frames.faiss"))
        if (
            self.index.ntotal != self.manifest["count"]
            or self.index.d != self.manifest["dim"]
        ):
            raise ValueError("FAISS count/dimension mismatch")

    def connect(self) -> sqlite3.Connection:
        """Percent-encode Windows paths, including spaces, for SQLite URI mode=ro."""
        return sqlite3.connect((self.root / "meta.db").as_uri() + "?mode=ro", uri=True)

    def stored_vector(self, relative_file: str) -> np.ndarray:
        """Load one committed vector for index/locator round-trip verification."""
        with closing(self.connect()) as conn:
            try:
                row = conn.execute(
                    "SELECT v.vector,v.dim,v.sha256 FROM cctv_vectors v JOIN frames f ON v.id=f.id WHERE f.file=?",
                    (relative_file,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Frame not indexed: {relative_file}")
                return check_vector(*row).reshape(1, -1)
            finally:
                conn.close()

    def query(self, vector: np.ndarray, top_k: int = 5) -> list[dict]:
        """Return cosine similarities with original camera/video/decoded timestamp."""
        vector = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if (
            vector.shape[1] != self.index.d
            or not np.isfinite(vector).all()
            or not 1 <= top_k <= 100
        ):
            raise ValueError("Invalid query vector or top_k (1-100)")
        norm = np.linalg.norm(vector)
        if norm == 0:
            raise ValueError("Query vector is zero")
        scores, ids = self.index.search(vector / norm, min(top_k, self.index.ntotal))
        results = []
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            try:
                for score, integer_id in zip(scores[0], ids[0]):
                    row = conn.execute(
                        "SELECT f.id,f.file,c.camera_id,c.video_id,c.timestamp_sec,c.frame_idx,c.source_video,c.start_time FROM frames f JOIN cctv_frames c ON c.id=f.id WHERE f.faiss_id=?",
                        (int(integer_id),),
                    ).fetchone()
                    if row is None:
                        raise ValueError("FAISS ID has no metadata")
                    results.append(
                        {
                            **dict(row),
                            "score": float(score),
                            "image_path": str(
                                Path(self.manifest["keyframes_root"]) / row["file"]
                            ),
                        }
                    )
            finally:
                conn.close()
        return results


def main() -> int:
    """Search without modifying the database, FAISS, raw videos or keyframes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--top-k", type=int, default=5)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--image", type=Path)
    mode.add_argument("--stored-frame")
    args = parser.parse_args()
    encoder = None
    try:
        search = CctvSearch(args.index_dir)
        if args.stored_frame:
            vector = search.stored_vector(args.stored_frame)
        else:
            if args.model_config is None:
                raise ValueError("--model-config required for text/image inference")
            recipe, model = model_recipe(args.model_config, args.device)
            for field in ("clip_model", "revision", "model_sha256"):
                if recipe[field] != search.manifest["recipe"][field]:
                    raise ValueError("Query model must match the indexed model")
            encoder = ClipEncoder(model, args.device)
            if args.query:
                prompt, _ = expand_bilingual_query(args.query)
                vector = encoder.text(prompt)
            else:
                with Image.open(args.image) as image:
                    rgb = image.convert("RGB")
                    try:
                        vector = encoder.images([rgb])
                    finally:
                        rgb.close()
        print(
            json.dumps(search.query(vector, args.top_k), ensure_ascii=False, indent=2)
        )
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
        parser.exit(1, f"Search error: {exc}\n")
    finally:
        if encoder is not None:
            encoder.close()


if __name__ == "__main__":
    raise SystemExit(main())
