"""Resumable CLIP/FAISS/SQLite indexing of completed read-only CCTV extraction."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Callable

import faiss
import numpy as np
from PIL import Image
from tqdm import tqdm

from pipeline.cctv.clip import ClipEncoder
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, relative_video
from pipeline.preprocess.merge import _create_output_db, frame_id, stable_faiss_id

LOG = logging.getLogger("cctv.index")
PIPELINE = "cctv-index-v1"


def collect_frames(source: Path) -> tuple[list[dict], str, Path]:
    """Bind exact image bytes/maps to ingestion checkpoint and camera identities."""
    dataset_path = source / "dataset_manifest.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    checkpoint = json.loads((source / "ingest_state.json").read_text(encoding="utf-8"))
    raw = Path(dataset["raw_root"]).resolve()
    if checkpoint.get("pipeline") != "cctv-ingest-v1" or not dataset.get("videos"):
        raise ValueError("Input must be a completed CCTV ingestion dataset")
    records: list[dict] = []
    # Run counters vary on resume; bind actual metadata, not transient ingestion counters.
    signature = hashlib.sha256(
        json.dumps(
            {k: v for k, v in dataset.items() if k != "run_counts"}, sort_keys=True
        ).encode()
    )
    ids: set[int] = set()
    for video in tqdm(dataset["videos"], desc="Inventory", unit="video"):
        vid = video["video_id"]
        identity(vid, "video_id")
        identity(video["camera_id"], "camera_id")
        if video["status"] != "complete" or vid in checkpoint.get("errors", {}):
            raise ValueError(f"Video {vid} has not completed ingestion")
        owned = checkpoint["videos"][vid]
        mapping = source / "map-keyframes" / f"{vid}.csv"
        if digest(mapping) != owned["artifacts"]["map_sha256"]:
            raise ValueError(f"Map changed: {mapping}")
        with mapping.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        image_hash = hashlib.sha256()
        folder = source / "keyframes" / vid
        expected = {f"{n:03d}.jpg" for n in range(1, len(rows) + 1)}
        if {p.name for p in folder.iterdir()} != expected:
            raise ValueError(f"Unexpected frame coverage for {vid}")
        hashes = {}
        for image in sorted(folder.iterdir()):
            reject_link(image)
            hashes[image.name] = digest(image)
            image_hash.update(f"{image.name}:{hashes[image.name]}\n".encode())
        if image_hash.hexdigest() != owned["artifacts"]["images_sha256"]:
            raise ValueError(f"Frames changed after ingestion: {vid}")
        signature.update(mapping.read_bytes())
        signature.update(image_hash.digest())
        last_time, last_idx = -1.0, -1
        for number, row in enumerate(rows, 1):
            stamp, original = float(row["pts_time"]), int(row["frame_idx"])
            if (
                int(row["n"]) != number
                or not math.isfinite(stamp)
                or stamp < 0
                or stamp < last_time
                or original <= last_idx
            ):
                raise ValueError(f"Invalid frame map for {vid}")
            last_time, last_idx = stamp, original
            rel = f"{vid}/{number:03d}.jpg"
            fid = frame_id(rel)
            integer_id = stable_faiss_id(fid)
            if integer_id in ids:
                raise ValueError("Duplicate or colliding frame ID")
            ids.add(integer_id)
            records.append(
                {
                    "id": fid,
                    "faiss_id": integer_id,
                    "file": rel,
                    "path": source / "keyframes" / rel,
                    "video_id": vid,
                    "camera_id": video["camera_id"],
                    "timestamp_sec": stamp,
                    "frame_idx": original,
                    "start_time": video.get("start_time"),
                    "source_video": str(raw / relative_video(video["path"])),
                    "sha256": hashes[f"{number:03d}.jpg"],
                }
            )
    if not records:
        raise ValueError("Dataset has no frames")
    return records, signature.hexdigest(), raw


def model_recipe(config_path: Path, device: str) -> tuple[dict, Path]:
    """Hash actual local model files as well as the pinned upstream revision."""
    import torch

    config = json.loads(config_path.read_text(encoding="utf-8"))
    local = Path(config["path"]).resolve()
    if not local.is_dir() or not config.get("model") or not config.get("revision"):
        raise ValueError(
            "Model cache descriptor requires model, revision and local path"
        )
    weights = local / "model.safetensors"
    if not weights.is_file():
        weights = local / "pytorch_model.bin"
    if not weights.is_file():
        raise ValueError("Local CLIP weights missing")
    bound = hashlib.sha256()
    for path in sorted([weights, *local.glob("*.json"), local / "merges.txt"]):
        bound.update(f"{path.name}:{digest(path)}\n".encode())
    return {
        "clip_model": config["model"],
        "revision": config["revision"],
        "model_sha256": bound.hexdigest(),
        "device": device,
        "precision": "fp16" if device == "cuda" else "fp32",
        "torch": torch.__version__,
        "metric": "cosine_ip",
    }, local


def open_checkpoint(path: Path) -> sqlite3.Connection:
    """Extend the legacy frames schema with CCTV locator and embedding checkpoint."""
    conn = sqlite3.connect(path) if path.exists() else _create_output_db(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cctv_frames (
            id TEXT PRIMARY KEY REFERENCES frames(id), camera_id TEXT NOT NULL,
            video_id TEXT NOT NULL, timestamp_sec REAL NOT NULL, frame_idx INTEGER NOT NULL,
            source_video TEXT NOT NULL, start_time TEXT, sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cctv_vectors (
            id TEXT PRIMARY KEY REFERENCES frames(id), dim INTEGER NOT NULL,
            vector BLOB NOT NULL, sha256 TEXT NOT NULL);
    """)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def check_vector(raw: bytes, dim: int, checksum: str) -> np.ndarray:
    """Reject corrupt or unnormalized checkpoints before resume/export."""
    vector = np.frombuffer(raw, dtype="<f4")
    if (
        len(vector) != dim
        or not np.isfinite(vector).all()
        or abs(float(np.linalg.norm(vector)) - 1) > 1e-4
    ):
        raise ValueError("Invalid checkpoint vector")
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("Checkpoint vector checksum mismatch")
    return vector


