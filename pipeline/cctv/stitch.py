"""Geometric segment-boundary hypotheses from an immutable tracking publication.

This is not validated reidentification and does not change analytics continuity.
Source paths cannot be recovered from publication hashes: callers must choose a
separate derived output, outside their raw/config/model directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import tempfile
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import scipy
from scipy.optimize import linear_sum_assignment

from pipeline.cctv.detect import canonical
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity
from pipeline.cctv.track import PIPELINE as TRACK_PIPELINE, TrackFrame

PIPELINE = "cctv-stitch-v1"
ARTIFACTS = {"stitched_tracks.jsonl", "stitched_mapping.json"}
MAX_ENDPOINT_TRACKS = 512
MAX_FRAME_OBSERVATIONS = 300
MAX_FRAME_PAYLOAD_BYTES = 1024 * 1024
WINDOW_SEC = 1.0
MAX_SHAPE_RATIO = 1.8
MIN_DIRECTION_COSINE = 0.5
TrackKey = tuple[str, str, str, str, int]


def _hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _checksum(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value
    )


def _read_json(path: Path) -> dict:
    reject_link(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read publication: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid publication object: {path.name}")
    return value


def _tracking(tracking_dir: Path) -> tuple[dict, dict, str]:
    """Verify every sealed original artifact before opening its database."""
    reject_link(tracking_dir)
    state_path = tracking_dir / "track_state.json"
    reject_link(state_path)
    try:
        state_hash = digest(state_path)
    except OSError as exc:
        raise ValueError("Cannot read complete tracking state") from exc
    state = _read_json(state_path)
    signature, publication = state.get("signature"), state.get("publication")
    if (
        state.get("status") != "complete"
        or not isinstance(signature, dict)
        or signature.get("pipeline") != TRACK_PIPELINE
        or not isinstance(publication, dict)
        or not {"tracks.db", "frames.jsonl", "tracks.jsonl", "tracking_manifest.json"}
        <= publication.keys()
    ):
        raise ValueError("Tracking must be a complete original publication")
    identity(state.get("run_id"), "run_id")
    for name, checksum in publication.items():
        if not isinstance(name, str) or not _checksum(checksum):
            raise ValueError("Invalid tracking publication entry")
        parts = name.split("/")
        if name not in {"tracks.db", "frames.jsonl", "tracks.jsonl", "tracking_manifest.json"} and (
            len(parts) != 2 or parts[0] != "previews" or not parts[1].endswith(".jpg")
        ):
            raise ValueError("Unexpected tracking artifact path")
        if any(p in {"", ".", ".."} or "\\" in p or ":" in p for p in parts):
            raise ValueError("Unsafe tracking artifact path")
        path = tracking_dir / name
        reject_link(path)
        if not path.is_file() or digest(path) != checksum:
            raise ValueError(f"Tracking input changed: {name}")
    for suffix in ("-wal", "-shm", "-journal"):
        if (tracking_dir / ("tracks.db" + suffix)).exists():
            raise ValueError("Tracking database must be a standalone sealed snapshot")
    manifest = _read_json(tracking_dir / "tracking_manifest.json")
    videos = manifest.get("videos")
    if (
        manifest.get("pipeline") != TRACK_PIPELINE
        or manifest.get("signature") != signature
        or manifest.get("run_id") != state["run_id"]
        or manifest.get("tracking_ready") is not True
        or not isinstance(videos, list)
        or not videos
        or any(not isinstance(v, str) for v in videos)
        or videos != sorted(set(videos))
        or not isinstance(state.get("videos_done"), list)
        or any(not isinstance(v, str) for v in state["videos_done"])
        or sorted(state.get("videos_done", [])) != videos
        or any(type(manifest.get(k)) is not int or manifest[k] < 0 for k in ("frames", "observations", "segments"))
    ):
        raise ValueError("Tracking manifest/state mismatch")
    for video in videos:
        identity(video, "video_id")
    start, segment = signature.get("start_sec"), signature.get("segment_seconds")
    duration = signature.get("duration_sec")
    if (
        type(start) not in (int, float) or not math.isfinite(start) or start < 0
        or type(segment) not in (int, float) or not math.isfinite(segment) or not 1 <= segment <= 60
        or (duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0))
        or signature.get("sample_interval_sec", 0) not in (0, 0.5)
    ):
        raise ValueError("Invalid tracking clip/segment recipe")
    if digest(state_path) != state_hash:
        raise ValueError("Tracking state changed during publication validation")
    return state, manifest, state_hash


def _source(tracking_dir: Path) -> sqlite3.Connection:
    # immutable prevents SQLite from creating sidecars or consuming an unsealed WAL.
    return sqlite3.connect(
        f"{(tracking_dir / 'tracks.db').as_uri()}?mode=ro&immutable=1", uri=True
    )


def _records(conn: sqlite3.Connection, state: dict, manifest: dict) -> Iterator[TrackFrame]:
    """Validate ledger/checkpoints while retaining only one decoded JSON frame."""
    if {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {"metadata", "frames", "segments"}:
        raise ValueError("Unexpected tracking database tables")
    metadata = conn.execute("SELECT signature FROM metadata")
    if metadata.fetchone() != (canonical(state["signature"]),) or metadata.fetchone() is not None:
        raise ValueError("Tracking database signature mismatch")
    if conn.execute("SELECT count(*) FROM segments").fetchone()[0] != manifest["segments"]:
        raise ValueError("Tracking segment coverage mismatch")
    if set(state["publication"]) != {
        "tracks.db", "frames.jsonl", "tracks.jsonl", "tracking_manifest.json",
        *(f"previews/{v}-{s}.jpg" for v, s in conn.execute("SELECT video_id,segment_id FROM segments")),
    }:
        raise ValueError("Tracking preview coverage mismatch")
    if [r[0] for r in conn.execute("SELECT DISTINCT video_id FROM frames ORDER BY video_id")] != manifest["videos"]:
        raise ValueError("Tracking video coverage mismatch")
    frames_hash, tracks_hash = hashlib.sha256(), hashlib.sha256()
    frame_count = observation_count = segment_count = 0
    current_segment = previous = video_scope = None
    segment_hash, segment_frames = hashlib.sha256(), 0

    def finish_segment() -> None:
        nonlocal segment_count
        if current_segment is None:
            return
        checkpoint = conn.execute(
            "SELECT frames,sha256,preview_sha256,rowid FROM segments WHERE video_id=? AND segment_id=?",
            current_segment,
        ).fetchone()
        if checkpoint is None or checkpoint[0] != segment_frames or checkpoint[1] != segment_hash.hexdigest():
            raise ValueError("Tracking segment checkpoint mismatch")
        preview_digest = hashlib.sha256()
        with conn.blobopen("segments", "preview", checkpoint[3], readonly=True) as preview:
            for block in iter(lambda: preview.read(65536), b""):
                preview_digest.update(block)
        preview_hash = preview_digest.hexdigest()
        if preview_hash != checkpoint[2] or preview_hash != state["publication"].get(f"previews/{current_segment[0]}-{current_segment[1]}.jpg"):
            raise ValueError("Tracking checkpoint preview mismatch")
        segment_count += 1

    start, length = state["signature"]["start_sec"], state["signature"]["segment_seconds"]
    duration = state["signature"]["duration_sec"]
    for video, index, sid, stamp, payload in conn.execute(
        "SELECT video_id,frame_idx,segment_id,timestamp_sec,"
        "CASE WHEN length(CAST(payload AS BLOB))<=? THEN payload ELSE NULL END "
        "FROM frames ORDER BY video_id,frame_idx",
        (MAX_FRAME_PAYLOAD_BYTES,),
    ):
        if not isinstance(payload, str):
            raise ValueError("Tracking frame payload exceeds memory bound")
        frame = TrackFrame.model_validate_json(payload)
        if len(frame.observations) > MAX_FRAME_OBSERVATIONS or frame.width * frame.height > 16_000_000:
            raise ValueError("Tracking frame exceeds the native tracker memory bounds")
        loc = frame.locator
        bucket = int((loc.timestamp_sec - start) // length)
        if (
            frame.run_id != state["run_id"] or loc.video_id != video
            or loc.frame_idx != index or loc.timestamp_sec != stamp
            or frame.segment_id != sid or sid != f"seg-{bucket:06d}"
            or stamp < start or (duration is not None and stamp >= start + duration)
        ):
            raise ValueError("Tracking row/record scope mismatch")
        scope = (frame.run_id, loc.camera_id, video, frame.width, frame.height)
        if previous is not None and previous[0] == video:
            if scope != video_scope or index <= previous[1] or stamp <= previous[2]:
                raise ValueError("Tracking video scope/PTS changed")
            if not state["signature"].get("sample_interval_sec", 0) and index != previous[1] + 1:
                raise ValueError("Tracking frame ordinals are not consecutive")
        else:
            video_scope = scope
        previous = (video, index, stamp)
        if current_segment != (video, sid):
            finish_segment()
            current_segment = (video, sid)
            segment_hash, segment_frames = hashlib.sha256(), 0
        encoded = (payload + "\n").encode("utf-8")
        frames_hash.update(encoded)
        segment_hash.update(encoded)
        segment_frames += 1
        frame_count += 1
        for obs in frame.observations:
            tracks_hash.update((canonical(obs.model_dump(mode="json")) + "\n").encode("utf-8"))
            observation_count += 1
        yield frame
    finish_segment()
    if (
        frame_count != manifest["frames"] or observation_count != manifest["observations"]
        or segment_count != manifest["segments"]
        or frames_hash.hexdigest() != state["publication"]["frames.jsonl"]
        or tracks_hash.hexdigest() != state["publication"]["tracks.jsonl"]
    ):
        raise ValueError("Tracking ledger/observation coverage mismatch")


@dataclass(frozen=True)
class Sample:
    stamp: float
    point: tuple[float, float]
    diagonal: float
    area: float
    aspect: float
    label: str


@dataclass
class Endpoint:
    first: Sample
    samples: deque[Sample] = field(default_factory=lambda: deque(maxlen=3))

    def velocity(self) -> tuple[float, float]:
        if len(self.samples) < 2:
            return (0.0, 0.0)
        first, last = self.samples[0], self.samples[-1]
        dt = last.stamp - first.stamp
        return ((last.point[0] - first.point[0]) / dt, (last.point[1] - first.point[1]) / dt)


def _sample(obs, frame: TrackFrame) -> Sample:
    x1, y1, x2, y2 = obs.detection.bbox
    width, height = (x2 - x1) * frame.width, (y2 - y1) * frame.height
    diagonal, area, aspect = math.hypot(width, height), width * height, width / height
    if any(not math.isfinite(v) or v <= 0 for v in (diagonal, area, aspect)):
        raise ValueError("Non-finite or degenerate endpoint geometry")
    return Sample(
        obs.locator.timestamp_sec, ((x1 + x2) * frame.width / 2, y2 * frame.height),
        diagonal, area, aspect, obs.detection.label,
    )


def _retain(window: dict[TrackKey, Endpoint], key: TrackKey, sample: Sample) -> None:
    if key not in window:
        if len(window) >= MAX_ENDPOINT_TRACKS:
            raise ValueError("Boundary tracklets exceed memory bound; use a NEW tracking recipe")
        window[key] = Endpoint(sample)
    window[key].samples.append(sample)


def _cost(outgoing: Endpoint, incoming: Endpoint, gap_limit: float, distance_limit: float) -> float:
    last, first = outgoing.samples[-1], incoming.first
    gap = first.stamp - last.stamp
    compatible = last.label == first.label or {last.label, first.label} <= {"car", "truck"}
    if not 0 < gap <= gap_limit or not compatible:
        return math.inf
    if max(last.area, first.area) / min(last.area, first.area) > MAX_SHAPE_RATIO or max(last.aspect, first.aspect) / min(last.aspect, first.aspect) > MAX_SHAPE_RATIO:
        return math.inf
    velocity, incoming_velocity = outgoing.velocity(), incoming.velocity()
    speed, incoming_speed = math.hypot(*velocity), math.hypot(*incoming_velocity)
    scale = (last.diagonal + first.diagonal) / 2
    if speed / scale > 0.04 and incoming_speed / scale > 0.04:
        cosine = sum(a * b for a, b in zip(velocity, incoming_velocity)) / (speed * incoming_speed)
        if cosine < MIN_DIRECTION_COSINE:
            return math.inf
    predicted = tuple(p + v * gap for p, v in zip(last.point, velocity))
    residual = math.dist(predicted, first.point) / scale
    return residual if residual <= distance_limit else math.inf


def _matches(tail: dict[TrackKey, Endpoint], head: dict[TrackKey, Endpoint], max_gap: float, max_distance: float) -> list[tuple[TrackKey, TrackKey]]:
    """Maximum-cardinality gated assignment, discarding non-unique optimal edges."""
    outgoing, incoming = sorted(tail), sorted(head)
    if not outgoing or not incoming:
        return []
    n, m = len(outgoing), len(incoming)
    # Each outgoing row can remain unmatched. Invalid edges are never selected;
    # unmatched penalty dominates all possible valid residuals together.
    penalty = (n + 1) * (max_distance + 1)
    costs = np.full((n, m + n), penalty, dtype=np.float64)
    for i, key in enumerate(outgoing):
        for j, other in enumerate(incoming):
            costs[i, j] = _cost(tail[key], head[other], max_gap, max_distance)
    rows, columns = linear_sum_assignment(costs)
    best = float(costs[rows, columns].sum())
    tolerance = 1e-9 * max(1, n)
    result = []
    for i, j in zip(rows, columns):
        if j >= m:
            continue
        value = costs[i, j]
        # Isolated edges cannot have an alternative equally optimal assignment.
        if np.isfinite(costs[i, :m]).sum() > 1 or np.isfinite(costs[:, j]).sum() > 1:
            costs[i, j] = math.inf
            alt_rows, alt_columns = linear_sum_assignment(costs)
            alternative = float(costs[alt_rows, alt_columns].sum())
            costs[i, j] = value
            if abs(alternative - best) <= tolerance:
                continue
        result.append((outgoing[i], incoming[j]))
    return result


def _derive(source, target, state, manifest, max_gap, max_distance) -> tuple[int, int]:
    target.execute(
        "CREATE TABLE mapping (run_id TEXT,camera_id TEXT,video_id TEXT,segment_id TEXT,track_id INTEGER,global_track_id INTEGER,PRIMARY KEY(run_id,camera_id,video_id,segment_id,track_id))"
    )
    next_id, links = 1, 0
    current = previous = None
    head, tail, previous_tail = {}, {}, {}

    def finish() -> None:
        nonlocal next_id, links, previous, previous_tail
        if current is None:
            return
        if previous is not None and current[:3] == previous[:3] and current[3] == previous[3] + 1:
            for old, new in _matches(previous_tail, head, max_gap, max_distance):
                global_id = target.execute(
                    "SELECT global_track_id FROM mapping WHERE run_id=? AND camera_id=? AND video_id=? AND segment_id=? AND track_id=?", old,
                ).fetchone()[0]
                target.execute(
                    "UPDATE mapping SET global_track_id=? WHERE run_id=? AND camera_id=? AND video_id=? AND segment_id=? AND track_id=?",
                    (global_id, *new),
                )
                links += 1
        sid = f"seg-{current[3]:06d}"
        for (track_id,) in target.execute(
            "SELECT track_id FROM mapping WHERE run_id=? AND camera_id=? AND video_id=? AND segment_id=? AND global_track_id IS NULL ORDER BY track_id",
            (*current[:3], sid),
        ):
            target.execute(
                "UPDATE mapping SET global_track_id=? WHERE run_id=? AND camera_id=? AND video_id=? AND segment_id=? AND track_id=?",
                (next_id, *current[:3], sid, track_id),
            )
            next_id += 1
        previous, previous_tail = current, tail

    start, length = state["signature"]["start_sec"], state["signature"]["segment_seconds"]
    with target:
        for frame in _records(source, state, manifest):
            loc = frame.locator
            bucket = int((loc.timestamp_sec - start) // length)
            scope = (frame.run_id, loc.camera_id, loc.video_id, bucket)
            if current != scope:
                finish()
                current, head, tail = scope, {}, {}
            boundary = start + bucket * length
            for obs in frame.observations:
                key = obs.track_key
                target.execute("INSERT OR IGNORE INTO mapping VALUES (?,?,?,?,?,NULL)", key)
                if loc.timestamp_sec < boundary + WINDOW_SEC:
                    _retain(head, key, _sample(obs, frame))
                if loc.timestamp_sec >= boundary + length - WINDOW_SEC:
                    _retain(tail, key, _sample(obs, frame))
        finish()
    return next_id - 1, links


def _signature(state: dict, state_hash: str, max_gap: float, max_distance: float) -> dict:
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (max_gap, max_distance)):
        raise ValueError("Stitching gates must be finite positive numbers")
    # Keep assignment penalties finite, even for an otherwise finite huge gate.
    if not math.isfinite(MAX_ENDPOINT_TRACKS * (MAX_ENDPOINT_TRACKS + 1) * (max_distance + 1)):
        raise ValueError("Stitching distance gate exceeds numerical assignment range")
    return {
        "pipeline": PIPELINE, "tracking_state_sha256": state_hash,
        "tracking_publication": state["publication"], "run_id": state["run_id"],
        "max_gap_sec": float(max_gap), "max_distance_diagonals": float(max_distance),
        "endpoint_window_sec": WINDOW_SEC, "endpoint_samples": 3,
        "max_endpoint_tracks": MAX_ENDPOINT_TRACKS, "max_shape_ratio": MAX_SHAPE_RATIO,
        "max_frame_observations": MAX_FRAME_OBSERVATIONS,
        "max_frame_payload_bytes": MAX_FRAME_PAYLOAD_BYTES,
        "min_direction_cosine": MIN_DIRECTION_COSINE,
        "code_sha256": digest(Path(__file__)),
        "contracts": {name: digest(Path(__file__).with_name(name)) for name in ("track.py", "traffic.py", "ingest.py", "detect.py")},
        "numpy": np.__version__, "scipy": scipy.__version__,
    }


def _save_state(path: Path, state: dict) -> None:
    state["seal_sha256"] = _hash({k: v for k, v in state.items() if k != "seal_sha256"})
    save_json(path, state)


def _state(path: Path) -> dict:
    state = _read_json(path)
    if state.get("seal_sha256") != _hash({k: v for k, v in state.items() if k != "seal_sha256"}):
        raise ValueError("Stitch state seal mismatch; preserved")
    if type(state.get("schema_version")) is not int or state["schema_version"] != 1 or state.get("pipeline") != PIPELINE:
        raise ValueError("Invalid stitch publication state")
    expected = {
        "schema_version", "pipeline", "signature", "run_id",
        "tracking_state_sha256", "status", "seal_sha256",
    }
    if state.get("status") in {"publishing", "complete"}:
        expected |= {"artifacts", "tracks", "global_tracks", "links", "observations"}
        if any(type(state.get(k)) is not int or state[k] < 0 for k in ("tracks", "observations", "global_tracks", "links")):
            raise ValueError("Invalid stitch publication counts")
    elif state.get("status") != "processing":
        raise ValueError("Invalid stitch publication status")
    if set(state) != expected or not isinstance(state.get("signature"), dict) or not _checksum(state.get("tracking_state_sha256")):
        raise ValueError("Invalid stitch publication fields")
    identity(state.get("run_id"), "run_id")
    return state


def _verify_artifacts(output: Path, state: dict) -> None:
    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACTS:
        raise ValueError("Incomplete stitching artifact seal")
    for name, checksum in artifacts.items():
        path = output / name
        reject_link(path)
        if not _checksum(checksum) or not path.is_file() or digest(path) != checksum:
            raise ValueError(f"Stitch artifact changed: {name}")


def load_mapping(stitching_dir: Path, tracking_dir: Path) -> dict[TrackKey, int]:
    """Fail closed on incomplete/tampered output or any five-field input mismatch."""
    stitching_dir, tracking_dir = stitching_dir.absolute(), tracking_dir.absolute()
    reject_link(stitching_dir)
    tracking, manifest, state_hash = _tracking(tracking_dir)
    state = _state(stitching_dir / "stitch_state.json")
    recipe = state.get("signature", {})
    if (
        state.get("status") != "complete" or state.get("run_id") != tracking["run_id"]
        or state.get("tracking_state_sha256") != state_hash
        or not isinstance(recipe, dict)
        or recipe != _signature(tracking, state_hash, recipe.get("max_gap_sec"), recipe.get("max_distance_diagonals"))
    ):
        raise ValueError("Stitch publication/input signature mismatch or incomplete publication")
    _verify_artifacts(stitching_dir, state)
    document = _read_json(stitching_dir / "stitched_mapping.json")
    if set(document) != {"schema_version", "run_id", "tracking_state_sha256", "mapping"} or type(document.get("schema_version")) is not int or document["schema_version"] != 1 or document.get("run_id") != tracking["run_id"] or document.get("tracking_state_sha256") != state_hash or not isinstance(document.get("mapping"), list):
        raise ValueError("Stitch mapping/input binding mismatch")
    mapping, owners, segment_ids = {}, {}, set()
    fields = ("run_id", "camera_id", "video_id", "segment_id", "track_id", "global_track_id")
    for row in document["mapping"]:
        if not isinstance(row, dict) or set(row) != set(fields):
            raise ValueError("Invalid stitch mapping scope")
        key = tuple(row[k] for k in fields[:5])
        for value, name in zip(key[:4], fields[:4]):
            identity(value, name)
        gid = row["global_track_id"]
        if type(key[4]) is not int or key[4] < 0 or type(gid) is not int or gid < 1 or key in mapping or key[0] != tracking["run_id"]:
            raise ValueError("Invalid/duplicate stitch mapping identity")
        owner = key[:3]
        if owners.setdefault(gid, owner) != owner or (*key[:4], gid) in segment_ids:
            raise ValueError("Global identity crosses scope or collides within a segment")
        segment_ids.add((*key[:4], gid))
        mapping[key] = gid
    missing = set(mapping)
    observations = 0
    with closing(_source(tracking_dir)) as source, (stitching_dir / "stitched_tracks.jsonl").open(encoding="utf-8") as stitched:
        for frame in _records(source, tracking, manifest):
            for obs in frame.observations:
                if obs.track_key not in mapping:
                    raise ValueError("Stitch mapping missing original local scope")
                missing.discard(obs.track_key)
                expected = {**obs.model_dump(mode="json"), "global_track_id": mapping[obs.track_key]}
                try:
                    line = stitched.readline(MAX_FRAME_PAYLOAD_BYTES + 1)
                    if not line or len(line) > MAX_FRAME_PAYLOAD_BYTES:
                        raise ValueError("Stitched observation exceeds ledger memory bound or is missing")
                    actual = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError("Incomplete/invalid stitched observation ledger") from exc
                if actual != expected:
                    raise ValueError("Stitched observation differs from original input/mapping")
                observations += 1
        if stitched.read(1) or missing:
            raise ValueError("Stitch publication contains foreign scopes/observations")
    if state.get("tracks") != len(mapping) or state.get("observations") != observations or state.get("global_tracks") != len(owners):
        raise ValueError("Stitch publication coverage mismatch")
    if _tracking(tracking_dir)[2] != state_hash:
        raise ValueError("Tracking publication changed during mapping validation")
    _verify_artifacts(stitching_dir, state)
    if _state(stitching_dir / "stitch_state.json") != state:
        raise ValueError("Stitch publication changed during mapping validation")
    return mapping


def stitch_tracks(tracking_dir: Path, output_dir: Path, max_gap_sec: float = 1.0, max_distance_diagonals: float = 2.5) -> Path:
    """Stream native PTS boundaries, seal derived files, resume without overwrites."""
    tracking_dir, output_dir = tracking_dir.absolute(), output_dir.absolute()
    tracking, manifest, state_hash = _tracking(tracking_dir)
    signature = _signature(tracking, state_hash, max_gap_sec, max_distance_diagonals)
    reject_link(output_dir)
    for protected in (tracking_dir, Path(__file__).resolve().parents[1]):
        if output_dir.is_relative_to(protected) or protected.is_relative_to(output_dir):
            raise ValueError("Stitch output overlaps protected tracking/code directory")
    state_path = output_dir / "stitch_state.json"
    if output_dir.exists():
        if not state_path.exists() and any(output_dir.iterdir()):
            raise ValueError("Foreign stitch output preserved")
        for path in output_dir.iterdir():
            reject_link(path)
            if path.name not in {*ARTIFACTS, "stitch_state.json", ".ingest.lock"}:
                raise ValueError(f"Foreign stitch output preserved: {path.name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    reject_link(output_dir / ".ingest.lock")
    reject_link(state_path)
    with output_lease(output_dir):
        state = _state(state_path) if state_path.exists() else {
            "schema_version": 1, "pipeline": PIPELINE, "signature": signature,
            "run_id": tracking["run_id"], "tracking_state_sha256": state_hash, "status": "processing",
        }
        if state.get("signature") != signature or state.get("run_id") != tracking["run_id"] or state.get("tracking_state_sha256") != state_hash:
            raise ValueError("Stitch input/recipe changed; use a NEW output directory")
        if state.get("status") == "complete":
            load_mapping(output_dir, tracking_dir)
            return output_dir
        if state.get("status") not in {"processing", "publishing"}:
            raise ValueError("Invalid stitch processing state")
        if not state_path.exists():
            _save_state(state_path, state)
        # Same-volume temporary staging is outside the publication directory;
        # unfinished staging can never be mistaken for a consumer-ready output.
        with tempfile.TemporaryDirectory(prefix=".stitch-", dir=output_dir.parent) as folder:
            stage = Path(folder)
            with closing(_source(tracking_dir)) as source, closing(sqlite3.connect(stage / "mapping.db")) as target:
                globals_count, links = _derive(source, target, tracking, manifest, max_gap_sec, max_distance_diagonals)
                tracks = target.execute("SELECT count(*) FROM mapping").fetchone()[0]
                with (stage / "stitched_mapping.json").open("w", encoding="utf-8", newline="\n") as handle:
                    header = {"schema_version": 1, "run_id": tracking["run_id"], "tracking_state_sha256": state_hash}
                    handle.write(canonical(header)[:-1] + ',"mapping":[')
                    separator = ""
                    fields = ("run_id", "camera_id", "video_id", "segment_id", "track_id", "global_track_id")
                    for row in target.execute("SELECT * FROM mapping ORDER BY run_id,camera_id,video_id,segment_id,track_id"):
                        handle.write(separator + canonical(dict(zip(fields, row))))
                        separator = ","
                    handle.write("]}\n")
                observations = 0
                with (stage / "stitched_tracks.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
                    for frame in _records(source, tracking, manifest):
                        for obs in frame.observations:
                            gid = target.execute(
                                "SELECT global_track_id FROM mapping WHERE run_id=? AND camera_id=? AND video_id=? AND segment_id=? AND track_id=?", obs.track_key,
                            ).fetchone()[0]
                            handle.write(canonical({**obs.model_dump(mode="json"), "global_track_id": gid}) + "\n")
                            observations += 1
            if _tracking(tracking_dir)[2] != state_hash:
                raise ValueError("Tracking publication changed during stitching")
            artifacts = {name: digest(stage / name) for name in sorted(ARTIFACTS)}
            if state.get("artifacts") is not None and state["artifacts"] != artifacts:
                raise ValueError("Interrupted stitch publication differs; preserved")
            for name in sorted(ARTIFACTS):
                path = output_dir / name
                reject_link(path)
                if path.exists() and (not state.get("artifacts") or digest(path) != artifacts[name]):
                    raise ValueError(f"Foreign/changed stitch output preserved: {name}")
            state.update(status="publishing", artifacts=artifacts, tracks=tracks, global_tracks=globals_count, links=links, observations=observations)
            _save_state(state_path, state)
            for name in sorted(ARTIFACTS):
                if not (output_dir / name).exists():
                    (stage / name).replace(output_dir / name)
            state["status"] = "complete"
            _save_state(state_path, state)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-gap-sec", type=float, default=1.0)
    parser.add_argument("--max-distance-diagonals", type=float, default=2.5)
    args = parser.parse_args()
    try:
        output = stitch_tracks(args.tracking_dir, args.output_dir, args.max_gap_sec, args.max_distance_diagonals)
        print(json.dumps({"output_dir": str(output), "geometric_hypothesis_only": True}))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(2, f"Track stitching error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
