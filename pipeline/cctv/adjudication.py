"""Explicit human adjudication to legacy annotations; pending never becomes negative."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv import benchmark_events, review_notes
from pipeline.cctv.benchmark_events import SHA, AnnotationSet, Interval, Truth
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import Manifest, load_manifest
from pipeline.cctv.review_notes import ReviewerNotes, compare, person, scope
from pipeline.cctv.traffic import Contract


def timestamp(value: str) -> datetime:
    """Require a timezone-aware declared decision/review time."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp requires timezone")
    return parsed


class Decision(Interval):
    """One explicit resolution; acknowledgment is declared, never authenticated."""

    camera_id: str
    video_id: str
    status: Literal["pending", "unknown", "reviewed"]
    decided_at: str | None
    reason: Annotated[str, Field(max_length=4000)]
    reviewers_accepted_final: bool
    blind_to_predictions: bool
    events: Annotated[tuple[Truth, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.status == "pending":
            if (
                self.decided_at is not None
                or self.reason
                or self.events
                or self.reviewers_accepted_final
                or self.blind_to_predictions
            ):
                raise ValueError("Pending decision cannot claim adjudication")
        else:
            if self.decided_at is None or not self.reason.strip():
                raise ValueError("Decision needs timestamp and reason")
            timestamp(self.decided_at)
        if self.status != "reviewed" and (self.events or self.reviewers_accepted_final):
            raise ValueError(
                "Only reviewed decisions can contain final events/acceptance"
            )
        if self.status == "reviewed" and not self.reviewers_accepted_final:
            raise ValueError(
                "Reviewed decision requires declared acceptance by both reviewers"
            )
        if self.status == "reviewed" and not self.blind_to_predictions:
            raise ValueError("Reviewed decision requires declared blind adjudication")
        return self


class Adjudication(Contract):
    """Source/template/ordered notes-bound decisions made by a named adjudicator."""

    schema_version: Literal[1]
    source_manifest_sha256: SHA
    annotation_template_sha256: SHA
    reviewer_notes_sha256: tuple[SHA, SHA]
    adjudicator: str | None
    intervals: Annotated[tuple[Decision, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.adjudicator is not None:
            person(self.adjudicator)
        if (
            any(row.status != "pending" for row in self.intervals)
            and self.adjudicator is None
        ):
            raise ValueError("Performed adjudication requires a named adjudicator")
        if len({scope(row) for row in self.intervals}) != len(self.intervals):
            raise ValueError("Duplicate decision coverage")
        return self


def convert(
    template: AnnotationSet,
    first: ReviewerNotes,
    second: ReviewerNotes,
    decisions: Adjudication,
    comparison: dict,
    fixture_mode: bool,
) -> tuple[dict, dict]:
    """Gate reviewed coverage and validate the complete legacy annotation schema."""
    expected = {scope(row) for row in template.intervals}
    if {scope(row) for row in decisions.intervals} != expected:
        raise ValueError("Decisions must cover every template interval exactly")
    left, right = (
        {scope(row): row for row in notes.intervals} for notes in (first, second)
    )
    rows = []
    for decision in tqdm(
        decisions.intervals, desc="Validate adjudication", unit="interval"
    ):
        key = scope(decision)
        a, b = left[key], right[key]
        reviewed = decision.status == "reviewed"
        if decision.decided_at is not None and any(
            note.recorded_at is not None
            and timestamp(decision.decided_at) < timestamp(note.recorded_at)
            for note in (a, b)
        ):
            raise ValueError("Decision predates reviewer notes")
        if reviewed:
            if not (
                a.status == b.status == "complete"
                and a.blind_to_predictions
                and b.blind_to_predictions
            ):
                raise ValueError(
                    "Reviewed export needs two complete blind reviewer notes"
                )
        rows.append(
            dict(
                camera_id=key[0],
                video_id=key[1],
                start_sec=key[2],
                end_sec=key[3],
                review_status=decision.status,
                reviewed_by=[first.reviewer, second.reviewer] if reviewed else [],
                reviewed_at=decision.decided_at,
                blind_to_predictions=reviewed,
                events=[event.model_dump(mode="json") for event in decision.events],
            )
        )
    labels = AnnotationSet.model_validate_json(
        json.dumps(
            dict(
                schema_version=1,
                mode="synthetic" if fixture_mode else "independent",
                source_manifest_sha256=template.source_manifest_sha256,
                intervals=rows,
            )
        )
    )
    reviewed_count = sum(row.review_status == "reviewed" for row in labels.intervals)
    report = dict(
        schema_version=1,
        mode=labels.mode,
        adjudicator=decisions.adjudicator,
        reviewed_intervals=reviewed_count,
        pending_intervals=sum(
            row.review_status == "pending" for row in labels.intervals
        ),
        unknown_intervals=sum(
            row.review_status == "unknown" for row in labels.intervals
        ),
        independent_ground_truth_exported=reviewed_count > 0 and not fixture_mode,
        independent_review_authenticated=False,
        operational_ready=False,
        comparison_counts=comparison["counts"],
        provenance=decisions.model_dump(mode="json"),
        limitation="Human declarations only; no footage authentication, metrics or split approval",
    )
    return labels.model_dump(mode="json"), report


def publish(
    manifest: Manifest,
    output: Path,
    signature: dict,
    initial: dict[str, str],
    values: dict[str, dict],
) -> bool:
    """Publish immutable artifacts; reject changed inputs/edits, resume without writes."""
    output = protect_output(
        output, [manifest.raw_root, *[Path(p).parent for p in initial]]
    )
    state_path = output / "adjudication_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved")
    output.mkdir(parents=True, exist_ok=True)
    reject_link(state_path)
    with output_lease(output):
        if initial != {p: digest(Path(p)) for p in initial}:
            raise ValueError("Inputs changed before publication")
        if state_path.exists():
            state = read_json(state_path)
            if state["signature"] != signature or state["status"] != "complete":
                raise ValueError("Inputs/recipe changed; choose a NEW output")
            if set(state["artifacts"]) != set(values):
                raise ValueError("Incomplete artifact checkpoint")
            for name, sha in state["artifacts"].items():
                reject_link(output / name)
                if digest(output / name) != sha:
                    raise ValueError("Edited output preserved: " + name)
            return True
        for name in values:
            reject_link(output / name)
            if (output / name).exists():
                raise ValueError("Unowned output preserved")
        for name, value in values.items():
            save_json(output / name, value)
        if initial != {p: digest(Path(p)) for p in initial}:
            raise ValueError("Inputs changed during publication")
        save_json(
            state_path,
            dict(
                status="complete",
                signature=signature,
                artifacts={name: digest(output / name) for name in values},
            ),
        )
    return False


def run(
    manifest_path: Path,
    annotations: Path,
    first: Path,
    second: Path,
    output: Path,
    decisions_path: Path | None = None,
    fixture_mode: bool = False,
) -> dict:
    """Initialize blank decisions or export explicit decisions to T6.1 annotations."""
    paths = [p.absolute() for p in (manifest_path, annotations, first, second)]
    manifest_path, annotations, first, second = paths
    if decisions_path is not None:
        decisions_path = decisions_path.absolute()
        paths.append(decisions_path)
    paths.extend(
        Path(module.__file__).resolve() for module in (benchmark_events, review_notes)
    )
    paths.append(Path(__file__).resolve())
    for path in paths:
        reject_link(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("JSON/recipe input exceeds 64 MiB")
    initial = {str(p): digest(p) for p in paths}
    manifest = load_manifest(manifest_path, require_files=False)
    template = AnnotationSet.model_validate_json(
        annotations.read_text(encoding="utf-8-sig")
    )
    template.bind(manifest)
    if (
        template.mode != "independent"
        or not template.intervals
        or any(row.review_status != "pending" for row in template.intervals)
    ):
        raise ValueError("Use a nonempty independent pending template")
    if first == second:
        raise ValueError("Two separate reviewer files are required")
    notes = [
        ReviewerNotes.model_validate_json(p.read_text(encoding="utf-8-sig"))
        for p in (first, second)
    ]
    comparison = compare(template, initial[str(annotations)], *notes)
    binding = dict(
        source_manifest_sha256=initial[str(manifest_path)],
        annotation_template_sha256=initial[str(annotations)],
        reviewer_notes_sha256=[initial[str(first)], initial[str(second)]],
    )
    if decisions_path is None:
        pending = Adjudication.model_validate_json(
            json.dumps(
                dict(
                    schema_version=1,
                    **binding,
                    adjudicator=None,
                    intervals=[
                        dict(
                            camera_id=row.camera_id,
                            video_id=row.video_id,
                            start_sec=row.start_sec,
                            end_sec=row.end_sec,
                            status="pending",
                            decided_at=None,
                            reason="",
                            reviewers_accepted_final=False,
                            blind_to_predictions=False,
                            events=[],
                        )
                        for row in template.intervals
                    ],
                )
            )
        )
        values = {"adjudication.pending.json": pending.model_dump(mode="json")}
        report = dict(reviewed_intervals=0, independent_ground_truth_exported=False)
    else:
        decisions = Adjudication.model_validate_json(
            decisions_path.read_text(encoding="utf-8-sig")
        )
        actual = decisions.model_dump(mode="json")
        if any(actual[key] != value for key, value in binding.items()):
            raise ValueError(
                "Adjudication belongs to different manifest/template/ordered notes"
            )
        labels, report = convert(template, *notes, decisions, comparison, fixture_mode)
        AnnotationSet.model_validate_json(json.dumps(labels)).bind(manifest)
        values = {"annotations.json": labels, "adjudication_report.json": report}
    signature = dict(
        pipeline="cctv-adjudication-v1", fixture_mode=fixture_mode, inputs=initial
    )
    resumed = publish(manifest, output, signature, initial, values)
    return dict(
        resumed=resumed,
        files=list(values),
        reviewed_intervals=report["reviewed_intervals"],
        independent_ground_truth_exported=report["independent_ground_truth_exported"],
        operational_ready=False,
    )


def main() -> int:
    """Expose CPU-only Windows commands with explicit fixture export marking."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "export"):
        child = commands.add_parser(name)
        for flag in ("manifest", "annotations", "review-a", "review-b", "output"):
            child.add_argument("--" + flag, type=Path, required=True)
        if name == "export":
            child.add_argument("--decisions", type=Path, required=True)
            child.add_argument("--synthetic-fixture", action="store_true")
    args = parser.parse_args()
    try:
        result = run(
            args.manifest,
            args.annotations,
            args.review_a,
            args.review_b,
            args.output,
            getattr(args, "decisions", None),
            getattr(args, "synthetic_fixture", False),
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Adjudication failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