def encode_records(
    records: list[dict], encoder: ClipEncoder
) -> tuple[list[dict], np.ndarray, list[dict]]:
    """Isolate unreadable frames and reduce batches recursively on CUDA OOM."""
    import torch

    good, images, errors = [], [], []
    try:
        for record in records:
            try:
                if digest(record["path"]) != record["sha256"]:
                    raise ValueError("Frame changed during indexing")
                with Image.open(record["path"]) as image:
                    rgb = image.convert("RGB")
                    record["width"], record["height"] = rgb.size
                images.append(rgb)
                good.append(record)
            except (OSError, ValueError) as exc:
                LOG.exception("frame_error | %s", record["file"])
                errors.append({"file": record["file"], "error": str(exc)})
        if not good:
            return [], np.empty((0, encoder.dim), dtype=np.float32), errors
        try:
            vectors = encoder.images(images)
        except torch.cuda.OutOfMemoryError as exc:
            exc.__traceback__ = None
            for image in images:
                image.close()
            images.clear()
            import gc

            gc.collect()
            torch.cuda.empty_cache()
            if len(good) == 1:
                raise
            LOG.warning("CUDA OOM; splitting batch of %d", len(good))
            middle = len(good) // 2
            left = encode_records(good[:middle], encoder)
            right = encode_records(good[middle:], encoder)
            return (
                left[0] + right[0],
                np.concatenate([left[1], right[1]]),
                errors + left[2] + right[2],
            )
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.shape != (len(good), encoder.dim) or not np.isfinite(vectors).all():
            raise ValueError("Encoder returned invalid vectors")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise ValueError("Encoder returned zero vector")
        return good, vectors / norms, errors
    finally:
        for image in images:
            image.close()


