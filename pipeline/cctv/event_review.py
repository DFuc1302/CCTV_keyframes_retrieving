"""Explicit review decisions in a separate, append-only SQLite audit journal."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator

from pipeline.cctv.events import (
    Event,
    canonical,
    checksum,
    load_events,
    protect_output,
    read_json,
)
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.traffic import Contract


class Review(Contract):
    """A named decision with a timezone-aware audit timestamp and revision."""

    schema_version: Literal[1] = 1
    event_id: str
    revision: Annotated[int, Field(ge=1)]
    decision: Literal["confirmed", "rejected", "needs_more_evidence"]
    reviewer: Annotated[str, Field(min_length=1, max_length=80)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    reviewed_at: str
    scope: Literal["demo_review", "event_review"]

    @field_validator("reviewer", "reason")
    @classmethod
    def meaningful(cls, value):
        if (
            value != value.strip()
            or not value.strip()
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError(
                "Reviewer/reason must be trimmed, nonempty text without controls"
            )
        return value

    @field_validator("reviewed_at")
    @classmethod
    def aware_time(cls, value):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Review timestamp requires a timezone")
        return value


def scope(event: Event) -> str:
    """Draft ROI and fabricated hypotheses remain demo even after confirmation."""
    return "demo_review" if event.demo_only or event.synthetic_event else "event_review"


def allowed(previous: str, decision: str) -> bool:
    """Final decisions require an explicit reopen before being reversed."""
    return (
        previous in ("pending_review", "needs_more_evidence")
        or decision == "needs_more_evidence"
    )


def binding(events_dir: Path, report: dict) -> dict:
    """Bind reviews to exact immutable event bytes, not just reused identifiers."""
    return dict(
        schema_version=1,
        pipeline="cctv-event-review-v1",
        events_sha256=digest(events_dir / "events.jsonl"),
        manifest_sha256=digest(events_dir / "event_manifest.json"),
        signature_sha256=checksum(report["signature"]),
    )


def history(conn: sqlite3.Connection, events: dict[str, Event]) -> list[Review]:
    """Validate the full hash chain, revisions, scopes and transitions before use."""
    previous_hash = "0" * 64
    latest: dict[str, Review] = {}
    result = []
    cursor = conn.execute(
        "SELECT sequence,event_id,revision,payload,previous_hash,sha256 FROM reviews ORDER BY sequence"
    )
    for sequence, event_id, revision, payload, prev, sha in cursor:
        if len(result) >= 100000:
            raise ValueError("Review history exceeds record limit")
        record = Review.model_validate_json(payload)
        prior = latest.get(event_id)
        event = events.get(event_id)
        if event is None or (record.event_id, record.revision) != (event_id, revision):
            raise ValueError("Review source/revision mismatch")
        if (
            sequence != len(result) + 1
            or prev != previous_hash
            or sha
            != checksum(dict(previous_hash=prev, review=record.model_dump(mode="json")))
        ):
            raise ValueError("Review audit chain changed")
        if revision != (prior.revision + 1 if prior else 1) or record.scope != scope(
            event
        ):
            raise ValueError("Review revision/scope changed")
        if not allowed(prior.decision if prior else "pending_review", record.decision):
            raise ValueError("Invalid review transition in history")
        latest[event_id] = record
        result.append(record)
        previous_hash = sha
    return result


def load_history(
    events_dir: Path, review_dir: Path, report: dict, events: dict[str, Event]
) -> list[Review]:
    """A missing journal means pending; listing never creates directories."""
    if not review_dir.exists():
        return []
    reject_link(review_dir)
    meta = review_dir / "review_manifest.json"
    db = review_dir / "reviews.db"
    reject_link(db)
    if read_json(meta) != binding(events_dir, report) or not db.is_file():
        raise ValueError("Review journal belongs to another event package")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        return history(conn, events)


def list_events(events_dir: Path, review_dir: Path) -> list[dict]:
    """Return original flags alongside review state without changing source bytes."""
    events_dir, review_dir = events_dir.resolve(), review_dir.absolute()
    report, records = load_events(events_dir)
    events = {event.event_id: event for event in records}
    latest = {
        r.event_id: r for r in load_history(events_dir, review_dir, report, events)
    }
    return [
        dict(
            event=event.model_dump(mode="json"),
            status=latest[key].decision if key in latest else "pending_review",
            revision=latest[key].revision if key in latest else 0,
            review=latest[key].model_dump(mode="json") if key in latest else None,
        )
        for key, event in events.items()
    ]


def submit_review(
    events_dir: Path,
    review_dir: Path,
    event_id: str,
    decision: str,
    reviewer: str,
    reason: str,
    expected_revision: int,
    reviewed_at: str | None = None,
) -> Review:
    """Append one CAS-protected decision; never update/delete previous reviews."""
    events_dir = events_dir.resolve()
    report, records = load_events(events_dir)
    events = {event.event_id: event for event in records}
    if event_id not in events:
        raise ValueError("Unknown event ID")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Expected revision must be a nonnegative integer")
    record = Review.model_validate_json(
        canonical(
            dict(
                schema_version=1,
                event_id=event_id,
                revision=expected_revision + 1,
                decision=decision,
                reviewer=reviewer,
                reason=reason,
                reviewed_at=reviewed_at or datetime.now(timezone.utc).isoformat(),
                scope=scope(events[event_id]),
            )
        )
    )
    protected = [events_dir, *[Path(p) for p in report["protected_roots"]]]
    review_dir = protect_output(review_dir, protected)
    meta = review_dir / "review_manifest.json"
    if review_dir.exists() and not meta.exists() and any(review_dir.iterdir()):
        raise ValueError("Foreign review directory; use an empty folder")
    review_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        ".ingest.lock",
        "review_manifest.json",
        "reviews.db-journal",
        "reviews.db-wal",
        "reviews.db-shm",
    ):
        reject_link(review_dir / name)
    with output_lease(review_dir):
        expected = binding(events_dir, report)
        db = review_dir / "reviews.db"
        reject_link(db)
        if meta.exists():
            if read_json(meta) != expected or not db.is_file():
                raise ValueError("Review journal belongs to another event package")
        else:
            save_json(meta, expected)
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reviews (sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, sha256 TEXT NOT NULL, UNIQUE(event_id,revision))"
            )
            conn.execute("BEGIN IMMEDIATE")
            previous = history(conn, events)
            current = next(
                (r for r in reversed(previous) if r.event_id == event_id), None
            )
            if (current.revision if current else 0) != expected_revision:
                raise ValueError("Stale revision; list events before reviewing again")
            if not allowed(current.decision if current else "pending_review", decision):
                raise ValueError(
                    "Reopen as needs_more_evidence before changing a final decision"
                )
            prev = conn.execute(
                "SELECT sha256 FROM reviews ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev[0] if prev else "0" * 64
            payload = record.model_dump(mode="json")
            sha = checksum(dict(previous_hash=prev_hash, review=payload))
            conn.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                (
                    len(previous) + 1,
                    event_id,
                    record.revision,
                    canonical(payload),
                    prev_hash,
                    sha,
                ),
            )
            conn.commit()
    return record


def main() -> int:
    """List and explicitly review an event package without model inference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "review"))
    parser.add_argument("--events-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--event-id")
    parser.add_argument(
        "--decision", choices=("confirmed", "rejected", "needs_more_evidence")
    )
    parser.add_argument("--reviewer")
    parser.add_argument("--reason")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = list_events(args.events_dir, args.review_dir)
        else:
            if any(
                getattr(args, name) is None
                for name in (
                    "event_id",
                    "decision",
                    "reviewer",
                    "reason",
                    "expected_revision",
                )
            ):
                raise ValueError(
                    "Review needs event-id, decision, reviewer, reason and expected-revision"
                )
            result = submit_review(
                args.events_dir,
                args.review_dir,
                args.event_id,
                args.decision,
                args.reviewer,
                args.reason,
                args.expected_revision,
            ).model_dump(mode="json")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"Event review failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
