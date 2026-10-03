"""Read-only, current-input-bound simulation proposals; never invoke the solver."""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.cctv_events import EventStore
from pipeline.cctv.dispatch import EventScenario, Scenario
from pipeline.cctv.events import read_json
from pipeline.cctv.ingest import digest, reject_link
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.resources import ResourceConfig, summarize

LOG = logging.getLogger("cctv.dispatch.api")


@dataclass(frozen=True)
class DispatchPackage:
    """Explicit local inputs for one immutable proposal and optional event journal."""

    output: Path
    demands: Path
    events: Path | None = None
    reviews: Path | None = None


class DispatchStore:
    """Validate publication/current inputs on request, without inference or writes."""

    def __init__(
        self, manifest: Path, resources: Path, packages: dict[str, DispatchPackage]
    ) -> None:
        self.manifest, self.resources, self.packages = manifest, resources, packages

    def report(self, package_id: str, sources: dict[str, dict]) -> dict:
        """Reject stale resource/demand/review snapshots and inconsistent assignments."""
        package = self.packages[package_id]
        review_hash = None
        files = [
            self.manifest,
            self.resources,
            package.demands,
            package.output / "dispatch_state.json",
            package.output / "dispatch_report.json",
        ]
        if package.events is not None:
            if package.reviews is None:
                raise ValueError("Event snapshot requires review path")
            files.extend(
                package.events / name
                for name in ("event_manifest.json", "events.jsonl", "event_state.json")
            )
            if package.reviews.exists():
                files.extend(
                    package.reviews / name
                    for name in ("review_manifest.json", "reviews.db")
                )
        before = {}
        for path in files:
            reject_link(path.absolute())
            before[path] = digest(path)
        state = read_json(files[3])
        report = read_json(files[4])
        if not isinstance(state, dict) or not isinstance(report, dict):
            raise ValueError("Dispatch publication must be an object")
        signature = state["signature"]
        if not isinstance(signature, dict):
            raise ValueError("Dispatch signature must be an object")
        if (
            state.get("status") != "complete"
            or state.get("report_sha256") != before[files[4]]
            or report.get("signature") != signature
            or signature.get("pipeline") != "cctv-dispatch-v2"
            or report.get("schema_version") != 1
        ):
            raise ValueError("Dispatch publication changed or incomplete")
        for key, path in (
            ("manifest_sha256", self.manifest),
            ("resources_sha256", self.resources),
            ("demands_sha256", package.demands),
        ):
            if signature.get(key) != before[path]:
                raise ValueError("Dispatch inputs changed; rebuild with a new output")
        if report.get("mode") != "simulation" or any(
            report.get(key) is not value
            for key, value in (
                ("dry_run", True),
                ("fictional_resource_data", True),
                ("operational_ready", False),
            )
        ):
            raise ValueError(
                "Only explicitly fictional simulation reports are supported"
            )
        manifest = load_manifest(self.manifest, require_files=False)
        if any(
            sources.get(v.video_id, {}).get("camera_id") != v.camera_id
            for v in manifest.videos
        ):
            raise ValueError("Dispatch manifest video differs from retrieval")
        resources = ResourceConfig.model_validate_json(
            self.resources.read_text(encoding="utf-8")
        )
        resources.bind(manifest)
        value = read_json(package.demands)
        if value["kind"] == "synthetic_fixture":
            if (
                package.events is not None
                or package.reviews is not None
                or signature["event_review"] is not None
            ):
                raise ValueError("Fixture cannot claim journal-backed review")
            scenario = Scenario.model_validate_json(
                package.demands.read_text(encoding="utf-8")
            )
            demands = [
                dict(
                    d.model_dump(mode="json"),
                    synthetic_demand=True,
                    source_demo_only=True,
                )
                for d in scenario.demands
            ]
        else:
            if package.events is None or package.reviews is None:
                raise ValueError("Event snapshot paths missing")
            scenario = EventScenario.model_validate_json(
                package.demands.read_text(encoding="utf-8")
            )
            review_hash = (
                digest(package.reviews / "reviews.db")
                if package.reviews.exists()
                else None
            )
            expected = dict(
                events_sha256=before[package.events / "events.jsonl"],
                event_manifest_sha256=before[package.events / "event_manifest.json"],
                reviews_sha256=review_hash,
            )
            if signature["event_review"] != expected:
                raise ValueError(
                    "Event/review snapshot changed; rebuild with a new output"
                )
            rows = {
                r["event_id"]: r
                for r in EventStore({"source": (package.events, package.reviews)}).rows(
                    "source", sources
                )
            }
            demands = []
            for request in scenario.demands:
                row = rows[request.event_id]
                demands.append(
                    dict(
                        request.model_dump(mode="json"),
                        camera_id=row["camera_id"],
                        review_status=row["status"],
                        synthetic_demand=row["synthetic_event"],
                        source_demo_only=row["demo_only"],
                    )
                )
        if report["demand_source"] != scenario.kind:
            raise ValueError("Dispatch demand kind mismatch")
        summary = summarize(resources)
        self.validate_assignments(report, resources, demands, summary)
        if any(digest(path) != sha for path, sha in before.items()):
            raise ValueError("Dispatch source changed during request")
        if package.reviews is not None and (
            package.reviews.exists() != (review_hash is not None)
        ):
            raise ValueError("Review journal appeared/disappeared during request")
        fields = (
            "mode",
            "dry_run",
            "fictional_resource_data",
            "operational_ready",
            "demand_source",
            "dispatchable_officer_limit",
            "assigned_officers",
            "available_officers_remaining",
            "required_reserve_officers",
            "reserve_met",
            "assignments",
            "unmet_demands",
            "total_simulated_travel_sec",
        )
        return dict(
            package_id=package_id,
            **{key: report[key] for key in fields},
            roster_officers=summary["roster_officers"],
            available_officers=summary["available_officers"],
            read_only=True,
            snapshot_sha256=before[files[4]],
        )

    @staticmethod
    def validate_assignments(
        report: dict, resources: ResourceConfig, demands: list[dict], summary: dict
    ) -> None:
        """Check feasibility/coverage against current inputs without recomputing optimum."""
        requested = {d["demand_id"]: d for d in demands}
        if any(
            not isinstance(report.get(key), list)
            or not all(isinstance(row, dict) for row in report[key])
            for key in ("assignments", "unmet_demands")
        ):
            raise ValueError("Dispatch rows must be lists of objects")
        if len(requested) != len(demands):
            raise ValueError("Duplicate demand IDs")
        units = {u.unit_id: u for u in resources.units}
        zones = {b.camera_id: b.zone_id for b in resources.camera_bindings}
        costs = {(e.from_zone, e.to_zone): e.seconds for e in resources.travel_times}
        used, seen, assigned, total_travel = set(), set(), 0, 0.0
        for row in report["assignments"] + report["unmet_demands"]:
            key = row["demand_id"]
            if key not in requested or key in seen:
                raise ValueError("Dispatch demand coverage mismatch")
            seen.add(key)
            demand = requested[key]
            for name in (
                "camera_id",
                "priority",
                "min_officers",
                "review_status",
                "synthetic_demand",
                "source_demo_only",
            ):
                if row[name] != demand[name]:
                    raise ValueError("Dispatch demand metadata mismatch")
            if (
                row.get("event_id") != demand.get("event_id")
                or row["zone_id"] != zones[demand["camera_id"]]
            ):
                raise ValueError("Dispatch event/zone mismatch")
        for row in report["assignments"]:
            demand = requested[row["demand_id"]]
            if demand["review_status"] != "confirmed" or not row["units"]:
                raise ValueError("Assignment review gate or team missing")
            staff, travel = 0, 0.0
            for key in row["units"]:
                if key in used or key not in units:
                    raise ValueError("Team used twice or unknown")
                used.add(key)
                unit, zone = units[key], row["zone_id"]
                seconds = (
                    0.0 if zone == unit.home_zone else costs.get((unit.home_zone, zone))
                )
                if (
                    unit.status != "available"
                    or zone not in unit.allowed_zones
                    or not set(demand["required_skills"]) <= set(unit.skills)
                    or seconds is None
                    or seconds > demand["max_travel_sec"]
                ):
                    raise ValueError("Assigned team not eligible")
                staff += unit.officers
                travel += seconds
            if (
                staff != row["officers"]
                or staff < demand["min_officers"]
                or not math.isclose(travel, row["total_simulated_travel_sec"])
            ):
                raise ValueError("Assigned staff/travel mismatch")
            assigned += staff
            total_travel += travel
        for row in report["unmet_demands"]:
            if not row["reasons"] or not all(
                isinstance(reason, str) for reason in row["reasons"]
            ):
                raise ValueError("Unmet demand explanation missing")
            if (
                row["review_status"] != "confirmed"
                and "review_not_confirmed" not in row["reasons"]
            ):
                raise ValueError("Unmet review gate explanation missing")
        cap, available, reserve = (
            summary[k]
            for k in (
                "dispatchable_officer_limit",
                "available_officers",
                "reserve_officers",
            )
        )
        if (
            seen != set(requested)
            or not math.isclose(total_travel, report["total_simulated_travel_sec"])
            or assigned > cap
            or report["assigned_officers"] != assigned
            or report["dispatchable_officer_limit"] != cap
            or report["required_reserve_officers"] != reserve
            or report["available_officers_remaining"] != available - assigned
            or report["reserve_met"] != (available - assigned >= reserve)
        ):
            raise ValueError("Dispatch cap/reserve/coverage mismatch")

    def catalog(self, sources: dict[str, dict]) -> dict:
        """Return availability per package; a stale snapshot never becomes an empty plan."""
        packages = []
        for name in self.packages:
            try:
                report = self.report(name, sources)
                packages.append(
                    dict(
                        package_id=name,
                        available=True,
                        assignments=len(report["assignments"]),
                        unmet_demands=len(report["unmet_demands"]),
                    )
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                sqlite3.Error,
            ) as exc:
                LOG.warning("Dispatch package %s unavailable: %s", name, exc)
                packages.append(
                    dict(
                        package_id=name,
                        available=False,
                        assignments=None,
                        unmet_demands=None,
                    )
                )
        return dict(
            packages=packages,
            available=any(p["available"] for p in packages),
            read_only=True,
            operational_ready=False,
        )
