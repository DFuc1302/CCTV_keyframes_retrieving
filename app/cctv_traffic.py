"""Read-only optional traffic artifacts; isolated from retrieval/model readiness."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from pipeline.cctv.ingest import digest, reject_link
from pipeline.cctv.manifest import identity
from pipeline.cctv.signals import Signal
from pipeline.cctv.congestion import DraftWindow
from pipeline.cctv.traffic import TrafficWindow

LOG = logging.getLogger("cctv.traffic.api")
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_WINDOWS = 10000


class TrafficStore:
    """Validate provenance once; serve an immutable, bounded traffic snapshot."""

    def __init__(self, windows: Path, tracking: Path, roi_previews: Path) -> None:
        self.windows, self.tracking, self.roi_previews = windows, tracking, roi_previews
        self.ready = False
        self.error: str | None = None
        self.video_rows: dict[str, dict] = {}
        self.rows: dict[str, list[dict]] = {}
        self.images: dict[str, tuple[Path, str]] = {}
        self.manifest: dict = {}
        self.watched: dict[Path, tuple[int, int, int]] = {}
        self.lock = threading.RLock()

    def verify(self, path: Path, checksum: str) -> None:
        """Register exact owned bytes and reject links/junctions."""
        reject_link(path)
        if not path.is_file() or digest(path) != checksum:
            raise ValueError(f"Traffic artifact changed: {path.name}")
        stat = path.stat()
        self.watched[path] = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def start(self, sources: dict[str, dict]) -> None:
        """Bind traffic/ROI artifacts to the same source videos as retrieval."""
        with self.lock:
            self.ready = False
            self.error = None
            self.video_rows.clear()
            self.rows.clear()
            self.images.clear()
            self.watched.clear()
            try:
                state_path = self.windows / "window_state.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("status") != "complete":
                    raise ValueError("Traffic publication is incomplete")
                for name in (
                    "window_manifest.json",
                    "windows.jsonl",
                    "signals.jsonl",
                    "windows.db",
                ):
                    self.verify(self.windows / name, state["artifacts"][name])
                self.manifest = json.loads(
                    (self.windows / "window_manifest.json").read_text()
                )
                manifest = self.manifest
                if (
                    manifest.get("pipeline") != "cctv-congestion-v1"
                    or manifest["signature"] != state["signature"]
                    or manifest["operational_ready"] is not False
                    or manifest["thresholds_calibrated"] is not False
                    or manifest["roi_ready"] != (not manifest["demo_only"])
                ):
                    raise ValueError("Invalid traffic manifest/readiness")
                track_state = json.loads(
                    (self.tracking / "track_state.json").read_text()
                )
                if track_state["status"] != "complete":
                    raise ValueError("Tracking publication incomplete")
                for name in ("tracks.db", "frames.jsonl", "tracking_manifest.json"):
                    checksum = manifest["signature"]["tracking"][name]
                    if track_state["publication"][name] != checksum:
                        raise ValueError("Traffic/tracking provenance mismatch")
                    self.verify(self.tracking / name, checksum)
                tracks = json.loads(
                    (self.tracking / "tracking_manifest.json").read_text()
                )
                if (
                    tracks["signature"] != track_state["signature"]
                    or tracks["run_id"] != track_state["run_id"]
                ):
                    raise ValueError("Tracking manifest/state mismatch")
                selected = manifest["videos"]
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or set(selected) != set(tracks["videos"])
                ):
                    raise ValueError("Traffic/tracking video coverage mismatch")
                roi_state = json.loads(
                    (self.roi_previews / "roi_preview_state.json").read_text()
                )
                if roi_state["signature"]["roi"] != manifest["signature"]["roi_sha256"]:
                    raise ValueError("ROI preview/config provenance mismatch")
                if (
                    roi_state["signature"]["manifest"]
                    != tracks["signature"]["manifest_sha256"]
                ):
                    raise ValueError("ROI preview/source manifest mismatch")
                for vid in selected:
                    identity(vid, "video_id")
                    if (
                        vid not in sources
                        or sources[vid]["sha256"] != tracks["signature"]["sources"][vid]
                    ):
                        raise ValueError("Traffic source differs from retrieval")
                    name = f"{vid}.png"
                    self.verify(self.roi_previews / name, roi_state["artifacts"][name])
                    self.images[vid] = (
                        self.roi_previews / name,
                        roi_state["artifacts"][name],
                    )
                    previews = sorted(
                        n
                        for n in roi_state["signature"]["inputs"]
                        if n.startswith(f"previews/{vid}-")
                    )
                    if len(previews) != 1:
                        raise ValueError("ROI preview source is ambiguous")
                    preview_name = previews[0]
                    if (
                        roi_state["signature"]["inputs"][preview_name]
                        != track_state["publication"][preview_name]
                    ):
                        raise ValueError("ROI/tracker preview hash mismatch")
                    self.verify(
                        self.tracking / preview_name,
                        track_state["publication"][preview_name],
                    )
                    self.rows[vid] = []
                    self.video_rows[vid] = {
                        "video_id": vid,
                        "camera_id": sources[vid]["camera_id"],
                        "roi_image_url": f"/cctv/traffic/roi/{vid}",
                    }
                signals = self.windows / "signals.jsonl"
                if signals.stat().st_size > MAX_LEDGER_BYTES:
                    raise ValueError("Traffic ledger exceeds serving memory bound")
                window_lines = self.windows / "windows.jsonl"
                if window_lines.stat().st_size > MAX_LEDGER_BYTES:
                    raise ValueError("Window ledger exceeds serving memory bound")
                total, candidates = 0, 0
                with closing(
                    sqlite3.connect(
                        f"{(self.tracking / 'tracks.db').as_uri()}?mode=ro", uri=True
                    )
                ) as conn:
                    with (
                        signals.open(encoding="utf-8") as ledger,
                        window_lines.open(encoding="utf-8") as windows,
                    ):
                        for line in ledger:
                            row = Signal.model_validate_json(line)
                            window = row.window
                            vid = window.video_id
                            total += 1
                            if total > MAX_WINDOWS:
                                raise ValueError(
                                    "Too many traffic windows for demo serving"
                                )
                            if (
                                vid not in self.rows
                                or window.camera_id != sources[vid]["camera_id"]
                                or window.run_id != tracks["run_id"]
                                or row.demo_only != manifest["demo_only"]
                            ):
                                raise ValueError("Window source/scope mismatch")
                            start = tracks["signature"]["start_sec"]
                            duration = tracks["signature"]["duration_sec"]
                            if window.start_sec < start or (
                                duration is not None
                                and window.end_sec > start + duration + 1e-8
                            ):
                                raise ValueError("Window outside processed clip")
                            if (
                                self.rows[vid]
                                and self.rows[vid][-1]["window"]["end_sec"]
                                > window.start_sec
                            ):
                                raise ValueError("Windows overlap or are unordered")
                            ledger_window = (DraftWindow if window.kind == "draft_window" else TrafficWindow).model_validate_json(windows.readline())
                            if ledger_window != window:
                                raise ValueError("Window/signal ledger mismatch")
                            calibration_hashes = manifest.get("camera_calibration_sha256", {})
                            if window.calibration_sha256 != calibration_hashes.get(window.camera_id):
                                raise ValueError("Window calibration provenance mismatch")
                            evidence = conn.execute(
                                "SELECT frame_idx,timestamp_sec FROM frames WHERE video_id=? AND timestamp_sec>=? AND timestamp_sec<? ORDER BY frame_idx LIMIT 1",
                                (vid, window.start_sec, window.end_sec),
                            ).fetchone()
                            if evidence is None:
                                raise ValueError("Window has no tracked evidence frame")
                            item = row.model_dump(mode="json")
                            item["evidence"] = self.evidence(vid, evidence, sources)
                            self.rows[vid].append(item)
                            candidates += row.status == "congestion_candidate"
                        if windows.readline():
                            raise ValueError("Extra window records")
                    for vid in selected:
                        if not self.rows[vid]:
                            raise ValueError("Video has no traffic windows")
                        preview_name = next(
                            n
                            for n in roi_state["signature"]["inputs"]
                            if n.startswith(f"previews/{vid}-")
                        )
                        segment = Path(preview_name).stem[len(vid) + 1 :]
                        last = conn.execute(
                            "SELECT frame_idx,timestamp_sec FROM frames WHERE video_id=? AND segment_id=? ORDER BY frame_idx DESC LIMIT 1",
                            (vid, segment),
                        ).fetchone()
                        if last is None:
                            raise ValueError("ROI preview locator unavailable")
                        self.video_rows[vid].update(
                            window_count=len(self.rows[vid]),
                            start_sec=self.rows[vid][0]["window"]["start_sec"],
                            end_sec=self.rows[vid][-1]["window"]["end_sec"],
                            roi_evidence=self.evidence(vid, last, sources),
                        )
                if (
                    total != manifest["windows"]
                    or total != state["windows"]
                    or candidates != manifest["candidates"]
                ):
                    raise ValueError("Traffic publication counts mismatch")
                # Observe checkpoint replacement/deletion while serving, without rewriting it.
                for path in (
                    state_path,
                    self.tracking / "track_state.json",
                    self.roi_previews / "roi_preview_state.json",
                ):
                    reject_link(path)
                    stat = path.stat()
                    self.watched[path] = (
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                    )
                self.ready = True
            except Exception as exc:
                self.error = str(exc)
                self.rows.clear()
                self.video_rows.clear()
                self.images.clear()
                LOG.warning("Traffic unavailable: %s", exc)

    @staticmethod
    def evidence(vid: str, pair: tuple, sources: dict) -> dict:
        """Map the tracked source PTS to the existing MP4 preview timeline."""
        offset = sources[vid]["seek_offset_sec"]
        if not math.isfinite(offset):
            raise ValueError("Invalid preview seek offset")
        return {
            "video_id": vid,
            "camera_id": sources[vid]["camera_id"],
            "frame_idx": pair[0],
            "timestamp_sec": pair[1],
            "video_seek_sec": pair[1] + offset,
            "video_url": f"/cctv/video/{vid}",
        }

    def available(self) -> bool:
        """Invalidate the snapshot on ordinary file edits/removal; restart to reload."""
        with self.lock:
            if self.ready:
                try:
                    for path, expected in self.watched.items():
                        reject_link(path)
                        stat = path.stat()
                        if expected != (
                            stat.st_size,
                            stat.st_mtime_ns,
                            stat.st_ctime_ns,
                        ):
                            raise ValueError(
                                "Traffic artifacts changed; restart after pipeline verification"
                            )
                except (OSError, ValueError) as exc:
                    self.ready, self.error = False, str(exc)
            return self.ready

    def status(self) -> dict:
        """Expose readiness separately from model/search and operational validation."""
        ready = self.available()
        return {
            "available": ready,
            "demo_only": self.manifest.get("demo_only") if ready else None,
            "roi_ready": bool(ready and self.manifest.get("roi_ready")),
            "thresholds_calibrated": False,
            "operational_ready": False,
            "windows": self.manifest.get("windows", 0) if ready else 0,
            "videos": sorted(self.video_rows.values(), key=lambda r: r["video_id"])
            if ready
            else [],
            "message": (
                "ROI nháp; ngưỡng chưa hiệu chỉnh. Chỉ dùng chạy thử."
                if ready and self.manifest["demo_only"]
                else "ROI đã duyệt; ngưỡng chưa hiệu chỉnh."
                if ready
                else "Thống kê giao thông chưa khả dụng. Kiểm tra output pipeline và khởi động lại demo."
            ),
        }
