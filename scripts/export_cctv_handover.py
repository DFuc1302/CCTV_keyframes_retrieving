"""Export sealed local benchmark summaries and a pending second-machine checklist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest


def export_handover(
    manifest_path: Path,
    runtime_dir: Path,
    event_dir: Path,
    dispatch_dir: Path,
    output: Path,
) -> dict:
    """Read only sealed publications; export summaries without media or models."""
    manifest = load_manifest(manifest_path)
    specs = {
        "runtime": (runtime_dir, "runtime_state.json", "runtime_report.json"),
        "event_benchmark": (event_dir, "benchmark_state.json", "benchmark_report.json"),
        "dispatch": (dispatch_dir, "dispatch_state.json", "dispatch_report.json"),
    }
    reports, hashes = {}, {str(manifest.path): digest(manifest.path)}
    for label, (folder, state_name, report_name) in specs.items():
        folder = folder.absolute()
        reject_link(folder)
        state_path, report_path = folder / state_name, folder / report_name
        for path in (state_path, report_path):
            reject_link(path)
            hashes[str(path)] = digest(path)
        state = read_json(state_path)
        if state.get("status") != "complete" or hashes[str(report_path)] != state.get(
            "report_sha256"
        ):
            raise ValueError(f"Incomplete or changed publication: {label}")
        reports[label] = read_json(report_path)
    runtime, events, dispatch = (reports[k] for k in specs)
    source_sha = digest(manifest.path)
    for report in (runtime, events):
        if report["signature"]["inputs"].get(str(manifest.path)) != source_sha:
            raise ValueError("Benchmark source manifest mismatch")
    dispatch_state = read_json(dispatch_dir.absolute() / "dispatch_state.json")
    if dispatch_state["signature"]["manifest_sha256"] != source_sha:
        raise ValueError("Dispatch source manifest mismatch")
    if runtime["status"] not in {"PASSED_TECHNICAL_BENCHMARK", "FAILED_REQUESTS"}:
        raise ValueError("Unknown runtime benchmark status")
    if (
        dispatch.get("mode") != "simulation"
        or dispatch.get("operational_ready") is not False
    ):
        raise ValueError("Only simulation dispatch is supported for this handover")
    hashes[str(Path(__file__).resolve())] = digest(Path(__file__).resolve())
    signature = dict(pipeline="cctv-handover-v1", inputs=hashes)
    protected = [
        manifest.raw_root,
        manifest.path,
        Path(__file__).resolve(),
        *[folder.absolute() for folder, _, _ in specs.values()],
    ]
    output = protect_output(output, protected)
    state_path = output / "handover_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output directory")
    output.mkdir(parents=True, exist_ok=True)
    names = ("handover_report.json", "second-machine.pending.json")
    for name in (*names, "handover_state.json", ".ingest.lock"):
        reject_link(output / name)

    def unchanged() -> None:
        if any(digest(Path(path)) != sha for path, sha in hashes.items()):
            raise ValueError("Handover source changed while reading")

    with output_lease(output):
        unchanged()
        if state_path.exists():
            state = read_json(state_path)
            if state["signature"] != signature:
                raise ValueError("Handover input changed; use a new output directory")
            if state["status"] == "complete":
                if set(state["artifacts"]) != set(names) or any(
                    digest(output / n) != state["artifacts"][n] for n in names
                ):
                    raise ValueError("Handover artifact changed")
                return dict(read_json(output / names[0]), resumed=True)
        save_json(state_path, dict(signature=signature, status="processing"))
        report = dict(
            schema_version=1,
            signature=signature,
            status="EXPORTED_LOCAL_EVIDENCE",
            cameras=len(manifest.cameras),
            videos=len(manifest.videos),
            runtime=dict(
                status=runtime["status"],
                by_kind=runtime["by_kind"],
                request_errors_including_warmup=runtime[
                    "request_errors_including_warmup"
                ],
                model_memory=runtime["model_memory_after"],
                memory_scope=runtime["memory_scope"],
                server=runtime["server"],
                semantic_retrieval_metrics=runtime["semantic_retrieval_metrics"],
            ),
            event_benchmark=dict(
                status=events["status"],
                annotation_mode=events["annotation_mode"],
                split_ready=events["split_ready"],
                results=events["results"],
            ),
            dispatch=dict(
                mode="simulation",
                assigned_officers=dispatch["assigned_officers"],
                dispatchable_officer_limit=dispatch["dispatchable_officer_limit"],
                required_reserve_officers=dispatch["required_reserve_officers"],
                reserve_met=dispatch["reserve_met"],
                assignments=len(dispatch["assignments"]),
                unmet_demands=len(dispatch["unmet_demands"]),
            ),
            second_machine_status="NOT_RUN",
            operational_ready=False,
        )
        template = dict(
            schema_version=1,
            status="NOT_RUN",
            machine_id=None,
            tested_commit=None,
            tested_at=None,
            reviewer=None,
            checks={
                name: "pending"
                for name in (
                    "environment",
                    "core_tests",
                    "raw_integrity",
                    "ingestion_resume",
                    "retrieval_index",
                    "api_ready",
                    "text_image_video",
                    "traffic_draft",
                    "event_fixtures",
                    "dispatch_simulation",
                    "runtime_benchmark",
                )
            },
            evidence_files=[],
            notes=None,
            operational_ready=False,
        )
        unchanged()
        save_json(output / names[0], report)
        save_json(output / names[1], template)
        save_json(
            state_path,
            dict(
                signature=signature,
                status="complete",
                artifacts={name: digest(output / name) for name in names},
            ),
        )
        return dict(report, resumed=False)


def main() -> int:
    """Create the local evidence package; never mark another machine as tested."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "runtime-dir", "event-dir", "dispatch-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        report = export_handover(
            args.manifest,
            args.runtime_dir,
            args.event_dir,
            args.dispatch_dir,
            args.output,
        )
        print(
            json.dumps(
                {k: report[k] for k in ("status", "second_machine_status", "resumed")},
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"Handover export failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
