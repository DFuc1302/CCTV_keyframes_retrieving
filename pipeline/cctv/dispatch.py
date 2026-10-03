"""Certified, bounded simulation allocation; proposals only, no real dispatch actions."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import warnings
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from tqdm import tqdm

from pipeline.cctv.event_review import list_events, load_events
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.resources import ResourceConfig, Skill, Unit
from pipeline.cctv.traffic import Contract

MAX_TEAMS = 100
MAX_DEMANDS = 100
LEGACY_LIMIT = 12
MAX_SEARCH_STEPS = 2_000_000  # Only the private exhaustive DP is bounded by this.
MAX_TRAVEL_INTEGER = 1 << 40
MASK_CHUNK_BITS = 20
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


@dataclass
class _AllocationProblem:
    units: list[Unit]
    demands: list[dict]
    cap: int
    travel: list[dict[int, float]]
    diagnostics: list[dict]
    reserve: int


def _prepare(
    resources: ResourceConfig, demands: list[dict], *, progress: bool = True
) -> _AllocationProblem:
    """Share the eligibility and failure explanations between both backends."""
    if len(resources.units) > MAX_TEAMS or len(demands) > MAX_DEMANDS:
        raise ValueError(
            "Certified simulation solver supports at most 100 teams and 100 demands"
        )
    if len({d["demand_id"].casefold() for d in demands}) != len(demands):
        raise ValueError("Duplicate demand identity")
    units = sorted(resources.units, key=lambda u: u.unit_id)
    demands = sorted(demands, key=lambda d: (-d["priority"], d["demand_id"]))
    bindings = {b.camera_id: b.zone_id for b in resources.camera_bindings}
    costs = {(e.from_zone, e.to_zone): e.seconds for e in resources.travel_times}
    available = sum(u.officers for u in units if u.status == "available")
    cap = min(
        resources.policy.max_dispatch_officers,
        max(0, available - resources.policy.reserve_officers),
    )
    travel_by_demand, diagnostics = [], []
    for demand in tqdm(
        demands, desc="Simulation demand constraints", leave=False, disable=not progress
    ):
        camera = demand["camera_id"]
        if camera not in bindings:
            raise ValueError(f"Demand camera is not canonical: {camera}")
        zone = bindings[camera]
        blocked = []
        if demand["review_status"] != "confirmed":
            blocked.append("review_not_confirmed")
        if zone is None:
            blocked.append("camera_mapping_unknown")
        travel, team_reasons = {}, {}
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
                travel[i] = seconds
            team_reasons[unit.unit_id] = reasons
        if not blocked:
            # Integer subset-sum is bounded by the 2,000-officer policy, not 2**teams.
            reachable = 1
            cap_bits = (1 << (cap + 1)) - 1
            for i in travel:
                reachable = (reachable | (reachable << units[i].officers)) & cap_bits
            if not reachable >> demand["min_officers"]:
                blocked.append(
                    "insufficient_eligible_staff"
                    if sum(units[i].officers for i in travel) < demand["min_officers"]
                    else "dispatch_cap_insufficient"
                )
        travel_by_demand.append(travel)
        diagnostics.append(
            dict(zone_id=zone, blocked_reasons=blocked, unit_reasons=team_reasons)
        )
    return _AllocationProblem(
        units, demands, cap, travel_by_demand, diagnostics,
        resources.policy.reserve_officers,
    )


def _publish(problem: _AllocationProblem, chosen: tuple[int, ...], **backend) -> dict:
    """Independently check whole-team feasibility before constructing any proposal."""
    if len(chosen) != len(problem.demands):
        raise ValueError("Allocation certificate has the wrong demand count")
    units, assignments, unmet = problem.units, [], []
    used, assigned, total_cost = 0, 0, 0.0
    counts = [0] * 5
    for index, (demand, mask) in enumerate(zip(problem.demands, chosen)):
        diagnostic = problem.diagnostics[index]
        if not isinstance(mask, int) or mask < 0 or mask >> len(units) or mask & used:
            raise ValueError("Allocation certificate reuses or invents a unit")
        selected_indices = [i for i in range(len(units)) if mask & (1 << i)]
        officers = sum(units[i].officers for i in selected_indices)
        if mask and (
            diagnostic["blocked_reasons"]
            or officers < demand["min_officers"]
            or any(i not in problem.travel[index] for i in selected_indices)
        ):
            raise ValueError("Allocation certificate violates demand eligibility/staffing")
        used |= mask
        assigned += officers
        if assigned > problem.cap:
            raise ValueError("Allocation certificate exceeds the shared staff/reserve cap")
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
            cost = sum(problem.travel[index][i] for i in selected_indices)
            total_cost += cost
            counts[5 - demand["priority"]] += 1
            assignments.append(
                dict(
                    base,
                    units=[units[i].unit_id for i in selected_indices],
                    officers=officers,
                    total_simulated_travel_sec=cost,
                    rationale="Confirmed review/fixture; eligible skills/zone/travel; whole teams fit shared cap and reserve; chosen by priority objective.",
                )
            )
        else:
            unmet.append(
                dict(
                    base,
                    reasons=diagnostic["blocked_reasons"]
                    or ["shared_staff_cap_or_competing_demand"],
                    unit_reasons=diagnostic["unit_reasons"],
                )
            )
    available = sum(u.officers for u in units if u.status == "available")
    return dict(
        mode="simulation",
        operational_ready=False,
        dry_run=True,
        fictional_resource_data=True,
        dispatchable_officer_limit=problem.cap,
        assigned_officers=assigned,
        available_officers_remaining=available - assigned,
        required_reserve_officers=problem.reserve,
        reserve_met=available - assigned >= problem.reserve,
        assignments=assignments,
        unmet_demands=unmet,
        objective="maximize fulfilled counts at priorities 5,4,3,2,1; minimize total team travel; minimize officers; deterministic mask tie-break",
        fulfilled_by_priority=counts,
        total_simulated_travel_sec=total_cost,
        exact=True,
        message="Simulation proposal only; no dispatch commands or incident confirmations issued.",
        **backend,
    )


def _legacy(problem: _AllocationProblem, *, backend: str = "legacy_dp") -> dict:
    if len(problem.units) > LEGACY_LIMIT or len(problem.demands) > LEGACY_LIMIT:
        raise ValueError("Private legacy oracle supports at most 12 teams and 12 demands")
    units = problem.units
    staff = {
        mask: sum(u.officers for i, u in enumerate(units) if mask & (1 << i))
        for mask in range(1 << len(units))
    }
    options = []
    for index, demand in enumerate(problem.demands):
        travel = problem.travel[index]
        eligible = sum(1 << i for i in travel)
        choices = []
        if not problem.diagnostics[index]["blocked_reasons"]:
            for mask in range(1, 1 << len(units)):
                if mask & ~eligible or not demand["min_officers"] <= staff[mask] <= problem.cap:
                    continue
                if any(
                    mask & (1 << i) and staff[mask ^ (1 << i)] >= demand["min_officers"]
                    for i in travel
                ):
                    continue
                choices.append(
                    (mask, sum(travel[i] for i in travel if mask & (1 << i)))
                )
        options.append(choices)
    states = {0: ((0, 0, 0, 0, 0), 0.0, ())}
    steps = 0
    for index, demand in enumerate(
        tqdm(problem.demands, desc="Legacy exact simulation allocation", leave=False)
    ):
        following = {}
        for used, (counts, total_cost, chosen) in states.items():
            for mask, cost in [(0, 0.0), *options[index]]:
                steps += 1
                if steps > MAX_SEARCH_STEPS:
                    raise ValueError(
                        "Exact search limit exceeded; reduce scenario size; no partial proposal published"
                    )
                if mask & used or staff[used | mask] > problem.cap:
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
    _, best = min(
        states.items(),
        key=lambda item: (
            tuple(-n for n in item[1][0]), item[1][1], staff[item[0]], item[1][2]
        ),
    )
    return _publish(
        problem, best[2], search_steps=steps, solver_backend=backend,
        solver_version=None, solver_status="optimal", solver_nodes=0,
        solver_stages=0, solver_certificate="exhaustive_dp",
    )


def _allocate_legacy(resources: ResourceConfig, demands: list[dict]) -> dict:
    """Private <=12 oracle and explicit missing-SciPy/precision fallback only."""
    if len(resources.units) > LEGACY_LIMIT or len(demands) > LEGACY_LIMIT:
        raise ValueError("Private legacy oracle supports at most 12 teams and 12 demands")
    return _legacy(_prepare(resources, demands))


def _load_milp():
    import numpy as np
    import scipy
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_array, vstack

    return scipy.__version__, np, Bounds, LinearConstraint, milp, coo_array, vstack


def _travel_weights(problem: _AllocationProblem) -> tuple[dict, str]:
    """Preserve every Decimal(str(float)) distinction; never quantize travel."""
    fractions, denominator = {}, 1
    for d, travel in enumerate(problem.travel):
        if problem.diagnostics[d]["blocked_reasons"]:
            continue
        for u, seconds in travel.items():
            if problem.units[u].officers > problem.cap:
                continue
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Travel must be finite and nonnegative")
            numerator, divisor = Decimal(str(seconds)).as_integer_ratio()
            fractions[u, d] = (numerator, divisor)
            denominator = math.lcm(denominator, divisor)
    weights = {
        edge: numerator * (denominator // divisor)
        for edge, (numerator, divisor) in fractions.items()
    }
    common = math.gcd(*weights.values()) if weights else 0
    if common:
        weights = {edge: value // common for edge, value in weights.items()}
    else:
        common, denominator = 1, 1
    # At most one edge per unit can be selected. This bound is exact in binary64
    # and leaves substantial headroom for independent objective comparisons.
    maxima = [0] * len(problem.units)
    for (u, _), value in weights.items():
        maxima[u] = max(maxima[u], value)
    if sum(maxima) > MAX_TRAVEL_INTEGER:
        raise ValueError(
            "Travel precision/range cannot be represented conservatively for MILP "
            "(exact decimal-scaled 100-unit total exceeds 2**40); "
            "use coarser explicitly authored travel inputs, never silently rounded costs"
        )
    return weights, f"{common}/{denominator} seconds"


def _select_backend(problem: _AllocationProblem):
    recipe = dict(
        algorithm="lexicographic-sparse-binary-v2",
        travel_encoding="Decimal(str(float_seconds)); exact integer scale/gcd",
        max_travel_integer=MAX_TRAVEL_INTEGER,
        mask_chunk_bits=MASK_CHUNK_BITS,
        fallback_limit=LEGACY_LIMIT,
    )
    if all(d["blocked_reasons"] for d in problem.diagnostics):
        return None, None, dict(recipe, backend="trivial", version=None), None
    small = len(problem.units) <= LEGACY_LIMIT and len(problem.demands) <= LEGACY_LIMIT
    try:
        loaded = _load_milp()
    except ImportError as exc:
        if not small:
            raise ValueError(
                "SciPy MILP is required above 12 teams or 12 demands; "
                "no heuristic/partial proposal is available"
            ) from exc
        return (
            None, None, dict(recipe, backend="legacy_dp", version=None),
            "SciPy MILP unavailable; using the private <=12 exhaustive legacy DP",
        )
    try:
        weights, unit = _travel_weights(problem)
    except ValueError as exc:
        if not small:
            raise
        return (
            None, None, dict(recipe, backend="legacy_dp_precision", version=None),
            f"{exc}; using the private <=12 exhaustive legacy DP",
        )
    return (
        loaded, weights,
        dict(recipe, backend="scipy.optimize.milp", version=loaded[0], travel_unit=unit),
        None,
    )


def _allocate_milp(problem: _AllocationProblem, loaded, weights: dict, recipe: dict) -> dict:
    version, np, Bounds, LinearConstraint, milp, coo_array, vstack = loaded
    edges = list(weights)
    edge_index = {edge: i for i, edge in enumerate(edges)}
    offset = len(edges)
    size = offset + len(problem.demands)
    lower, upper = np.zeros(size), np.ones(size)
    by_unit = [[] for _ in problem.units]
    by_demand = [[] for _ in problem.demands]
    for i, (u, d) in enumerate(edges):
        by_unit[u].append(i)
        by_demand[d].append(i)
    for d, diagnostic in enumerate(problem.diagnostics):
        if diagnostic["blocked_reasons"]:
            upper[offset + d] = 0
    rows = []
    for indices in by_unit:
        if indices:
            rows.append(({i: 1 for i in indices}, -math.inf, 1))
    for i, (_, d) in enumerate(edges):
        rows.append(({i: 1, offset + d: -1}, -math.inf, 0))
    for d, demand in enumerate(problem.demands):
        row = {i: -problem.units[edges[i][0]].officers for i in by_demand[d]}
        row[offset + d] = demand["min_officers"]
        rows.append((row, -math.inf, 0))
    officers = {i: problem.units[u].officers for i, (u, _) in enumerate(edges)}
    rows.append((officers, -math.inf, problem.cap))

    def matrix(constraint_rows):
        rr, cc, values = [], [], []
        for r, (coefficients, _, _) in enumerate(constraint_rows):
            for c, value in coefficients.items():
                rr.append(r)
                cc.append(c)
                values.append(value)
        return coo_array(
            (np.asarray(values, dtype=float),
             (np.asarray(rr, dtype=np.int32), np.asarray(cc, dtype=np.int32))),
            shape=(len(constraint_rows), size),
        ).tocsc()

    base_matrix = matrix(rows)
    locks, current = [], [0] * size
    nodes, calls = 0, 0

    def validate(values):
        if any(not lower[i] <= value <= upper[i] for i, value in enumerate(values)):
            raise ValueError("MILP certificate violates binary variable bounds")
        for coefficients, lo, hi in (*rows, *locks):
            value = sum(coefficient * values[i] for i, coefficient in coefficients.items())
            if not lo <= value <= hi:
                raise ValueError("MILP certificate violates feasibility or an objective lock")

    def stage(coefficients, *, lock=True):
        nonlocal current, nodes, calls
        coefficients = {i: value for i, value in coefficients.items() if value}
        if not coefficients:
            return 0
        value = sum(coefficient * current[i] for i, coefficient in coefficients.items())
        bound = sum(
            coefficient * int(lower[i] if coefficient > 0 else upper[i])
            for i, coefficient in coefficients.items()
        )
        # A feasible point at the binary-bound lower bound proves this stage
        # without a redundant backend call (not an invented optimal status).
        if value != bound:
            objective = np.zeros(size)
            for i, coefficient in coefficients.items():
                objective[i] = coefficient
            constraint_rows = [*rows, *locks]
            constraints = LinearConstraint(
                vstack((base_matrix, matrix(locks)), format="csc") if locks else base_matrix,
                np.asarray([lo for _, lo, _ in constraint_rows]),
                np.asarray([hi for _, _, hi in constraint_rows]),
            )
            result = milp(
                objective, integrality=np.ones(size, dtype=np.uint8),
                bounds=Bounds(lower, upper), constraints=constraints,
                options={"presolve": True, "mip_rel_gap": 0.0},
            )
            calls += 1
            gap = getattr(result, "mip_gap", None)
            if (
                result.status != 0 or not result.success or gap is None
                or not math.isfinite(gap) or gap != 0
            ):
                raise ValueError(
                    "MILP stage did not certify optimal status with zero gap; "
                    "no partial proposal published"
                )
            vector = np.asarray(result.x, dtype=float)
            if vector.shape != (size,) or not np.all(np.isfinite(vector)):
                raise ValueError("MILP certificate has missing/nonfinite variables")
            rounded = np.rint(vector)
            if np.any(np.abs(vector - rounded) > 1e-6) or np.any((rounded < 0) | (rounded > 1)):
                raise ValueError("MILP certificate is not binary/integer")
            current = rounded.astype(np.int8).tolist()
            validate(current)
            value = sum(
                coefficient * current[i] for i, coefficient in coefficients.items()
            )
            # Less than one quarter of an integer objective unit; never a
            # relative travel tolerance that could admit a costlier proposal.
            for certified in (result.fun, getattr(result, "mip_dual_bound", None)):
                if (
                    certified is None or not math.isfinite(certified)
                    or abs(certified - value) >= 0.25
                ):
                    raise ValueError("MILP objective/dual-bound certificate is inconsistent")
            work = getattr(result, "mip_node_count", None)
            if work is None or not math.isfinite(work) or work < 0 or int(work) != work:
                raise ValueError("MILP node-count certificate is invalid")
            nodes += int(work)
        if lock:
            locks.append((coefficients, value, value))
        return value

    for priority in range(5, 0, -1):
        group = [
            offset + d for d, demand in enumerate(problem.demands)
            if demand["priority"] == priority
        ]
        count = -stage({i: -1 for i in group})
        active = [i for i in group if upper[i]]
        if count == 0 or count == len(active):
            for i in active:
                lower[i] = upper[i] = int(count != 0)
                if not count:
                    for edge in by_demand[i - offset]:
                        upper[edge] = 0
    stage({edge_index[edge]: value for edge, value in weights.items()})
    stage(officers)
    # Masks are compared in sorted-demand order and from the highest unit bit
    # down. Bounded chunks preserve arbitrary-size integer mask ordering.
    # A solved chunk uniquely identifies its bits, so fix bounds rather than
    # retaining a poorly scaled mask equality row.
    for d in range(len(problem.demands)):
        indices = sorted(by_demand[d], key=lambda i: edges[i][0], reverse=True)
        for start in range(0, len(indices), MASK_CHUNK_BITS):
            chunk = indices[start:start + MASK_CHUNK_BITS]
            stage(
                {i: 1 << (len(chunk) - rank - 1) for rank, i in enumerate(chunk)},
                lock=False,
            )
            for i in chunk:
                lower[i] = upper[i] = current[i]
                if current[i]:
                    for other in by_unit[edges[i][0]]:
                        if other != i:
                            upper[other] = 0
    validate(current)
    chosen = [0] * len(problem.demands)
    for i, (u, d) in enumerate(edges):
        if current[i]:
            chosen[d] |= 1 << u
    for d, mask in enumerate(chosen):
        if bool(mask) != bool(current[offset + d]):
            raise ValueError("MILP fulfillment certificate disagrees with whole-team masks")
    return _publish(
        problem, tuple(chosen), search_steps=nodes,
        solver_backend="scipy.optimize.milp", solver_version=version,
        solver_status="optimal", solver_nodes=nodes, solver_stages=calls,
        solver_certificate="zero_gap_optimal_with_exact_integer_validation",
        solver_recipe=recipe,
        numerical_certification="HiGHS floating-point optimality certificate, not a symbolic proof",
    )


def allocate(resources: ResourceConfig, demands: list[dict]) -> dict:
    """Lexicographic whole-team MILP; fail closed unless every stage is certified."""
    problem = _prepare(resources, demands)
    loaded, weights, recipe, warning = _select_backend(problem)
    if recipe["backend"] == "trivial":
        return _publish(
            problem, (0,) * len(problem.demands), search_steps=0,
            solver_backend="trivial", solver_version=None,
            solver_status="trivial_optimal", solver_nodes=0, solver_stages=0,
            solver_certificate="zero_variable_or_blocked_feasibility",
            solver_recipe=recipe,
        )
    if warning:
        warnings.warn(warning, RuntimeWarning, stacklevel=2)
        return dict(_legacy(problem, backend=recipe["backend"]), solver_recipe=recipe)
    return _allocate_milp(problem, loaded, weights, recipe)


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
    _, _, solver_recipe, _ = _select_backend(
        _prepare(resources, demands, progress=False)
    )
    output = protect_output(output, protected)
    signature = dict(
        pipeline="cctv-dispatch-v2",
        manifest_sha256=digest(manifest.path),
        resources_sha256=digest(resource_path),
        demands_sha256=digest(demand_path),
        event_review=event_signature,
        solver_recipe=solver_recipe,
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
        if result.get("solver_recipe") != solver_recipe:
            raise ValueError("Solver recipe changed during allocation; use a new output directory")
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
