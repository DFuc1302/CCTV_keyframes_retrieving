"""Portable, checksum-verified batch jobs; cloud results become new local runs."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from pipeline.cctv.ingest import digest, save_json, reject_link

MAX_ARCHIVE = 4 * 1024**3
MAX_EXPANDED = 20 * 1024**3


def unpack(archive: Path, target: Path) -> dict:
    """Validate the entire ZIP before extracting, including size and link limits."""
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Cloud ZIP exceeds 4 GiB")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 100000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("Cloud ZIP exceeds file/expanded size limits")
        names = set()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.filename
                or ":" in entry.filename
                or entry.filename.casefold() in names
                or (entry.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe or duplicate cloud ZIP entry")
            names.add(entry.filename.casefold())
        package.extractall(target)
    meta = json.loads((target / "cloud.json").read_text(encoding="utf-8"))
    actual = {
        p.relative_to(target).as_posix(): digest(p)
        for p in target.rglob("*")
        if p.is_file() and p != target / "cloud.json"
    }
    if actual != meta["files"]:
        raise ValueError("Cloud ZIP checksums/coverage differ")
    return meta


def pack(folder: Path, archive: Path, meta: dict) -> None:
    """Package verified payloads; the destination must be outside the payload tree."""
    if archive.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Archive must be outside its payload folder")
    meta["files"] = {
        p.relative_to(folder).as_posix(): digest(p)
        for p in folder.rglob("*")
        if p.is_file() and p != folder / "cloud.json"
    }
    save_json(folder / "cloud.json", meta)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for path in folder.rglob("*"):
            if path.is_file():
                package.write(path, path.relative_to(folder).as_posix())
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Cloud ZIP exceeds 4 GiB; export fewer videos")


def code_identity(root: Path) -> dict:
    """Pin executable sources independent of Git's CRLF/LF checkout conversion."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for parent in ("app", "pipeline/cctv", "scripts")
        for p in (root / parent).rglob("*.py")
    }


def export_job(root: Path, data: dict, archive: Path) -> None:
    if not data.get("preparation"):
        raise ValueError("Lưu camera và ROI trước khi xuất job Colab")
    with tempfile.TemporaryDirectory(dir=archive.parent) as temporary:
        folder = Path(temporary)
        videos = []
        for video in data["videos"]:
            name = video["video_id"] + Path(video["source"]).suffix.lower()
            source = Path(video["source"])
            reject_link(source)
            before = digest(source)
            target = folder / "raw" / name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            if digest(target) != before or digest(source) != before:
                raise ValueError("Video changed while exporting cloud job")
            videos.append({**video, "source": name, "sha256": before})
        pack(
            folder,
            archive,
            dict(
                kind="cctv-cloud-job-v1",
                dataset_id=data["id"],
                name=data["name"],
                preparation=data["preparation"],
                videos=videos,
                code=code_identity(root),
                commit=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip(),
            ),
        )


