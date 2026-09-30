"""Resumable vehicle detection on completed keyframes; retrieval stays read-only."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw
from tqdm import tqdm

from pipeline.cctv.index import collect_frames
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.traffic import DetectionFrame, load_config, validate_record

LOG = logging.getLogger("cctv.detect")
PIPELINE = "cctv-detect-v1"
CLASSES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def canonical(value: object) -> str:
    """Stable JSON for hashing and SQLite transactions."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def model_recipe(
    config: Path, device: str, batch: int, stride: int
) -> tuple[dict, Path]:
    """Verify local weights before loading; fingerprint parameters and code."""
    descriptor = json.loads(config.read_text(encoding="utf-8-sig"))
    weights = Path(descriptor["path"]).resolve()
    if (
        not weights.is_file()
        or weights.suffix != ".pt"
        or digest(weights) != descriptor["sha256"]
    ):
        raise ValueError("Missing or changed local YOLO weights; verify cache first")
    return {
        "model": descriptor["model"],
        "revision": descriptor["revision"],
        "weights_sha256": descriptor["sha256"],
        "device": device,
        "precision": "fp16" if device == "cuda" else "fp32",
        "imgsz": 640,
        "confidence": 0.25,
        "iou": 0.7,
        "max_det": 300,
        "classes": CLASSES,
        "batch_size": batch,
        "stride": stride,
        "ultralytics": importlib.metadata.version("ultralytics"),
        "torch": importlib.metadata.version("torch"),
        "pillow": importlib.metadata.version("pillow"),
        "code_sha256": digest(Path(__file__)),
        "contract_sha256": digest(Path(__file__).with_name("traffic.py")),
    }, weights


class YoloDetector:
    """One local YOLO model with bounded image batches, FP16 and no saved SDK assets."""

    def __init__(self, weights: Path, device: str, recipe: dict) -> None:
        cache = Path(__file__).resolve().parents[2] / "outputs/model-cache/ultralytics"
        reject_link(cache)
        cache.mkdir(parents=True, exist_ok=True)
        # Set before SDK import; keep its settings/telemetry out of user profile.
        os.environ["YOLO_CONFIG_DIR"] = str(cache)
        os.environ["YOLO_AUTOINSTALL"] = "false"
        import torch
        from ultralytics import YOLO
        from ultralytics.utils import DEFAULT_CFG_DICT, SETTINGS

        SETTINGS.update({"sync": False})
        torch.set_num_threads(2)
        if device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA unavailable; choose cpu with a new output")
        self.device, self.recipe = device, recipe
        self.precision_args = (
            {"quantize": 16 if device == "cuda" else 32}
            if "quantize" in DEFAULT_CFG_DICT
            else {"half": device == "cuda"}
        )
        self.model = YOLO(str(weights), task="detect")
        if any(self.model.names.get(k) != v for k, v in CLASSES.items()):
            raise ValueError("Detector class map differs from expected COCO vehicles")
        if device == "cuda":
            torch.cuda.reset_peak_memory_stats()

    def predict(self, images: list[Image.Image]) -> list[list[dict]]:
        """SDK normalizes boxes back to original image dimensions."""
        import torch

        with (
            torch.inference_mode(),
            torch.amp.autocast("cuda", enabled=self.device == "cuda"),
        ):
            results = self.model.predict(
                source=images,
                device=0 if self.device == "cuda" else "cpu",
                **self.precision_args,
                imgsz=640,
                conf=0.25,
                iou=0.7,
                max_det=300,
                classes=list(CLASSES),
                batch=len(images),
                stream=False,
                verbose=False,
                save=False,
                save_txt=False,
                save_crop=False,
                show=False,
                augment=False,
                rect=False,
            )
            output = []
            for result in results:
                boxes = result.boxes
                output.append(
                    [
                        {
                            "label": CLASSES[int(label)],
                            "confidence": float(score),
                            "bbox": [max(0.0, min(1.0, float(x))) for x in box],
                        }
                        for box, label, score in zip(
                            boxes.xyxyn.cpu().tolist(),
                            boxes.cls.cpu().tolist(),
                            boxes.conf.cpu().tolist(),
                        )
                    ]
                )
        return output

    def memory(self) -> dict:
        import torch

        return (
            {
                "peak_allocated_mib": round(
                    torch.cuda.max_memory_allocated() / 2**20, 2
                ),
                "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 2),
            }
            if self.device == "cuda"
            else {}
        )

    def close(self) -> None:
        """Release model before later stages; never hold CLIP in this pipeline."""
        import torch

        self.model = None
        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()


