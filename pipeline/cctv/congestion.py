"""CPU traffic windows and uncalibrated congestion candidates from native tracks."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import median
from typing import Annotated, Iterable, Literal

import cv2
import numpy as np
from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import Manifest, identity, load_manifest
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import (
    CameraROI, CameraCalibration, Contract, TrafficWindow, Unit,
    calibration_geometry, load_config, load_calibration_config,
)

LOG = logging.getLogger("cctv.congestion")
PIPELINE = "cctv-congestion-v1"
Positive = Annotated[float, Field(gt=0)]


def canonical(value: object) -> str:
    """Stable serialized recipe and JSONL records."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CameraThresholds(Contract):
    """Per-camera exploratory thresholds; not a calibrated event detector."""

    camera_id: str
    occupancy_min: Unit
    vehicle_count_min: Positive
    motion_max_px_per_sec: Positive
    persistence_sec: Positive

    @model_validator(mode="after")
    def valid_id(self):
        identity(self.camera_id, "camera_id")
        return self


class MetricsConfig(Contract):
    """Explicit processing resolution, time windows and evidence guards."""

    schema_version: Literal[1]
    window_sec: Annotated[float, Field(ge=0.5, le=60)]
    mask_width: Annotated[int, Field(ge=32, le=1024)]
    max_gap_sec: Annotated[float, Field(gt=0, le=1)]
    min_coverage: Annotated[float, Field(gt=0, le=1)]
    min_motion_pairs: Annotated[int, Field(ge=1)]
    cameras: tuple[CameraThresholds, ...]

    def bind(self, manifest: Manifest) -> None:
        ids = [c.camera_id for c in self.cameras]
        if len(set(ids)) != len(ids) or set(ids) != {
            c["camera_id"] for c in manifest.cameras
        }:
            raise ValueError("Thresholds must cover every canonical camera once")
        if any(c.persistence_sec < self.window_sec for c in self.cameras):
            raise ValueError("Persistence must be at least one window")


class DraftWindow(TrafficWindow):
    """Demo output cannot be mistaken for a reviewed T3.1 TrafficWindow."""

    kind: Literal["draft_window"]
    roi_status: Literal["pending"]


class RasterROI:
    """Bounded raster union occupancy and normalized bottom-center inclusion."""

    def __init__(self, roi: CameraROI, width: int, height: int, mask_width: int):
        if roi.polygon is None:
            raise ValueError("ROI geometry is required, even in draft mode")
        self.width, self.height = width, height
        self.contour = np.asarray(roi.polygon, dtype=np.float32)
        mh = max(1, round(mask_width * height / width))
        if mh > 2048:
            raise ValueError("ROI raster aspect ratio exceeds memory bound")
        self.mask = np.zeros((mh, mask_width), dtype=np.uint8)
        points = np.rint(self.contour * [mask_width - 1, mh - 1]).astype(np.int32)
        cv2.fillPoly(self.mask, [points], 1)
        self.area = int(self.mask.sum())
        if self.area == 0:
            raise ValueError("ROI too small at configured mask resolution")
        self.covered = np.zeros_like(self.mask)

    def measure(self, frame: TrackFrame) -> tuple[int, float, dict]:
        """Count included anchors; union all box intersections without double counting."""
        self.covered.fill(0)
        mh, mw = self.mask.shape
        anchors = {}
        for obs in frame.observations:
            x1, y1, x2, y2 = obs.detection.bbox
            self.covered[
                math.floor(y1 * mh) : math.ceil(y2 * mh),
                math.floor(x1 * mw) : math.ceil(x2 * mw),
            ] = 1
            center = ((x1 + x2) / 2, y2)
            if cv2.pointPolygonTest(self.contour, center, False) >= 0:
                anchors[obs.track_key] = (
                    center[0] * self.width,
                    center[1] * self.height,
                )
        occupancy = float(np.count_nonzero(self.covered & self.mask) / self.area)
        return len(anchors), occupancy, anchors