def run_job(root: Path, archive: Path, persistent: Path) -> Path:
    """Run existing stages locally in Colab; snapshot completed stages to Drive."""
    from app.cctv_workflow import Workflow

    started_job = time.perf_counter()
    persistent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        incoming = Path(temporary)
        meta = unpack(archive, incoming)
        if meta["kind"] != "cctv-cloud-job-v1" or meta["code"] != code_identity(root):
            raise ValueError("Checkout the exported job commit before running")
        raw = root.parent / "Demo_Data"
        raw.mkdir(exist_ok=True)
        videos = []
        for video in meta["videos"]:
            if Path(video["source"]).name != video["source"] or "\\" in video["source"]:
                raise ValueError("Cloud source must be a filename")
            source = raw / video["source"]
            if source.exists() and digest(source) != video["sha256"]:
                raise ValueError(
                    "Cloud raw destination already contains different data"
                )
            if not source.exists():
                shutil.copyfile(incoming / "raw" / video["source"], source)
            videos.append({**video, "source": str(source)})
    workflow = Workflow(
        root / "outputs/ui-workflow",
        lambda: None,
        lambda run: None,
        lambda: None,
        "cuda",
    )
    from pipeline.cctv.manifest import identity

    identity(meta["dataset_id"], "dataset_id")
    dataset = workflow.root / meta["dataset_id"]
    reject_link(dataset)
    dataset.mkdir(parents=True, exist_ok=True)
    data = dict(
        id=meta["dataset_id"],
        name=meta["name"],
        demo=True,
        videos=videos,
        preparation=meta["preparation"],
        run=None,
    )
    save_json(dataset / "dataset.json", data)
    run = dataset / "run-colab"
    checkpoint = persistent / (digest(archive) + ".checkpoint.zip")
    track_checkpoint = persistent / (digest(archive) + ".tracking.zip")
    completed = 0
    if checkpoint.exists() and not run.exists():
        with tempfile.TemporaryDirectory() as temp:
            restored = Path(temp)
            saved = unpack(checkpoint, restored)
            if saved["remote_root"] != str(root) or saved[
                "origin_job_sha256"
            ] != digest(archive):
                raise ValueError("Colab path changed; start a fresh job runtime")
            shutil.copytree(restored / "run", run)
            completed = saved["completed"]
    elif checkpoint.exists():
        with tempfile.TemporaryDirectory() as temp:
            saved = unpack(checkpoint, Path(temp))
            completed = saved["completed"]
    run.mkdir(exist_ok=True)
    import platform
    import importlib.metadata
    import torch

    if not torch.cuda.is_available():
        raise ValueError("Select a Colab GPU runtime before running the job")
    save_json(
        run / "cloud-environment.json",
        dict(
            python=platform.python_version(),
            platform=platform.platform(),
            gpu=torch.cuda.get_device_name(0),
            packages={
                p: importlib.metadata.version(p)
                for p in (
                    "torch",
                    "torchvision",
                    "av",
                    "ultralytics",
                    "transformers",
                    "faiss-cpu",
                )
            },
            quality_evaluated=False,
        ),
    )
    if completed == 4 and track_checkpoint.exists():
        with tempfile.TemporaryDirectory() as temp:
            restored = Path(temp)
            unpack(track_checkpoint, restored)
            shutil.copytree(
                restored / "tracking", run / "tracking-clips", dirs_exist_ok=True
            )
    for index, (name, module, args) in enumerate(workflow.stages(data, run), 1):
        if index <= completed:
            continue
        if module == "pipeline.cctv.track":
            args += ["--checkpoint-zip", str(track_checkpoint)]
        started = time.perf_counter()
        workflow.command(module, args, run)
        with (run / "stage-timings.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    dict(
                        stage=name,
                        module=module,
                        elapsed_sec=time.perf_counter() - started,
                    )
                )
                + "\n"
            )
        # ponytail: whole stage snapshots; incremental sync if measured Drive time dominates.
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp)
            shutil.copytree(
                run, snapshot / "run", ignore=shutil.ignore_patterns(".ingest.lock")
            )
            pack(
                snapshot,
                checkpoint.with_suffix(".partial.zip"),
                dict(
                    kind="cctv-cloud-checkpoint-v1",
                    remote_root=str(root),
                    completed=index,
                    origin_job_sha256=digest(archive),
                ),
            )
            checkpoint.with_suffix(".partial.zip").replace(checkpoint)
    result = persistent / (digest(archive) + ".result.zip")
    save_json(
        run / "cloud-runtime.json",
        dict(
            elapsed_sec=time.perf_counter() - started_job,
            includes="job unpack, stages, checkpoint writes; excludes setup, model download and result transfer",
            resumed_stages=completed,
            quality_evaluated=False,
        ),
    )
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        shutil.copytree(
            run, output / "run", ignore=shutil.ignore_patterns(".ingest.lock")
        )
        pack(
            output,
            result,
            dict(
                kind="cctv-cloud-result-v1",
                remote_root=str(root),
                remote_run=str(run),
                code=meta["code"],
                preparation=meta["preparation"],
                videos=meta["videos"],
                origin_job_sha256=digest(archive),
                commit=meta["commit"],
            ),
        )
    return result


