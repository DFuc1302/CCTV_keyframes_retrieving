"""Versioned traffic contracts, separate from legacy retrieval data."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.cctv.manifest import Manifest, identity, load_manifest

Unit = Annotated[float, Field(ge=0, le=1)]
NonNegative = Annotated[float, Field(ge=0)]
Point = tuple[Unit, Unit]


class Contract(BaseModel):
    """Reject unknown fields, non-finite numbers and ambiguous scalar coercion."""

    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, frozen=True
    )

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def validate_version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value


def polygon_valid(points: tuple[Point, ...]) -> None:
    """Reject repeated vertices, degeneracy and intersecting non-adjacent edges."""
    if len(points) < 3 or len(points) > 64 or len(set(points)) != len(points):
        raise ValueError(
            "ROI needs 3-64 distinct vertices; do not repeat closing point"
        )

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, c):
        return (
            abs(cross(a, b, c)) <= 1e-12
            and min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
        )

    edges = list(zip(points, points[1:] + points[:1]))
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    if abs(area) <= 1e-9:
        raise ValueError("ROI area must be nonzero")
    for i, (a, b) in enumerate(edges):
        previous = points[(i - 1) % len(points)]
        if on_segment(previous, a, b) or on_segment(a, b, previous):
            raise ValueError("ROI has overlapping adjacent edges")
        for j, (c, d) in enumerate(edges):
            if j <= i or j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            proper = (
                cross(a, b, c) * cross(a, b, d) < 0
                and cross(c, d, a) * cross(c, d, b) < 0
            )
            if proper or any(
                (
                    on_segment(a, b, c),
                    on_segment(a, b, d),
                    on_segment(c, d, a),
                    on_segment(c, d, b),
                )
            ):
                raise ValueError("ROI has intersecting edges")


class CameraROI(Contract):
    """Explicit human approval; pending geometry never implies traffic readiness."""

    camera_id: str
    status: Literal["pending", "approved"] = "pending"
    polygon: tuple[Point, ...] | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None

    @model_validator(mode="after")
    def validate_roi(self):
        identity(self.camera_id, "camera_id")
        if self.polygon is not None:
            polygon_valid(self.polygon)
        if self.status == "approved":
            if (
                self.polygon is None
                or not self.reviewed_by
                or not self.reviewed_by.strip()
                or not self.reviewed_at
            ):
                raise ValueError(
                    "Approved ROI requires geometry, reviewer and review time"
                )
            stamp = datetime.fromisoformat(self.reviewed_at)
            if stamp.utcoffset() is None:
                raise ValueError("ROI review time requires explicit timezone")
        elif self.reviewed_by is not None or self.reviewed_at is not None:
            raise ValueError("Pending ROI must not contain approval fields")
        return self


class TrafficConfig(Contract):
    """An independent configuration bound to every camera in an ingestion manifest."""

    schema_version: Literal[1]
    cameras: tuple[CameraROI, ...]

    def bind(self, manifest: Manifest) -> None:
        actual = [c.camera_id for c in self.cameras]
        expected = {str(c["camera_id"]) for c in manifest.cameras}
        if (
            len({c.casefold() for c in actual}) != len(actual)
            or set(actual) != expected
        ):
            raise ValueError(
                "Traffic config must cover each canonical manifest camera exactly once"
            )



def calibration_geometry(points: tuple[tuple[float, float], ...]) -> None:
    """Reject duplicate and poorly conditioned two-dimensional survey geometry."""
    import numpy as np

    values = np.asarray(points, dtype=np.float64)
    if len(points) < 4 or len(points) > 64 or len(set(points)) != len(points):
        raise ValueError("Calibration needs 4-64 distinct correspondences")
    centered = values - values.mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False)
    if singular[0] <= 1e-12 or singular[1] <= singular[0] * 1e-5:
        raise ValueError("Calibration geometry is degenerate or near-collinear")
    if len(points) == 4:
        scale = singular[0] ** 2
        for i in range(4):
            for j in range(i + 1, 4):
                for k in range(j + 1, 4):
                    a, b, c = values[i], values[j], values[k]
                    area = abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
                    if area <= scale * 1e-5:
                        raise ValueError("Four-point fit contains near-collinear points")


class GroundPointPair(Contract):
    image_point: Point
    ground_point_meters: tuple[float, float]


class CameraCalibration(Contract):
    camera_id: str
    calibration_version: Literal[1] = 1
    points: tuple[GroundPointPair, ...]
    roi_polygon: tuple[Point, ...]
    reviewer: str | None = None
    reviewed_at: str | None = None

    @field_validator("calibration_version", mode="before")
    @classmethod
    def validate_calibration_version(cls, value):
        if type(value) is not int:
            raise ValueError("calibration_version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_calibration(self):
        identity(self.camera_id, "camera_id")
        calibration_geometry(tuple(p.image_point for p in self.points))
        calibration_geometry(tuple(p.ground_point_meters for p in self.points))
        polygon_valid(self.roi_polygon)
        if (self.reviewer is None) != (self.reviewed_at is None):
            raise ValueError("Calibration reviewer and review time must be paired")
        if self.reviewer is not None:
            if not self.reviewer.strip():
                raise ValueError("Calibration reviewer must be nonblank")
            if datetime.fromisoformat(self.reviewed_at).utcoffset() is None:
                raise ValueError("Calibration review time requires explicit timezone")
        return self


class CalibrationConfig(Contract):
    schema_version: Literal[1]
    calibrations: tuple[CameraCalibration, ...]
    pending_cameras: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_cameras(self):
        names = [c.camera_id for c in self.calibrations] + list(self.pending_cameras)
        for name in names:
            identity(name, "camera_id")
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError("Duplicate calibration/pending camera")
        return self

    def bind(self, manifest: Manifest) -> None:
        actual = {c.camera_id for c in self.calibrations} | set(self.pending_cameras)
        if actual != {str(c["camera_id"]) for c in manifest.cameras}:
            raise ValueError("Calibration config must cover canonical manifest cameras")


def load_calibration_config(path: Path, manifest: Manifest) -> CalibrationConfig:
    config = CalibrationConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    config.bind(manifest)
    return config

class Locator(Contract):
    """PTS seconds relative to first decoded source frame; ordinal starts at zero."""

    camera_id: str
    video_id: str
    timestamp_sec: NonNegative
    frame_idx: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_ids(self):
        identity(self.camera_id, "camera_id")
        identity(self.video_id, "video_id")
        return self

    def bind(self, manifest: Manifest) -> None:
        if not any(
            v.video_id == self.video_id and v.camera_id == self.camera_id
            for v in manifest.videos
        ):
            raise ValueError("Unknown video or camera/video mismatch")


class Detection(Contract):
    """Normalized xyxy box, model score and explicit vehicle class."""

    label: Literal["bicycle", "motorcycle", "car", "bus", "truck"]
    confidence: Unit
    bbox: tuple[Unit, Unit, Unit, Unit]

    @model_validator(mode="after")
    def validate_box(self):
        x1, y1, x2, y2 = self.bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox must have positive width and height")
        return self


class DetectionFrame(Contract):
    """One processed frame; empty detections are valid and differ from missing frames."""

    schema_version: Literal[1]
    kind: Literal["detections"]
    run_id: str
    locator: Locator
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    detections: tuple[Detection, ...]

    @model_validator(mode="after")
    def validate_run(self):
        identity(self.run_id, "run_id")
        return self


class TrackObservation(Contract):
    """Track identity scoped to run/video/segment; resets never join across videos."""

    schema_version: Literal[1]
    kind: Literal["track"]
    run_id: str
    segment_id: str
    track_id: Annotated[int, Field(ge=0)]
    locator: Locator
    detection: Detection

    @model_validator(mode="after")
    def validate_scope(self):
        identity(self.run_id, "run_id")
        identity(self.segment_id, "segment_id")
        return self

    @property
    def track_key(self) -> tuple[str, str, str, str, int]:
        """Avoid collisions when trackers reuse integer IDs after a reset."""
        return (
            self.run_id,
            self.locator.camera_id,
            self.locator.video_id,
            self.segment_id,
            self.track_id,
        )


class TrafficWindow(Contract):
    """Relative-time summary, with unavailable motion retained as null."""

    schema_version: Literal[1]
    kind: Literal["window"]
    run_id: str
    camera_id: str
    video_id: str
    start_sec: NonNegative
    end_sec: NonNegative
    sample_count: Annotated[int, Field(gt=0)]
    mean_vehicle_count: NonNegative
    mean_occupancy: Unit
    median_motion_px_per_sec: NonNegative | None
    median_speed_kmh: NonNegative | None = None
    calibration_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    roi_status: Literal["approved"]

    @model_validator(mode="after")
    def validate_window(self):
        for field in ("run_id", "camera_id", "video_id"):
            identity(getattr(self, field), field)
        if self.end_sec <= self.start_sec:
            raise ValueError("Window end must exceed start")
        if self.median_speed_kmh is not None and self.calibration_sha256 is None:
            raise ValueError("Metric speed requires calibration provenance")
        return self


def load_config(path: Path, manifest: Manifest) -> TrafficConfig:
    """Validate an external ROI config without mutating ingestion/index metadata."""
    config = TrafficConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    config.bind(manifest)
    return config


def validate_record(payload: str, manifest: Manifest, config: TrafficConfig):
    """Validate one JSONL record and its source/ROI references, without writing data."""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Traffic record must be an object")
    models = {
        "detections": DetectionFrame,
        "track": TrackObservation,
        "window": TrafficWindow,
    }
    if not isinstance(data.get("kind"), str) or data["kind"] not in models:
        raise ValueError("Unknown traffic record kind")
    record = models[data["kind"]].model_validate_json(payload)
    config.bind(manifest)
    if isinstance(record, TrafficWindow):
        Locator(
            camera_id=record.camera_id,
            video_id=record.video_id,
            timestamp_sec=record.start_sec,
            frame_idx=0,
        ).bind(manifest)
        if not any(
            c.camera_id == record.camera_id and c.status == "approved"
            for c in config.cameras
        ):
            raise ValueError("Traffic windows require a reviewed camera ROI")
    else:
        record.locator.bind(manifest)
    return record


def main() -> int:
    """Read-only ROI readiness inspection; never loads models or generates detections."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(
            args.config, load_manifest(args.manifest, require_files=False)
        )
        pending = [c.camera_id for c in config.cameras if c.status != "approved"]
        ready = bool(config.cameras) and not pending
        print(
            json.dumps(
                {"schema_valid": True, "roi_ready": ready, "pending_cameras": pending},
                indent=2,
            )
        )
        return 1 if args.require_approved and not ready else 0
    except (OSError, ValueError, ValidationError) as exc:
        parser.exit(2, f"Traffic config error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
