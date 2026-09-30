"""Exact, bounded simulation allocation; proposals only, no real dispatch actions."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.event_review import list_events, load_events
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.resources import ResourceConfig, Skill, summarize
from pipeline.cctv.traffic import Contract

MAX_TEAMS = 12
MAX_DEMANDS = 12
MAX_SEARCH_STEPS = 2_000_000
ReviewStatus = Literal["confirmed", "pending_review", "rejected", "needs_more_evidence"]


class Requirement(Contract):
    """Caller-defined staffing requirement, not an inferred emergency severity."""

    demand_id: str
    min_officers: Annotated[int, Field(ge=1, le=240)]
    required_skills: Annotated[tuple[Skill, ...], Field(min_length=1, max_length=2)]
    priority: Annotated[int, Field(ge=1, le=5)]
    max_travel_sec: Annotated[float, Field(gt=0, le=86400)]

    @model_validator(mode="after")
    def valid(self) -> Self:
        identity(self.demand_id, "demand_id")
        if len(set(self.required_skills)) != len(self.required_skills):
            raise ValueError("Duplicate required skills")
        return self


class SyntheticDemand(Requirement):
    """A fully fictional request and review state; never incident ground truth."""

    camera_id: str
    review_status: ReviewStatus


class EventDemand(Requirement):
    """No review-status field: camera and status must come from sealed evidence."""

    event_id: str


class Scenario(Contract):
    """Bounded snapshot of artificial demands."""

    schema_version: Literal[1]
    mode: Literal["simulation"]
    kind: Literal["synthetic_fixture"]
    demands: Annotated[tuple[SyntheticDemand, ...], Field(max_length=MAX_DEMANDS)]


class EventScenario(Contract):
    """Explicit event-to-demand requests, still using fictional resources."""

    schema_version: Literal[1]
    mode: Literal["simulation"]
    kind: Literal["event_package"]
    demands: Annotated[tuple[EventDemand, ...], Field(max_length=MAX_DEMANDS)]


def allocate(resources: ResourceConfig, demands: list[dict]) -> dict:
    """Optimize whole-team assignments with exact lexicographic priority counts."""
    if len(resources.units) > MAX_TEAMS or len(demands) > MAX_DEMANDS:
        raise ValueError(
            "Exact simulation solver supports at most 12 teams and 12 demands"
        )
    if len({d["demand_id"].casefold() for d in demands}) != len(demands):
        raise ValueError("Duplicate demand identity")
    units = sorted(resources.units, key=lambda u: u.unit_id)
    demands = sorted(demands, key=lambda d: (-d["priority"], d["demand_id"]))
    bindings = {b.camera_id: b.zone_id for b in resources.camera_bindings}
    costs = {(e.from_zone, e.to_zone): e.seconds for e in resources.travel_times}
    cap = summarize(resources)["dispatchable_officer_limit"]
    options, diagnostics = [], []
    staff = {
        mask: sum(u.officers for i, u in enumerate(units) if mask & (1 << i))
        for mask in range(1 << len(units))
    }
    for demand in tqdm(demands, desc="Simulation demand constraints", leave=False):
        camera = demand["camera_id"]
        if camera not in bindings:
            raise ValueError(f"Demand camera is not canonical: {camera}")
        zone = bindings[camera]
        blocked = []
        if demand["review_status"] != "confirmed":
            blocked.append("review_not_confirmed")
        if zone is None:
            blocked.append("camera_mapping_unknown")
        eligible = 0
        travel = {}
        team_reasons = {}
        for i, unit in enumerate(units):
            reasons = []
            seconds = (
                0.0 if zone == unit.home_zone else costs.get((unit.home_zone, zone))
            )
            if unit.status != "available":
                reasons.append("unit_not_available")
            if not set(demand["required_skills"]) <= set(unit.skills):
                reasons.append("required_skill_missing")
            if zone not in unit.allowed_zones:
                reasons.append("zone_not_allowed")
            if seconds is None:
                reasons.append("travel_time_missing")
            elif seconds > demand["max_travel_sec"]:
                reasons.append("travel_deadline_exceeded")
            if not reasons:
                eligible |= 1 << i
                travel[i] = seconds
            team_reasons[unit.unit_id] = reasons
        choices = []
        if not blocked:
            for mask in range(1, 1 << len(units)):
                if mask & ~eligible or not demand["min_officers"] <= staff[mask] <= cap:
                    continue
                # Non-minimal supersets waste teams and can never improve this objective.
                if any(
                    mask & (1 << i) and staff[mask ^ (1 << i)] >= demand["min_officers"]
                    for i in travel
                ):
                    continue
                choices.append(
                    (mask, sum(travel[i] for i in travel if mask & (1 << i)))
                )
            if not choices:
                blocked.append(
                    "insufficient_eligible_staff"
                    if staff[eligible] < demand["min_officers"]
                    else "dispatch_cap_insufficient"
                )
        options.append(choices)
        diagnostics.append(
            dict(zone_id=zone, blocked_reasons=blocked, unit_reasons=team_reasons)
        )
    # DP over used-team masks; exact for the explicit limit, no heuristic fallback.
    states = {0: ((0, 0, 0, 0, 0), 0.0, ())}
    steps = 0
    for index, demand in enumerate(
        tqdm(demands, desc="Exact simulation allocation", leave=False)
    ):
        following = {}
        for used, (counts, total_cost, chosen) in states.items():
            for mask, cost in [(0, 0.0), *options[index]]:
                steps += 1
                if steps > MAX_SEARCH_STEPS:
                    raise ValueError(
                        "Exact search limit exceeded; reduce scenario size; no partial proposal published"
                    )
                if mask & used or staff[used | mask] > cap:
                    continue
                new_counts = list(counts)
                if mask:
                    new_counts[5 - demand["priority"]] += 1
                proposal = (tuple(new_counts), total_cost + cost, chosen + (mask,))
                key = used | mask
                old = following.get(key)
                if (
                    old is None
                    or (proposal[0], -proposal[1]) > (old[0], -old[1])
                    or (
                        (proposal[0], proposal[1]) == (old[0], old[1])
                        and proposal[2] < old[2]
                    )
                ):
                    following[key] = proposal
        states = following
    used, best = min(
        states.items(),
        key=lambda item: (
            tuple(-n for n in item[1][0]),
            item[1][1],
            staff[item[0]],
            item[1][2],
        ),
    )
    counts, total_cost, chosen = best
    assignments, unmet = [], []
    for index, (demand, mask) in enumerate(zip(demands, chosen)):
        diagnostic = diagnostics[index]
        base = dict(
            demand_id=demand["demand_id"],
            camera_id=demand["camera_id"],
            event_id=demand.get("event_id"),
            priority=demand["priority"],
            min_officers=demand["min_officers"],
            zone_id=diagnostic["zone_id"],
            review_status=demand["review_status"],
            synthetic_demand=demand["synthetic_demand"],
            source_demo_only=demand["source_demo_only"],
        )
        if mask:
            selected = [u for i, u in enumerate(units) if mask & (1 << i)]
            assignments.append(
                dict(
                    base,
                    units=[u.unit_id for u in selected],
                    officers=staff[mask],
                    total_simulated_travel_sec=next(
                        c for m, c in options[index] if m == mask
                    ),
                    rationale="Confirmed review/fixture; eligible skills/zone/travel; whole teams fit shared cap and reserve; chosen by priority objective.",
                )
            )
        else:
            reasons = diagnostic["blocked_reasons"] or [
                "shared_staff_cap_or_competing_demand"
            ]
            unmet.append(
                dict(base, reasons=reasons, unit_reasons=diagnostic["unit_reasons"])
            )
    available = sum(u.officers for u in units if u.status == "available")
    return dict(
        mode="simulation",
        operational_ready=False,
        dry_run=True,
        fictional_resource_data=True,
        dispatchable_officer_limit=cap,
        assigned_officers=staff[used],
        available_officers_remaining=available - staff[used],
        required_reserve_officers=resources.policy.reserve_officers,
        reserve_met=available - staff[used] >= resources.policy.reserve_officers,
        assignments=assignments,
        unmet_demands=unmet,
        objective="maximize fulfilled counts at priorities 5,4,3,2,1; minimize total team travel; minimize officers; deterministic mask tie-break",
        fulfilled_by_priority=list(counts),
        total_simulated_travel_sec=total_cost,
        exact=True,
        search_steps=steps,
        message="Simulation proposal only; no dispatch commands or incident confirmations issued.",
    )


def propose_dispatch(
    manifest_path: Path,
    resource_path: Path,
    demand_path: Path,
    output: Path,
    events_dir: Path | None = None,
    review_dir: Path | None = None,
) -> dict:
    """Read snapshot inputs/journal and publish an idempotent, provenance-bound proposal."""
    manifest_sha = digest(manifest_path.resolve())
    manifest = load_manifest(manifest_path, require_files=False)
    for path in (resource_path, demand_path):
        reject_link(path.absolute())
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Simulation input exceeds 1 MiB")
    input_hashes = {
        path.resolve(): digest(path)
        for path in (manifest.path, resource_path, demand_path)
    }
    input_hashes[manifest.path] = manifest_sha
    resources = ResourceConfig.model_validate_json(
        resource_path.read_text(encoding="utf-8-sig")
    )
    resources.bind(manifest)
    text = demand_path.read_text(encoding="utf-8-sig")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Demand configuration must be an object")
    protected = [
        manifest.raw_root,
        manifest.path,
        resource_path.resolve(),
        demand_path.resolve(),
    ]
    demands = []
    event_signature = None
    if value.get("kind") == "synthetic_fixture":
        if events_dir is not None or review_dir is not None:
            raise ValueError(
                "Synthetic fixtures cannot imply a journal-backed event review"
            )
        scenario = Scenario.model_validate_json(text)
        demands = [
            dict(
                d.model_dump(mode="json"), synthetic_demand=True, source_demo_only=True
            )
            for d in scenario.demands
        ]
    else:
        scenario = EventScenario.model_validate_json(text)
        if events_dir is None or review_dir is None:
            raise ValueError("Event demands require --events-dir and --review-dir")
        events_dir, review_dir = events_dir.resolve(), review_dir.absolute()
        tracked_event_inputs = [
            events_dir / "events.jsonl",
            events_dir / "event_manifest.json",
        ]
        if review_dir.exists():
            tracked_event_inputs.extend(
                [review_dir / "reviews.db", review_dir / "review_manifest.json"]
            )
        for path in tracked_event_inputs:
            reject_link(path)
            input_hashes[path] = digest(path)
        journal_existed = review_dir.exists()
        report, _ = load_events(events_dir)
        if report["signature"]["source_manifest_sha256"] != digest(manifest.path):
            raise ValueError("Events belong to another source manifest")
        rows = {
            row["event"]["event_id"]: row for row in list_events(events_dir, review_dir)
        }
        if len({d.event_id for d in scenario.demands}) != len(scenario.demands):
            raise ValueError("Same event cannot be requested twice")
        videos = {v.video_id: v.camera_id for v in manifest.videos}
        for request in scenario.demands:
            if request.event_id not in rows:
                raise ValueError("Requested event is not in sealed package")
            row = rows[request.event_id]
            event = row["event"]
            if videos.get(event["video_id"]) != event["camera_id"]:
                raise ValueError("Event source camera/video mismatch")
            demands.append(
                dict(
                    request.model_dump(mode="json"),
                    camera_id=event["camera_id"],
                    review_status=row["status"],
                    synthetic_demand=event["synthetic_event"],
                    source_demo_only=event["demo_only"],
                )
            )
        protected.extend(
            [events_dir, review_dir, *[Path(p) for p in report["protected_roots"]]]
        )
        event_signature = dict(
            events_sha256=digest(events_dir / "events.jsonl"),
            event_manifest_sha256=digest(events_dir / "event_manifest.json"),
            reviews_sha256=digest(review_dir / "reviews.db")
            if review_dir.exists()
            else None,
        )
        if journal_existed != review_dir.exists():
            raise ValueError("Review journal changed while reading snapshot; retry")
    if any(digest(path) != sha for path, sha in input_hashes.items()):
        raise ValueError("Input/review changed while reading snapshot; retry")
    if len(resources.units) > MAX_TEAMS or len(demands) > MAX_DEMANDS:
        raise ValueError(
            "Exact simulation solver supports at most 12 teams and 12 demands"
        )
    output = protect_output(output, protected)
    signature = dict(
        pipeline="cctv-dispatch-v1",
        manifest_sha256=digest(manifest.path),
        resources_sha256=digest(resource_path),
        demands_sha256=digest(demand_path),
        event_review=event_signature,
        code_sha256=digest(Path(__file__).resolve()),
        resource_contract_sha256=digest(
            Path(__file__).with_name("resources.py").resolve()
        ),
        review_contract_sha256=digest(
            Path(__file__).with_name("event_review.py").resolve()
        ),
    )
    state_path, report_path = (
        output / "dispatch_state.json",
        output / "dispatch_report.json",
    )
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory")
    output.mkdir(parents=True, exist_ok=True)
    for name in (".ingest.lock", "dispatch_state.json", "dispatch_report.json"):
        reject_link(output / name)
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(signature=signature, status="processing")
        )
        if state["signature"] != signature:
            raise ValueError("Input/recipe/review changed; use a new output directory")
        if state["status"] == "complete":
            if digest(report_path) != state["report_sha256"]:
                raise ValueError("Dispatch report artifact changed")
            return dict(read_json(report_path), resumed=True)
        save_json(state_path, state)
        result = allocate(resources, demands)
        if any(digest(path) != sha for path, sha in input_hashes.items()):
            raise ValueError("Input/review changed during allocation; retry")
        if scenario.kind == "event_package" and journal_existed != review_dir.exists():
            raise ValueError("Review journal changed during allocation; retry")
        result.update(
            schema_version=1, signature=signature, demand_source=scenario.kind
        )
        save_json(report_path, result)
        save_json(
            state_path,
            dict(
                signature=signature,
                status="complete",
                report_sha256=digest(report_path),
            ),
        )
        return dict(result, resumed=False)


def main() -> int:
    """Run a small simulation or a review-bound event request snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "resources", "demands", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--events-dir", type=Path)
    parser.add_argument("--review-dir", type=Path)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                propose_dispatch(
                    args.manifest,
                    args.resources,
                    args.demands,
                    args.output,
                    args.events_dir,
                    args.review_dir,
                ),
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError, KeyError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"Simulation allocation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