def export_index(conn: sqlite3.Connection, output: Path, recipe: dict) -> dict:
    """Rebuild a consistent legacy FAISS IDMap2/sidecar from committed vectors."""
    dim = conn.execute("SELECT dim FROM cctv_vectors LIMIT 1").fetchone()[0]
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
    metadata = {}
    cursor = conn.execute(
        "SELECT f.id,f.file,f.mtime,f.faiss_id,v.dim,v.vector,v.sha256 FROM frames f JOIN cctv_vectors v ON f.id=v.id ORDER BY f.file"
    )
    while rows := cursor.fetchmany(256):
        vectors = np.stack([check_vector(row[5], row[4], row[6]) for row in rows])
        index.add_with_ids(
            vectors, np.asarray([row[3] for row in rows], dtype=np.int64)
        )
        metadata.update(
            {
                row[0]: {"file": row[1], "mtime": row[2], "faiss_id": row[3]}
                for row in rows
            }
        )
    directory = output / "faiss_index"
    directory.mkdir(exist_ok=True)
    temporary = directory / "frames.faiss.tmp"
    reject_link(temporary)
    faiss.write_index(index, str(temporary))
    # Verify serialization before publishing either member.
    restored = faiss.read_index(str(temporary))
    if restored.ntotal != len(metadata) or restored.d != dim:
        raise ValueError("FAISS serialization mismatch")
    temporary.replace(directory / "frames.faiss")
    save_json(
        directory / "frames_meta.json",
        {
            "format_version": 2,
            "dim": dim,
            "index_type": "flat",
            "clip_model": recipe["clip_model"],
            "metric": "cosine_ip",
            "fid_to_meta": metadata,
        },
    )
    return {"dim": dim, "count": int(index.ntotal)}