def draw_preview(source: Path, record: DetectionFrame, target: Path) -> None:
    """Draw normalized boxes on a copy; leave original JPEG bytes untouched."""
    with Image.open(source) as image:
        rgb = image.convert("RGB")
    try:
        draw = ImageDraw.Draw(rgb)
        width, height = rgb.size
        for detection in record.detections:
            x1, y1, x2, y2 = detection.bbox
            box = (
                x1 * (width - 1),
                y1 * (height - 1),
                x2 * (width - 1),
                y2 * (height - 1),
            )
            draw.rectangle(box, outline="#ffcc00", width=3)
            draw.text(
                (box[0], max(0, box[1] - 13)),
                f"{detection.label} {detection.confidence:.2f}",
                fill="#ffcc00",
                stroke_width=1,
                stroke_fill="black",
            )
        rgb.save(target, format="JPEG", quality=90)
    finally:
        rgb.close()


def publish(conn, output: Path, state: dict, frames: list[dict]) -> None:
    """Deterministic JSONL/six-video previews with recoverable publication journal."""
    inputs = {row["file"]: row for row in frames}
    preview_rows = {}
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=output) as folder:
        staged_root = Path(folder)
        jsonl = staged_root / "detections.jsonl"
        with jsonl.open("w", encoding="utf-8", newline="\n") as handle:
            for file, payload in conn.execute(
                "SELECT file,payload FROM frames ORDER BY video_id,timestamp_sec"
            ):
                handle.write(payload + "\n")
                record = DetectionFrame.model_validate_json(payload)
                vid = record.locator.video_id
                if vid not in preview_rows or abs(
                    record.locator.timestamp_sec - 30
                ) < abs(preview_rows[vid][1].locator.timestamp_sec - 30):
                    preview_rows[vid] = (file, record)
        staged = {"detections.jsonl": jsonl}
        for vid, (file, record) in preview_rows.items():
            path = staged_root / f"{vid}.jpg"
            draw_preview(inputs[file]["path"], record, path)
            staged[f"previews/{vid}.jpg"] = path
        manifest = {
            "pipeline": PIPELINE,
            "run_id": state["run_id"],
            "signature": state["signature"],
            "frames": len(frames),
            "videos": len(preview_rows),
            "memory": state.get("memory", {}),
            "inference_seconds": state.get("inference_seconds", 0),
            "tracking_ready": False,
            "roi_ready": state["roi_ready"],
        }
        save_json(staged_root / "detection_manifest.json", manifest)
        staged["detection_manifest.json"] = staged_root / "detection_manifest.json"
        artifacts = {name: digest(path) for name, path in staged.items()}
        artifacts["detections.db"] = digest(output / "detections.db")
        if state.get("publication") and state["publication"] != artifacts:
            raise ValueError("Interrupted publication differs; outputs preserved")
        for name in staged:
            target = output / name
            reject_link(target)
            if target.exists() and (
                not state.get("publication") or digest(target) != artifacts[name]
            ):
                raise ValueError(f"Unowned or changed publication preserved: {name}")
        state.update(status="publishing", publication=artifacts)
        save_json(output / "detect_state.json", state)
        preview = output / "previews"
        reject_link(preview)
        preview.mkdir(exist_ok=True)
        for name, path in staged.items():
            target = output / name
            if not target.exists():
                path.replace(target)
        state["status"] = "complete"
        save_json(output / "detect_state.json", state)


