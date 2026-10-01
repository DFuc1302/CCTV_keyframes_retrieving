"""Separate reviewer notes and agreement checks; never publish ground truth."""

from __future__ import annotations

import argparse
import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.benchmark_events import SHA, AnnotationSet, Interval, Truth
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.traffic import Contract


def person(value: str) -> str:
    """Normalize identity comparison without claiming identity authentication."""
    if not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("Reviewer name must be nonempty, trimmed and printable")
    return unicodedata.normalize("NFKC", value).casefold()


class Note(Interval):
    """One person's observations over the entire declared source interval."""

    camera_id: str
    video_id: str
    status: Literal["pending", "unknown", "complete"]
    recorded_at: str | None
    blind_to_predictions: bool
    full_interval_watched: bool
    ontology_reviewed: bool
    reason: Annotated[str, Field(max_length=4000)]
    events: Annotated[tuple[Truth, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        identity(self.camera_id, "camera_id")
        identity(self.video_id, "video_id")
        if self.status == "pending":
            if (
                self.recorded_at is not None
                or self.blind_to_predictions
                or self.full_interval_watched
                or self.ontology_reviewed
                or self.reason
                or self.events
            ):
                raise ValueError(
                    "Pending note cannot claim observations or performed review"
                )
        else:
            if not self.reason.strip() or self.recorded_at is None:
                raise ValueError("Completed/unknown notes require reason and timestamp")
            if (
                datetime.fromisoformat(
                    self.recorded_at.replace("Z", "+00:00")
                ).utcoffset()
                is None
            ):
                raise ValueError("Reviewer timestamp requires timezone")
        if self.status == "complete" and not (
            self.full_interval_watched and self.ontology_reviewed
        ):
            raise ValueError("Complete note requires full interval and ontology review")
        if self.status != "complete" and self.events:
            raise ValueError("Only complete observations may contain proposed events")
        for event in self.events:
            identity(event.event_id, "event_id")
            if not self.start_sec <= event.start_sec < event.end_sec <= self.end_sec:
                raise ValueError("Proposed event outside note coverage")
        return self


class ReviewerNotes(Contract):
    """One source/template-bound reviewer file; reviewer is unset until real input."""

    schema_version: Literal[1]
    mode: Literal["independent"]
    source_manifest_sha256: SHA
    annotation_template_sha256: SHA
    slot_id: str
    reviewer: str | None
    intervals: Annotated[tuple[Note, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        identity(self.slot_id, "slot_id")
        if self.reviewer is not None:
            person(self.reviewer)
        if any(r.status != "pending" for r in self.intervals) and self.reviewer is None:
            raise ValueError("Performed observations require an actual reviewer")
        keys, ids = set(), set()
        for row in self.intervals:
            key = scope(row)
            if key in keys:
                raise ValueError("Duplicate note coverage")
            keys.add(key)
            for event in row.events:
                if event.event_id in ids:
                    raise ValueError("Duplicate proposed event ID")
                ids.add(event.event_id)
        return self


def scope(row: Interval) -> tuple:
    """Preserve exact camera/video and half-open source PTS coverage."""
    return row.camera_id, row.video_id, row.start_sec, row.end_sec


def spans(note: Note) -> list[tuple[str, float, float]]:
    """Compare event multisets without treating reviewer-local IDs as labels."""
    return sorted((e.label, e.start_sec, e.end_sec) for e in note.events)


def compare(
    template: AnnotationSet,
    template_sha: str,
    first: ReviewerNotes,
    second: ReviewerNotes,
) -> dict:
    """Compare observations exactly; agreement is an input to human adjudication."""
    expected = {scope(row) for row in template.intervals}
    for notes in (first, second):
        if (
            notes.source_manifest_sha256 != template.source_manifest_sha256
            or notes.annotation_template_sha256 != template_sha
        ):
            raise ValueError("Notes belong to a different source/template")
        if {scope(row) for row in notes.intervals} != expected:
            raise ValueError("Notes must cover every template interval exactly")
    if first.slot_id.casefold() == second.slot_id.casefold():
        raise ValueError("Two distinct reviewer slots are required")
    if (
        first.reviewer is not None
        and second.reviewer is not None
        and person(first.reviewer) == person(second.reviewer)
    ):
        raise ValueError("Two distinct reviewer identities are required")
    rows = []
    a, b = ({scope(row): row for row in notes.intervals} for notes in (first, second))
    for key in tqdm(sorted(expected), desc="Compare reviewer notes", unit="interval"):
        left, right = a[key], b[key]
        if "pending" in (left.status, right.status):
            status = "PENDING_REVIEW"
        elif "unknown" in (left.status, right.status):
            status = "UNKNOWN_NEEDS_CONTEXT"
        elif not (left.blind_to_predictions and right.blind_to_predictions):
            status = "INELIGIBLE_NOT_BLIND"
        else:
            status = (
                "AGREEMENT_REQUIRES_ADJUDICATION"
                if spans(left) == spans(right)
                else "DISAGREEMENT_REQUIRES_ADJUDICATION"
            )
        rows.append(
            dict(
                camera_id=key[0],
                video_id=key[1],
                start_sec=key[2],
                end_sec=key[3],
                status=status,
                reviewer_a_status=left.status,
                reviewer_b_status=right.status,
                proposed_a=[e.model_dump(mode="json") for e in left.events],
                proposed_b=[e.model_dump(mode="json") for e in right.events],
            )
        )
    return dict(
        schema_version=1,
        mode="independent",
        comparison_only=True,
        ground_truth_published=False,
        reviewed_intervals=0,
        operational_ready=False,
        independent_review_authenticated=False,
        comparison="exact label/start/end multisets; IDs and free-text reasons excluded; no tolerance or auto-merge",
        reviewers=[first.reviewer, second.reviewer],
        intervals=rows,
        counts={
            name: sum(row["status"] == name for row in rows)
            for name in (
                "PENDING_REVIEW",
                "UNKNOWN_NEEDS_CONTEXT",
                "INELIGIBLE_NOT_BLIND",
                "AGREEMENT_REQUIRES_ADJUDICATION",
                "DISAGREEMENT_REQUIRES_ADJUDICATION",
            )
        },
    )


def publish(
    manifest,
    output: Path,
    inputs: list[Path],
    values: dict[str, dict],
    initial: dict[str, str],
) -> dict:
    """Seal deterministic JSON outputs, with no-op resume and preserved human edits."""
    files = [manifest.path, *[p.absolute() for p in inputs], Path(__file__).resolve()]
    before = {str(p): (digest(p), p.stat().st_mtime_ns) for p in files}
    if any(before[p][0] != sha for p, sha in initial.items()):
        raise ValueError("Inputs changed before publication")
    signature = dict(
        pipeline="cctv-review-notes-v1",
        inputs={p: sha for p, (sha, _) in before.items()},
    )
    output = protect_output(
        output, [manifest.raw_root, manifest.path.parent, *[p.parent for p in files]]
    )
    state_path = output / "notes_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved")
    output.mkdir(parents=True, exist_ok=True)
    reject_link(state_path)
    with output_lease(output):
        if state_path.exists():
            state = read_json(state_path)
            if state["signature"] != signature or state["status"] != "complete":
                raise ValueError(
                    "Input/recipe changed or incomplete; choose a NEW output"
                )
            for name, sha in state["artifacts"].items():
                if name not in values:
                    raise ValueError("Unexpected artifact in checkpoint")
                reject_link(output / name)
                if digest(output / name) != sha:
                    raise ValueError("Edited output preserved: " + name)
            if set(state["artifacts"]) != set(values):
                raise ValueError("Incomplete artifact checkpoint")
            return dict(
                resumed=True,
                files=list(state["artifacts"]),
                ground_truth_published=False,
            )
        for name, value in values.items():
            reject_link(output / name)
            if (output / name).exists():
                raise ValueError("Unowned output preserved")
            save_json(output / name, value)
        if before != {str(p): (digest(p), p.stat().st_mtime_ns) for p in files}:
            raise ValueError("Notes inputs changed during publication")
        save_json(
            state_path,
            dict(
                signature=signature,
                status="complete",
                artifacts={name: digest(output / name) for name in values},
            ),
        )
        return dict(resumed=False, files=list(values), ground_truth_published=False)


def run(
    manifest_path: Path,
    annotations: Path,
    output: Path,
    first: Path | None = None,
    second: Path | None = None,
) -> dict:
    """Create blank pending slots or validate two independent working copies."""
    manifest_path, annotations = manifest_path.absolute(), annotations.absolute()
    first = first.absolute() if first is not None else None
    second = second.absolute() if second is not None else None
    files = [manifest_path, annotations, *[p for p in (first, second) if p is not None]]
    for path in files:
        reject_link(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Notes input exceeds 64 MiB")
    initial = {str(p.absolute()): digest(p) for p in files}
    manifest = load_manifest(manifest_path, require_files=False)
    template = AnnotationSet.model_validate_json(
        annotations.read_text(encoding="utf-8-sig")
    )
    template.bind(manifest)
    if (
        template.mode != "independent"
        or not template.intervals
        or any(r.review_status != "pending" for r in template.intervals)
    ):
        raise ValueError("Use an independent, nonempty pending template")
    template_sha = initial[str(annotations.absolute())]
    if (first is None) != (second is None):
        raise ValueError("Both reviewer files are required")
    if first is None:
        values = {}
        for slot in ("reviewer-a", "reviewer-b"):
            payload = dict(
                schema_version=1,
                mode="independent",
                source_manifest_sha256=template.source_manifest_sha256,
                annotation_template_sha256=template_sha,
                slot_id=slot,
                reviewer=None,
                intervals=[
                    dict(
                        camera_id=r.camera_id,
                        video_id=r.video_id,
                        start_sec=r.start_sec,
                        end_sec=r.end_sec,
                        status="pending",
                        recorded_at=None,
                        blind_to_predictions=False,
                        full_interval_watched=False,
                        ontology_reviewed=False,
                        reason="",
                        events=[],
                    )
                    for r in template.intervals
                ],
            )
            values[slot + ".pending.json"] = ReviewerNotes.model_validate_json(
                json.dumps(payload)
            ).model_dump(mode="json")
    else:
        if first.resolve() == second.resolve():
            raise ValueError("Two separate reviewer files are required")
        notes = [
            ReviewerNotes.model_validate_json(p.read_text(encoding="utf-8-sig"))
            for p in (first, second)
        ]
        values = {"comparison_report.json": compare(template, template_sha, *notes)}
    if initial != {str(p.absolute()): digest(p) for p in files}:
        raise ValueError("Inputs changed while reading notes")
    result = publish(
        manifest,
        output,
        [annotations, *[p for p in (first, second) if p is not None]],
        values,
        initial,
    )
    return dict(
        result,
        counts=values.get("comparison_report.json", {}).get("counts"),
        operational_ready=False,
    )


def main() -> int:
    """Windows-ready JSON workflow; no models, footage edits or human impersonation."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "check"):
        child = sub.add_parser(command)
        for name in ("manifest", "annotations", "output"):
            child.add_argument("--" + name, type=Path, required=True)
        if command == "check":
            child.add_argument("--review-a", type=Path, required=True)
            child.add_argument("--review-b", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(
            args.manifest,
            args.annotations,
            args.output,
            getattr(args, "review_a", None),
            getattr(args, "review_b", None),
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Reviewer notes failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
