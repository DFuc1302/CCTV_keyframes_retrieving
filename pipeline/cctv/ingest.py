"""Manifest-driven, resumable CCTV extraction using the legacy 2 FPS FFmpeg stage."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterator

import cv2
import numpy as np
from tqdm import tqdm

from pipeline.cctv.manifest import Manifest, ManifestError, Video, load_manifest
from pipeline.frames import extract as legacy


MAP_FIELDS = ["n", "pts_time", "fps", "frame_idx"]
PIPELINE = "cctv-ingest-v1"
LOG = logging.getLogger("cctv.ingest")


def digest(path: Path) -> str:
    """Hash input or output incrementally, without loading the whole file."""
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def save_json(path: Path, value: object) -> None:
    """Publish one checkpoint atomically while the output lease is held."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}-", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def reject_link(path: Path) -> None:
    """Do not follow pre-existing links/junctions when publishing generated files."""
    if path.is_symlink() or path.resolve() != path:
        raise ValueError(f"Generated path cannot be a link/junction: {path}")


def safe_output(manifest: Manifest, output: Path) -> Path:
    """Reject output/input overlap, including symlinks and junction resolution."""
    output = output.resolve()
    if output.is_relative_to(manifest.raw_root) or manifest.raw_root.is_relative_to(output):
        raise ValueError("Output and raw_root must be separate, non-overlapping directories")
    if manifest.path.is_relative_to(output):
        raise ValueError("Keep the source manifest outside the generated output directory")
    return output


@contextmanager
def output_lease(output: Path) -> Iterator[None]:
    """Hold a nonblocking OS lock, released even when the owner is interrupted."""
    if os.name == "nt":
        import msvcrt
    elif os.name == "posix":
        import fcntl
    else:
        raise RuntimeError(f"Output leases are unsupported on {os.name}")

    with (output / ".ingest.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another ingestion process owns this output directory") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def images_digest(folder: Path) -> str:
    """Describe every JPEG name and byte content; reject unrelated folder contents."""
    files = sorted(folder.iterdir())
    if not files or any(not p.is_file() or p.suffix != ".jpg" or not p.stem.isdigit() for p in files):
        raise ValueError(f"Unexpected or empty frame folder: {folder}")
    result = hashlib.sha256()
    for image in files:
        result.update(f"{image.name}:{digest(image)}\n".encode("utf-8"))
    return result.hexdigest()


