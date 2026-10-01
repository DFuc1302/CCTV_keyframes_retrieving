"""CPU event packaging: immutable evidence references and resumable publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.signals import Signal
from pipeline.cctv.track import TrackFrame
from pipeline.cctv.traffic import Contract, Locator, NonNegative

MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 10000


def canonical(value: object) -> str:
    """Stable JSON bytes for identities, checkpoints and audit records."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checksum(value: object) -> str:
    """Hash a structured record rather than its whitespace formatting."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Evidence(Contract):
    """An actual tracked frame in the source video; no copied raw footage."""

    locator: Locator
    segment_id: str


class Event(Contract):
    """A review candidate, never an automatically confirmed accident."""

    schema_version: Literal[1] = 1
    kind: Literal["event"] = "event"
    event_id: str
    event_type: Literal["congestion_candidate", "demo_case"]
    basis: Literal["traffic_rule", "synthetic_fixture"]
    run_id: str
    camera_id: str
    video_id: str
    start_sec: NonNegative
    end_sec: NonNegative
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1, max_length=2)]
    provenance_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    initial_status: Literal["pending_review"] = "pending_review"
    synthetic_event: bool
    demo_only: bool
    thresholds_calibrated: Literal[False] = False
    operational_ready: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        for name in ("run_id", "camera_id", "video_id"):
            identity(getattr(self, name), name)
        if self.end_sec <= self.start_sec:
            raise ValueError("Event interval must be positive")
        synthetic = self.basis == "synthetic_fixture"
        if synthetic != self.synthetic_event or synthetic != (
            self.event_type == "demo_case"
        ):
            raise ValueError("Synthetic event must be explicitly a demo_case")
        if synthetic and not self.demo_only:
            raise ValueError("Synthetic event cannot be operational")
        for item in self.evidence:
            identity(item.segment_id, "segment_id")
            if (item.locator.camera_id, item.locator.video_id) != (
                self.camera_id,
                self.video_id,
            ):
                raise ValueError("Evidence belongs to another source")
            if not self.start_sec <= item.locator.timestamp_sec < self.end_sec:
                raise ValueError("Evidence is outside event interval")
        positions = [item.locator.frame_idx for item in self.evidence]
        if positions != sorted(set(positions)):
            raise ValueError("Evidence must be unique and chronological")
        payload = self.model_dump(mode="json", exclude={"event_id"})
        if self.event_id != "event-" + checksum(payload)[:24]:
            raise ValueError("Event identity/checksum mismatch")
        return self


def make_event(**value) -> Event:
    """Build and validate a deterministic identifier with all source evidence."""
    payload = dict(
        schema_version=1,
        kind="event",
        initial_status="pending_review",
        thresholds_calibrated=False,
        operational_ready=False,
        **value,
    )
    payload["event_id"] = "event-" + checksum(payload)[:24]
    return Event.model_validate_json(canonical(payload))


def read_json(path: Path) -> dict:
    """Bounded UTF-8 metadata with link rejection."""
    reject_link(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Metadata exceeds size limit: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected metadata object: {path}")
    return value


def protect_output(output: Path, protected: list[Path]) -> Path:
    """Reject overlapping inputs and links before creating output files."""
    output = output.absolute()
    reject_link(output)
    for path in protected:
        path = path.resolve()
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError(f"Output overlaps protected input: {path}")
    return output


def publication(
    folder: Path, state_name: str, names: tuple[str, ...], key: str
) -> dict:
    """Verify complete upstream artifacts against their publication hashes."""
    state = read_json(folder / state_name)
    if state.get("status") != "complete":
        raise ValueError(f"Incomplete publication: {folder}")
    for name in names:
        path = folder / name
        reject_link(path)
        if digest(path) != state[key][name]:
            raise ValueError(f"Input artifact changed: {path}")
    return state


def candidate_intervals(rows: list[Signal]) -> list[tuple[float, float, bool]]:
    """Merge a contiguous slow/dense episode only after it reaches persistence."""
    result: list[tuple[float, float, bool]] = []
    onset = end = None
    qualified = False
    demo = False

    def flush():
        if qualified and onset is not None and end is not None:
            result.append((onset, end, demo))

    for row in rows:
        w = row.window
        if row.status not in ("slow_dense", "congestion_candidate"):
            flush()
            onset = end = None
            qualified = False
            continue
        if end is None or not math.isclose(w.start_sec, end, abs_tol=1e-6):
            flush()
            onset = w.start_sec
            qualified = False
            demo = row.demo_only
        if demo != row.demo_only:
            raise ValueError("Mixed ROI status in one episode")
        end = w.end_sec
        if row.status == "congestion_candidate":
            if not math.isclose(end - onset, row.persistent_sec, abs_tol=1e-5):
                raise ValueError(
                    "Candidate persistence does not match contiguous evidence"
                )
            qualified = True
    flush()
    return result


def evidence_frames(
    conn: sqlite3.Connection,
    video: str,
    camera: str,
    run: str,
    start: float,
    end: float,
) -> list[dict]:
    """Fetch first/last evidence using read-only SQLite and validate actual PTS."""
    result = []
    for order in ("ASC", "DESC"):
        row = conn.execute(
            "SELECT frame_idx,timestamp_sec,segment_id,payload FROM frames "
            f"WHERE video_id=? AND timestamp_sec>=? AND timestamp_sec<? ORDER BY frame_idx {order} LIMIT 1",
            (video, start, end),
        ).fetchone()
        if row is None:
            raise ValueError(f"No tracked evidence in {video} [{start}, {end})")
        frame = TrackFrame.model_validate_json(row[3])
        loc = frame.locator
        if (loc.frame_idx, loc.timestamp_sec, frame.segment_id) != row[:3] or (
            loc.video_id,
            loc.camera_id,
            frame.run_id,
        ) != (video, camera, run):
            raise ValueError("Evidence frame/checkpoint scope mismatch")
        item = dict(locator=loc.model_dump(mode="json"), segment_id=frame.segment_id)
        if item not in result:
            result.append(item)
    return result


def load_events(folder: Path) -> tuple[dict, list[Event]]:
    """Read a sealed package; reject malformed, duplicate or altered events."""
    state = publication(
        folder, "event_state.json", ("events.jsonl", "event_manifest.json"), "artifacts"
    )
    report = read_json(folder / "event_manifest.json")
    if report["signature"] != state["signature"]:
        raise ValueError("Event publication signature mismatch")
    ledger = folder / "events.jsonl"
    if ledger.stat().st_size > MAX_BYTES:
        raise ValueError("Event ledger exceeds size limit")
    events = [
        Event.model_validate_json(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    if len(events) > MAX_RECORDS or len({e.event_id for e in events}) != len(events):
        raise ValueError("Duplicate or excessive events")
    if len(events) != report["events"]:
        raise ValueError("Event count mismatch")
    for event in events:
        if event.provenance_sha256 != checksum(report["signature"]):
            raise ValueError("Event provenance mismatch")
    return report, events


def build_events(
    manifest_path: Path,
    windows: Path,
    tracking: Path,
    output: Path,
    demo_video: str | None = None,
    demo_start: float = 30.0,
    demo_end: float = 36.0,
) -> dict:
    """Package rule candidates and an optional explicitly synthetic review case."""
    manifest = load_manifest(manifest_path, require_files=False)
    windows, tracking = windows.resolve(), tracking.resolve()
    protected = [manifest.raw_root, manifest.path, windows, tracking]
    output = protect_output(output, protected)
    ws = publication(
        windows,
        "window_state.json",
        ("signals.jsonl", "windows.jsonl", "window_manifest.json", "windows.db"),
        "artifacts",
    )
    ts = publication(
        tracking,
        "track_state.json",
        ("tracks.db", "frames.jsonl", "tracking_manifest.json"),
        "publication",
    )
    wm = read_json(windows / "window_manifest.json")
    tm = read_json(tracking / "tracking_manifest.json")
    if wm["signature"] != ws["signature"] or tm["signature"] != ts["signature"]:
        raise ValueError("Upstream signature mismatch")
    if tm["run_id"] != ts["run_id"]:
        raise ValueError("Tracking run mismatch")
    if tm["signature"]["manifest_sha256"] != digest(manifest.path):
        raise ValueError("Tracking belongs to another source manifest")
    for name, sha in wm["signature"]["tracking"].items():
        if sha != ts["publication"][name]:
            raise ValueError("Traffic belongs to another tracking run")
    video_map = {v.video_id: v for v in manifest.videos}
    if demo_video is not None and (
        demo_video not in video_map
        or not (
            math.isfinite(demo_start)
            and math.isfinite(demo_end)
            and 0 <= demo_start < demo_end
        )
    ):
        raise ValueError("Demo video/interval is invalid")
    signature = dict(
        pipeline="cctv-events-v1",
        source_manifest_sha256=digest(manifest.path),
        windows=ws["artifacts"],
        tracking=wm["signature"]["tracking"],
        source_signature=tm["signature"],
        traffic_signature=wm["signature"],
        code_sha256=digest(Path(__file__).resolve()),
        signal_contract_sha256=digest(Path(__file__).with_name("signals.py").resolve()),
        demo_video=demo_video,
        demo_start=demo_start if demo_video else None,
        demo_end=demo_end if demo_video else None,
    )
    grouped: dict[str, list[Signal]] = {}
    ledger = windows / "signals.jsonl"
    if ledger.stat().st_size > MAX_BYTES:
        raise ValueError("Signal ledger exceeds size limit")
    for line in ledger.read_text(encoding="utf-8").splitlines():
        signal = Signal.model_validate_json(line)
        w = signal.window
        if (
            w.video_id not in video_map
            or w.camera_id != video_map[w.video_id].camera_id
            or w.run_id != ts["run_id"]
        ):
            raise ValueError("Signal source/run mismatch")
        clip_start = tm["signature"]["start_sec"]
        if w.start_sec < clip_start or (
            tm["signature"]["duration_sec"] is not None
            and w.end_sec > clip_start + tm["signature"]["duration_sec"] + 1e-6
        ):
            raise ValueError("Signal outside tracking clip")
        rows = grouped.setdefault(w.video_id, [])
        if rows and w.start_sec < rows[-1].window.end_sec - 1e-6:
            raise ValueError("Overlapping/unordered windows")
        rows.append(signal)
    if (
        sum(map(len, grouped.values())) > MAX_RECORDS
        or sum(map(len, grouped.values())) != wm["windows"]
    ):
        raise ValueError("Signal count mismatch")
    if set(grouped) != set(wm["videos"]) or not set(grouped) <= set(tm["videos"]):
        raise ValueError("Published video set mismatch")
    if demo_video and demo_video not in grouped:
        raise ValueError("Demo video has no published traffic evidence")
    state_path = output / "event_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory; choose an empty folder")
    output.mkdir(parents=True, exist_ok=True)
    for name in (
        ".ingest.lock",
        "events.log",
        "event_manifest.json",
        "event_state.json",
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
            _, events = load_events(output)
            return dict(events=len(events), videos_skipped=len(grouped), resumed=True)
        save_json(state_path, state)
        handler = logging.FileHandler(output / "events.log", encoding="utf-8")
        logger = logging.getLogger("cctv.events")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        errors = {}
        try:
            with closing(
                sqlite3.connect(
                    (tracking / "tracks.db").as_uri() + "?mode=ro", uri=True
                )
            ) as conn:
                for video_id, rows in tqdm(grouped.items(), desc="Event videos"):
                    try:
                        if video_id in state["videos"]:
                            saved = state["videos"][video_id]
                            if saved["sha256"] != checksum(saved["events"]):
                                raise ValueError("Event checkpoint changed")
                            for item in saved["events"]:
                                restored = Event.model_validate_json(canonical(item))
                                if (
                                    restored.video_id != video_id
                                    or restored.provenance_sha256 != checksum(signature)
                                ):
                                    raise ValueError(
                                        "Event checkpoint provenance changed"
                                    )
                            continue
                        intervals = [
                            (a, b, draft, False)
                            for a, b, draft in candidate_intervals(rows)
                        ]
                        if video_id == demo_video:
                            if (
                                demo_start < rows[0].window.start_sec
                                or demo_end > rows[-1].window.end_sec
                            ):
                                raise ValueError(
                                    "Demo interval outside processed windows"
                                )
                            intervals.append((demo_start, demo_end, True, True))
                        records = []
                        for start, end, demo, synthetic in intervals:
                            records.append(
                                make_event(
                                    event_type="demo_case"
                                    if synthetic
                                    else "congestion_candidate",
                                    basis="synthetic_fixture"
                                    if synthetic
                                    else "traffic_rule",
                                    run_id=ts["run_id"],
                                    camera_id=video_map[video_id].camera_id,
                                    video_id=video_id,
                                    start_sec=start,
                                    end_sec=end,
                                    evidence=evidence_frames(
                                        conn,
                                        video_id,
                                        video_map[video_id].camera_id,
                                        ts["run_id"],
                                        start,
                                        end,
                                    ),
                                    provenance_sha256=checksum(signature),
                                    synthetic_event=synthetic,
                                    demo_only=demo,
                                    rationale=(
                                        "Synthetic review exercise on real reference frames; no detected incident."
                                        if synthetic
                                        else "Uncalibrated slow/dense traffic rule reached persistence; human review required."
                                    ),
                                ).model_dump(mode="json")
                            )
                        state["videos"][video_id] = dict(
                            events=records, sha256=checksum(records)
                        )
                        save_json(state_path, state)
                        logger.info(
                            "video_complete %s events=%s", video_id, len(records)
                        )
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        errors[video_id] = str(exc)
                        logger.exception("video_error %s", video_id)
        finally:
            logger.removeHandler(handler)
            handler.close()
        if errors:
            save_json(state_path, dict(state, errors=errors))
            raise ValueError(f"Event videos failed: {errors}")
        records = [e for vid in grouped for e in state["videos"][vid]["events"]]
        temp = output / "events.jsonl.tmp"
        reject_link(temp)
        temp.write_text("".join(canonical(e) + "\n" for e in records), encoding="utf-8")
        reject_link(output / "events.jsonl")
        temp.replace(output / "events.jsonl")
        save_json(
            output / "event_manifest.json",
            dict(
                pipeline="cctv-events-v1",
                signature=signature,
                events=len(records),
                synthetic_cases=sum(e["synthetic_event"] for e in records),
                candidate_events=sum(not e["synthetic_event"] for e in records),
                protected_roots=[str(p) for p in protected],
                operational_ready=False,
                thresholds_calibrated=False,
            ),
        )
        state.update(
            status="complete",
            artifacts={
                name: digest(output / name)
                for name in ("events.jsonl", "event_manifest.json")
            },
        )
        save_json(state_path, state)
        return dict(events=len(records), videos_done=len(grouped), resumed=False)


def main() -> int:
    """Windows-ready CLI; errors are nonzero and leave original data intact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--windows-dir", type=Path, required=True)
    parser.add_argument("--tracking-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--demo-video")
    parser.add_argument("--demo-start-sec", type=float, default=30.0)
    parser.add_argument("--demo-end-sec", type=float, default=36.0)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                build_events(
                    args.manifest,
                    args.windows_dir,
                    args.tracking_dir,
                    args.output,
                    args.demo_video,
                    args.demo_start_sec,
                    args.demo_end_sec,
                ),
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(1, f"Event packaging failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
