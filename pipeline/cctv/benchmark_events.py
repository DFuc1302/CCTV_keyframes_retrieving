"""Validated annotation/splits and exact temporal-IoU event evaluation."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from pipeline.cctv.event_review import load_events
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import Manifest, identity, load_manifest
from pipeline.cctv.traffic import Contract, NonNegative

Label = Literal["congestion", "road_incident", "accident", "moving_to_stop"]
Split = Literal["development", "test", "unassigned"]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Interval(Contract):
    """Source PTS interval, half-open and strictly positive in duration."""

    start_sec: NonNegative
    end_sec: NonNegative

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_sec <= self.start_sec:
            raise ValueError("Interval must have end > start")
        return self


class Truth(Interval):
    """Independently annotated observable event, not a rule review decision."""

    event_id: str
    label: Label


class Annotation(Interval):
    """Pending/unknown intervals never imply negative footage."""

    camera_id: str
    video_id: str
    review_status: Literal["pending", "unknown", "reviewed"]
    reviewed_by: Annotated[tuple[str, ...], Field(max_length=10)]
    reviewed_at: str | None
    blind_to_predictions: bool
    events: Annotated[tuple[Truth, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        identity(self.camera_id, "camera_id")
        identity(self.video_id, "video_id")
        if self.review_status == "reviewed":
            if (
                len({name.casefold() for name in self.reviewed_by}) < 2
                or not self.blind_to_predictions
                or self.reviewed_at is None
            ):
                raise ValueError(
                    "Reviewed interval needs two independent reviewers, blind review and timestamp"
                )
        elif self.events:
            raise ValueError("Pending/unknown intervals must not contain event labels")
        if self.review_status == "pending" and (
            self.reviewed_by or self.reviewed_at or self.blind_to_predictions
        ):
            raise ValueError("Pending interval cannot claim a performed review")
        for reviewer in self.reviewed_by:
            if (
                not reviewer.strip()
                or reviewer != reviewer.strip()
                or any(ord(c) < 32 for c in reviewer)
            ):
                raise ValueError("Invalid reviewer name")
        if (
            self.reviewed_at is not None
            and datetime.fromisoformat(
                self.reviewed_at.replace("Z", "+00:00")
            ).utcoffset()
            is None
        ):
            raise ValueError("Review timestamp requires timezone")
        for event in self.events:
            identity(event.event_id, "event_id")
            if not self.start_sec <= event.start_sec < event.end_sec <= self.end_sec:
                raise ValueError("Truth interval outside reviewed coverage")
        return self


class AnnotationSet(Contract):
    """Source-bound label collection, explicitly independent or synthetic."""

    schema_version: Literal[1]
    mode: Literal["independent", "synthetic"]
    source_manifest_sha256: SHA
    intervals: Annotated[tuple[Annotation, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def no_overlap(self) -> Self:
        previous = {}
        ids = set()
        for row in sorted(self.intervals, key=lambda r: (r.video_id, r.start_sec)):
            if row.video_id in previous and row.start_sec < previous[row.video_id]:
                raise ValueError(
                    "Overlapping annotation intervals double-count coverage"
                )
            previous[row.video_id] = row.end_sec
            for event in row.events:
                if event.event_id in ids:
                    raise ValueError("Duplicate truth identity")
                ids.add(event.event_id)
        return self

    def bind(self, manifest: Manifest) -> None:
        """Validate exact manifest fingerprint and canonical camera/video scopes."""
        if self.source_manifest_sha256 != digest(manifest.path):
            raise ValueError("Annotations belong to another manifest")
        videos = {v.video_id: v.camera_id for v in manifest.videos}
        if any(videos.get(r.video_id) != r.camera_id for r in self.intervals):
            raise ValueError("Annotation source scope mismatch")


class SplitRow(Contract):
    """Recording groups are declared provenance, not inferred from filenames."""

    video_id: str
    recording_group_id: str
    grouping_status: Literal["unverified", "verified", "synthetic"]
    split: Split

    @model_validator(mode="after")
    def valid(self) -> Self:
        identity(self.video_id, "video_id")
        identity(self.recording_group_id, "recording_group_id")
        if self.grouping_status == "unverified" and self.split != "unassigned":
            raise ValueError("Unverified recording group must remain unassigned")
        return self


class SplitManifest(Contract):
    """Block video/session leakage; optional camera holdout."""

    schema_version: Literal[1]
    mode: Literal["independent", "synthetic"]
    source_manifest_sha256: SHA
    camera_holdout: bool
    videos: Annotated[tuple[SplitRow, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        videos, groups = set(), {}
        for row in self.videos:
            if row.video_id in videos:
                raise ValueError("Video occurs more than once across splits")
            videos.add(row.video_id)
            if (
                row.recording_group_id in groups
                and groups[row.recording_group_id] != row.split
            ):
                raise ValueError("Recording group leakage across splits")
            groups[row.recording_group_id] = row.split
            if (row.grouping_status == "synthetic") != (self.mode == "synthetic"):
                raise ValueError("Synthetic grouping/mode mismatch")
        return self

    def bind(self, manifest: Manifest) -> None:
        """Require all canonical source videos and optional camera separation."""
        if self.source_manifest_sha256 != digest(manifest.path):
            raise ValueError("Splits belong to another manifest")
        videos = {v.video_id: v.camera_id for v in manifest.videos}
        if {r.video_id for r in self.videos} != set(videos):
            raise ValueError(
                "Split manifest must cover each canonical video exactly once"
            )
        if self.camera_holdout:
            camera_splits = {}
            for row in self.videos:
                if row.split == "unassigned":
                    continue
                camera = videos[row.video_id]
                if camera in camera_splits and camera_splits[camera] != row.split:
                    raise ValueError("Camera holdout leakage")
                camera_splits[camera] = row.split


class Prediction(Interval):
    """Projection from sealed events; review decisions never become truth labels."""

    event_id: str
    camera_id: str
    video_id: str
    label: Label
    trigger_source_sec: NonNegative | None
    synthetic_event: bool
    demo_only: bool

    @model_validator(mode="after")
    def trigger_inside(self) -> Self:
        for name in ("event_id", "camera_id", "video_id"):
            identity(getattr(self, name), name)
        if (
            self.trigger_source_sec is not None
            and not self.start_sec <= self.trigger_source_sec < self.end_sec
        ):
            raise ValueError("Prediction trigger must be inside its interval")
        return self


def temporal_iou(a: Interval, b: Interval) -> float:
    """Intersection duration divided by union duration, including zero overlap."""
    overlap = max(0.0, min(a.end_sec, b.end_sec) - max(a.start_sec, b.start_sec))
    return overlap / (a.end_sec - a.start_sec + b.end_sec - b.start_sec - overlap)


def optimal_pairs(weights: list[list[float]]) -> list[tuple[int, int]]:
    """Exact max-total-IoU matching, with bounded bitmask DP and no greedy fallback."""
    n = len(weights)
    m = len(weights[0]) if n else 0
    if n > 16 or m > 16 or any(len(r) != m for r in weights):
        raise ValueError(
            "Exact matching supports at most 16 predictions/truths per video/label"
        )
    if any(not math.isfinite(w) or not 0 <= w <= 1 for row in weights for w in row):
        raise ValueError("Invalid IoU weight")
    states = {0: (0.0, ())}
    steps = 0
    for i, row in enumerate(weights):
        following = dict(states)
        for used, (score, pairs) in states.items():
            for j, weight in enumerate(row):
                steps += 1
                if steps > 2_000_000:
                    raise ValueError("Exact matching search limit exceeded")
                if weight <= 0 or used & (1 << j):
                    continue
                mask = used | (1 << j)
                proposal = (score + weight, pairs + ((i, j),))
                old = following.get(mask)
                if (
                    old is None
                    or proposal[0] > old[0]
                    or (proposal[0] == old[0] and proposal[1] < old[1])
                ):
                    following[mask] = proposal
        states = following
    return list(min(states.values(), key=lambda p: (-p[0], -len(p[1]), p[1]))[1])


def metrics(tp: int, fp: int, fn: int, seconds: float) -> dict:
    """Undefined denominators remain null; event evaluation has no TN count."""
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return dict(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        reviewed_camera_seconds=seconds,
        false_alarms_per_camera_hour=fp / (seconds / 3600) if seconds else None,
    )


def evaluate_records(
    annotations: AnnotationSet,
    splits: SplitManifest,
    predictions: list[Prediction],
    target: Literal["development", "test"] = "test",
    iou_threshold: float = 0.3,
) -> dict:
    """Score only full predictions inside reviewed, selected-split coverage."""
    if not math.isfinite(iou_threshold) or not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be finite and in (0,1]")
    if (
        annotations.mode != splits.mode
        or annotations.source_manifest_sha256 != splits.source_manifest_sha256
    ):
        raise ValueError("Annotation/split provenance mismatch")
    if len({p.event_id for p in predictions}) != len(predictions):
        raise ValueError("Duplicate prediction identity")
    video_splits = {r.video_id: r.split for r in splits.videos}
    covered = [
        r
        for r in annotations.intervals
        if r.review_status == "reviewed" and video_splits.get(r.video_id) == target
    ]
    seconds = sum(r.end_sec - r.start_sec for r in covered)
    considered, ignored = [], []
    for prediction in predictions:
        reason = None
        if annotations.mode == "independent" and prediction.synthetic_event:
            reason = "synthetic_prediction_in_independent_benchmark"
        elif video_splits.get(prediction.video_id) != target:
            reason = "outside_selected_split"
        elif not any(
            (r.camera_id, r.video_id) == (prediction.camera_id, prediction.video_id)
            and r.start_sec <= prediction.start_sec < prediction.end_sec <= r.end_sec
            for r in covered
        ):
            reason = "boundary_unknown_or_unreviewed"
        if reason:
            ignored.append(dict(event_id=prediction.event_id, reason=reason))
        else:
            considered.append(prediction)
    truths = [(r.camera_id, r.video_id, t) for r in covered for t in r.events]
    matches = []
    matched_p, matched_t = set(), set()
    groups = {(p.camera_id, p.video_id, p.label) for p in considered} | {
        (c, v, t.label) for c, v, t in truths
    }
    for camera, video, label in sorted(groups):
        pred = sorted(
            [
                p
                for p in considered
                if (p.camera_id, p.video_id, p.label) == (camera, video, label)
            ],
            key=lambda p: p.event_id,
        )
        truth = sorted(
            [t for c, v, t in truths if (c, v, t.label) == (camera, video, label)],
            key=lambda t: t.event_id,
        )
        if len(pred) > 16 or len(truth) > 16:
            raise ValueError(
                "Exact matching supports at most 16 predictions/truths per video/label"
            )
        weights = [
            [
                score if (score := temporal_iou(p, t)) >= iou_threshold else 0.0
                for t in truth
            ]
            for p in pred
        ]
        for i, j in optimal_pairs(weights):
            p, t = pred[i], truth[j]
            matched_p.add(p.event_id)
            matched_t.add(t.event_id)
            matches.append(
                dict(
                    prediction_id=p.event_id,
                    truth_id=t.event_id,
                    camera_id=camera,
                    video_id=video,
                    label=label,
                    temporal_iou=weights[i][j],
                    detection_delay_source_sec=p.trigger_source_sec - t.start_sec
                    if p.trigger_source_sec is not None
                    else None,
                    delay_basis="confirmation_evidence_pts"
                    if p.trigger_source_sec is not None
                    else "unavailable",
                )
            )
    fp = [p for p in considered if p.event_id not in matched_p]
    fn = [(c, v, t) for c, v, t in truths if t.event_id not in matched_t]
    by_camera = {}
    for camera in sorted({r.camera_id for r in covered}):
        by_camera[camera] = metrics(
            sum(m["camera_id"] == camera for m in matches),
            sum(p.camera_id == camera for p in fp),
            sum(c == camera for c, v, t in fn),
            sum(r.end_sec - r.start_sec for r in covered if r.camera_id == camera),
        )
    by_label = {
        label: metrics(
            sum(m["label"] == label for m in matches),
            sum(p.label == label for p in fp),
            sum(t.label == label for c, v, t in fn),
            seconds,
        )
        for label in sorted(
            {p.label for p in considered} | {t.label for c, v, t in truths}
        )
    }
    return dict(
        status="EVALUATED_SYNTHETIC"
        if seconds and annotations.mode == "synthetic"
        else "EVALUATED_INDEPENDENT"
        if seconds
        else "NOT_EVALUATED_NO_REVIEWED_INTERVALS",
        synthetic_only=annotations.mode == "synthetic",
        operational_ready=False,
        split=target,
        iou_threshold=iou_threshold,
        metrics=metrics(len(matches), len(fp), len(fn), seconds) if seconds else None,
        by_camera=by_camera,
        by_label=by_label,
        matches=matches,
        false_positive_ids=[p.event_id for p in fp],
        false_negative_ids=[t.event_id for c, v, t in fn],
        ignored_predictions=ignored,
        reviewed_intervals=len(covered),
        source_demo_predictions=sum(p.demo_only for p in considered),
    )


def run_benchmark(
    manifest_path: Path,
    annotation_path: Path,
    split_path: Path,
    events_dir: Path,
    output: Path,
    target: Literal["development", "test"] = "test",
) -> dict:
    """Validate inputs and publish a frozen score/report without modifying labels."""
    events_dir = events_dir.resolve()
    paths = [
        p.absolute()
        for p in (
            manifest_path,
            annotation_path,
            split_path,
            events_dir / "events.jsonl",
            events_dir / "event_manifest.json",
        )
    ]
    for path in paths:
        reject_link(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Benchmark input exceeds 64 MiB")
    hashes = {str(p): digest(p) for p in paths}
    manifest = load_manifest(manifest_path, require_files=False)
    annotations = AnnotationSet.model_validate_json(
        annotation_path.read_text(encoding="utf-8-sig")
    )
    splits = SplitManifest.model_validate_json(
        split_path.read_text(encoding="utf-8-sig")
    )
    annotations.bind(manifest)
    splits.bind(manifest)
    events_dir = events_dir.resolve()
    package, events = load_events(events_dir)
    if package["signature"]["source_manifest_sha256"] != digest(manifest.path):
        raise ValueError("Predictions belong to another manifest")
    predictions = []
    excluded = []
    labels = {
        "congestion_candidate": "congestion",
        "suspected_road_incident": "road_incident",
    }
    videos = {v.video_id: v.camera_id for v in manifest.videos}
    for event in events:
        if videos.get(event.video_id) != event.camera_id:
            raise ValueError("Prediction source scope mismatch")
        if event.event_type not in labels:
            excluded.append(
                dict(event_id=event.event_id, reason="non_evaluation_demo_case")
            )
            continue
        predictions.append(
            Prediction.model_validate_json(
                json.dumps(
                    dict(
                        event_id=event.event_id,
                        camera_id=event.camera_id,
                        video_id=event.video_id,
                        label=labels[event.event_type],
                        start_sec=event.start_sec,
                        end_sec=event.end_sec,
                        trigger_source_sec=event.evidence[-1].locator.timestamp_sec
                        if event.schema_version == 2
                        else None,
                        synthetic_event=event.synthetic_event,
                        demo_only=event.demo_only,
                    )
                )
            )
        )
    protected = [
        manifest.path,
        manifest.raw_root,
        annotation_path.resolve(),
        split_path.resolve(),
        events_dir,
        *[Path(p) for p in package["protected_roots"]],
    ]
    output = protect_output(output, protected)
    if any(digest(Path(p)) != sha for p, sha in hashes.items()):
        raise ValueError("Benchmark input changed while reading")
    signature = dict(
        pipeline="cctv-event-benchmark-v1",
        inputs=hashes,
        split=target,
        thresholds=[0.1, 0.3, 0.5],
        matching="max_total_temporal_iou_one_to_one",
        code_sha256=digest(Path(__file__).resolve()),
    )
    state_path, report_path = (
        output / "benchmark_state.json",
        output / "benchmark_report.json",
    )
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in (".ingest.lock", "benchmark_state.json", "benchmark_report.json"):
        reject_link(output / name)
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(signature=signature, status="processing")
        )
        if state["signature"] != signature:
            raise ValueError("Input/recipe changed; use a new output directory")
        if state["status"] == "complete":
            if digest(report_path) != state["report_sha256"]:
                raise ValueError("Benchmark report artifact changed")
            return dict(read_json(report_path), resumed=True)
        save_json(state_path, state)
        results = [
            evaluate_records(annotations, splits, predictions, target, threshold)
            for threshold in (0.1, 0.3, 0.5)
        ]
        if any(digest(Path(p)) != sha for p, sha in hashes.items()):
            raise ValueError("Benchmark input changed during evaluation")
        report = dict(
            schema_version=1,
            signature=signature,
            status=results[1]["status"],
            operational_ready=False,
            annotation_mode=annotations.mode,
            results=results,
            excluded_non_evaluation_events=excluded,
            split_ready=all(r.split != "unassigned" for r in splits.videos)
            and {r.split for r in splits.videos} == {"development", "test"},
        )
        save_json(report_path, report)
        save_json(
            state_path,
            dict(
                signature=signature,
                status="complete",
                report_sha256=digest(report_path),
            ),
        )
        return dict(report, resumed=False)


def main() -> int:
    """Run annotation validation and sensitivity scores for sealed v1/v2 events."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "annotations", "splits", "events-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--split", choices=("development", "test"), default="test")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                run_benchmark(
                    args.manifest,
                    args.annotations,
                    args.splits,
                    args.events_dir,
                    args.output,
                    args.split,
                ),
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Event benchmark failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
