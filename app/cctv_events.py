"""Optional read-only event publications and live review state for the CCTV UI."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from pipeline.cctv.event_review import list_events, load_events
from pipeline.cctv.ingest import digest, reject_link

LOG = logging.getLogger("cctv.events.api")


class EventStore:
    """Reload bounded sealed ledgers and read-only journals on each request."""

    def __init__(self, packages: dict[str, tuple[Path, Path]]) -> None:
        self.packages = packages

    def rows(self, package_id: str, sources: dict[str, dict]) -> list[dict]:
        """Bind events to served source bytes and expose HTTP evidence locators."""
        events_dir, review_dir = self.packages[package_id]
        reject_link(events_dir.absolute())
        paths = [
            events_dir / name
            for name in ("event_manifest.json", "event_state.json", "events.jsonl")
        ]
        before = {p: digest(p) for p in paths}
        report, _ = load_events(events_dir)
        signature = report["signature"]
        origin = signature.get("tracking_signature", signature.get("source_signature"))
        if not origin or not origin.get("sources"):
            raise ValueError("Missing event source provenance")
        if any(
            sources.get(vid, {}).get("sha256") != sha
            for vid, sha in origin["sources"].items()
        ):
            raise ValueError("Event source differs from retrieval")
        records = list_events(events_dir, review_dir)
        output = []
        for record in records:
            event = record["event"]
            source = sources.get(event["video_id"])
            if source is None or source["camera_id"] != event["camera_id"]:
                raise ValueError("Event camera/video scope mismatch")
            row = {
                key: event[key]
                for key in (
                    "event_id",
                    "schema_version",
                    "event_type",
                    "basis",
                    "camera_id",
                    "video_id",
                    "start_sec",
                    "end_sec",
                    "rationale",
                    "synthetic_event",
                    "demo_only",
                    "thresholds_calibrated",
                    "operational_ready",
                )
            }
            row.update(
                status=record["status"],
                revision=record["revision"],
                review=record["review"],
                evidence=[],
            )
            for item in event["evidence"]:
                loc = item["locator"]
                row["evidence"].append(
                    dict(
                        camera_id=loc["camera_id"],
                        video_id=loc["video_id"],
                        timestamp_sec=loc["timestamp_sec"],
                        frame_idx=loc["frame_idx"],
                        segment_id=item["segment_id"],
                        video_url=f"/cctv/video/{loc['video_id']}",
                        video_seek_sec=loc["timestamp_sec"] + source["seek_offset_sec"],
                    )
                )
            output.append(row)
        if any(digest(p) != sha for p, sha in before.items()):
            raise ValueError("Event publication changed during request")
        return output

    def catalog(self, sources: dict[str, dict]) -> dict:
        """Keep unavailable packages distinct from verified packages with zero events."""
        packages = []
        for name in self.packages:
            try:
                rows = self.rows(name, sources)
                packages.append(
                    dict(
                        package_id=name,
                        available=True,
                        total=len(rows),
                        synthetic_cases=sum(r["synthetic_event"] for r in rows),
                    )
                )
            except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
                LOG.warning("Event package %s unavailable: %s", name, exc)
                packages.append(
                    dict(
                        package_id=name,
                        available=False,
                        total=None,
                        synthetic_cases=None,
                    )
                )
        return dict(
            packages=packages,
            available=any(p["available"] for p in packages),
            read_only=True,
            operational_ready=False,
        )