def validate_artifacts(frames: Path, mapping: Path) -> dict[str, object]:
    """Check legacy timestamps, image/map coverage and decode each frame separately."""
    with mapping.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != MAP_FIELDS:
            raise ValueError(f"Wrong map columns: {mapping}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"Empty frame map: {mapping}")
    previous_idx, previous_time = -1, -1.0
    expected: set[str] = set()
    failures = 0
    for number, row in enumerate(tqdm(rows, desc="Validating frames", unit="frame", leave=False), 1):
        image: Path | None = None
        try:
            n, idx = int(row["n"]), int(row["frame_idx"])
            stamp, fps = float(row["pts_time"]), float(row["fps"])
            if (
                n != number or idx <= previous_idx or not math.isfinite(stamp)
                or stamp < 0 or stamp < previous_time
            ):
                raise ValueError("Map frame numbers or timestamps are invalid")
            if not math.isfinite(fps) or fps < 0:
                raise ValueError("Invalid nominal FPS")
            previous_idx, previous_time = idx, stamp
            filename = f"{n:03d}.jpg"
            expected.add(filename)
            image = frames / filename
            if image.is_symlink() or not image.is_file():
                raise ValueError("Missing frame or unsupported symbolic link")
            # imdecode from bytes handles Unicode Windows filenames reliably.
            decoded = cv2.imdecode(np.frombuffer(image.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None:
                raise ValueError("JPEG cannot be decoded")
            del decoded
        except (OSError, ValueError, TypeError, KeyError, cv2.error) as exc:
            failures += 1
            LOG.error("frame_error | %s | %s", image or mapping, exc)
    actual = {p.name for p in frames.iterdir()}
    if failures or expected != actual:
        raise ValueError(
            f"Frame/map integrity failure: {failures} invalid rows/frames; "
            f"coverage_match={expected == actual}"
        )
    return {"frame_count": len(rows), "images_sha256": images_digest(frames), "map_sha256": digest(mapping)}


def recipe(ffmpeg: str, device: str) -> dict[str, str | int]:
    """Fingerprint the actual extractor and FFmpeg version for safe resume."""
    result = subprocess.run(
        [ffmpeg, "-version"], capture_output=True, text=True,
        errors="replace", timeout=30, check=True,
    )
    return {"fps": 2, "device": device, "ffmpeg": result.stdout.splitlines()[0],
            "extractor_sha256": digest(Path(legacy.__file__))}


def source_signature(video: Video, processing: dict[str, str | int]) -> str:
    """Bind an output to source bytes, stable IDs, timestamps and recipe."""
    value = {"source": str(video.source), "source_sha256": digest(video.source),
             "video_id": video.video_id, "camera_id": video.camera_id,
             "start_time": video.start_time, "recipe": processing}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def process_video(video: Video, output: Path, ffmpeg: str, device: str,
                  processing: dict[str, str | int], state: dict[str, Any], state_path: Path) -> str:
    """Resume only owned, matching output; never replace a published frame silently."""
    records: dict[str, dict[str, object]] = state["videos"]
    frames = output / "keyframes" / video.video_id
    mapping = output / "map-keyframes" / f"{video.video_id}.csv"
    reject_link(frames)
    reject_link(mapping)
    before = video.source.stat()
    signature = source_signature(video, processing)
    previous = records.get(video.video_id)
    if previous and previous.get("signature") != signature:
        raise ValueError("Input, camera association or recipe changed; use a NEW output directory")
    if previous and previous.get("status") == "complete":
        if validate_artifacts(frames, mapping) != previous.get("artifacts"):
            raise ValueError("Published output changed; preserve it and use a NEW output directory")
        return "skipped"
    if (frames.exists() or mapping.exists()) and not (previous and previous.get("owns_output") is True):
        raise ValueError("Refusing to adopt existing frames/map without a matching pipeline checkpoint")
    records[video.video_id] = {"signature": signature, "owns_output": True, "status": "running"}
    save_json(state_path, state)
    # Private staging cannot make interrupted extraction appear completed.
    with tempfile.TemporaryDirectory(prefix=f"{video.video_id}-", dir=output / ".staging") as temporary:
        base = Path(temporary)
        staged_frames = base / "frames"
        staged_map = base / "map.csv"
        _, status, _, message = legacy.extract_one((
            str(video.source), str(staged_frames), ffmpeg, device, str(staged_map),
        ))
        if status != "done":
            raise RuntimeError(f"Legacy extraction failed: {message}")
        if message:
            LOG.warning("legacy_recovery | %s | %s", video.video_id, message)
        artifacts = validate_artifacts(staged_frames, staged_map)
        after = video.source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source changed during extraction; no output published")
        # Recovery after interruption between publishing frames and publishing map.
        if frames.exists() and images_digest(frames) != artifacts["images_sha256"]:
            raise ValueError("Interrupted frame output differs; preserve it and use a NEW output directory")
        if mapping.exists() and digest(mapping) != artifacts["map_sha256"]:
            raise ValueError("Interrupted map differs; preserve it and use a NEW output directory")
        if not frames.exists():
            staged_frames.rename(frames)
        if not mapping.exists():
            staged_map.replace(mapping)
    records[video.video_id].update(status="complete", artifacts=artifacts)
    save_json(state_path, state)
    return "done"


def run_ingestion(
    manifest_path: Path, output: Path, *, device: str = "cpu", ffmpeg: str = "ffmpeg",
) -> dict[str, int]:
    """Process videos sequentially; isolate video failures and checkpoint every result."""
    manifest = load_manifest(manifest_path)
    if not manifest.videos:
        raise ManifestError("No videos registered; provide CCTV inputs before running extraction")
    output = safe_output(manifest, output)
    executable = shutil.which(ffmpeg)
    if executable is None:
        raise RuntimeError("FFmpeg was not found")
    if device not in {"cpu", "cuda"}:
        raise ValueError("device must be cpu or cuda")
    processing = recipe(executable, device)
    output.mkdir(parents=True, exist_ok=True)
    for name in (".ingest.lock", "ingest_state.json", "dataset_manifest.json"):
        reject_link(output / name)
    counts = {"done": 0, "skipped": 0, "error": 0}
    with output_lease(output):
        for name in ("keyframes", "map-keyframes", ".staging", "logs"):
            generated = output / name
            reject_link(generated)
            generated.mkdir(exist_ok=True)
        reject_link(output / "logs" / "ingest.log")
        handler = logging.FileHandler(output / "logs" / "ingest.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        try:
            state_path = output / "ingest_state.json"
            state = (
                json.loads(state_path.read_text(encoding="utf-8"))
                if state_path.exists() else {"pipeline": PIPELINE, "videos": {}}
            )
            if (
                not isinstance(state, dict) or state.get("pipeline") != PIPELINE
                or not isinstance(state.get("videos"), dict)
            ):
                raise ValueError("Invalid or unrelated ingestion checkpoint")
            if any(not isinstance(entry, dict) for entry in state["videos"].values()):
                raise ValueError("Invalid video checkpoint entry")
            if "errors" in state and not isinstance(state["errors"], dict):
                raise ValueError("Invalid checkpoint error records")
            for video in tqdm(manifest.videos, desc="CCTV extraction", unit="video"):
                try:
                    status = process_video(video, output, executable, device, processing, state, state_path)
                except Exception as exc:
                    status = "error"
                    # Never assign ownership to unknown pre-existing output after failure.
                    record = state["videos"].get(video.video_id)
                    if record and record.get("status") != "complete":
                        record.update(status="error", last_error=f"{type(exc).__name__}: {exc}")
                    state.setdefault("errors", {})[video.video_id] = f"{type(exc).__name__}: {exc}"
                    save_json(state_path, state)
                    LOG.exception("video_error | %s", video.video_id)
                    tqdm.write(f"ERROR {video.video_id}: {exc}")
                counts[status] += 1
                if status != "error":
                    state.setdefault("errors", {}).pop(video.video_id, None)
                    save_json(state_path, state)
                LOG.info("%s | %s", status, video.video_id)
            save_json(output / "dataset_manifest.json", {
                "schema_version": 1, "raw_root": str(manifest.raw_root), "recipe": processing,
                "cameras": manifest.cameras, "videos": [
                    {"video_id": v.video_id, "camera_id": v.camera_id, "path": v.relative_path,
                     "start_time": v.start_time, "keyframe_prefix": f"{v.video_id}/",
                     "status": (
                         "error" if v.video_id in state.get("errors", {})
                         else state["videos"][v.video_id]["status"]
                     ),
                     "map_file": f"map-keyframes/{v.video_id}.csv"} for v in manifest.videos],
                "run_counts": counts, "serving_ready": False,
            })
            LOG.info("summary | %s", counts)
        finally:
            LOG.removeHandler(handler)
            handler.close()
    return counts


def main() -> int:
    """Expose a Windows-ready extraction CLI; video failures return exit code 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    try:
        counts = run_ingestion(args.manifest, args.output_dir, device=args.device, ffmpeg=args.ffmpeg)
        print(json.dumps(counts, indent=2))
        return 1 if counts["error"] else 0
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; rerun the same command to resume owned output.\n")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Ingestion error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
