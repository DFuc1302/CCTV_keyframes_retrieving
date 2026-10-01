"""Local, single-job demo workflow wrapping the existing CCTV CLI stages."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import threading
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path

import av
from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from tqdm import tqdm

from pipeline.cctv.events import read_json
from pipeline.cctv.ingest import reject_link, save_json
from pipeline.cctv.manifest import identity, load_manifest
from pipeline.cctv.preview import probe
from pipeline.cctv.traffic import TrafficConfig

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 1024**3


async def read_payload(request: Request) -> dict:
    """Bound JSON mutations and reject requests from another browser origin."""
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Chỉ thao tác từ giao diện ứng dụng này.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise HTTPException(413, "Biểu mẫu quá lớn.")
        body.extend(chunk)
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected object")
        return data
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "Biểu mẫu không hợp lệ.") from exc


class Workflow:
    """Persist datasets/jobs as JSON; one worker serializes all pipeline processes."""

    def __init__(
        self,
        root: Path,
        release: Callable[[], None],
        activate: Callable[[Path], None],
        restore: Callable[[], None],
        device: str = "cuda",
    ):
        self.root = root.absolute()
        reject_link(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.release, self.activate, self.restore, self.device = (
            release,
            activate,
            restore,
            device,
        )
        # ponytail: one local job; use a queue only if concurrent users are required.
        self.lock = threading.RLock()
        self.thread = None
        self.process = None
        self.cancelled = threading.Event()
        self.closing = False
        self.active = None
        for path in [
            *self.root.glob("*/job.json"),
            *self.root.glob("*/verify-job.json"),
        ]:
            job = read_json(path)
            if job["status"] in {"running", "queued"}:
                job.update(status="interrupted", error="Ứng dụng đã dừng; bấm Thử lại.")
                save_json(path, job)

    def folder(self, dataset_id: str) -> Path:
        identity(dataset_id, "dataset_id")
        path = self.root / dataset_id
        reject_link(path)
        if not (path / "dataset.json").is_file():
            raise ValueError("Không có bộ dữ liệu này.")
        return path

    def get(self, dataset_id: str) -> dict:
        with self.lock:
            folder = self.folder(dataset_id)
            result = read_json(folder / "dataset.json")
            result["job"] = (
                read_json(folder / "job.json")
                if (folder / "job.json").exists()
                else None
            )
            result["active"] = self.active == dataset_id
            return result

    def list(self) -> list[dict]:
        return [
            self.public(p.parent.name) for p in sorted(self.root.glob("*/dataset.json"))
        ]

    def public(self, dataset_id: str) -> dict:
        data = copy.deepcopy(self.get(dataset_id))
        source_run = data.get("run")
        data["has_results"] = bool(data.get("run"))
        data.pop("run", None)
        for video in data["videos"]:
            video.pop("source", None)
        if data["job"]:
            data["job"].pop("run", None)
            data["job"].pop("snapshot", None)
        with self.lock:
            path = self.folder(dataset_id) / "verify-job.json"
            verification = read_json(path) if path.exists() else None
            if verification:
                output = Path(verification["run"])
                if verification["source_run"] != source_run:
                    verification.update(
                        status="stale", stage="Run đã đổi; kiểm chứng lại", completed=0
                    )
                progress = output / "progress.jsonl"
                if progress.exists() and verification["status"] == "running":
                    with progress.open("rb") as handle:
                        handle.seek(max(0, progress.stat().st_size - 4096))
                        for line in handle.read().splitlines():
                            try:
                                verification.update(json.loads(line))
                            except ValueError:
                                pass
                if verification["status"] == "complete":
                    report = read_json(output / "report.json")
                    verification["report"] = {
                        k: report[k]
                        for k in (
                            "status",
                            "pending",
                            "verified_utc",
                            "m8",
                            "second_machine",
                            "real_accuracy_measured",
                        )
                    }
                verification.pop("run", None)
                verification.pop("source_run", None)
                verification.pop("url", None)
            data["verification_job"] = verification
        return data

    def create(self, name: str, demo: bool = False) -> dict:
        if not isinstance(demo, bool):
            raise ValueError("demo must be a boolean")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("Tên bộ dữ liệu cần 1–100 ký tự.")
        with self.lock:
            dataset_id = uuid.uuid4().hex
            folder = self.root / dataset_id
            folder.mkdir()
            videos = []
            if demo:
                manifest = load_manifest(ROOT / "configs/cctv.demo.json")
                for video in tqdm(manifest.videos, desc="Register demo videos"):
                    metadata = probe(video.source)
                    videos.append(
                        dict(
                            video_id=video.video_id,
                            camera_id=video.camera_id,
                            source=str(video.source),
                            name=video.source.name,
                            **metadata,
                        )
                    )
            save_json(
                folder / "dataset.json",
                dict(
                    id=dataset_id,
                    name=name.strip(),
                    demo=demo,
                    videos=videos,
                    preparation=None,
                    run=None,
                ),
            )
            return self.get(dataset_id)

    def idle(self) -> None:
        if self.thread and self.thread.is_alive():
            raise ValueError("Đang xử lý một tác vụ; đợi hoàn tất hoặc bấm Dừng.")

    def prepare(self, dataset_id: str, payload: dict) -> dict:
        with self.lock:
            self.idle()
            data = self.get(dataset_id)
            if not data["videos"]:
                raise ValueError("Chọn video trước.")
            start, duration = (
                float(payload["start_sec"]),
                float(payload["duration_sec"]),
            )
            if (
                not 0 <= start
                or not 1 <= duration <= 60
                or any(
                    start + duration > v["duration"] - v["first_pts"]
                    for v in data["videos"]
                )
            ):
                raise ValueError(
                    "Khoảng tracking phải nằm trong mọi video, dài 1–60 giây."
                )
            bindings = payload["cameras"]
            if set(bindings) != {v["video_id"] for v in data["videos"]}:
                raise ValueError("Gán camera cho từng video.")
            for camera in bindings.values():
                identity(camera, "camera_id")
            cameras = sorted(set(bindings.values()))
            roi = dict(
                schema_version=1,
                cameras=[
                    dict(camera_id=c, status="pending", polygon=payload["rois"].get(c))
                    for c in cameras
                ],
            )
            if any(r["polygon"] is None for r in roi["cameras"]):
                raise ValueError("Vẽ vùng đường cho mọi camera.")
            try:
                TrafficConfig.model_validate_json(json.dumps(roi))
            except ValidationError as exc:
                raise ValueError(
                    "ROI chưa hợp lệ. Vẽ ít nhất 3 đỉnh khác nhau trong ảnh, "
                    "không click lại điểm đầu để đóng vùng và không vẽ cạnh cắt nhau. "
                    "Dùng Bỏ đỉnh cuối hoặc Vẽ lại để sửa."
                ) from exc
            for video in data["videos"]:
                video["camera_id"] = bindings[video["video_id"]]
            data["preparation"] = dict(
                start_sec=start,
                duration_sec=duration,
                roi=roi,
                synthetic_case=bool(payload.get("synthetic_case", True)),
            )
            data.pop("job", None)
            data.pop("active", None)
            save_json(self.folder(dataset_id) / "dataset.json", data)
            return self.get(dataset_id)

    def write(self, path: Path, value: dict) -> None:
        reject_link(path)
        if path.exists():
            if read_json(path) != value:
                raise ValueError("Recipe khác checkpoint; lưu chuẩn bị và tạo run mới.")
        else:
            save_json(path, value)

    def start(
        self, dataset_id: str, retry: bool = False, download: bool = False
    ) -> None:
        with self.lock:
            self.idle()
            data = self.get(dataset_id)
            if not download and not data["preparation"]:
                raise ValueError("Lưu camera, ROI và khoảng thời gian trước.")
            folder = self.folder(dataset_id)
            previous = data["job"]
            if retry:
                if not previous or previous["status"] not in {
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    raise ValueError("Chỉ thử lại tác vụ đã lỗi hoặc dừng.")
                run = Path(previous["run"])
                data = previous["snapshot"]
                download = previous.get("download", False)
            else:
                run = folder / ("run-" + uuid.uuid4().hex)
                run.mkdir()
            job = dict(
                status="queued",
                stage="Chuẩn bị",
                completed=0,
                total=0,
                error=None,
                id=run.name,
                run=str(run),
                snapshot=data,
                download=download,
            )
            # Do not recursively store prior jobs inside snapshots.
            job["snapshot"] = {
                k: v for k, v in data.items() if k not in {"job", "active"}
            }
            save_json(folder / "job.json", job)
            self.cancelled.clear()
            self.thread = threading.Thread(
                target=self.worker, args=(dataset_id, job), daemon=True
            )
            self.thread.start()
            return self.get(dataset_id)

    def stages(self, data: dict, run: Path) -> list[tuple[str, str, list[str]]]:
        p = data["preparation"]
        self.write(run / "preparation.json", p)
        configs = run / "configs"
        configs.mkdir(exist_ok=True)
        manifest = configs / "manifest.json"
        sources = [Path(v["source"]) for v in data["videos"]]
        raw_root = (
            Path(ROOT.parent / "Demo_Data")
            if data["demo"]
            else self.folder(data["id"]) / "uploads"
        )
        cameras = sorted({v["camera_id"] for v in data["videos"]})
        self.write(
            manifest,
            dict(
                schema_version=1,
                raw_root=str(raw_root),
                cameras=[dict(camera_id=c) for c in cameras],
                videos=[
                    dict(
                        video_id=v["video_id"],
                        camera_id=v["camera_id"],
                        path=str(s.relative_to(raw_root)).replace("\\", "/"),
                    )
                    for v, s in zip(data["videos"], sources)
                ],
            ),
        )
        self.write(configs / "roi.json", p["roi"])
        metrics = read_json(ROOT / "configs/cctv.congestion.demo.json")
        metrics["cameras"] = [dict(metrics["cameras"][0], camera_id=c) for c in cameras]
        self.write(configs / "metrics.json", metrics)
        resources = read_json(ROOT / "configs/cctv.resources.simulation.json")
        resources["camera_bindings"] = [
            dict(camera_id=c, zone_id="SIM-A", status="simulated") for c in cameras
        ]
        self.write(configs / "resources.json", resources)
        synthetic = read_json(ROOT / "configs/cctv.demands.simulation.json")
        for demand in synthetic["demands"]:
            demand["camera_id"] = cameras[0]
        self.write(configs / "simulation.json", synthetic)
        m = str(manifest)

        def r(name):
            return str((configs if name.endswith(".json") else run) / name)

        common = ["--manifest", m]
        clip = str(ROOT / "outputs/model-cache/clip.json")
        yolo = str(ROOT / "outputs/model-cache/yolo/yolo.json")
        commands = [
            (
                "Kiểm kê",
                "scripts.inventory_cctv_evaluation",
                common + ["--output", r("inventory")],
            ),
            (
                "Trích keyframes",
                "pipeline.cctv.ingest",
                common + ["--output-dir", r("processed"), "--device", "cpu"],
            ),
            (
                "Tạo index CLIP",
                "pipeline.cctv.index",
                [
                    "--input-dir",
                    r("processed"),
                    "--output-dir",
                    r("search-index"),
                    "--model-config",
                    clip,
                    "--device",
                    self.device,
                    "--batch-size",
                    "4",
                ],
            ),
            (
                "Video preview",
                "pipeline.cctv.preview",
                ["--index-dir", r("search-index"), "--output-dir", r("video-preview")],
            ),
            (
                "Tracking xe",
                "pipeline.cctv.track",
                common
                + [
                    "--traffic-config",
                    r("roi.json"),
                    "--model-config",
                    yolo,
                    "--tracker-config",
                    str(ROOT / "configs/cctv.bytetrack.yaml"),
                    "--output-dir",
                    r("tracking-clips"),
                    "--start-sec",
                    str(p["start_sec"]),
                    "--duration-sec",
                    str(p["duration_sec"]),
                    "--segment-seconds",
                    "5",
                    "--device",
                    self.device,
                ],
            ),
            (
                "Ảnh ROI",
                "scripts.preview_cctv_roi",
                common
                + [
                    "--roi-config",
                    r("roi.json"),
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output-dir",
                    r("roi-draft-previews"),
                ],
            ),
            (
                "Thống kê traffic",
                "pipeline.cctv.congestion",
                common
                + [
                    "--roi-config",
                    r("roi.json"),
                    "--metrics-config",
                    r("metrics.json"),
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output-dir",
                    r("traffic-draft-v1"),
                    "--allow-draft",
                ],
            ),
            (
                "Ứng viên tắc đường",
                "pipeline.cctv.events",
                common
                + [
                    "--windows-dir",
                    r("traffic-draft-v1"),
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output",
                    r("events-v1"),
                ],
            ),
            (
                "Ứng viên nghi sự cố",
                "pipeline.cctv.incidents",
                common
                + [
                    "--roi",
                    r("roi.json"),
                    "--config",
                    str(ROOT / "configs/cctv.incidents.demo.json"),
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output",
                    r("incidents-draft-v1"),
                    "--allow-draft",
                ],
            ),
            (
                "Case demo",
                "pipeline.cctv.events",
                common
                + [
                    "--windows-dir",
                    r("traffic-draft-v1"),
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output",
                    r("events-demo-v1"),
                ]
                + (
                    [
                        "--demo-video",
                        data["videos"][0]["video_id"],
                        "--demo-start-sec",
                        str(p["start_sec"]),
                        "--demo-end-sec",
                        str(p["start_sec"] + min(6, p["duration_sec"])),
                    ]
                    if p["synthetic_case"]
                    else []
                ),
            ),
            (
                "Video bbox/ID",
                "pipeline.cctv.tracking_overlay",
                common
                + [
                    "--tracking-dir",
                    r("tracking-clips"),
                    "--output",
                    r("tracking-overlay-v2"),
                ],
            ),
            (
                "Phân bổ mô phỏng",
                "pipeline.cctv.dispatch",
                common
                + [
                    "--resources",
                    r("resources.json"),
                    "--demands",
                    r("simulation.json"),
                    "--output",
                    r("dispatch-simulation-v1"),
                ],
            ),
        ]
        return commands

    def worker(self, dataset_id: str, job: dict) -> None:
        folder = self.folder(dataset_id)
        run = Path(job["run"])
        try:
            self.release()
            commands = (
                [
                    ("Tải CLIP", "scripts.download_cctv_clip", []),
                    ("Tải YOLO", "scripts.download_cctv_yolo", []),
                ]
                if job["download"]
                else self.stages(job["snapshot"], run)
            )
            job.update(status="running", total=len(commands))
            for index, (name, module, args) in enumerate(commands):
                if self.cancelled.is_set():
                    raise InterruptedError(
                        "Đã dừng; checkpoint đã hoàn thành được giữ lại."
                    )
                job.update(stage=name, completed=index)
                with self.lock:
                    save_json(folder / "job.json", job)
                self.command(module, args, run)
            if not job["download"]:
                if self.cancelled.is_set():
                    raise InterruptedError("Đã dừng trước khi kích hoạt kết quả.")
                self.activate(run)
                self.active = dataset_id
                data = self.get(dataset_id)
                data.update(run=str(run))
                data.pop("job", None)
                data.pop("active", None)
                with self.lock:
                    save_json(folder / "dataset.json", data)
            else:
                if not self.closing:
                    self.restore()
            job.update(status="complete", stage="Hoàn tất", completed=len(commands))
        except Exception as exc:
            job.update(
                status="cancelled" if isinstance(exc, InterruptedError) else "failed",
                error=str(exc),
            )
            try:
                if not self.closing:
                    self.restore()
            except Exception:
                pass
        finally:
            with self.lock:
                save_json(folder / "job.json", job)

    def command(self, module: str, args: list[str], output: Path) -> None:
        """Run one owned child tree; shared cancellation covers both job types."""
        if self.cancelled.is_set() or self.closing:
            raise InterruptedError("Đã dừng; bấm Thử lại để tiếp tục.")
        with (output / "console.log").open("ab") as log:
            self.process = subprocess.Popen(
                [sys.executable, "-m", module, *args], cwd=ROOT, stdout=log, stderr=log
            )
            if self.cancelled.is_set():
                self.stop()
            code = self.process.wait()
            self.process = None
        if self.cancelled.is_set() or self.closing:
            raise InterruptedError("Đã dừng; bấm Thử lại để tiếp tục.")
        if code:
            raise ValueError(
                f"{module} chưa hoàn tất (exit {code}). Xem nhật ký rồi Thử lại."
            )

    def verify_start(self, dataset_id: str, url: str, retry: bool = False) -> None:
        """Start independent verification metadata without replacing pipeline jobs."""
        from pipeline.cctv.benchmark_runtime import loopback_url

        with self.lock:
            self.idle()
            data = self.get(dataset_id)
            if not data.get("run"):
                raise ValueError("Chạy pipeline hoàn tất trước khi tự kiểm chứng T10.")
            folder = self.folder(dataset_id)
            path = folder / "verify-job.json"
            if retry:
                job = read_json(path)
                if job["status"] not in {"failed", "cancelled", "interrupted"}:
                    raise ValueError("Chỉ thử lại kiểm chứng đã lỗi hoặc dừng.")
                if job["source_run"] != data["run"] or job["url"] != loopback_url(url):
                    raise ValueError("Dataset/server đã đổi; tạo lượt kiểm chứng mới.")
            else:
                output = folder / ("t10-" + uuid.uuid4().hex)
                output.mkdir()
                job = dict(
                    id=output.name,
                    run=str(output),
                    source_run=data["run"],
                    url=loopback_url(url),
                )
            job.update(
                status="queued", stage="Chuẩn bị T10", completed=0, total=5, error=None
            )
            save_json(path, job)
            self.cancelled.clear()
            self.thread = threading.Thread(
                target=self.verify_worker, args=(dataset_id, job), daemon=True
            )
            self.thread.start()

    def verify_worker(self, dataset_id: str, job: dict) -> None:
        """Activate once if necessary; CPU checks retain the serving model."""
        path = self.folder(dataset_id) / "verify-job.json"
        output = Path(job["run"])
        switched = False
        try:
            if self.active != dataset_id:
                self.release()
                switched = True
                self.activate(Path(job["source_run"]))
                self.active = dataset_id
            job.update(status="running", stage="Kiểm chứng demo T10")
            with self.lock:
                save_json(path, job)
            self.command(
                "scripts.verify_cctv_t10",
                [
                    "--run",
                    job["source_run"],
                    "--output",
                    str(output),
                    "--url",
                    job["url"],
                ],
                output,
            )
            report = read_json(output / "report.json")
            if report["status"] != "PASSED_DEMO_CHECKS":
                raise ValueError("T10 report chưa đạt.")
            job.update(status="complete", stage="Kiểm chứng demo đạt", completed=5)
        except Exception as exc:
            job.update(
                status="cancelled" if isinstance(exc, InterruptedError) else "failed",
                error=str(exc),
            )
            if switched and not self.closing:
                self.restore()
                self.active = None
        finally:
            with self.lock:
                save_json(path, job)

    def stop(self) -> None:
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                )
            else:
                process.terminate()

    def bundle(self, dataset_id: str) -> bytes:
        data = self.get(dataset_id)
        if not data["run"]:
            raise ValueError("Chạy xử lý trước khi xuất báo cáo.")
        run = Path(data["run"])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "summary.json",
                json.dumps(
                    dict(
                        name=data["name"],
                        demo_only=True,
                        operational_ready=False,
                        m8="PENDING",
                        preparation=read_json(run / "preparation.json"),
                    ),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for path in run.rglob("*.json"):
                if path.stat().st_size <= 4 * 1024**2:
                    archive.write(path, str(path.relative_to(run)))
            verification = self.folder(dataset_id) / "verify-job.json"
            with self.lock:
                job = read_json(verification) if verification.exists() else None
            if job and job["status"] == "complete" and job["source_run"] == str(run):
                output = Path(job["run"])
                for path in output.rglob("*"):
                    if (
                        path.is_file()
                        and path.suffix in {".json", ".md", ".log"}
                        and path.stat().st_size <= 4 * 1024**2
                    ):
                        archive.write(path, "t10/" + str(path.relative_to(output)))
        return buffer.getvalue()


def install(app, workflow: Workflow):
    """Same-origin mutation API; no arbitrary filesystem paths from browsers."""

    def origin(request):
        expected = str(request.base_url).rstrip("/")
        if request.headers.get("origin") != expected:
            raise HTTPException(403, "Chỉ thao tác từ giao diện ứng dụng này.")

    def failure(exc):
        return HTTPException(400, str(exc))

    @app.get("/cctv/workflow")
    def catalog():
        return dict(
            datasets=workflow.list(),
            active=workflow.active,
            busy=bool(workflow.thread and workflow.thread.is_alive()),
            models_ready=(ROOT / "outputs/model-cache/clip.json").exists()
            and (ROOT / "outputs/model-cache/yolo/yolo.json").exists(),
        )

    @app.post("/cctv/workflow/datasets")
    async def create(request: Request):
        origin(request)
        payload = await read_payload(request)
        try:
            data = await run_in_threadpool(
                workflow.create,
                payload.get("name", "Demo CCTV"),
                payload.get("demo", False),
            )
            return workflow.public(data["id"])
        except (ValueError, OSError) as exc:
            raise failure(exc)

    @app.post("/cctv/workflow/{dataset_id}/upload")
    async def upload(dataset_id: str, request: Request, filename: str):
        origin(request)
        try:
            with workflow.lock:
                workflow.idle()
                data = workflow.get(dataset_id)
                if data["demo"] or len(data["videos"]) >= 6:
                    raise ValueError(
                        "Bộ upload tối đa sáu video; demo có sẵn không nhận upload."
                    )
            if (
                len(filename) > 200
                or Path(filename).name != filename
                or any(c in filename for c in "/\\:")
                or Path(filename).suffix.lower() not in {".mov", ".mp4", ".mkv", ".avi"}
            ):
                raise ValueError(
                    "Chọn MOV, MP4, MKV hoặc AVI; tên file không được chứa đường dẫn."
                )
            video_id = "V" + uuid.uuid4().hex[:12]
            folder = workflow.folder(dataset_id) / "uploads"
            reject_link(folder)
            folder.mkdir(exist_ok=True)
            path = folder / (video_id + Path(filename).suffix.lower())
            partial = path.with_suffix(path.suffix + ".partial")
            size = 0
            try:
                with partial.open("xb") as handle:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > LIMIT:
                            raise ValueError("Mỗi video tối đa 1 GiB.")
                        handle.write(chunk)
                partial.replace(path)
                metadata = await run_in_threadpool(probe, path)
                if metadata["codec"] != "h264":
                    raise ValueError(
                        "Demo hiện hỗ trợ video H.264; chọn bản H.264 trước khi upload."
                    )
                with workflow.lock:
                    workflow.idle()
                    data = workflow.get(dataset_id)
                    if len(data["videos"]) >= 6:
                        raise ValueError("Đã đủ sáu video.")
                    data["videos"].append(
                        dict(
                            video_id=video_id,
                            camera_id="CAM_1",
                            name=filename,
                            source=str(path),
                            **metadata,
                        )
                    )
                    data["preparation"] = None
                    data.pop("job", None)
                    data.pop("active", None)
                    save_json(workflow.folder(dataset_id) / "dataset.json", data)
                return workflow.public(dataset_id)
            except Exception:
                partial.unlink(missing_ok=True)
                # Only remove this request's owned upload, never source footage.
                path.unlink(missing_ok=True)
                raise
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise failure(exc)

    @app.get("/cctv/workflow/{dataset_id}/frame/{video_id}")
    def frame(dataset_id: str, video_id: str, second: float = 0):
        try:
            data = workflow.get(dataset_id)
            video = next(v for v in data["videos"] if v["video_id"] == video_id)
            if not 0 <= second < video["duration"] - video["first_pts"]:
                raise ValueError("Thời điểm ngoài video.")
            with av.open(video["source"]) as container:
                stream = container.streams.video[0]
                target = second + video["first_pts"]
                container.seek(int(target / stream.time_base), stream=stream)
                for decoded in container.decode(stream):
                    if decoded.time is not None and decoded.time >= target:
                        image = decoded.to_image()
                        image.thumbnail((960, 540))
                        out = io.BytesIO()
                        image.save(out, format="JPEG")
                        return Response(out.getvalue(), media_type="image/jpeg")
            raise ValueError("Không đọc được frame.")
        except (ValueError, OSError, StopIteration, av.FFmpegError) as exc:
            raise failure(exc)

    @app.post("/cctv/workflow/{dataset_id}/prepare")
    async def prepare(dataset_id: str, request: Request):
        origin(request)
        try:
            workflow.prepare(dataset_id, await read_payload(request))
            return workflow.public(dataset_id)
        except (ValueError, KeyError, TypeError) as exc:
            raise failure(exc)

    @app.post("/cctv/workflow/{dataset_id}/jobs")
    async def start(dataset_id: str, request: Request):
        origin(request)
        try:
            payload = await read_payload(request)
            if any(
                not isinstance(payload.get(flag, False), bool)
                for flag in ("retry", "download")
            ):
                raise ValueError("retry/download must be booleans")
            workflow.start(
                dataset_id, bool(payload.get("retry")), bool(payload.get("download"))
            )
            return workflow.public(dataset_id)
        except (ValueError, OSError) as exc:
            raise failure(exc)

    @app.post("/cctv/workflow/stop")
    def stop(request: Request):
        origin(request)
        workflow.stop()
        return {"stopping": True}

    @app.post("/cctv/workflow/{dataset_id}/verify")
    async def verify(dataset_id: str, request: Request):
        payload = await read_payload(request)
        try:
            retry = payload.get("retry", False)
            if not isinstance(retry, bool):
                raise ValueError("retry must be a boolean")
            workflow.verify_start(dataset_id, str(request.base_url).rstrip("/"), retry)
            return workflow.public(dataset_id)
        except (ValueError, OSError) as exc:
            raise failure(exc)

    @app.post("/cctv/workflow/{dataset_id}/activate")
    def activate(dataset_id: str, request: Request):
        origin(request)
        try:
            with workflow.lock:
                workflow.idle()
                data = workflow.get(dataset_id)
                if not data["run"]:
                    raise ValueError("Bộ dữ liệu chưa xử lý xong.")
                workflow.release()
                try:
                    workflow.activate(Path(data["run"]))
                except Exception:
                    workflow.restore()
                    raise
                workflow.active = dataset_id
                return {"active": dataset_id}
        except (ValueError, OSError) as exc:
            raise failure(exc)

    @app.get("/cctv/workflow/{dataset_id}/log")
    def log(dataset_id: str, verification: bool = False):
        data = workflow.get(dataset_id)
        if verification:
            with workflow.lock:
                path = workflow.folder(dataset_id) / "verify-job.json"
                data["job"] = read_json(path) if path.exists() else None
        if not data["job"]:
            return {"text": "Chưa có tác vụ."}
        path = Path(data["job"]["run"]) / "console.log"
        if not path.exists():
            return {"text": "Đang chuẩn bị…"}
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 12000))
            return {"text": handle.read().decode("utf-8", errors="replace")}

    @app.get("/cctv/workflow/{dataset_id}/report")
    def report(dataset_id: str):
        try:
            return Response(
                workflow.bundle(dataset_id),
                media_type="application/zip",
                headers={
                    "Content-Disposition": 'attachment; filename="cctv-demo-report.zip"'
                },
            )
        except (ValueError, OSError) as exc:
            raise failure(exc)