def tracking_checkpoint(
    connection: sqlite3.Connection, state: dict, archive: Path
) -> None:
    """Persist only committed segments; replace the previous snapshot atomically."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        tracking = folder / "tracking"
        tracking.mkdir()
        with closing(sqlite3.connect(tracking / "tracks.db")) as backup:
            connection.backup(backup)
        save_json(tracking / "track_state.json", state)
        staged = archive.with_suffix(".partial.zip")
        # ponytail: whole committed DB per segment; incremental backup if Drive I/O dominates.
        pack(folder, staged, dict(kind="cctv-tracking-checkpoint-v1"))
        staged.replace(archive)


def rebase(run: Path, paths: dict, source_hashes: dict[str, str]) -> None:
    """Rebind a new imported copy and reseal dependency hashes, preserving originals."""
    original = {p: digest(p) for p in run.rglob("*") if p.is_file()}
    hashes = dict(source_hashes)
    ordered = sorted(paths.items(), key=lambda item: len(item[0]), reverse=True)

    def transform(value):
        if isinstance(value, dict):
            return {transform(k): transform(v) for k, v in value.items()}
        if isinstance(value, list):
            return [transform(v) for v in value]
        if isinstance(value, str):
            seen = set()
            while value in hashes and hashes[value] != value and value not in seen:
                seen.add(value)
                value = hashes[value]
            for source, target in ordered:
                if (
                    value == source
                    or value.startswith(source + "/")
                    or value.startswith(source + "\\")
                ):
                    return str(
                        Path(target).joinpath(
                            *PurePosixPath(
                                value[len(source) :].replace("\\", "/").lstrip("/")
                            ).parts
                        )
                    )
        return value

    for _ in range(32):
        changed = False
        for path in original:
            previous_hash = digest(path)
            if path.suffix in {".json", ".jsonl"}:
                if path.suffix == ".jsonl":
                    staged = path.with_suffix(".rebase-tmp")
                    try:
                        with (
                            path.open(encoding="utf-8") as source,
                            staged.open("w", encoding="utf-8") as target,
                        ):
                            for line in source:
                                target.write(
                                    json.dumps(
                                        transform(json.loads(line)), ensure_ascii=False
                                    )
                                    + "\n"
                                )
                        if digest(staged) != previous_hash:
                            staged.replace(path)
                            changed = True
                    except ValueError:
                        continue  # Console JSONL is not an artifact contract.
                    finally:
                        staged.unlink(missing_ok=True)
                else:
                    text = path.read_text(encoding="utf-8")
                    updated = json.dumps(
                        transform(json.loads(text)), ensure_ascii=False, indent=2
                    )
                    if updated != text:
                        path.write_text(updated, encoding="utf-8")
                        changed = True
            elif path.suffix == ".db":
                with closing(sqlite3.connect(path)) as connection, connection:
                    if connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='trigger'"
                    ).fetchone():
                        raise ValueError(
                            "Triggers are not allowed in cloud SQLite artifacts"
                        )
                    for table, column in (
                        ("cctv_frames", "source_video"),
                        ("metadata", "signature"),
                    ):
                        if not connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone():
                            continue
                        for rowid, value in connection.execute(
                            f'SELECT rowid,"{column}" FROM "{table}"'
                        ).fetchall():
                            updated = (
                                transform(value)
                                if column == "source_video"
                                else json.dumps(
                                    transform(json.loads(value)),
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                            )
                            if updated != value:
                                connection.execute(
                                    f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                                    (updated, rowid),
                                )
                                changed = True
            hashes[original[path]] = hashes[previous_hash] = digest(path)
        if not changed:
            return
    raise ValueError("Cloud artifact dependencies did not converge")


def import_result(root: Path, archive: Path, data: dict, destination: Path) -> dict:
    """Validate and materialize a fresh run; caller activates before changing dataset."""
    if destination.exists():
        raise ValueError("Cloud import destination must be new")
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        incoming = Path(temporary)
        meta = unpack(archive, incoming)
        if meta["kind"] != "cctv-cloud-result-v1" or meta["code"] != code_identity(
            root
        ):
            raise ValueError(
                "Cloud result executable version differs from this checkout"
            )
        if meta["preparation"] != data["preparation"]:
            raise ValueError("Camera/ROI/tracking settings changed since cloud export")
        local = {v["video_id"]: v for v in data["videos"]}
        if set(local) != {v["video_id"] for v in meta["videos"]}:
            raise ValueError("Cloud result video coverage differs")
        paths = {meta["remote_run"]: str(destination), meta["remote_root"]: str(root)}
        for video in meta["videos"]:
            source = Path(local[video["video_id"]]["source"])
            if (
                digest(source) != video["sha256"]
                or local[video["video_id"]]["camera_id"] != video["camera_id"]
            ):
                raise ValueError("Cloud source/camera mismatch")
            paths[
                str(
                    PurePosixPath(meta["remote_root"]).parent
                    / "Demo_Data"
                    / video["source"]
                )
            ] = str(source)
        parents = {str(Path(v["source"]).parent) for v in local.values()}
        if len(parents) != 1:
            raise ValueError("Cloud import needs sources in one dataset raw folder")
        paths[str(PurePosixPath(meta["remote_root"]).parent / "Demo_Data")] = (
            parents.pop()
        )
        shutil.copytree(incoming / "run", destination)
    # Identity validation above proves these LF digests describe the local source.
    # Bind imported source dependencies to checkout bytes before resealing artifacts.
    rebase(
        destination,
        paths,
        {checksum: digest(root / name) for name, checksum in meta["code"].items()},
    )
    from pipeline.cctv.manifest import load_manifest

    manifest = load_manifest(destination / "configs/manifest.json")
    if {v.video_id: str(v.source.resolve()) for v in manifest.videos} != {
        k: str(Path(v["source"]).resolve()) for k, v in local.items()
    }:
        raise ValueError("Imported manifest references unexpected raw paths")
    index = json.loads(
        (destination / "search-index/index_manifest.json").read_text(encoding="utf-8")
    )
    if (
        not Path(index["keyframes_root"])
        .resolve()
        .is_relative_to(destination.resolve())
    ):
        raise ValueError("Imported keyframes must be inside the new run")
    save_json(
        destination / "cloud-provenance.json",
        dict(
            origin_commit=meta["commit"],
            origin_job_sha256=meta["origin_job_sha256"],
            archive_sha256=digest(archive),
            imported_copy=True,
            cloud_quality_evaluated=False,
        ),
    )
    return meta
