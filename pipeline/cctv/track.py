"""Bounded ByteTrack segments from raw video with true PTS and atomic resume."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import math
import sqlite3
import tempfile
from collections import deque
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Iterator, Literal

import numpy as np
import yaml
from PIL import Image, ImageDraw
from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.detect import (
    CLASSES,
    YoloDetector,
    canonical,
    model_recipe,
    text_hash,
)
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.traffic import Contract, Locator, TrackObservation, load_config

LOG = logging.getLogger("cctv.track")
PIPELINE = "cctv-track-v1"


@dataclass
class DecodedFrame:
    """An owned RGB image, exact PTS timebase and global decoded ordinal."""

    frame_idx: int
    timestamp_sec: float
    source_pts: int
    origin_pts: int
    time_base: tuple[int, int]
    image: Image.Image


def decoded_frames(
    path: Path, start: float, duration: float | None
) -> Iterator[DecodedFrame]:
    """Decode from the first source frame; no approximate seek or FPS-generated PTS."""
    import av

    with av.open(str(path), mode="r") as container:
        if not container.streams.video:
            raise ValueError("Source has no video stream")
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 2
        origin, base, previous = None, None, -1.0
        for ordinal, frame in enumerate(container.decode(video=0)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError(f"Missing PTS at decoded frame {ordinal}")
            if origin is None:
                origin, base = frame.pts, frame.time_base
            if frame.time_base != base:
                raise ValueError("Timebase changed; refusing ambiguous locator")
            stamp = float((frame.pts - origin) * base)
            if not math.isfinite(stamp) or stamp <= previous:
                raise ValueError(f"Non-monotonic PTS at frame {ordinal}")
            previous = stamp
            if stamp < start:
                continue
            if duration is not None and stamp >= start + duration:
                break
            if frame.is_corrupt or frame.width * frame.height > 16_000_000:
                raise ValueError(f"Corrupt/oversized decoded frame {ordinal}")
            yield DecodedFrame(
                ordinal,
                stamp,
                int(frame.pts),
                int(origin),
                (base.numerator, base.denominator),
                frame.to_image(),
            )


class TrackFrame(Contract):
    """Frame ledger keeps empty observations and verifies rational PTS consistency."""

    schema_version: Literal[1]
    kind: Literal["track_frame"]
    run_id: str
    segment_id: str
    locator: Locator
    source_pts: int
    origin_pts: int
    time_base: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    observations: tuple[TrackObservation, ...]

    @model_validator(mode="after")
    def consistent(self):
        identity(self.run_id, "run_id")
        identity(self.segment_id, "segment_id")
        stamp = (
            (self.source_pts - self.origin_pts) * self.time_base[0] / self.time_base[1]
        )
        if not math.isclose(stamp, self.locator.timestamp_sec, rel_tol=0, abs_tol=1e-9):
            raise ValueError("Locator timestamp differs from source PTS")
        ids = set()
        for row in self.observations:
            if (
                row.locator != self.locator
                or row.run_id != self.run_id
                or row.segment_id != self.segment_id
                or row.track_id in ids
            ):
                raise ValueError(
                    "Observation scope differs or track ID repeats within frame"
                )
            ids.add(row.track_id)
        return self


class ByteTrackBackend(YoloDetector):
    """Reuse local YOLO setup, add CPU association without a second GPU model."""

    def __init__(self, weights: Path, device: str, recipe: dict, tracker_config: dict):
        super().__init__(weights, device, recipe)
        from ultralytics.trackers.byte_tracker import BYTETracker

        self.tracker_class = BYTETracker
        self.tracker_config = tracker_config

    def reset(self) -> None:
        """New segment resets Kalman state and IDs; IDs remain scoped by segment."""
        self.tracker = self.tracker_class(SimpleNamespace(**self.tracker_config))

    def process(self, image: Image.Image) -> list[dict]:
        """Feed every decoded frame once; low-score boxes can rescue existing tracks."""
        import torch

        with (
            torch.inference_mode(),
            torch.amp.autocast("cuda", enabled=self.device == "cuda"),
        ):
            result = self.model.predict(
                source=[image],
                device=0 if self.device == "cuda" else "cpu",
                **self.precision_args,
                imgsz=640,
                conf=0.1,
                iou=0.7,
                max_det=300,
                classes=list(CLASSES),
                batch=1,
                stream=False,
                verbose=False,
                save=False,
                save_txt=False,
                save_crop=False,
                show=False,
                augment=False,
                rect=False,
            )[0]
            tracks = self.tracker.update(result.boxes.cpu().numpy())
        output = []
        for row in tracks:
            if not np.isfinite(row).all():
                raise ValueError("Tracker emitted non-finite coordinates")
            x1, y1, x2, y2 = row[:4]
            bbox = [
                float(np.clip(x1 / image.width, 0, 1)),
                float(np.clip(y1 / image.height, 0, 1)),
                float(np.clip(x2 / image.width, 0, 1)),
                float(np.clip(y2 / image.height, 0, 1)),
            ]
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                # Kalman estimates may leave the image; do not expose invisible boxes.
                continue
            output.append(
                {
                    "track_id": int(row[4]),
                    "detection": {
                        "label": CLASSES[int(row[6])],
                        "confidence": float(row[5]),
                        "bbox": bbox,
                    },
                }
            )
        return output


class Segment:
    """Bounded frame payloads and trails; commit the entire segment or discard it."""

    def __init__(self, video_id: str, segment_id: str):
        self.video_id, self.segment_id = video_id, segment_id
        self.rows = []
        self.last_image = None
        self.last_record = None
        self.trails = {}
        self.failed = False

    def append(self, frame: DecodedFrame, record: TrackFrame) -> None:
        if len(self.rows) >= 2000:
            raise ValueError("Segment exceeds 2000 frames; reduce segment seconds")
        payload = canonical(record.model_dump(mode="json"))
        self.rows.append(
            (record.locator.frame_idx, record.locator.timestamp_sec, payload)
        )
        if self.last_image is not None:
            self.last_image.close()
        self.last_image, self.last_record = frame.image.copy(), record
        for row in record.observations:
            box = row.detection.bbox
            self.trails.setdefault(row.track_id, deque(maxlen=30)).append(
                ((box[0] + box[2]) / 2, box[3])
            )

    def preview(self) -> bytes:
        """Draw only active IDs and their last 30 bottom-center observations."""
        from io import BytesIO

        draw = ImageDraw.Draw(self.last_image)
        width, height = self.last_image.size
        for row in self.last_record.observations:
            color = (
                64 + row.track_id * 67 % 192,
                64 + row.track_id * 101 % 192,
                64 + row.track_id * 37 % 192,
            )
            box = row.detection.bbox
            xy = (
                box[0] * (width - 1),
                box[1] * (height - 1),
                box[2] * (width - 1),
                box[3] * (height - 1),
            )
            draw.rectangle(xy, outline=color, width=3)
            draw.text(
                (xy[0], max(0, xy[1] - 13)),
                f"ID {row.track_id} {row.detection.label}",
                fill=color,
                stroke_width=1,
                stroke_fill="black",
            )
            trail = [
                (x * (width - 1), y * (height - 1))
                for x, y in self.trails[row.track_id]
            ]
            if len(trail) > 1:
                draw.line(trail, fill=color, width=3)
        with BytesIO() as handle:
            self.last_image.save(handle, format="JPEG", quality=90)
            return handle.getvalue()

    def close(self) -> None:
        if self.last_image is not None:
            self.last_image.close()
            self.last_image = None


def commit_segment(conn, segment: Segment) -> None:
    """Atomically checkpoint all successful frames and the last-frame preview."""
    if segment.failed or not segment.rows:
        return
    signature = text_hash("".join(payload + "\n" for _, _, payload in segment.rows))
    preview = segment.preview()
    with conn:
        for index, stamp, payload in segment.rows:
            conn.execute(
                "INSERT INTO frames VALUES (?,?,?,?,?)",
                (segment.video_id, index, segment.segment_id, stamp, payload),
            )
        conn.execute(
            "INSERT INTO segments VALUES (?,?,?,?,?,?)",
            (
                segment.video_id,
                segment.segment_id,
                len(segment.rows),
                signature,
                preview,
                hashlib.sha256(preview).hexdigest(),
            ),
        )


def publish(conn, output: Path, state: dict) -> None:
    """Export ledgers, observations and previews through a recoverable journal."""
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=output) as folder:
        stage = Path(folder)
        count, observations = 0, 0
        with (
            (stage / "frames.jsonl").open(
                "w", encoding="utf-8", newline="\n"
            ) as frames,
            (stage / "tracks.jsonl").open(
                "w", encoding="utf-8", newline="\n"
            ) as tracks,
        ):
            for (payload,) in conn.execute(
                "SELECT payload FROM frames ORDER BY video_id,frame_idx"
            ):
                record = TrackFrame.model_validate_json(payload)
                frames.write(payload + "\n")
                count += 1
                for row in record.observations:
                    tracks.write(canonical(row.model_dump(mode="json")) + "\n")
                    observations += 1
        staged = {
            "frames.jsonl": stage / "frames.jsonl",
            "tracks.jsonl": stage / "tracks.jsonl",
        }
        for vid, sid, preview in conn.execute(
            "SELECT video_id,segment_id,preview FROM segments"
        ):
            path = stage / f"{vid}-{sid}.jpg"
            path.write_bytes(preview)
            staged[f"previews/{vid}-{sid}.jpg"] = path
        manifest = {
            "pipeline": PIPELINE,
            "signature": state["signature"],
            "run_id": state["run_id"],
            "frames": count,
            "observations": observations,
            "segments": len(staged) - 2,
            "videos": sorted(state["videos_done"]),
            "memory": state.get("memory", {}),
            "roi_ready": state["roi_ready"],
            "tracking_ready": True,
            "congestion_ready": False,
        }
        save_json(stage / "tracking_manifest.json", manifest)
        staged["tracking_manifest.json"] = stage / "tracking_manifest.json"
        artifacts = {name: digest(path) for name, path in staged.items()}
        artifacts["tracks.db"] = digest(output / "tracks.db")
        if state.get("publication") and state["publication"] != artifacts:
            raise ValueError("Interrupted tracking publication differs; preserved")
        for name in staged:
            target = output / name
            reject_link(target)
            if target.exists() and (
                not state.get("publication") or digest(target) != artifacts[name]
            ):
                raise ValueError(f"Unowned/changed tracking output preserved: {name}")
        state.update(status="publishing", publication=artifacts)
        save_json(output / "track_state.json", state)
        reject_link(output / "previews")
        (output / "previews").mkdir(exist_ok=True)
        for name, path in staged.items():
            if not (output / name).exists():
                path.replace(output / name)
        state["status"] = "complete"
        save_json(output / "track_state.json", state)


def track_videos(
    manifest_path: Path,
    traffic_path: Path,
    model_path: Path,
    tracker_path: Path,
    output: Path,
    start: float = 0.0,
    duration: float | None = 10.0,
    segment_seconds: float = 10.0,
    device: str = "cuda",
    video_ids: list[str] | None = None,
    backend_factory=ByteTrackBackend,
    reader_factory=decoded_frames,
) -> dict:
    """Resume committed segments; replay incomplete segments with fresh tracker IDs."""
    if (
        device not in {"cuda", "cpu"}
        or not math.isfinite(start)
        or start < 0
        or not math.isfinite(segment_seconds)
        or not 1 <= segment_seconds <= 60
        or (duration is not None and (not math.isfinite(duration) or duration <= 0))
    ):
        raise ValueError("Invalid device, start/duration or segment seconds (1-60)")
    manifest = load_manifest(manifest_path)
    traffic = load_config(traffic_path, manifest)
    videos = [
        v for v in manifest.videos if video_ids is None or v.video_id in video_ids
    ]
    if not videos or (
        video_ids is not None and set(video_ids) != {v.video_id for v in videos}
    ):
        raise ValueError("Select known video IDs")
    tracker_config = yaml.safe_load(tracker_path.read_text(encoding="utf-8-sig"))
    if tracker_config != {
        "tracker_type": "bytetrack",
        "track_high_thresh": 0.25,
        "track_low_thresh": 0.1,
        "new_track_thresh": 0.25,
        "track_buffer": 30,
        "match_thresh": 0.8,
        "fuse_score": True,
    }:
        raise ValueError("Tracker config must match the validated ByteTrack baseline")
    recipe, weights = model_recipe(model_path, device, 1, 1)
    recipe.update(
        confidence=0.1,
        tracker_config_sha256=digest(tracker_path),
        track_code_sha256=digest(Path(__file__)),
        av=importlib.metadata.version("av"),
        lap=importlib.metadata.version("lap"),
        decoder="pyav-from-origin-v1",
    )
    output = output.absolute()
    reject_link(output)
    for protected in (
        manifest.raw_root,
        manifest_path.resolve().parent,
        traffic_path.resolve().parent,
        model_path.resolve().parent,
        tracker_path.resolve().parent,
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError(
                "Tracking output overlaps protected input/config/model folders"
            )
    source_hashes = {
        v.video_id: digest(v.source)
        for v in tqdm(videos, desc="Hash raw videos", unit="video")
    }
    signature = json.loads(
        canonical(
            {
                "pipeline": PIPELINE,
                "manifest_sha256": digest(manifest_path),
                "traffic_sha256": digest(traffic_path),
                "sources": source_hashes,
                "recipe": recipe,
                "start_sec": start,
                "duration_sec": duration,
                "segment_seconds": segment_seconds,
            }
        )
    )
    state_path, database = output / "track_state.json", output / "tracks.db"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign tracking output preserved")
    output.mkdir(parents=True, exist_ok=True)
    for path in (state_path, database, output / ".ingest.lock", output / "track.log"):
        reject_link(path)
    result = {
        "segments_done": 0,
        "frames_done": 0,
        "frames_skipped": 0,
        "error": 0,
        "model_loaded": False,
    }
    with output_lease(output):
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {
                "signature": signature,
                "run_id": "track-" + text_hash(canonical(signature))[:16],
                "status": "processing",
                "videos_done": [],
                "roi_ready": all(c.status == "approved" for c in traffic.cameras),
            }
        )
        if state.get("signature") != signature:
            raise ValueError("Tracking input/recipe changed; use a NEW output")
        if state["status"] == "complete":
            for name, checksum in state["publication"].items():
                reject_link(output / name)
                if digest(output / name) != checksum:
                    raise ValueError(f"Tracking artifact changed: {name}")
            with closing(
                sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            ) as conn:
                result["frames_skipped"] = conn.execute(
                    "SELECT count(*) FROM frames"
                ).fetchone()[0]
            return result
        save_json(state_path, state)
        handler = logging.FileHandler(output / "track.log", encoding="utf-8")
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        backend = None
        segment = None
        conn = sqlite3.connect(database)
        try:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not tables.issubset({"metadata", "frames", "segments"}):
                raise ValueError("Foreign tracking database preserved")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS metadata (signature TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS frames (video_id TEXT,frame_idx INTEGER,segment_id TEXT,timestamp_sec REAL,payload TEXT,PRIMARY KEY(video_id,frame_idx))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS segments (video_id TEXT,segment_id TEXT,frames INTEGER,sha256 TEXT,preview BLOB,preview_sha256 TEXT,PRIMARY KEY(video_id,segment_id))"
            )
            saved = conn.execute("SELECT signature FROM metadata").fetchall()
            if not saved:
                conn.execute("INSERT INTO metadata VALUES (?)", (canonical(signature),))
                conn.commit()
            elif saved != [(canonical(signature),)]:
                raise ValueError("Tracking DB signature mismatch")
            completed = set()
            selected = {v.video_id for v in videos}
            verified_frames = 0
            for vid, sid, count, checksum, preview, preview_checksum in conn.execute(
                "SELECT * FROM segments"
            ):
                identity(vid, "video_id")
                identity(sid, "segment_id")
                rows = conn.execute(
                    "SELECT frame_idx,timestamp_sec,payload FROM frames WHERE video_id=? AND segment_id=? ORDER BY frame_idx",
                    (vid, sid),
                ).fetchall()
                if (
                    vid not in selected
                    or count != len(rows)
                    or checksum != text_hash("".join(r[2] + "\n" for r in rows))
                ):
                    raise ValueError("Corrupt segment checkpoint preserved")
                for frame_idx, timestamp, payload in rows:
                    record = TrackFrame.model_validate_json(payload)
                    record.locator.bind(manifest)
                    if (
                        record.run_id != state["run_id"]
                        or record.segment_id != sid
                        or record.locator.video_id != vid
                        or record.locator.frame_idx != frame_idx
                        or record.locator.timestamp_sec != timestamp
                    ):
                        raise ValueError("Segment scope differs from checkpoint")
                if (
                    not preview
                    or hashlib.sha256(preview).hexdigest() != preview_checksum
                ):
                    raise ValueError("Checkpoint preview missing or changed")
                completed.add((vid, sid))
                verified_frames += count
            if (
                verified_frames
                != conn.execute("SELECT count(*) FROM frames").fetchone()[0]
            ):
                raise ValueError("Orphan tracking frames preserved")
            errors = {}
            for video in tqdm(videos, desc="Track videos", unit="video"):
                if video.video_id in state["videos_done"]:
                    result["frames_skipped"] += conn.execute(
                        "SELECT count(*) FROM frames WHERE video_id=?",
                        (video.video_id,),
                    ).fetchone()[0]
                    continue
                current, seen, video_failed, previous = None, 0, False, None
                try:
                    with (
                        closing(
                            reader_factory(video.source, start, duration)
                        ) as reader,
                        tqdm(
                            desc=video.video_id, unit="frame", leave=False
                        ) as progress,
                    ):
                        for frame in reader:
                            try:
                                sid = f"seg-{int((frame.timestamp_sec - start) // segment_seconds):06d}"
                                if sid != current:
                                    if segment is not None:
                                        commit_segment(conn, segment)
                                        if not segment.failed:
                                            result["segments_done"] += 1
                                            result["frames_done"] += len(segment.rows)
                                        segment.close()
                                    current = sid
                                    segment = (
                                        None
                                        if (video.video_id, sid) in completed
                                        else Segment(video.video_id, sid)
                                    )
                                    if segment is not None:
                                        if backend is None:
                                            backend = backend_factory(
                                                weights, device, recipe, tracker_config
                                            )
                                            result["model_loaded"] = True
                                        backend.reset()
                                seen += 1
                                progress.update()
                                if segment is None:
                                    result["frames_skipped"] += 1
                                elif not segment.failed:
                                    try:
                                        if (
                                            previous is not None
                                            and frame.timestamp_sec - previous > 0.5
                                        ):
                                            raise ValueError(
                                                "PTS gap exceeds 0.5s; refusing to connect tracks"
                                            )
                                        locator = {
                                            "camera_id": video.camera_id,
                                            "video_id": video.video_id,
                                            "timestamp_sec": frame.timestamp_sec,
                                            "frame_idx": frame.frame_idx,
                                        }
                                        observations = [
                                            {
                                                "schema_version": 1,
                                                "kind": "track",
                                                "run_id": state["run_id"],
                                                "segment_id": sid,
                                                "locator": locator,
                                                **row,
                                            }
                                            for row in backend.process(frame.image)
                                        ]
                                        record = TrackFrame.model_validate_json(
                                            canonical(
                                                {
                                                    "schema_version": 1,
                                                    "kind": "track_frame",
                                                    "run_id": state["run_id"],
                                                    "segment_id": sid,
                                                    "locator": locator,
                                                    "source_pts": frame.source_pts,
                                                    "origin_pts": frame.origin_pts,
                                                    "time_base": frame.time_base,
                                                    "width": frame.image.width,
                                                    "height": frame.image.height,
                                                    "observations": observations,
                                                }
                                            )
                                        )
                                        segment.append(frame, record)
                                    except Exception as exc:
                                        LOG.exception(
                                            "frame_error | %s | %s",
                                            video.video_id,
                                            frame.frame_idx,
                                        )
                                        errors[f"{video.video_id}/{sid}"] = str(exc)
                                        segment.failed, video_failed = True, True
                                previous = frame.timestamp_sec
                            finally:
                                frame.image.close()
                    if not seen:
                        raise ValueError("No decoded frames in requested clip")
                    if segment is not None:
                        commit_segment(conn, segment)
                        if not segment.failed:
                            result["segments_done"] += 1
                            result["frames_done"] += len(segment.rows)
                        segment.close()
                        segment = None
                    if digest(video.source) != source_hashes[video.video_id]:
                        raise ValueError("Raw source changed during tracking")
                    if not video_failed:
                        state["videos_done"].append(video.video_id)
                        save_json(state_path, state)
                except Exception as exc:
                    errors[video.video_id] = str(exc)
                    LOG.exception("video_error | %s", video.video_id)
                finally:
                    if segment is not None:
                        segment.close()
                        segment = None
            state["errors"] = errors
            result["error"] = len(errors)
            if backend is not None:
                state["memory"] = backend.memory()
            save_json(state_path, state)
            if errors:
                return result
            conn.close()
            conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
            publish(conn, output, state)
            LOG.info("complete | %s", result)
            return result
        finally:
            conn.close()
            if segment is not None:
                segment.close()
            if backend is not None:
                backend.close()
            LOG.removeHandler(handler)
            handler.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--traffic-config", required=True, type=Path)
    parser.add_argument("--model-config", required=True, type=Path)
    parser.add_argument("--tracker-config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--start-sec", type=float, default=0)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--duration-sec", type=float, default=10)
    duration.add_argument("--full-video", action="store_true")
    parser.add_argument("--segment-seconds", type=float, default=10)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--video-id", action="append")
    args = parser.parse_args()
    try:
        result = track_videos(
            args.manifest,
            args.traffic_config,
            args.model_config,
            args.tracker_config,
            args.output_dir,
            args.start_sec,
            None if args.full_video else args.duration_sec,
            args.segment_seconds,
            args.device,
            args.video_id,
        )
        print(json.dumps(result, indent=2))
        return 1 if result["error"] else 0
    except KeyboardInterrupt:
        parser.exit(
            130,
            "Interrupted; committed segments retained, partial segment replayed on resume.\n",
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        ModuleNotFoundError,
    ) as exc:
        parser.exit(1, f"Tracking error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
