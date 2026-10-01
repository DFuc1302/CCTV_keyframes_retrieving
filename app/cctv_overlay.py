"""Optional read-only tracking MP4s bound to the current retrieval/tracking sources."""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from pipeline.cctv.events import read_json
from pipeline.cctv.ingest import digest, reject_link
from pipeline.cctv.manifest import load_manifest

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


class OverlayStore:
    """Validate a bounded sealed publication once, invalidate edits while serving."""

    def __init__(self, output: Path, tracking: Path, manifest: Path) -> None:
        self.output, self.tracking, self.manifest = (
            output.absolute(),
            tracking.absolute(),
            manifest.absolute(),
        )
        self.ready = False
        self.rows: dict[str, dict] = {}
        self.paths: dict[str, tuple[Path, str]] = {}
        self.watched: dict[Path, tuple[int, int, int]] = {}
        self.lock = threading.RLock()

    def watch(self, path: Path, checksum: str) -> None:
        """Check exact bytes, reject links and remember ordinary edit metadata."""
        reject_link(path)
        if digest(path) != checksum:
            raise ValueError("Overlay input/asset checksum changed: " + path.name)
        stat = path.stat()
        self.watched[path] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def start(self, sources: dict[str, dict]) -> None:
        """Load only the renderer's explicit input allowlist, never arbitrary paths."""
        with self.lock:
            self.ready = False
            self.rows.clear()
            self.paths.clear()
            self.watched.clear()
            try:
                reject_link(self.output)
                checkpoint = self.output / "overlay_state.json"
                reject_link(checkpoint)
                if checkpoint.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError("Overlay checkpoint too large")
                state = read_json(checkpoint)
                if (
                    state["status"] != "complete"
                    or state.get("errors")
                    or state["signature"]["pipeline"] != "cctv-tracking-overlay-v1"
                ):
                    raise ValueError("Overlay publication incomplete")
                manifest = load_manifest(self.manifest)
                track_state = read_json(self.tracking / "track_state.json")
                if track_state["status"] != "complete" or track_state["signature"][
                    "manifest_sha256"
                ] != digest(self.manifest):
                    raise ValueError("Overlay tracking/source mismatch")
                videos = {v.video_id: v for v in manifest.videos}
                selected = set(track_state["signature"]["sources"])
                if (
                    not selected
                    or len(selected) > 24
                    or set(state["videos"]) != selected
                ):
                    raise ValueError("Overlay video coverage mismatch")
                inputs = [
                    self.manifest,
                    self.tracking / "track_state.json",
                    ROOT / "pipeline/cctv/tracking_overlay.py",
                    ROOT / "pipeline/cctv/track.py",
                    *[
                        self.tracking / name
                        for name in (
                            "tracks.db",
                            "frames.jsonl",
                            "tracking_manifest.json",
                        )
                    ],
                    *[videos[vid].source for vid in selected],
                ]
                checksums = state["signature"]["inputs"]
                if set(checksums) != {str(p) for p in inputs}:
                    raise ValueError("Unexpected overlay input paths")
                for path in inputs:
                    self.watch(path, checksums[str(path)])
                for name in ("tracks.db", "frames.jsonl", "tracking_manifest.json"):
                    if (
                        checksums[str(self.tracking / name)]
                        != track_state["publication"][name]
                    ):
                        raise ValueError("Overlay/tracking artifact mismatch")
                with closing(
                    sqlite3.connect(
                        f"{(self.tracking / 'tracks.db').as_uri()}?mode=ro", uri=True
                    )
                ) as conn:
                    for vid in sorted(selected):
                        entry = state["videos"][vid]
                        source = videos[vid]
                        if (
                            vid not in sources
                            or sources[vid]["camera_id"] != source.camera_id
                            or sources[vid]["sha256"]
                            != track_state["signature"]["sources"][vid]
                            or checksums[str(source.source)] != sources[vid]["sha256"]
                            or entry["video_id"] != vid
                            or entry["camera_id"] != source.camera_id
                            or entry["pts_verified"] is not True
                            or entry["roi_approved"] is not False
                            or entry["operational_ready"] is not False
                        ):
                            raise ValueError("Overlay retrieval/source/flags mismatch")
                        count, start, end = conn.execute(
                            "SELECT COUNT(*),MIN(timestamp_sec),MAX(timestamp_sec) FROM frames WHERE video_id=?",
                            (vid,),
                        ).fetchone()
                        offset = entry["playback_to_source_offset_sec"]
                        if (
                            type(entry["frames"]) is not int
                            or entry["frames"] != count
                            or not 2 <= count <= 5000
                            or not math.isfinite(offset)
                            or offset != start
                            or entry["source_start_sec"] != start
                        ):
                            raise ValueError("Overlay count/source offset mismatch")
                        path = self.output / f"{vid}.mp4"
                        if path.stat().st_size > 100 * 1024 * 1024:
                            raise ValueError("Overlay clip too large")
                        self.watch(path, entry["sha256"])
                        self.paths[vid] = (path, entry["sha256"])
                        self.rows[vid] = dict(
                            video_id=vid,
                            camera_id=source.camera_id,
                            frames=count,
                            start_sec=start,
                            end_sec=end,
                            playback_to_source_offset_sec=offset,
                            video_url=f"/cctv/traffic/overlay/{vid}",
                            predictions_only=True,
                            roi_approved=False,
                            operational_ready=False,
                        )
                self.watch(checkpoint, digest(checkpoint))
                self.ready = True
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                self.rows.clear()
                self.paths.clear()
                LOG.warning("Tracking overlay unavailable: %s", exc)

    def available(self) -> bool:
        """Ordinary source/publication edits require restart and verified outputs."""
        with self.lock:
            if self.ready:
                try:
                    for path, expected in self.watched.items():
                        reject_link(path)
                        stat = path.stat()
                        if (
                            stat.st_size,
                            stat.st_mtime_ns,
                            stat.st_ctime_ns,
                        ) != expected:
                            raise ValueError("Overlay snapshot changed")
                except (OSError, ValueError):
                    self.ready = False
            return self.ready

    def evidence(self, source: dict) -> dict | None:
        """Convert source PTS to overlay PTS; do not use the raw-preview offset."""
        if not self.available() or source["video_id"] not in self.rows:
            return None
        row = self.rows[source["video_id"]]
        stamp = source["timestamp_sec"]
        if (
            source["camera_id"] != row["camera_id"]
            or not row["start_sec"] <= stamp <= row["end_sec"]
        ):
            return None
        return dict(
            source,
            video_url=row["video_url"],
            video_seek_sec=stamp - row["playback_to_source_offset_sec"],
            tracking_overlay=True,
        )

    def status(self) -> dict:
        """An unavailable optional overlay does not disable retrieval or traffic."""
        ready = self.available()
        return dict(
            available=ready,
            read_only=True,
            predictions_only=True,
            roi_approved=False,
            operational_ready=False,
            videos=list(self.rows.values()) if ready else [],
        )
