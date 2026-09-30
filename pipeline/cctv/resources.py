"""Simulation-only zones and patrol roster, separate from CCTV source metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import Manifest, identity, load_manifest
from pipeline.cctv.traffic import Contract

Skill = Literal["traffic_control", "scene_assessment"]


class Zone(Contract):
    """A fictional area; no geographic coordinates or inferred camera location."""

    zone_id: str
    name: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("zone_id")
    @classmethod
    def valid_id(cls, value):
        return identity(value, "zone_id")

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value):
        if value != value.strip() or any(ord(c) < 32 for c in value) or not value:
            raise ValueError(
                "Zone name must be trimmed, nonempty text without controls"
            )
        return value


class CameraBinding(Contract):
    """An unknown mapping stays null; simulated mappings do not confirm locations."""

    camera_id: str
    zone_id: str | None
    status: Literal["unknown", "simulated"]

    @model_validator(mode="after")
    def valid(self):
        identity(self.camera_id, "camera_id")
        if self.zone_id is not None:
            identity(self.zone_id, "zone_id")
        if (self.status == "unknown") != (self.zone_id is None):
            raise ValueError(
                "Unknown camera mapping must have null zone; simulated needs a zone"
            )
        return self


class Unit(Contract):
    """An indivisible, fictional patrol team with explicit capability constraints."""

    unit_id: str
    home_zone: str
    officers: Annotated[int, Field(ge=1, le=20)]
    status: Literal["available", "busy", "offline"]
    skills: Annotated[tuple[Skill, ...], Field(min_length=1, max_length=2)]
    allowed_zones: Annotated[tuple[str, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def valid(self):
        identity(self.unit_id, "unit_id")
        identity(self.home_zone, "home_zone")
        for zone in self.allowed_zones:
            identity(zone, "allowed_zone")
        if len(set(self.skills)) != len(self.skills) or len(
            set(self.allowed_zones)
        ) != len(self.allowed_zones):
            raise ValueError("Duplicate unit skills or zones")
        if self.home_zone not in self.allowed_zones:
            raise ValueError("Home zone must be included in allowed zones")
        return self


class TravelTime(Contract):
    """Directed, invented travel cost; missing pairs remain unavailable."""

    from_zone: str
    to_zone: str
    seconds: Annotated[float, Field(gt=0, le=86400)]

    @model_validator(mode="after")
    def valid(self):
        identity(self.from_zone, "from_zone")
        identity(self.to_zone, "to_zone")
        if self.from_zone == self.to_zone:
            raise ValueError(
                "Self travel is implicitly zero; do not provide self edges"
            )
        return self


class ResourcePolicy(Contract):
    """Personnel caps apply to this simulation snapshot, not a real duty roster."""

    staff_budget: Annotated[int, Field(ge=0, le=2000)]
    max_dispatch_officers: Annotated[int, Field(ge=0, le=2000)]
    reserve_officers: Annotated[int, Field(ge=0, le=2000)]
    require_confirmed_events: Literal[True] = True

    @field_validator("require_confirmed_events", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError(
                "Confirmed review is required; use the literal boolean true"
            )
        return value

    @model_validator(mode="after")
    def limits(self):
        if (
            self.reserve_officers > self.staff_budget
            or self.max_dispatch_officers > self.staff_budget
        ):
            raise ValueError("Reserve/dispatch cap exceeds staff budget")
        return self


class ResourceConfig(Contract):
    """Validated scenario configuration; cannot be labeled operational."""

    schema_version: Literal[1]
    mode: Literal["simulation"]
    zones: Annotated[tuple[Zone, ...], Field(min_length=1, max_length=100)]
    camera_bindings: Annotated[
        tuple[CameraBinding, ...], Field(min_length=1, max_length=1000)
    ]
    units: Annotated[tuple[Unit, ...], Field(max_length=100)]
    travel_times: Annotated[tuple[TravelTime, ...], Field(max_length=9900)]
    policy: ResourcePolicy

    @model_validator(mode="after")
    def relations(self):
        for name, values in (
            ("zone", [z.zone_id for z in self.zones]),
            ("unit", [u.unit_id for u in self.units]),
            ("camera", [c.camera_id for c in self.camera_bindings]),
        ):
            if len({v.casefold() for v in values}) != len(values):
                raise ValueError(f"Duplicate {name} identity")
        zones = {z.zone_id for z in self.zones}
        for binding in self.camera_bindings:
            if binding.zone_id is not None and binding.zone_id not in zones:
                raise ValueError("Camera references an unknown zone")
        for unit in self.units:
            if unit.home_zone not in zones or not set(unit.allowed_zones) <= zones:
                raise ValueError("Unit references an unknown zone")
        pairs = set()
        for edge in self.travel_times:
            pair = (edge.from_zone, edge.to_zone)
            if not set(pair) <= zones or pair in pairs:
                raise ValueError("Travel edge has unknown zone or duplicate direction")
            pairs.add(pair)
        roster = sum(u.officers for u in self.units)
        if roster > self.policy.staff_budget:
            raise ValueError("Roster exceeds staff budget")
        return self

    def bind(self, manifest: Manifest) -> None:
        """Require every canonical source camera once; never modify source metadata."""
        if {c.camera_id for c in self.camera_bindings} != {
            str(c["camera_id"]) for c in manifest.cameras
        }:
            raise ValueError(
                "Resource bindings must cover each canonical manifest camera exactly once"
            )


def summarize(config: ResourceConfig) -> dict:
    """Describe constraints, not assignments; caps do not guarantee feasible demand."""
    roster = sum(u.officers for u in config.units)
    available = sum(u.officers for u in config.units if u.status == "available")
    capacity = min(
        config.policy.max_dispatch_officers,
        max(0, available - config.policy.reserve_officers),
    )
    costs = {(e.from_zone, e.to_zone): e.seconds for e in config.travel_times}
    pairs = []
    for unit in config.units:
        for zone in unit.allowed_zones:
            seconds = (
                0.0 if zone == unit.home_zone else costs.get((unit.home_zone, zone))
            )
            reasons = []
            if unit.status != "available":
                reasons.append("unit_not_available")
            if unit.officers > capacity:
                reasons.append("unit_exceeds_dispatchable_cap")
            if seconds is None:
                reasons.append("travel_time_missing")
            pairs.append(
                dict(
                    unit_id=unit.unit_id,
                    zone_id=zone,
                    simulated_travel_sec=seconds,
                    eligible_without_demand=not reasons,
                    blocked_reasons=reasons,
                )
            )
    unknown = [b.camera_id for b in config.camera_bindings if b.status == "unknown"]
    warnings = []
    if unknown:
        warnings.append("camera_mapping_unknown")
    if available < config.policy.reserve_officers:
        warnings.append("available_staff_below_reserve")
    if not any(p["eligible_without_demand"] for p in pairs):
        warnings.append("no_team_fits_current_constraints")
    if any(p["simulated_travel_sec"] is None for p in pairs):
        warnings.append("some_direct_travel_times_missing")
    return dict(
        schema_version=1,
        mode="simulation",
        schema_valid=True,
        simulation_ready=not unknown
        and any(p["eligible_without_demand"] for p in pairs),
        operational_ready=False,
        fictional_resource_data=True,
        real_camera_locations_confirmed=False,
        roster_officers=roster,
        available_officers=available,
        unavailable_officers=roster - available,
        unused_staff_budget=config.policy.staff_budget - roster,
        reserve_officers=config.policy.reserve_officers,
        dispatchable_officer_limit=capacity,
        unit_zone_constraints=pairs,
        unknown_camera_mappings=unknown,
        warnings=warnings,
        assignments=[],
        message="Simulation configuration only; no patrol dispatch recommendation has been generated.",
    )


def validate_resources(manifest_path: Path, config_path: Path, output: Path) -> dict:
    """Publish a deterministic report with safe output isolation and no-op resume."""
    manifest = load_manifest(manifest_path, require_files=False)
    config_path = config_path.absolute()
    reject_link(config_path)
    if config_path.stat().st_size > 1024 * 1024:
        raise ValueError("Resource config exceeds 1 MiB")
    config = ResourceConfig.model_validate_json(
        config_path.read_text(encoding="utf-8-sig")
    )
    config.bind(manifest)
    output = protect_output(output, [manifest.raw_root, manifest.path, config_path])
    signature = dict(
        pipeline="cctv-resources-v1",
        manifest_sha256=digest(manifest.path),
        config_sha256=digest(config_path),
        code_sha256=digest(Path(__file__).resolve()),
    )
    state_path, report_path = (
        output / "resource_state.json",
        output / "resource_report.json",
    )
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory; choose an empty folder")
    output.mkdir(parents=True, exist_ok=True)
    for name in (".ingest.lock", "resource_state.json", "resource_report.json"):
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
                raise ValueError("Resource report artifact changed")
            return dict(read_json(report_path), resumed=True)
        save_json(state_path, state)
        report = dict(summarize(config), signature=signature)
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
    """Validate a Windows-ready simulation snapshot without loading CV models."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                validate_resources(args.manifest, args.config, args.output), indent=2
            )
        )
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Resource validation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