def build_index(
    source: Path,
    output: Path,
    recipe: dict,
    local_model: Path,
    *,
    batch_size: int = 4,
    encoder_factory: Callable = ClipEncoder,
) -> dict:
    """Checkpoint each successful batch and publish only a complete index."""
    if not 1 <= batch_size <= 16:
        raise ValueError("batch_size must be between 1 and 16 for the 8GB target")
    source, output = source.resolve(), output.absolute()
    reject_link(output)
    records, input_hash, raw = collect_frames(source)
    for protected in (source, raw, local_model.resolve()):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError(
                "Index output must not overlap raw, extraction or model directories"
            )
    signature = {
        "pipeline": PIPELINE,
        "source": str(source),
        "input_sha256": input_hash,
        "recipe": recipe,
    }
    # No adoption of existing user files when a checkpoint does not establish ownership.
    state_path = output / "index_state.json"
    if output.exists() and not state_path.is_file() and any(output.iterdir()):
        raise ValueError("Use an empty output directory; unowned files are preserved")
    output.mkdir(parents=True, exist_ok=True)
    for path in (
        state_path,
        output / ".ingest.lock",
        output / "meta.db",
        output / "meta.db-journal",
        output / "logs",
        output / "faiss_index",
        output / "index_manifest.json",
        output / "faiss_index" / "frames.faiss",
        output / "faiss_index" / "frames_meta.json",
    ):
        reject_link(path)
    with output_lease(output):
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {"signature": signature}
        )
        if state.get("signature") != signature:
            raise ValueError(
                "Input/model/recipe changed; use a NEW index output directory"
            )
        save_json(state_path, state)
        (output / "logs").mkdir(exist_ok=True)
        log_path = output / "logs" / "index.log"
        reject_link(log_path)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        conn, encoder = None, None
        try:
            conn = open_checkpoint(output / "meta.db")
            complete = {}
            for fid, dim, blob, checksum in conn.execute(
                "SELECT id,dim,vector,sha256 FROM cctv_vectors"
            ):
                check_vector(blob, dim, checksum)
                complete[fid] = dim
            expected_ids = {record["id"] for record in records}
            if not set(complete).issubset(expected_ids):
                raise ValueError("Checkpoint includes unexpected frame IDs")
            if conn.execute("SELECT count(*) FROM frames").fetchone()[0] != len(
                complete
            ) or conn.execute("SELECT count(*) FROM cctv_frames").fetchone()[0] != len(
                complete
            ):
                raise ValueError("Checkpoint metadata/vector coverage mismatch")
            if len(set(complete.values())) > 1:
                raise ValueError("Checkpoint mixes vector dimensions")
            by_id = {record["id"]: record for record in records}
            rows = conn.execute(
                "SELECT f.id,f.file,f.faiss_id,c.camera_id,c.video_id,c.timestamp_sec,c.frame_idx,c.source_video,c.start_time,c.sha256 FROM frames f JOIN cctv_frames c ON c.id=f.id"
            ).fetchall()
            if len(rows) != len(complete):
                raise ValueError("Checkpoint metadata IDs are inconsistent")
            fields = (
                "id",
                "file",
                "faiss_id",
                "camera_id",
                "video_id",
                "timestamp_sec",
                "frame_idx",
                "source_video",
                "start_time",
                "sha256",
            )
            for row in rows:
                if row[0] not in by_id or tuple(row) != tuple(
                    by_id[row[0]][key] for key in fields
                ):
                    raise ValueError("Checkpoint locator metadata differs from source")
            pending = [r for r in records if r["id"] not in complete]
            if not pending and state.get("status") == "complete":
                for rel, checksum in state["artifacts"].items():
                    if digest(output / rel) != checksum:
                        raise ValueError(f"Published artifact changed: {rel}")
                return {
                    "done": 0,
                    "skipped": len(complete),
                    "error": 0,
                    "model_loaded": False,
                }
            state["status"] = "processing"
            save_json(state_path, state)
            errors = []
            memory = {}
            if pending:
                encoder = encoder_factory(local_model, recipe["device"])
                if complete and next(iter(complete.values())) != encoder.dim:
                    raise ValueError("Encoder dimension differs from checkpoint")
                for offset in tqdm(
                    range(0, len(pending), batch_size), desc="CLIP", unit="batch"
                ):
                    good, vectors, failed = encode_records(
                        pending[offset : offset + batch_size], encoder
                    )
                    errors.extend(failed)
                    with conn:
                        for record, vector in zip(good, vectors):
                            blob = np.asarray(vector, dtype="<f4").tobytes()
                            conn.execute(
                                "INSERT INTO frames(id,file,mtime,width,height,faiss_id,ocr_status) VALUES(?,?,?,?,?,?,?)",
                                (
                                    record["id"],
                                    record["file"],
                                    record["path"].stat().st_mtime,
                                    record["width"],
                                    record["height"],
                                    record["faiss_id"],
                                    "skipped",
                                ),
                            )
                            conn.execute(
                                "INSERT INTO cctv_frames VALUES(?,?,?,?,?,?,?,?)",
                                tuple(
                                    record[k]
                                    for k in (
                                        "id",
                                        "camera_id",
                                        "video_id",
                                        "timestamp_sec",
                                        "frame_idx",
                                        "source_video",
                                        "start_time",
                                        "sha256",
                                    )
                                ),
                            )
                            conn.execute(
                                "INSERT INTO cctv_vectors VALUES(?,?,?,?)",
                                (
                                    record["id"],
                                    encoder.dim,
                                    blob,
                                    hashlib.sha256(blob).hexdigest(),
                                ),
                            )
                    if offset % (batch_size * 50) == 0:
                        LOG.info(
                            "checkpoint | %d/%d pending frames processed",
                            min(offset + batch_size, len(pending)),
                            len(pending),
                        )
                memory = encoder.memory()
                encoder.close()
                encoder = None
            if errors:
                state.update(status="error", errors=errors)
                save_json(state_path, state)
                return {
                    "done": len(pending) - len(errors),
                    "skipped": len(complete),
                    "error": len(errors),
                }
            _, final_hash, _ = collect_frames(source)
            if final_hash != input_hash:
                raise ValueError("Input changed during indexing; publication withheld")
            exported = export_index(conn, output, recipe)
            if exported["count"] != len(records):
                raise ValueError("Export does not cover all input frames")
            conn.close()
            conn = None
            save_json(
                output / "index_manifest.json",
                {
                    **signature,
                    **exported,
                    "keyframes_root": str(source / "keyframes"),
                    "maps_root": str(source / "map-keyframes"),
                    "serving_ready": False,
                    "retrieval_ready": True,
                    "memory": memory,
                    "batch_size": batch_size,
                },
            )
            artifacts = [
                "meta.db",
                "faiss_index/frames.faiss",
                "faiss_index/frames_meta.json",
                "index_manifest.json",
            ]
            for rel in artifacts:
                reject_link(output / rel)
            state.update(
                status="complete",
                errors=[],
                artifacts={rel: digest(output / rel) for rel in artifacts},
            )
            save_json(state_path, state)
            result = {
                "done": len(pending),
                "skipped": len(complete),
                "error": 0,
                **exported,
                **memory,
            }
            LOG.info("complete | %s", result)
            return result
        finally:
            if encoder is not None:
                encoder.close()
            if conn is not None:
                conn.close()
            LOG.removeHandler(handler)
            handler.close()


def main() -> int:
    """Build from a local pinned cache; never touch raw videos or auto-start API."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    try:
        recipe, model = model_recipe(args.model_config, args.device)
        result = build_index(
            args.input_dir, args.output_dir, recipe, model, batch_size=args.batch_size
        )
        print(json.dumps(result, indent=2))
        return 1 if result["error"] else 0
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; committed batches can be resumed.\n")
    except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
        parser.exit(1, f"Index error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
