"""Protocol-bounded 27+9 threshold grid search and frozen test split evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal

from pydantic import Field

from pipeline.cctv.benchmark_events import (
    AnnotationSet,
    Prediction,
    SplitManifest,
    evaluate_records,
)
from pipeline.cctv.congestion import (
    CameraThresholds,
    MetricsConfig,
    summarize_video,
)
from pipeline.cctv.events import (
    Signal,
    candidate_intervals,
    protect_output,
    read_json,
)
from pipeline.cctv.incidents import (
    IncidentConfig,
    IncidentEvent,
    screen_video,
)
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import Manifest, load_manifest
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import CameraROI, Contract, load_config as load_traffic_config

LOG = logging.getLogger(__name__)

# Protocol Grid Constants (Docs: CCTV_M10_EVALUATION.md)
CONGESTION_OCCUPANCIES = (0.20, 0.25, 0.30)
CONGESTION_MOTIONS = (8.0, 12.0, 16.0)
CONGESTION_PERSISTENCES = (4.0, 6.0, 8.0)

INCIDENT_STATIONARIES = (0.02, 0.04, 0.06)
INCIDENT_DURATIONS = (1.0, 2.0, 3.0)

BASELINE_CONGESTION = {
    "occupancy_min": 0.25,
    "motion_max_px_per_sec": 12.0,
    "persistence_sec": 6.0,
}

BASELINE_INCIDENT = {
    "stationary_max_body_lengths_per_sec": 0.04,
    "stationary_duration_sec": 2.0,
}


def load_video_frames_from_db(
    tracking_dir: Path, video_id: str, manifest: Manifest
) -> list[TrackFrame]:
    """Stream and validate TrackFrames from tracking SQLite DB."""
    db_path = tracking_dir / "tracks.db"
    reject_link(db_path)
    frames = []
    with closing(
        sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ) as conn:
        for payload, in conn.execute(
            "SELECT payload FROM frames WHERE video_id=? ORDER BY frame_idx",
            (video_id,),
        ):
            record = TrackFrame.model_validate_json(payload)
            record.locator.bind(manifest)
            frames.append(record)
    return frames


def congestion_predictions_from_windows(
    video_id: str,
    camera_id: str,
    windows: list[dict],
    demo: bool = True,
) -> list[Prediction]:
    """Convert summary traffic windows into evaluated congestion predictions."""
    signals = [Signal.model_validate(w) for w in windows]
    intervals = candidate_intervals(signals)
    predictions = []
    for idx, (start, end, is_draft) in enumerate(intervals):
        predictions.append(
            Prediction(
                event_id=f"pred-cong-{video_id}-{idx:04d}",
                camera_id=camera_id,
                video_id=video_id,
                label="congestion",
                start_sec=start,
                end_sec=end,
                trigger_source_sec=start,
                synthetic_event=False,
                demo_only=demo or is_draft,
            )
        )
    return predictions


def incident_predictions_from_events(
    events: list[IncidentEvent],
) -> list[Prediction]:
    """Map IncidentEvents into evaluated Prediction objects."""
    predictions = []
    for ev in events:
        trigger = (
            ev.evidence[-1].locator.timestamp_sec
            if ev.evidence
            else ev.start_sec
        )
        predictions.append(
            Prediction(
                event_id=ev.event_id,
                camera_id=ev.camera_id,
                video_id=ev.video_id,
                label="road_incident",
                start_sec=ev.start_sec,
                end_sec=ev.end_sec,
                trigger_source_sec=trigger,
                synthetic_event=ev.synthetic_event,
                demo_only=ev.demo_only,
            )
        )
    return predictions

def select_best_recipe(
    sweep: list[dict],
    param_keys: tuple[str, ...],
) -> tuple[dict, str]:
    """Select optimal recipe by F1 descending, FP/h ascending, distance ascending, parameters ascending."""
    defined = [r for r in sweep if r.get("f1") is not None]
    if not defined:
        baseline_entry = min(sweep, key=lambda r: (r["relative_distance"], tuple(r[k] for k in param_keys)))
        return baseline_entry, "baseline_retained_no_defined_f1"

    def sort_key(item: dict):
        return (
            -item["f1"],
            item["fp_per_hour"],
            item["relative_distance"],
            tuple(item[k] for k in param_keys),
        )

    best = min(defined, key=sort_key)
    reason = f"highest_development_f1_{best['f1']:.4f}_fp_h_{best['fp_per_hour']:.2f}"
    return best, reason


def relative_distance_congestion(
    occ: float, motion: float, persist: float
) -> float:
    """Normalized relative Euclidean distance from baseline congestion recipe."""
    return (
        ((occ - BASELINE_CONGESTION["occupancy_min"]) / BASELINE_CONGESTION["occupancy_min"]) ** 2
        + ((motion - BASELINE_CONGESTION["motion_max_px_per_sec"]) / BASELINE_CONGESTION["motion_max_px_per_sec"]) ** 2
        + ((persist - BASELINE_CONGESTION["persistence_sec"]) / BASELINE_CONGESTION["persistence_sec"]) ** 2
    )


def relative_distance_incident(stat: float, dur: float) -> float:
    """Normalized relative Euclidean distance from baseline incident recipe."""
    return (
        ((stat - BASELINE_INCIDENT["stationary_max_body_lengths_per_sec"]) / BASELINE_INCIDENT["stationary_max_body_lengths_per_sec"]) ** 2
        + ((dur - BASELINE_INCIDENT["stationary_duration_sec"]) / BASELINE_INCIDENT["stationary_duration_sec"]) ** 2
    )


def calibrate_and_evaluate(
    manifest_path: Path,
    tracking_dir: Path,
    roi_path: Path,
    annotation_path: Path,
    split_path: Path,
    output_dir: Path,
    congestion_config_path: Path | None = None,
    incident_config_path: Path | None = None,
    allow_draft_roi: bool = True,
    frames_cache: dict[str, list[TrackFrame]] | None = None,
) -> dict:
    """Execute the 27+9 grid calibration on development and evaluate frozen test split."""
    manifest_path = manifest_path.resolve()
    tracking_dir = tracking_dir.resolve()
    roi_path = roi_path.resolve()
    annotation_path = annotation_path.resolve()
    split_path = split_path.resolve()
    output_dir = output_dir.resolve()

    manifest = load_manifest(manifest_path, require_files=False)
    annotations = AnnotationSet.model_validate_json(
        annotation_path.read_text(encoding="utf-8-sig")
    )
    splits = SplitManifest.model_validate_json(
        split_path.read_text(encoding="utf-8-sig")
    )
    annotations.bind(manifest)
    splits.bind(manifest)

    roi_config = load_traffic_config(roi_path, manifest)
    rois = {c.camera_id: c for c in roi_config.cameras}

    # Baseline configurations
    if congestion_config_path is not None and congestion_config_path.exists():
        cong_doc = read_json(congestion_config_path)
    else:
        cong_doc = {
            "schema_version": 1,
            "window_sec": 2.0,
            "mask_width": 320,
            "max_gap_sec": 0.5,
            "min_coverage": 0.9,
            "min_motion_pairs": 10,
            "cameras": [
                {
                    "camera_id": c["camera_id"],
                    "occupancy_min": BASELINE_CONGESTION["occupancy_min"],
                    "vehicle_count_min": 5.0,
                    "motion_max_px_per_sec": BASELINE_CONGESTION["motion_max_px_per_sec"],
                    "persistence_sec": BASELINE_CONGESTION["persistence_sec"],
                }
                for c in manifest.cameras
            ],
        }

    if incident_config_path is not None and incident_config_path.exists():
        inc_doc = read_json(incident_config_path)
    else:
        inc_doc = {
            "schema_version": 1,
            "moving_min_body_lengths_per_sec": 0.3,
            "stationary_max_body_lengths_per_sec": BASELINE_INCIDENT["stationary_max_body_lengths_per_sec"],
            "moving_duration_sec": 1.0,
            "stationary_duration_sec": BASELINE_INCIDENT["stationary_duration_sec"],
            "max_gap_sec": 0.5,
            "confidence_min": 0.4,
            "box_area_min": 0.0002,
            "max_box_scale_ratio": 1.5,
            "queue_filter_enabled": True,
            "neighbor_radius_diagonals": 3.0,
            "min_queue_neighbors": 2,
            "stop_sync_window_sec": 2.0,
        }

    # Split assignments
    split_map = {v.video_id: v.split for v in splits.videos}
    dev_videos = [v.video_id for v in manifest.videos if split_map.get(v.video_id) == "development"]
    test_videos = [v.video_id for v in manifest.videos if split_map.get(v.video_id) == "test"]
    needed_videos = sorted(set(dev_videos + test_videos))

    # Load / cache frames for needed videos
    frames_by_video: dict[str, list[TrackFrame]] = {}
    if frames_cache is not None:
        for vid in needed_videos:
            if vid in frames_cache:
                frames_by_video[vid] = frames_cache[vid]
    else:
        for vid in needed_videos:
            frames_by_video[vid] = load_video_frames_from_db(tracking_dir, vid, manifest)

    # Signature and protections
    input_files = [manifest_path, roi_path, annotation_path, split_path]
    if (tracking_dir / "tracks.db").exists():
        input_files.append(tracking_dir / "tracks.db")
    signature = {
        "inputs": {str(p): digest(p) for p in input_files if p.exists()},
        "pipeline": "cctv-calibration-v1",
        "congestions": 27,
        "incidents": 9,
    }

    protect_output(output_dir, [manifest_path, roi_path, annotation_path, split_path, tracking_dir])
    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / "calibration_report.json"
    state_file = output_dir / "calibration_state.json"

    with output_lease(output_dir):
        if state_file.exists():
            state = read_json(state_file)
            if (
                state.get("status") == "complete"
                and state.get("signature") == signature
                and report_file.exists()
                and digest(report_file) == state.get("report_sha256")
            ):
                return read_json(report_file)

        # ---------------------------------------------------------
        # Part 1: Sweep 27 Congestion Recipes on Development Split
        # ---------------------------------------------------------
        congestion_sweep: list[dict] = []
        video_cameras = {v.video_id: v.camera_id for v in manifest.videos}

        cong_idx = 0
        for occ in sorted(CONGESTION_OCCUPANCIES):
            for mot in sorted(CONGESTION_MOTIONS):
                for per in sorted(CONGESTION_PERSISTENCES):
                    cong_idx += 1
                    recipe_id = f"cong-{cong_idx:02d}"
                    rel_dist = relative_distance_congestion(occ, mot, per)

                    # Build prediction set across all dev videos
                    dev_predictions: list[Prediction] = []
                    for vid in dev_videos:
                        cam_id = video_cameras[vid]
                        roi = rois[cam_id]
                        frames = frames_by_video.get(vid, [])
                        if not frames:
                            continue

                        # Construct CameraThresholds for this recipe
                        thresh = CameraThresholds(
                            camera_id=cam_id,
                            occupancy_min=occ,
                            vehicle_count_min=5.0,
                            motion_max_px_per_sec=mot,
                            persistence_sec=per,
                        )
                        # MetricsConfig
                        mconfig = MetricsConfig.model_validate(
                            dict(
                                cong_doc,
                                cameras=tuple(thresh if c["camera_id"] == cam_id else CameraThresholds(**c) for c in cong_doc["cameras"]),
                            )
                        )
                        windows = summarize_video(
                            frames, roi, mconfig, thresh, 0.0, None, draft=allow_draft_roi
                        )
                        dev_predictions.extend(
                            congestion_predictions_from_windows(
                                vid, cam_id, windows, demo=allow_draft_roi
                            )
                        )

                    # Evaluate on development split at IoU 0.3
                    eval_dev = evaluate_records(
                        annotations, splits, dev_predictions, target="development", iou_threshold=0.3
                    )
                    met = eval_dev.get("metrics")
                    f1 = met.get("f1") if met else None
                    prec = met.get("precision") if met else None
                    rec = met.get("recall") if met else None
                    tp = met.get("tp", 0) if met else 0
                    fp = met.get("fp", 0) if met else 0
                    fn = met.get("fn", 0) if met else 0
                    fp_per_h = met.get("false_alarms_per_camera_hour", 0.0) if met else 0.0

                    congestion_sweep.append({
                        "recipe_id": recipe_id,
                        "occupancy_min": occ,
                        "motion_max_px_per_sec": mot,
                        "persistence_sec": per,
                        "relative_distance": round(rel_dist, 6),
                        "f1": f1,
                        "precision": prec,
                        "recall": rec,
                        "tp": tp,
                        "fp": fp,
                        "fn": fn,
                        "fp_per_hour": fp_per_h,
                        "prediction_count": len(dev_predictions),
                    })

        # ---------------------------------------------------------
        # Part 2: Sweep 9 Incident Recipes on Development Split
        # ---------------------------------------------------------
        incident_sweep: list[dict] = []
        inc_idx = 0
        for stat in sorted(INCIDENT_STATIONARIES):
            for dur in sorted(INCIDENT_DURATIONS):
                inc_idx += 1
                recipe_id = f"inc-{inc_idx:02d}"
                rel_dist = relative_distance_incident(stat, dur)

                # Update config
                curr_inc_cfg = IncidentConfig.model_validate(
                    dict(
                        inc_doc,
                        stationary_max_body_lengths_per_sec=stat,
                        stationary_duration_sec=dur,
                    )
                )

                dev_predictions: list[Prediction] = []
                for vid in dev_videos:
                    cam_id = video_cameras[vid]
                    roi = rois[cam_id]
                    frames = frames_by_video.get(vid, [])
                    if not frames:
                        continue
                    prov_hash = hashlib.sha256(f"calibration-dev-{vid}".encode()).hexdigest()
                    events = screen_video(
                        frames,
                        roi,
                        curr_inc_cfg,
                        provenance=prov_hash,
                        synthetic=allow_draft_roi,
                    )
                    dev_predictions.extend(incident_predictions_from_events(events))

                eval_dev = evaluate_records(
                    annotations, splits, dev_predictions, target="development", iou_threshold=0.3
                )
                met = eval_dev.get("metrics")
                f1 = met.get("f1") if met else None
                prec = met.get("precision") if met else None
                rec = met.get("recall") if met else None
                tp = met.get("tp", 0) if met else 0
                fp = met.get("fp", 0) if met else 0
                fn = met.get("fn", 0) if met else 0
                fp_per_h = met.get("false_alarms_per_camera_hour", 0.0) if met else 0.0

                incident_sweep.append({
                    "recipe_id": recipe_id,
                    "stationary_max_body_lengths_per_sec": stat,
                    "stationary_duration_sec": dur,
                    "relative_distance": round(rel_dist, 6),
                    "f1": f1,
                    "precision": prec,
                    "recall": rec,
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "fp_per_hour": fp_per_h,
                    "prediction_count": len(dev_predictions),
                })

        # ---------------------------------------------------------
        # Part 3: Selection via Tie-Breaking Rules
        # ---------------------------------------------------------
        best_cong, cong_reason = select_best_recipe(
            congestion_sweep,
            ("occupancy_min", "motion_max_px_per_sec", "persistence_sec"),
        )
        best_inc, inc_reason = select_best_recipe(
            incident_sweep,
            ("stationary_max_body_lengths_per_sec", "stationary_duration_sec"),
        )

        # ---------------------------------------------------------
        # Part 4: Evaluate Selected Recipes on Test Split
        # ---------------------------------------------------------
        test_predictions: list[Prediction] = []

        # Generate congestion predictions on test split using best_cong
        opt_thresh_occ = best_cong["occupancy_min"]
        opt_thresh_mot = best_cong["motion_max_px_per_sec"]
        opt_thresh_per = best_cong["persistence_sec"]

        for vid in test_videos:
            cam_id = video_cameras[vid]
            roi = rois[cam_id]
            frames = frames_by_video.get(vid, [])
            if not frames:
                continue

            thresh = CameraThresholds(
                camera_id=cam_id,
                occupancy_min=opt_thresh_occ,
                vehicle_count_min=5.0,
                motion_max_px_per_sec=opt_thresh_mot,
                persistence_sec=opt_thresh_per,
            )
            mconfig = MetricsConfig.model_validate(
                dict(
                    cong_doc,
                    cameras=tuple(thresh if c["camera_id"] == cam_id else CameraThresholds(**c) for c in cong_doc["cameras"]),
                )
            )
            windows = summarize_video(frames, roi, mconfig, thresh, 0.0, None, draft=allow_draft_roi)
            test_predictions.extend(
                congestion_predictions_from_windows(vid, cam_id, windows, demo=allow_draft_roi)
            )

        # Generate incident predictions on test split using best_inc
        opt_inc_cfg = IncidentConfig.model_validate(
            dict(
                inc_doc,
                stationary_max_body_lengths_per_sec=best_inc["stationary_max_body_lengths_per_sec"],
                stationary_duration_sec=best_inc["stationary_duration_sec"],
            )
        )
        for vid in test_videos:
            cam_id = video_cameras[vid]
            roi = rois[cam_id]
            frames = frames_by_video.get(vid, [])
            if not frames:
                continue
            prov_hash = hashlib.sha256(f"calibration-test-{vid}".encode()).hexdigest()
            events = screen_video(
                frames, roi, opt_inc_cfg, provenance=prov_hash, synthetic=allow_draft_roi
            )
            test_predictions.extend(incident_predictions_from_events(events))

        # Evaluate across IoU 0.1, 0.3, 0.5 on test split
        test_eval_01 = evaluate_records(annotations, splits, test_predictions, target="test", iou_threshold=0.1)
        test_eval_03 = evaluate_records(annotations, splits, test_predictions, target="test", iou_threshold=0.3)
        test_eval_05 = evaluate_records(annotations, splits, test_predictions, target="test", iou_threshold=0.5)

        # ---------------------------------------------------------
        # Part 5: Assemble Report and Checkpoint State
        # ---------------------------------------------------------
        report = {
            "schema_version": 1,
            "pipeline": "cctv-calibration-v1",
            "status": "CALIBRATION_COMPLETE",
            "operational_ready": False,
            "synthetic_only": annotations.mode == "synthetic",
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "development_split": "development",
            "test_split": "test",
            "baseline_congestion": BASELINE_CONGESTION,
            "baseline_incident": BASELINE_INCIDENT,
            "congestion_sweep_count": len(congestion_sweep),
            "incident_sweep_count": len(incident_sweep),
            "congestion_sweep": congestion_sweep,
            "incident_sweep": incident_sweep,
            "selected_congestion": {
                "recipe_id": best_cong["recipe_id"],
                "parameters": {
                    "occupancy_min": best_cong["occupancy_min"],
                    "motion_max_px_per_sec": best_cong["motion_max_px_per_sec"],
                    "persistence_sec": best_cong["persistence_sec"],
                },
                "development_metrics": {
                    "f1": best_cong["f1"],
                    "precision": best_cong["precision"],
                    "recall": best_cong["recall"],
                    "tp": best_cong["tp"],
                    "fp": best_cong["fp"],
                    "fn": best_cong["fn"],
                    "fp_per_hour": best_cong["fp_per_hour"],
                },
                "selection_reason": cong_reason,
            },
            "selected_incident": {
                "recipe_id": best_inc["recipe_id"],
                "parameters": {
                    "stationary_max_body_lengths_per_sec": best_inc["stationary_max_body_lengths_per_sec"],
                    "stationary_duration_sec": best_inc["stationary_duration_sec"],
                },
                "development_metrics": {
                    "f1": best_inc["f1"],
                    "precision": best_inc["precision"],
                    "recall": best_inc["recall"],
                    "tp": best_inc["tp"],
                    "fp": best_inc["fp"],
                    "fn": best_inc["fn"],
                    "fp_per_hour": best_inc["fp_per_hour"],
                },
                "selection_reason": inc_reason,
            },
            "test_evaluation": {
                "iou_0_1": test_eval_01,
                "iou_0_3": test_eval_03,
                "iou_0_5": test_eval_05,
            },
            "signature": signature,
        }

        save_json(report_file, report)
        save_json(
            state_file,
            {
                "schema_version": 1,
                "pipeline": "cctv-calibration-v1",
                "signature": signature,
                "status": "complete",
                "report_sha256": digest(report_file),
            },
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--tracking-dir", type=Path, required=True)
    parser.add_argument("--roi", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--congestion-config", type=Path, default=None)
    parser.add_argument("--incident-config", type=Path, default=None)
    parser.add_argument("--allow-draft-roi", action="store_true", default=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    try:
        calibrate_and_evaluate(
            manifest_path=args.manifest,
            tracking_dir=args.tracking_dir,
            roi_path=args.roi,
            annotation_path=args.annotations,
            split_path=args.splits,
            output_dir=args.output,
            congestion_config_path=args.congestion_config,
            incident_config_path=args.incident_config,
            allow_draft_roi=args.allow_draft_roi,
        )
        print(f"Calibration and test evaluation complete: {args.output}")
        return 0
    except Exception as exc:
        LOG.exception("Calibration failed")
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