def detect_frames(
    source: Path,
    output: Path,
    model_config: Path,
    traffic_config: Path,
    device: str = "cuda",
    batch: int = 2,
    stride: int = 1,
    detector_factory=YoloDetector,
) -> dict:
    """Commit validated frames individually; resume without recomputing successful ones."""
    if (
        device not in {"cuda", "cpu"}
        or type(batch) is not int
        or not 1 <= batch <= 4
        or type(stride) is not int
        or stride < 1
    ):
        raise ValueError("Use cuda/cpu, batch 1-4 and positive integer stride")
    source = source.resolve()
    output = output.absolute()
    reject_link(output)
    frames, source_hash, raw = collect_frames(source)
    frames = [row for row in frames if (int(Path(row["file"]).stem) - 1) % stride == 0]
    manifest = load_manifest(source / "dataset_manifest.json", require_files=False)
    traffic = load_config(traffic_config, manifest)
    recipe, weights = model_recipe(model_config, device, batch, stride)
    for protected in (
        source,
        raw,
        model_config.resolve().parent,
        traffic_config.resolve().parent,
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Output overlaps protected input/config/model folders")
    signature = json.loads(
        canonical(
            {
                "pipeline": PIPELINE,
                "source": str(source),
                "input_sha256": source_hash,
                "traffic_config_sha256": digest(traffic_config),
                "recipe": recipe,
            }
        )
    )
    state_path, database = output / "detect_state.json", output / "detections.db"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved; use an empty folder")
    output.mkdir(parents=True, exist_ok=True)
    for path in (state_path, database, output / ".ingest.lock", output / "detect.log"):
        reject_link(path)
    counts = {"done": 0, "skipped": 0, "error": 0, "model_loaded": False}
    with output_lease(output):
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {
                "signature": signature,
                "run_id": "detect-" + text_hash(canonical(signature))[:16],
                "status": "processing",
                "roi_ready": all(c.status == "approved" for c in traffic.cameras),
            }
        )
        if state.get("signature") != signature:
            raise ValueError("Input/model/recipe changed; use a NEW output directory")
        if state["status"] == "complete":
            for name, expected in state["publication"].items():
                if digest(output / name) != expected:
                    raise ValueError(f"Published artifact changed: {name}")
            counts["skipped"] = len(frames)
            return counts
        save_json(state_path, state)
        handler = logging.FileHandler(output / "detect.log", encoding="utf-8")
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        backend = None
        conn = None
        try:
            conn = sqlite3.connect(database)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not tables.issubset({"metadata", "frames"}):
                raise ValueError("Foreign checkpoint database preserved")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata (signature TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS frames (file TEXT PRIMARY KEY,source_sha256 TEXT NOT NULL,payload TEXT NOT NULL,payload_sha256 TEXT NOT NULL,video_id TEXT NOT NULL,timestamp_sec REAL NOT NULL)"
            )
            saved = conn.execute("SELECT signature FROM metadata").fetchall()
            if not saved:
                conn.execute("INSERT INTO metadata VALUES (?)", (canonical(signature),))
                conn.commit()
            elif saved != [(canonical(signature),)]:
                raise ValueError("Checkpoint DB signature differs")
            inputs = {r["file"]: r for r in frames}
            completed = set()
            for file, sha, payload, record_sha in conn.execute(
                "SELECT file,source_sha256,payload,payload_sha256 FROM frames"
            ):
                if (
                    file not in inputs
                    or sha != inputs[file]["sha256"]
                    or text_hash(payload) != record_sha
                ):
                    raise ValueError("Changed checkpoint frame preserved")
                record = validate_record(payload, manifest, traffic)
                if not isinstance(record, DetectionFrame):
                    raise ValueError("Checkpoint contains non-detection record")
                expected_locator = {
                    k: inputs[file][k]
                    for k in ("camera_id", "video_id", "timestamp_sec", "frame_idx")
                }
                if (
                    record.run_id != state["run_id"]
                    or record.locator.model_dump() != expected_locator
                ):
                    raise ValueError("Checkpoint locator differs")
                completed.add(file)
            pending = [r for r in frames if r["file"] not in completed]
            counts["skipped"] = len(completed)
            failures = {}
            if pending:
                backend = detector_factory(weights, device, recipe)
                counts["model_loaded"] = True
                start = time.perf_counter()

                def infer(items: list[tuple[dict, Image.Image]]) -> None:
                    try:
                        predictions = backend.predict([image for _, image in items])
                        if len(predictions) != len(items):
                            raise ValueError(
                                "Detector output coverage differs from batch"
                            )
                    except Exception as exc:
                        LOG.exception("batch_error | %s frames", len(items))
                        # Drop failed inference stack tensors before smaller retries.
                        exc.__traceback__ = None
                        if device == "cuda":
                            import torch

                            torch.cuda.empty_cache()
                        if len(items) > 1:
                            middle = len(items) // 2
                            infer(items[:middle])
                            infer(items[middle:])
                        else:
                            failures[items[0][0]["file"]] = str(exc)
                        return
                    for (row, image), detections in zip(items, predictions):
                        try:
                            value = {
                                "schema_version": 1,
                                "kind": "detections",
                                "run_id": state["run_id"],
                                "locator": {
                                    k: row[k]
                                    for k in (
                                        "camera_id",
                                        "video_id",
                                        "timestamp_sec",
                                        "frame_idx",
                                    )
                                },
                                "width": image.width,
                                "height": image.height,
                                "detections": detections,
                            }
                            payload = canonical(value)
                            validate_record(payload, manifest, traffic)
                            with conn:
                                conn.execute(
                                    "INSERT INTO frames VALUES (?,?,?,?,?,?)",
                                    (
                                        row["file"],
                                        row["sha256"],
                                        payload,
                                        text_hash(payload),
                                        row["video_id"],
                                        row["timestamp_sec"],
                                    ),
                                )
                            counts["done"] += 1
                        except Exception as exc:
                            failures[row["file"]] = str(exc)
                            LOG.exception("frame_error | %s", row["file"])

                for offset in tqdm(
                    range(0, len(pending), batch), desc="Vehicle batches", unit="batch"
                ):
                    images = []
                    try:
                        for row in pending[offset : offset + batch]:
                            try:
                                with Image.open(row["path"]) as image:
                                    images.append((row, image.convert("RGB")))
                            except Exception as exc:
                                failures[row["file"]] = str(exc)
                                LOG.exception("decode_error | %s", row["file"])
                        if images:
                            infer(images)
                    finally:
                        for _, image in images:
                            image.close()
                state["inference_seconds"] = state.get("inference_seconds", 0) + round(
                    time.perf_counter() - start, 3
                )
                state["memory"] = backend.memory()
            state["errors"] = failures
            counts["error"] = len(failures)
            save_json(state_path, state)
            if failures:
                return counts
            # Recheck selected input bytes before publishing generated overlays.
            if any(digest(r["path"]) != r["sha256"] for r in frames):
                raise ValueError("Input changed during inference; publication blocked")
            conn.close()
            conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            publish(conn, output, state, frames)
            LOG.info("complete | %s", counts)
            return counts
        finally:
            if conn is not None:
                conn.close()
            if backend is not None:
                backend.close()
            LOG.removeHandler(handler)
            handler.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--traffic-config", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--stride", type=int, default=1)
    args = parser.parse_args()
    try:
        counts = detect_frames(
            args.input_dir,
            args.output_dir,
            args.model_config,
            args.traffic_config,
            args.device,
            args.batch_size,
            args.stride,
        )
        print(json.dumps(counts, indent=2))
        return 1 if counts["error"] else 0
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; rerun identical command to resume.\n")
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Detection error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
