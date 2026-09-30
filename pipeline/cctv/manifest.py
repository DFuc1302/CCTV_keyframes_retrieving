"""Validate CCTV identities and read-only input paths without changing legacy schemas."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path, PureWindowsPath
import re

from pipeline.frames.extract import VIDEO_EXTENSIONS


class ManifestError(ValueError):
    """The manifest cannot be safely used for CCTV ingestion."""


@dataclass(frozen=True)
class Video:
    """One stable video identity, independent of its original filename."""

    video_id: str
    camera_id: str
    relative_path: str
    source: Path
    start_time: str | None


@dataclass(frozen=True)
class Manifest:
    """Validated CCTV input contract; generated assets live elsewhere."""

    path: Path
    raw_root: Path
    cameras: tuple[dict[str, object], ...]
    videos: tuple[Video, ...]


def identity(value: object, field: str) -> str:
    """Require unique portable folder identifiers, including Windows device rules."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ManifestError(f"{field} must be 1-64 ASCII letters, digits, underscores or hyphens")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if value.upper() in reserved:
        raise ManifestError(f"{field} is a reserved Windows name: {value}")
    return value


def relative_video(value: object) -> str:
    """Accept relative paths with spaces/Unicode but reject traversal and Windows ADS."""
    if not isinstance(value, str) or not value:
        raise ManifestError("video path must be a non-empty string")
    value = value.replace("\\", "/")
    if PureWindowsPath(value).drive or value.startswith("/"):
        raise ManifestError(f"video path must be relative: {value}")
    parts = value.split("/")
    if any(
        p in {"", ".", ".."} or p.endswith((".", " "))
        or re.search(r'[<>:"|?*\x00-\x1f]', p)
        for p in parts
    ):
        raise ManifestError(f"unsafe relative video path: {value}")
    return value


def load_manifest(path: Path, *, require_files: bool = True) -> Manifest:
    """Validate schema, camera references, timestamps and resolved input containment."""
    path = path.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
    ):
        raise ManifestError("manifest must have schema_version=1")
    raw_value = payload.get("raw_root")
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ManifestError("raw_root must be a path string")
    root = Path(raw_value).expanduser()
    if root.drive and not root.is_absolute():
        raise ManifestError("raw_root cannot be a drive-relative Windows path")
    root = (root if root.is_absolute() else path.parent / root).resolve()
    if require_files and not root.is_dir():
        raise ManifestError(f"raw_root does not exist: {root}")
    raw_cameras, raw_videos = payload.get("cameras"), payload.get("videos")
    if not isinstance(raw_cameras, list) or not isinstance(raw_videos, list):
        raise ManifestError("cameras and videos must be arrays")
    cameras: list[dict[str, object]] = []
    camera_ids: set[str] = set()
    for item in raw_cameras:
        if not isinstance(item, dict):
            raise ManifestError("camera entry must be an object")
        camera_id = identity(item.get("camera_id"), "camera_id")
        if camera_id.casefold() in camera_ids:
            raise ManifestError(f"duplicate camera_id: {camera_id}")
        camera_ids.add(camera_id.casefold())
        for label in ("name", "zone"):
            if label in item and not isinstance(item[label], str):
                raise ManifestError(f"camera {label} must be a string")
        if "roi" in item:
            roi = item["roi"]
            if not isinstance(roi, list) or len(roi) < 3:
                raise ManifestError("ROI needs at least three normalized [x,y] points")
            for point in roi:
                if not isinstance(point, list) or len(point) != 2 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in point
                ):
                    raise ManifestError("ROI coordinates must be finite numbers in [0,1]")
            area = sum(
                roi[i][0] * roi[(i + 1) % len(roi)][1]
                - roi[(i + 1) % len(roi)][0] * roi[i][1]
                for i in range(len(roi))
            )
            if abs(area) < 1e-9:
                raise ManifestError("ROI polygon must have non-zero area")
        cameras.append(dict(item))
    canonical_cameras = {str(c["camera_id"]).casefold(): str(c["camera_id"]) for c in cameras}
    videos: list[Video] = []
    video_ids: set[str] = set()
    source_paths: set[str] = set()
    for item in raw_videos:
        if not isinstance(item, dict):
            raise ManifestError("video entry must be an object")
        video_id = identity(item.get("video_id"), "video_id")
        camera_id = identity(item.get("camera_id"), "camera_id")
        if video_id.casefold() in video_ids:
            raise ManifestError(f"duplicate video_id: {video_id}")
        video_ids.add(video_id.casefold())
        if camera_id.casefold() not in canonical_cameras:
            raise ManifestError(f"unknown camera_id: {camera_id}")
        relative = relative_video(item.get("path"))
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or source == root:
            raise ManifestError(f"video resolves outside raw_root: {relative}")
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ManifestError(f"unsupported video extension: {relative}")
        if str(source).casefold() in source_paths:
            raise ManifestError(f"same input video assigned twice: {relative}")
        source_paths.add(str(source).casefold())
        if require_files and not source.is_file():
            raise ManifestError(f"missing video: {source}")
        stamp = item.get("start_time")
        if stamp is not None:
            try:
                parsed = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
            except ValueError as exc:
                raise ManifestError(f"invalid start_time: {stamp}") from exc
            if parsed is None or parsed.utcoffset() is None:
                raise ManifestError("start_time must be ISO 8601 with an explicit timezone")
        videos.append(Video(video_id, canonical_cameras[camera_id.casefold()], relative, source, stamp))
    return Manifest(path, root, tuple(cameras), tuple(videos))


def main() -> int:
    """Validate structure alone, or additionally require actual input files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--require-files", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest, require_files=args.require_files)
        print(json.dumps({"schema_valid": True, "cameras": len(manifest.cameras),
                          "videos": len(manifest.videos), "files_checked": args.require_files}, indent=2))
        return 0
    except ManifestError as exc:
        parser.exit(2, f"Manifest error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
