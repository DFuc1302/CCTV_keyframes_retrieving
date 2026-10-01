"""Uncalibrated move-then-stop screening with scoped tracks and actual PTS."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Iterable, Literal

import cv2
import numpy as np
from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.events import (
    MAX_RECORDS,
    Event,
    canonical,
    checksum,
    protect_output,
    publication,
    read_json,
)
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import CameraROI, Contract, Unit, load_config


class IncidentConfig(Contract):
    """Experimental image-space thresholds, not physical speed or accident scores."""

    schema_version: Literal[1]
    moving_min_body_lengths_per_sec: Annotated[float, Field(gt=0)]
    stationary_max_body_lengths_per_sec: Annotated[float, Field(ge=0)]
    moving_duration_sec: Annotated[float, Field(gt=0)]
    stationary_duration_sec: Annotated[float, Field(gt=0)]
    max_gap_sec: Annotated[float, Field(gt=0, le=1)]
    confidence_min: Unit
    box_area_min: Annotated[float, Field(gt=0, le=1)]
    max_box_scale_ratio: Annotated[float, Field(gt=1, le=4)]

    @model_validator(mode="after")
    def ordered(self):
        if (
            self.stationary_max_body_lengths_per_sec
            >= self.moving_min_body_lengths_per_sec
        ):
            raise ValueError("Stationary threshold must be below moving threshold")
        return self


class IncidentEvent(Event):
    """Schema v2 preserves v1 evidence fields and adds a scoped temporal hypothesis."""

    schema_version: Literal[2] = 2
    event_type: Literal["suspected_road_incident"]
    basis: Literal["temporal_rule"]
    rule: Literal["move_then_stop"]
    segment_id: str
    track_id: Annotated[int, Field(ge=0)]
    moving_sec: Annotated[float, Field(gt=0)]
    stationary_sec: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def consistent(self):
        # Override v1's demo_case-only synthetic rule; v1 bytes remain unchanged.
        for name in ("camera_id", "video_id", "run_id", "segment_id"):
            identity(getattr(self, name), name)
        if self.end_sec <= self.start_sec or (
            self.synthetic_event and not self.demo_only
        ):
            raise ValueError("Invalid incident interval/demo flag")
        positions = []
        for evidence in self.evidence:
            loc = evidence.locator
            if (loc.camera_id, loc.video_id, evidence.segment_id) != (
                self.camera_id,
                self.video_id,
                self.segment_id,
            ):
                raise ValueError("Incident evidence scope mismatch")
            if not self.start_sec <= loc.timestamp_sec < self.end_sec:
                raise ValueError("Incident evidence outside interval")
            positions.append(loc.frame_idx)
        if positions != sorted(set(positions)):
            raise ValueError("Incident evidence must be chronological and unique")
        if (
            self.event_id
            != "event-"
            + checksum(self.model_dump(mode="json", exclude={"event_id"}))[:24]
        ):
            raise ValueError("Incident identity/checksum mismatch")
        return self


def screen_video(
    frames: Iterable[TrackFrame],
    roi: CameraROI,
    config: IncidentConfig,
    provenance: str,
    synthetic: bool = False,
) -> list[IncidentEvent]:
    """One candidate per continuous stop, reset on missing/weak/outside observations."""
    if roi.polygon is None:
        raise ValueError("Incident screening requires ROI geometry")
    contour = np.asarray(roi.polygon, dtype=np.float32)
    states: dict[tuple, dict] = {}
    result = []
    previous = None
    for frame in frames:
        loc = frame.locator
        if loc.camera_id != roi.camera_id:
            raise ValueError("Frame camera differs from ROI")
        if previous is not None:
            if (frame.run_id, loc.camera_id, loc.video_id) != (
                previous.run_id,
                previous.locator.camera_id,
                previous.locator.video_id,
            ):
                raise ValueError("Mixed source frames")
            dt = loc.timestamp_sec - previous.locator.timestamp_sec
            if dt <= 0 or loc.frame_idx <= previous.locator.frame_idx:
                raise ValueError("Unordered source frames")
            if (
                loc.frame_idx != previous.locator.frame_idx + 1
                or dt > config.max_gap_sec
                or frame.segment_id != previous.segment_id
                or (frame.width, frame.height) != (previous.width, previous.height)
            ):
                states.clear()
        active = {}
        for obs in frame.observations:
            box = obs.detection.bbox
            x1, y1, x2, y2 = box
            if (
                obs.detection.confidence < config.confidence_min
                or (x2 - x1) * (y2 - y1) < config.box_area_min
            ):
                continue
            if cv2.pointPolygonTest(contour, ((x1 + x2) / 2, y2), False) < 0:
                continue
            center = ((x1 + x2) / 2 * frame.width, (y1 + y2) / 2 * frame.height)
            diagonal = math.hypot((x2 - x1) * frame.width, (y2 - y1) * frame.height)
            key = obs.track_key
            old = states.get(key)
            current = dict(
                center=center,
                diagonal=diagonal,
                frame=frame,
                moving_start=None,
                moving_sec=0.0,
                stop_start=None,
                stationary_sec=0.0,
                emitted=False,
            )
            if (
                old is not None
                and max(old["diagonal"], diagonal) / min(old["diagonal"], diagonal)
                <= config.max_box_scale_ratio
            ):
                dt = loc.timestamp_sec - old["frame"].locator.timestamp_sec
                speed = (
                    math.dist(center, old["center"])
                    / ((diagonal + old["diagonal"]) / 2)
                    / dt
                )
                if speed >= config.moving_min_body_lengths_per_sec:
                    current["moving_start"] = (
                        old["moving_start"]
                        if old["stop_start"] is None and old["moving_start"] is not None
                        else old["frame"]
                    )
                    current["moving_sec"] = (
                        loc.timestamp_sec
                        - current["moving_start"].locator.timestamp_sec
                    )
                elif (
                    speed <= config.stationary_max_body_lengths_per_sec
                    and old["moving_sec"] >= config.moving_duration_sec
                ):
                    current.update(
                        moving_start=old["moving_start"],
                        moving_sec=old["moving_sec"],
                        stop_start=old["stop_start"] or old["frame"],
                        emitted=old["emitted"],
                    )
                    current["stationary_sec"] = (
                        loc.timestamp_sec - current["stop_start"].locator.timestamp_sec
                    )
                    if (
                        current["stationary_sec"] >= config.stationary_duration_sec
                        and not current["emitted"]
                    ):
                        onset = current["moving_start"]
                        value = dict(
                            schema_version=2,
                            kind="event",
                            event_type="suspected_road_incident",
                            basis="temporal_rule",
                            rule="move_then_stop",
                            run_id=frame.run_id,
                            camera_id=loc.camera_id,
                            video_id=loc.video_id,
                            segment_id=frame.segment_id,
                            track_id=obs.track_id,
                            start_sec=onset.locator.timestamp_sec,
                            end_sec=loc.timestamp_sec
                            + frame.time_base[0] / frame.time_base[1],
                            evidence=[
                                dict(
                                    locator=f.locator.model_dump(mode="json"),
                                    segment_id=f.segment_id,
                                )
                                for f in (onset, frame)
                            ],
                            provenance_sha256=provenance,
                            moving_sec=current["moving_sec"],
                            stationary_sec=current["stationary_sec"],
                            initial_status="pending_review",
                            synthetic_event=synthetic,
                            demo_only=synthetic or roi.status != "approved",
                            thresholds_calibrated=False,
                            operational_ready=False,
                            rationale="Uncalibrated move-then-stop hypothesis; traffic lights, queues and tracking errors can trigger it. Review video; no accident diagnosis.",
                        )
                        value["event_id"] = "event-" + checksum(value)[:24]
                        result.append(
                            IncidentEvent.model_validate_json(canonical(value))
                        )
                        if len(result) > MAX_RECORDS:
                            raise ValueError("Too many incident candidates")
                        current["emitted"] = True
            active[key] = current
        states = active
        previous = frame
    return result


def load_incidents(folder: Path) -> tuple[dict, list[IncidentEvent]]:
    """Validate sealed v2 packages for the same review workflow as v1."""
    state = publication(
        folder, "event_state.json", ("events.jsonl", "event_manifest.json"), "artifacts"
    )
    report = read_json(folder / "event_manifest.json")
    if (
        report["pipeline"] != "cctv-incidents-v1"
        or report["signature"] != state["signature"]
    ):
        raise ValueError("Incident publication signature mismatch")
    path = folder / "events.jsonl"
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Incident ledger exceeds size limit")
    records = [
        IncidentEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    if (
        len(records) != report["events"]
        or len(records) > MAX_RECORDS
        or len({e.event_id for e in records}) != len(records)
    ):
        raise ValueError("Incident count/identity mismatch")
    if any(e.provenance_sha256 != checksum(report["signature"]) for e in records):
        raise ValueError("Incident provenance changed")
    return report, records


def process_incidents(
    manifest_path: Path,
    roi_path: Path,
    config_path: Path,
    tracking: Path,
    output: Path,
    allow_draft: bool = False,
) -> dict:
    """CPU, read-only input, per-video checkpoint; invalid frames fail publication."""
    manifest = load_manifest(manifest_path, require_files=False)
    rois = load_config(roi_path, manifest)
    roi_map = {r.camera_id: r for r in rois.cameras}
    config = IncidentConfig.model_validate_json(
        config_path.read_text(encoding="utf-8-sig")
    )
    tracking = tracking.resolve()
    protected = [
        manifest.path,
        manifest.raw_root,
        roi_path.resolve(),
        config_path.resolve(),
        tracking,
    ]
    output = protect_output(output, protected)
    ts = publication(
        tracking,
        "track_state.json",
        ("tracks.db", "frames.jsonl", "tracking_manifest.json"),
        "publication",
    )
    tm = read_json(tracking / "tracking_manifest.json")
    if (
        tm["signature"] != ts["signature"]
        or tm["run_id"] != ts["run_id"]
        or ts["signature"]["manifest_sha256"] != digest(manifest.path)
    ):
        raise ValueError("Tracking manifest/run mismatch")
    sources = {v.video_id: v for v in manifest.videos}
    selected = tm["videos"]
    if (
        not selected
        or len(set(selected)) != len(selected)
        or not set(selected) <= sources.keys()
    ):
        raise ValueError("Invalid tracking video set")
    for vid in selected:
        roi = roi_map[sources[vid].camera_id]
        if roi.polygon is None or (roi.status != "approved" and not allow_draft):
            raise ValueError(
                "Reviewed ROI required; explicit --allow-draft is needed for demo"
            )
    signature = dict(
        pipeline="cctv-incidents-v1",
        source_manifest_sha256=digest(manifest.path),
        tracking={
            name: ts["publication"][name]
            for name in ("tracks.db", "frames.jsonl", "tracking_manifest.json")
        },
        tracking_signature=ts["signature"],
        roi_sha256=digest(roi_path),
        config_sha256=digest(config_path),
        config=config.model_dump(mode="json"),
        allow_draft=allow_draft,
        code_sha256=digest(Path(__file__).resolve()),
        event_contract_sha256=digest(Path(__file__).with_name("events.py").resolve()),
    )
    state_path = output / "event_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in (
        ".ingest.lock",
        "incidents.log",
        "event_manifest.json",
        "event_state.json",
        "events.jsonl",
        "events.jsonl.tmp",
    ):
        reject_link(output / name)
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(signature=signature, status="processing", videos={})
        )
        if state["signature"] != signature:
            raise ValueError("Input/recipe changed; use a new output directory")
        if state["status"] == "complete":
            _, records = load_incidents(output)
            return dict(events=len(records), videos_skipped=len(selected), resumed=True)
        save_json(state_path, state)
        logger = logging.getLogger("cctv.incidents")
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(output / "incidents.log", encoding="utf-8")
        logger.addHandler(handler)
        errors = {}
        try:
            with closing(
                sqlite3.connect(
                    (tracking / "tracks.db").as_uri() + "?mode=ro", uri=True
                )
            ) as conn:
                for vid in tqdm(selected, desc="Incident videos"):
                    try:
                        if vid in state["videos"]:
                            saved = state["videos"][vid]
                            if saved["sha256"] != checksum(saved["events"]):
                                raise ValueError("Incident checkpoint changed")
                            for row in saved["events"]:
                                e = IncidentEvent.model_validate_json(canonical(row))
                                if e.video_id != vid or e.provenance_sha256 != checksum(
                                    signature
                                ):
                                    raise ValueError(
                                        "Incident checkpoint scope mismatch"
                                    )
                            continue

                        def frames():
                            count = conn.execute(
                                "SELECT count(*) FROM frames WHERE video_id=?", (vid,)
                            ).fetchone()[0]
                            if not count:
                                raise ValueError("No tracked frames")
                            rows = conn.execute(
                                "SELECT frame_idx,timestamp_sec,segment_id,payload FROM frames WHERE video_id=? ORDER BY frame_idx",
                                (vid,),
                            )
                            for idx, stamp, segment, payload in tqdm(
                                rows, total=count, desc=vid, unit="frame", leave=False
                            ):
                                try:
                                    frame = TrackFrame.model_validate_json(payload)
                                    if (
                                        frame.locator.frame_idx,
                                        frame.locator.timestamp_sec,
                                        frame.segment_id,
                                    ) != (idx, stamp, segment) or (
                                        frame.run_id,
                                        frame.locator.video_id,
                                        frame.locator.camera_id,
                                    ) != (ts["run_id"], vid, sources[vid].camera_id):
                                        raise ValueError("Frame/checkpoint mismatch")
                                    start = ts["signature"]["start_sec"]
                                    if (
                                        not start
                                        <= stamp
                                        < (start + ts["signature"]["duration_sec"] if ts["signature"]["duration_sec"] is not None else float("inf"))
                                    ):
                                        raise ValueError("Frame outside processed clip")
                                    yield frame
                                except ValueError:
                                    logger.exception(
                                        "frame_error %s frame=%s", vid, idx
                                    )
                                    raise

                        records = [
                            e.model_dump(mode="json")
                            for e in screen_video(
                                frames(),
                                roi_map[sources[vid].camera_id],
                                config,
                                checksum(signature),
                            )
                        ]
                        state["videos"][vid] = dict(
                            events=records, sha256=checksum(records)
                        )
                        save_json(state_path, state)
                        logger.info("video_complete %s events=%s", vid, len(records))
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        errors[vid] = str(exc)
                        logger.exception("video_error %s", vid)
        finally:
            logger.removeHandler(handler)
            handler.close()
        if errors:
            save_json(state_path, dict(state, errors=errors))
            raise ValueError(f"Incident videos failed: {errors}")
        records = [e for vid in selected for e in state["videos"][vid]["events"]]
        temporary = output / "events.jsonl.tmp"
        temporary.write_text(
            "".join(canonical(e) + "\n" for e in records), encoding="utf-8"
        )
        temporary.replace(output / "events.jsonl")
        save_json(
            output / "event_manifest.json",
            dict(
                pipeline="cctv-incidents-v1",
                signature=signature,
                events=len(records),
                videos=selected,
                protected_roots=[str(p) for p in protected],
                demo_only=any(
                    roi_map[sources[v].camera_id].status != "approved" for v in selected
                ),
                thresholds_calibrated=False,
                operational_ready=False,
            ),
        )
        state.pop("errors", None)
        state.update(
            status="complete",
            artifacts={
                name: digest(output / name)
                for name in ("events.jsonl", "event_manifest.json")
            },
        )
        save_json(state_path, state)
        return dict(events=len(records), videos_done=len(selected), resumed=False)


def main() -> int:
    """Run screening on native tracking without loading GPU models."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "roi", "config", "tracking-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--allow-draft", action="store_true")
    args = parser.parse_args()
    try:
        result = process_incidents(
            args.manifest,
            args.roi,
            args.config,
            args.tracking_dir,
            args.output,
            args.allow_draft,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"Incident screening failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