def project_point_to_ground(point: tuple[float, float], H: np.ndarray) -> tuple[float, float]:
    """Project normalized image coordinates, rejecting horizons and nonfinite output."""
    matrix = np.asarray(H, dtype=np.float64)
    vector = np.asarray((*point, 1.0), dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.isfinite(vector).all():
        raise ValueError("Invalid projective matrix or point")
    projected = matrix @ vector
    bound = np.linalg.norm(matrix[2]) * np.linalg.norm(vector)
    if bound == 0 or abs(projected[2]) <= bound * 1e-10:
        raise ValueError("Unsafe projective denominator")
    result = projected[:2] / projected[2]
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite ground projection")
    return float(result[0]), float(result[1])


def compute_homography(calibration: CameraCalibration) -> np.ndarray:
    """Fit a planar metric transform; extra markers require a robust majority fit."""
    image = np.asarray([p.image_point for p in calibration.points], dtype=np.float64)
    ground = np.asarray([p.ground_point_meters for p in calibration.points], dtype=np.float64)
    if len(image) == 4:
        H = cv2.getPerspectiveTransform(image.astype(np.float32), ground.astype(np.float32))
    else:
        span = float(np.linalg.norm(np.ptp(ground, axis=0)))
        H, mask = cv2.findHomography(image, ground, cv2.RANSAC, max(1e-6, span * 0.005))
        if mask is None or int(mask.sum()) < max(4, math.ceil(len(image) * 0.75)):
            raise ValueError("Calibration lacks sufficient robust inliers")
        keep = mask.ravel().astype(bool)
        calibration_geometry(tuple(map(tuple, image[keep])))
        calibration_geometry(tuple(map(tuple, ground[keep])))
    if H is None or not np.isfinite(H).all():
        raise ValueError("Homography fit failed")
    norm = np.linalg.norm(H)
    if norm == 0 or np.linalg.svd(H / norm, compute_uv=False)[-1] <= 1e-12:
        raise ValueError("Singular or ill-conditioned homography")
    H = H / norm
    denominators = []
    for point in calibration.roi_polygon:
        project_point_to_ground(point, H)
        denominators.append(float(H[2] @ np.asarray((*point, 1.0))))
    if min(denominators) < 0 < max(denominators):
        raise ValueError("Calibration validity region crosses projective horizon")
    return H


def summarize_video(
    records: Iterable[TrackFrame],
    roi: CameraROI,
    config: MetricsConfig,
    threshold: CameraThresholds,
    start: float,
    end: float | None,
    draft: bool,
    sample_interval: float = 0.0,
    calibration: CameraCalibration | None = None,
) -> list[dict]:
    """Integrate sample-and-hold only over observed adjacent PTS intervals."""
    if not draft and roi.status != "approved":
        raise ValueError("Reviewed ROI required for non-demo windows")
    windows: dict[int, dict] = {}
    previous = None
    raster = None
    identity_key = None
    errors = 0
    last_stamp = start
    sealed_index = -1

    H = compute_homography(calibration) if calibration is not None else None
    calibration_hash = (
        hashlib.sha256(canonical(calibration.model_dump(mode="json")).encode()).hexdigest()
        if calibration is not None else None
    )
    valid_contour = (
        np.asarray(calibration.roi_polygon, dtype=np.float32) if calibration is not None else None
    )
    if calibration is not None and calibration.camera_id != roi.camera_id:
        raise ValueError("Calibration camera mismatch")
    previous_ground = {}
    def bucket(index: int) -> dict:
        if index > 100000:
            raise ValueError("Too many windows; increase window_sec")
        return windows.setdefault(
            index,
            {
                "samples": 0,
                "covered": 0.0,
                "count": 0.0,
                "occupancy": 0.0,
                "motion": [],
                "metric": [],
            },
        )

    for frame in records:
        try:
            loc = frame.locator
            key = (frame.run_id, loc.camera_id, loc.video_id, frame.width, frame.height)
            if identity_key is None:
                identity_key = key
                raster = RasterROI(roi, frame.width, frame.height, config.mask_width)
            if key != identity_key or loc.camera_id != roi.camera_id:
                raise ValueError("Video/camera/run/resolution changed inside stream")
            stamp = loc.timestamp_sec
            if stamp < start or (end is not None and stamp >= end):
                raise ValueError("Frame outside tracking clip bounds")
            if previous and (
                stamp <= previous[0]
                or loc.frame_idx <= previous[1]
                or (not sample_interval and loc.frame_idx != previous[1] + 1)
            ):
                raise ValueError(
                    "Frames must have increasing PTS and consecutive ordinals"
                )
            count, occupancy, anchors = raster.measure(frame)
            ground_anchors = {}
            if H is not None:
                for track_key, anchor in anchors.items():
                    point = (anchor[0] / frame.width, anchor[1] / frame.height)
                    if cv2.pointPolygonTest(valid_contour, point, False) >= 0:
                        try:
                            ground_anchors[track_key] = project_point_to_ground(point, H)
                        except ValueError:
                            pass  # An unsafe projection is unavailable, never zero speed.
            current = bucket(int((stamp - start) // config.window_sec))
            current["samples"] += 1
            if previous:
                old_stamp, _, old_count, old_occupancy, old_anchors = previous
                dt = stamp - old_stamp
                if dt <= config.max_gap_sec:
                    cursor = old_stamp
                    while cursor < stamp - 1e-10:
                        index = int((cursor - start + 1e-10) // config.window_sec)
                        stop = min(stamp, start + (index + 1) * config.window_sec)
                        item = bucket(index)
                        weight = stop - cursor
                        item["covered"] += weight
                        item["count"] += old_count * weight
                        item["occupancy"] += old_occupancy * weight
                        cursor = stop
                    for track_key in anchors.keys() & old_anchors.keys():
                        if len(current["motion"]) >= 100000:
                            raise ValueError(
                                "Motion pairs exceed window memory bound; reduce window_sec"
                            )
                        current["motion"].append(
                            math.dist(anchors[track_key], old_anchors[track_key]) / dt
                        )
                    for track_key in ground_anchors.keys() & previous_ground.keys():
                        if len(current["metric"]) >= 100000:
                            raise ValueError("Metric pairs exceed window memory bound")
                        current["metric"].append(
                            3.6 * math.dist(ground_anchors[track_key], previous_ground[track_key]) / dt
                        )
            current_index = int((stamp - start) // config.window_sec)
            while sealed_index < current_index - 1:
                sealed_index += 1
                if sealed_index in windows:
                    sealed = windows[sealed_index]
                    sealed["motion_count"] = len(sealed["motion"])
                    sealed["motion_median"] = (
                        median(sealed["motion"]) if sealed["motion"] else None
                    )
                    sealed["motion"].clear()
                    sealed["metric_count"] = len(sealed["metric"])
                    sealed["metric_median"] = median(sealed["metric"]) if sealed["metric"] else None
                    sealed["metric"].clear()
            previous = (stamp, loc.frame_idx, count, occupancy, anchors)
            previous_ground = ground_anchors
            last_stamp = stamp
        except Exception:
            LOG.exception("frame_error | %s", frame.locator.model_dump())
            errors += 1
            previous = None
            previous_ground = {}
    if errors:
        raise ValueError(f"Video rejected after {errors} invalid frames")
    if identity_key is None:
        raise ValueError("No tracking frames")
    finish = end if end is not None else last_stamp
    result, persistent = [], 0.0
    window_type = DraftWindow if draft else TrafficWindow
    for index, item in sorted(windows.items()):
        left = start + index * config.window_sec
        right = min(finish, left + config.window_sec)
        if right <= left or not item["samples"]:
            continue
        duration = right - left
        coverage = min(1.0, item["covered"] / duration)
        motion = item.get(
            "motion_median", median(item["motion"]) if item["motion"] else None
        )
        motion_count = item.get("motion_count", len(item["motion"]))
        eligible = (
            coverage >= config.min_coverage
            and duration >= config.window_sec - 1e-9
            and motion_count >= config.min_motion_pairs
        )
        count = item["count"] / item["covered"] if item["covered"] else 0.0
        occupancy = item["occupancy"] / item["covered"] if item["covered"] else 0.0
        slow_dense = (
            eligible
            and motion is not None
            and motion <= threshold.motion_max_px_per_sec
            and occupancy >= threshold.occupancy_min
            and count >= threshold.vehicle_count_min
        )
        if result and abs(result[-1]["window"]["end_sec"] - left) > 1e-8:
            persistent = 0.0
        persistent = persistent + duration if slow_dense else 0.0
        window = window_type(
            schema_version=1,
            kind="draft_window" if draft else "window",
            run_id=identity_key[0],
            camera_id=identity_key[1],
            video_id=identity_key[2],
            start_sec=left,
            end_sec=right,
            sample_count=item["samples"],
            mean_vehicle_count=count,
            mean_occupancy=min(1.0, occupancy),
            median_motion_px_per_sec=motion,
            median_speed_kmh=item.get("metric_median", median(item["metric"]) if item["metric"] else None),
            calibration_sha256=calibration_hash,
            roi_status="pending" if draft else "approved",
        )
        result.append(
            {
                "window": window.model_dump(mode="json"),
                "coverage": coverage,
                "motion_pairs": motion_count,
                "metric_motion_pairs": item.get("metric_count", len(item["metric"])),
                "evidence_sufficient": eligible,
                "status": (
                    "insufficient_evidence"
                    if not eligible
                    else "congestion_candidate"
                    if persistent >= threshold.persistence_sec
                    else "slow_dense"
                    if slow_dense
                    else "no_candidate"
                ),
                "persistent_sec": persistent,
                "thresholds_calibrated": False,
                "demo_only": draft,
            }
        )
    return result


def inspect_inputs(
    manifest_path: Path, roi_path: Path, metrics_path: Path, tracks: Path, draft: bool
) -> tuple:
    """Read committed tracking artifacts and gate pending ROI before any writes."""
    manifest = load_manifest(manifest_path, require_files=False)
    rois = load_config(roi_path, manifest)
    metrics = MetricsConfig.model_validate_json(
        metrics_path.read_text(encoding="utf-8-sig")
    )
    metrics.bind(manifest)
    state = json.loads((tracks / "track_state.json").read_text())
    if state.get("status") != "complete":
        raise ValueError("Tracking must be complete")
    for name in ("tracks.db", "frames.jsonl", "tracking_manifest.json"):
        path = tracks / name
        reject_link(path)
        if digest(path) != state["publication"].get(name):
            raise ValueError(f"Tracking input changed: {name}")
    publication = json.loads((tracks / "tracking_manifest.json").read_text())
    if (
        publication["signature"] != state["signature"]
        or publication["run_id"] != state["run_id"]
    ):
        raise ValueError("Tracking manifest/state mismatch")
    if state["signature"]["manifest_sha256"] != digest(manifest_path):
        raise ValueError("Tracking ingestion manifest mismatch")
    selected = publication["videos"]
    cameras = {v.video_id: v.camera_id for v in manifest.videos}
    if (
        not selected
        or len(set(selected)) != len(selected)
        or not set(selected) <= cameras.keys()
    ):
        raise ValueError("Tracking video list is invalid")
    for roi in rois.cameras:
        if roi.camera_id in {cameras[v] for v in selected}:
            if roi.polygon is None or (not draft and roi.status != "approved"):
                raise ValueError(
                    "Reviewed ROI required; use explicit --allow-draft with draft geometry for demo"
                )
    return manifest, rois, metrics, state, publication


def process_windows(
    manifest_path: Path,
    roi_path: Path,
    metrics_path: Path,
    tracks: Path,
    output: Path,
    draft: bool = False,
    calibration_path: Path | None = None,
) -> dict:
    """Resume by whole video; write only to an isolated owned CPU output folder."""
    tracks, output = tracks.absolute(), output.absolute()
    manifest, rois, metrics, tracking, publication = inspect_inputs(
        manifest_path, roi_path, metrics_path, tracks, draft
    )
    calibrations = load_calibration_config(calibration_path, manifest) if calibration_path is not None else None
    calibration_file_hash = digest(calibration_path) if calibration_path is not None else None
    reject_link(output)
    for protected in (
        manifest.raw_root,
        tracks,
        manifest_path.resolve().parent,
        roi_path.resolve().parent,
        metrics_path.resolve().parent,
        *([calibration_path.resolve().parent] if calibration_path is not None else []),
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Output overlaps input/config/raw directory")
    signature = {
        "pipeline": PIPELINE,
        "tracking": {
            k: tracking["publication"][k]
            for k in ("tracks.db", "frames.jsonl", "tracking_manifest.json")
        },
        "roi_sha256": digest(roi_path),
        "metrics_sha256": digest(metrics_path),
        "calibration_sha256": calibration_file_hash,
        "code_sha256": digest(Path(__file__)),
        "track_contract_sha256": digest(Path(__file__).with_name("track.py")),
        "traffic_contract_sha256": digest(Path(__file__).with_name("traffic.py")),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "allow_draft": draft,
    }
    state_path = output / "window_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("window_state.json", "windows.db", "windows.log", ".ingest.lock"):
        reject_link(output / name)
    result = {
        "videos_done": 0,
        "videos_skipped": 0,
        "windows": 0,
        "error": 0,
        "demo_only": draft,
    }
    with output_lease(output):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"signature": signature, "status": "processing"}
        )
        if state["signature"] != signature:
            raise ValueError("Input/recipe changed; use a NEW output directory")
        if state["status"] == "complete":
            for name, checksum in state["artifacts"].items():
                reject_link(output / name)
                if digest(output / name) != checksum:
                    raise ValueError(f"Output artifact changed: {name}")
            return {
                **result,
                "videos_skipped": len(publication["videos"]),
                "windows": state["windows"],
            }
        save_json(state_path, state)
        handler = logging.FileHandler(output / "windows.log", encoding="utf-8")
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        try:
            with (
                closing(sqlite3.connect(output / "windows.db")) as target,
                closing(
                    sqlite3.connect(
                        f"{(tracks / 'tracks.db').as_uri()}?mode=ro", uri=True
                    )
                ) as source,
            ):
                tables = {
                    r[0]
                    for r in target.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not tables <= {"videos"}:
                    raise ValueError("Foreign checkpoint preserved")
                target.execute(
                    "CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY,payload TEXT,sha256 TEXT)"
                )
                completed = {}

                for vid, payload, checksum in target.execute("SELECT * FROM videos"):
                    if (
                        vid not in publication["videos"]
                        or hashlib.sha256(payload.encode()).hexdigest() != checksum
                    ):
                        raise ValueError("Checkpoint video corrupted")
                    completed[vid] = json.loads(payload)
                if source.execute("SELECT signature FROM metadata").fetchall() != [
                    (canonical(tracking["signature"]),)
                ]:
                    raise ValueError("Tracking checkpoint signature mismatch")
                counts = dict(
                    source.execute(
                        "SELECT video_id,count(*) FROM frames GROUP BY video_id"
                    )
                )
                if (
                    set(counts) != set(publication["videos"])
                    or sum(counts.values()) != publication["frames"]
                ):
                    raise ValueError("Tracking frame coverage mismatch")
                cameras = {v.video_id: v.camera_id for v in manifest.videos}
                errors = {}
                for vid in tqdm(
                    publication["videos"], desc="Traffic videos", unit="video"
                ):
                    if vid in completed:
                        result["videos_skipped"] += 1
                        continue
                    try:

                        def records():
                            failures = 0
                            for idx, stamp, sid, payload in tqdm(
                                source.execute(
                                    "SELECT frame_idx,timestamp_sec,segment_id,payload FROM frames WHERE video_id=? ORDER BY frame_idx",
                                    (vid,),
                                ),
                                total=counts[vid],
                                desc=vid,
                                unit="frame",
                                leave=False,
                            ):
                                try:
                                    frame = TrackFrame.model_validate_json(payload)
                                    frame.locator.bind(manifest)
                                    if (
                                        frame.run_id != publication["run_id"]
                                        or frame.locator.video_id != vid
                                        or frame.locator.frame_idx != idx
                                        or frame.locator.timestamp_sec != stamp
                                        or frame.segment_id != sid
                                    ):
                                        raise ValueError("Tracking row/record mismatch")
                                    yield frame
                                except Exception:
                                    failures += 1
                                    LOG.exception("frame_error | %s | %s", vid, idx)
                            if failures:
                                raise ValueError(f"{failures} invalid tracking records")

                        camera = cameras[vid]
                        roi = next(r for r in rois.cameras if r.camera_id == camera)
                        threshold = next(
                            r for r in metrics.cameras if r.camera_id == camera
                        )
                        start = tracking["signature"]["start_sec"]
                        duration = tracking["signature"]["duration_sec"]
                        rows = summarize_video(
                            records(),
                            roi,
                            metrics,
                            threshold,
                            start,
                            None if duration is None else start + duration,
                            draft,
                            tracking["signature"].get("sample_interval_sec", 0),
                            next((c for c in calibrations.calibrations if c.camera_id == camera), None)
                            if calibrations is not None else None,
                        )
                        payload = canonical(rows)
                        with target:
                            target.execute(
                                "INSERT INTO videos VALUES (?,?,?)",
                                (
                                    vid,
                                    payload,
                                    hashlib.sha256(payload.encode()).hexdigest(),
                                ),
                            )
                        completed[vid] = rows
                        result["videos_done"] += 1
                    except Exception as exc:
                        LOG.exception("video_error | %s", vid)
                        errors[vid] = str(exc)
                state["errors"] = errors
                result["error"] = len(errors)
                save_json(state_path, state)
                if errors:
                    return result
            # Sources must remain identical through the CPU stage.
            inspect_inputs(manifest_path, roi_path, metrics_path, tracks, draft)
            if calibration_path is not None and digest(calibration_path) != calibration_file_hash:
                raise ValueError("Calibration input changed during processing")
            rows = [r for vid in sorted(completed) for r in completed[vid]]
            result["windows"] = len(rows)
            report = {
                "pipeline": PIPELINE,
                "signature": signature,
                "windows": len(rows),
                "videos": sorted(completed),
                "demo_only": draft,
                "camera_calibration_sha256": {
                    c.camera_id: hashlib.sha256(canonical(c.model_dump(mode="json")).encode()).hexdigest()
                    for c in calibrations.calibrations
                } if calibrations is not None else {},
                "roi_ready": not draft,
                "thresholds_calibrated": False,
                "operational_ready": False,
                "candidates": sum(r["status"] == "congestion_candidate" for r in rows),
            }
            content = {
                "windows.jsonl": "".join(canonical(r["window"]) + "\n" for r in rows),
                "signals.jsonl": "".join(canonical(r) + "\n" for r in rows),
                "window_manifest.json": json.dumps(report, indent=2) + "\n",
            }
            # Deterministic publication recovery without replacing unrelated files.

            artifacts = {
                name: hashlib.sha256(text.encode()).hexdigest()
                for name, text in content.items()
            }
            artifacts["windows.db"] = digest(output / "windows.db")
            if state.get("artifacts") and state["artifacts"] != artifacts:
                raise ValueError("Interrupted publication changed; preserved")
            for name in content:
                path = output / name
                reject_link(path)
                if path.exists() and (
                    not state.get("artifacts") or digest(path) != artifacts[name]
                ):
                    raise ValueError(f"Foreign publication preserved: {name}")
            state.update(status="publishing", artifacts=artifacts, windows=len(rows))
            save_json(state_path, state)
            for name, text in content.items():
                path = output / name
                if not path.exists():
                    temporary = output / f".{name}.tmp"
                    reject_link(temporary)
                    temporary.write_text(text, encoding="utf-8", newline="\n")
                    temporary.replace(path)
            state["status"] = "complete"
            save_json(state_path, state)
            LOG.info("complete | %s", result)
            return result
        finally:
            LOG.removeHandler(handler)
            handler.close()


def main() -> int:
    """Run without YOLO/CLIP loading; pending ROIs require explicit demo opt-in."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--roi-config", required=True, type=Path)
    parser.add_argument("--metrics-config", required=True, type=Path)
    parser.add_argument("--tracking-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--calibration-config", type=Path)
    args = parser.parse_args()
    try:
        result = process_windows(
            args.manifest,
            args.roi_config,
            args.metrics_config,
            args.tracking_dir,
            args.output_dir,
            args.allow_draft,
            args.calibration_config,
        )
        print(json.dumps(result, indent=2))
        return 1 if result["error"] else 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(2, f"Traffic windows error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
