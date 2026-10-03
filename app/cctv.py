"""CCTV FastAPI composition reusing the legacy-compatible retrieval pipeline."""

from __future__ import annotations

import json
import logging
import os
import platform
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager, closing
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from app.cctv_dispatch import DispatchPackage, DispatchStore
from app.cctv_events import EventStore
from app.cctv_overlay import OverlayStore
from app.cctv_traffic import TrafficStore
from app.query_hints import expand_bilingual_query
from pipeline.cctv.clip_process import ProcessClipEncoder
from pipeline.cctv.index import model_recipe
from pipeline.cctv.ingest import digest
from pipeline.cctv.manifest import identity, relative_video
from pipeline.cctv.search import CctvSearch

ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
LOG = logging.getLogger("cctv.api")


class Runtime:
    """Serialize queries; exit the serving encoder process before GPU stages."""

    def __init__(
        self,
        index: Path,
        model_config: Path,
        preview: Path,
        device: str,
        encoder_factory=ProcessClipEncoder,
    ) -> None:
        self.index_dir, self.model_config, self.preview_dir = (
            index,
            model_config,
            preview,
        )
        self.device, self.encoder_factory = device, encoder_factory
        self.search = None
        self.encoder = None
        self.videos: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.ready = False
        self.error: str | None = None
        self.benchmark_identity: dict | None = None

    def start(self) -> None:
        """Validate index/model/assets, then run a real text-to-FAISS readiness probe."""
        try:
            self.search = CctvSearch(self.index_dir)
            recipe, local = model_recipe(self.model_config, self.device)
            for field in ("clip_model", "revision", "model_sha256"):
                if recipe[field] != self.search.manifest["recipe"][field]:
                    raise ValueError("Model differs from indexed model")
            previews = json.loads(
                (self.preview_dir / "preview_state.json").read_text(encoding="utf-8")
            )
            if previews.get("pipeline") != "cctv-preview-v1":
                raise ValueError("Invalid video preview checkpoint")
            with closing(self.search.connect()) as conn:
                sources = conn.execute(
                    "SELECT DISTINCT video_id,source_video FROM cctv_frames"
                ).fetchall()
                if (
                    conn.execute("SELECT count(*) FROM cctv_frames").fetchone()[0]
                    != self.search.index.ntotal
                ):
                    raise ValueError("CCTV metadata coverage mismatch")
                for (rel,) in conn.execute("SELECT file FROM frames"):
                    self.image_path(rel)
            for vid, source in sources:
                identity(vid, "video_id")
                entry = previews["videos"][vid]
                if (
                    entry["status"] != "complete"
                    or Path(entry["signature"]["source"]).resolve()
                    != Path(source).resolve()
                ):
                    raise ValueError(f"Preview incomplete or has wrong source: {vid}")
                target = self.preview_dir / f"{vid}.mp4"
                if (
                    entry["file"] != target.name
                    or digest(target) != entry["output_sha256"]
                ):
                    raise ValueError(f"Preview checksum mismatch: {vid}")
                if (
                    not Path(source).is_file()
                    or digest(Path(source)) != entry["signature"]["sha256"]
                ):
                    raise ValueError(f"Source missing or changed: {vid}")
                self.videos[vid] = {**entry, "path": target}
            self.encoder = self.encoder_factory(local, self.device)
            if self.encoder.dim != self.search.index.d:
                raise ValueError("Model projection dimension differs from index")
            result = self.search.query(self.encoder.text("a road intersection"), 1)
            if not result or result[0]["video_id"] not in self.videos:
                raise ValueError("Readiness query returned invalid locator")
            self.benchmark_identity = {
                "session_id": str(uuid.uuid4()),
                "index_state_sha256": digest(self.index_dir / "index_state.json"),
                "model_recipe": recipe,
                "code_sha256": {
                    name: digest(ROOT / name)
                    for name in (
                        "app/cctv.py",
                        "pipeline/cctv/__init__.py",
                        "pipeline/cctv/clip.py",
                        "pipeline/cctv/clip_process.py",
                        "pipeline/cctv/search.py",
                    )
                },
                "device": self.device,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    name: version(name)
                    for name in ("torch", "transformers", "faiss-cpu", "fastapi")
                },
            }
            if self.device == "cuda":
                self.benchmark_identity["gpu"] = self.encoder.gpu
                self.benchmark_identity["cuda_build"] = self.encoder.cuda_build
            self.ready, self.error = True, None
        except Exception as exc:
            self.error = str(exc)
            LOG.exception("CCTV readiness failed")
            self.close()

    def close(self) -> None:
        """Wait for model process exit; retain readiness failure for diagnostics."""
        self.ready = False
        if self.encoder is not None:
            self.encoder.close()
            self.encoder = None

    def image_path(self, relative: str) -> Path:
        """Confine lookup to registered keyframes, including link resolution."""
        root = Path(self.search.manifest["keyframes_root"]).resolve()
        target = (root / relative_video(relative)).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Keyframe unavailable")
        return target

    def results(self, vector, top_k: int) -> dict:
        """Expose HTTP asset IDs, keeping absolute local paths out of responses."""
        results = self.search.query(vector, top_k)
        with closing(self.search.connect()) as conn:
            output = []
            for row in results:
                integer = conn.execute(
                    "SELECT faiss_id FROM frames WHERE id=?", (row["id"],)
                ).fetchone()[0]
                output.append(
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "file",
                            "camera_id",
                            "video_id",
                            "timestamp_sec",
                            "frame_idx",
                            "score",
                            "start_time",
                        )
                    }
                )
                output[-1].update(
                    image_url=f"/cctv/image/{integer}",
                    video_url=f"/cctv/video/{row['video_id']}",
                    video_seek_sec=row["timestamp_sec"]
                    + self.videos[row["video_id"]]["seek_offset_sec"],
                )
        return {"results": output, "total": len(output)}

    def text(self, query: str, top_k: int) -> dict:
        """Serialize text/image inference through a single model."""
        with self.lock:
            if not self.ready:
                raise HTTPException(
                    503, "Đang xử lý dữ liệu; tìm kiếm sẽ sẵn sàng sau khi hoàn tất."
                )
            prompt, terms = expand_bilingual_query(query)
            response = self.results(self.encoder.text(prompt), top_k)
            response.update(
                translated_prompt=prompt if terms else None,
                translated_terms=terms,
            )
            return response

    def image(self, payload: bytes, top_k: int) -> dict:
        """Validate/decode in memory only; do not persist query images."""
        try:
            with self.lock:
                if not self.ready:
                    raise HTTPException(
                        503, "Đang xử lý dữ liệu; chờ hoàn tất rồi tìm kiếm."
                    )
                with Image.open(BytesIO(payload)) as image:
                    if (
                        image.format not in {"JPEG", "PNG", "WEBP"}
                        or image.width * image.height > MAX_IMAGE_PIXELS
                    ):
                        raise ValueError("Use JPEG/PNG/WebP with at most 16 megapixels")
                    rgb = image.convert("RGB")
                try:
                    return self.results(self.encoder.images([rgb]), top_k)
                finally:
                    rgb.close()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ) as exc:
            raise HTTPException(
                422, "Ảnh không hợp lệ; dùng JPEG/PNG/WebP tối đa 16 megapixels."
            ) from exc


def create_app(
    index_dir: Path | None = None,
    model_config: Path | None = None,
    preview_dir: Path | None = None,
    device: str | None = None,
    encoder_factory=ProcessClipEncoder,
    traffic_dir: Path | None = None,
    tracking_dir: Path | None = None,
    roi_preview_dir: Path | None = None,
    event_packages: dict[str, tuple[Path, Path]] | None = None,
    dispatch_store: DispatchStore | None = None,
    overlay_dir: Path | None = None,
    overlay_manifest: Path | None = None,
    workflow_dir: Path | None = None,
    stitched_dir: Path | None = None,
) -> FastAPI:
    """Build the CCTV demo using independent config; legacy app remains available."""
    runtime = Runtime(
        (
            index_dir
            or Path(
                os.getenv("CCTV_INDEX_DIR", ROOT / "outputs/demo-data/search-index")
            )
        ).resolve(),
        (
            model_config
            or Path(
                os.getenv("CCTV_MODEL_CONFIG", ROOT / "outputs/model-cache/clip.json")
            )
        ).resolve(),
        (
            preview_dir
            or Path(
                os.getenv("CCTV_PREVIEW_DIR", ROOT / "outputs/demo-data/video-preview")
            )
        ).resolve(),
        device or os.getenv("CCTV_DEVICE", "cuda"),
        encoder_factory,
    )
    traffic = TrafficStore(
        (
            traffic_dir
            or Path(
                os.getenv(
                    "CCTV_TRAFFIC_DIR", ROOT / "outputs/demo-data/traffic-draft-v1"
                )
            )
        ).absolute(),
        (
            tracking_dir
            or Path(
                os.getenv(
                    "CCTV_TRACKING_DIR", ROOT / "outputs/demo-data/tracking-clips"
                )
            )
        ).absolute(),
        (
            roi_preview_dir
            or Path(
                os.getenv(
                    "CCTV_ROI_PREVIEW_DIR",
                    ROOT / "outputs/demo-data/roi-draft-previews",
                )
            )
        ).absolute(),
    )
    events = EventStore(
        event_packages
        if event_packages is not None
        else {
            name: (
                Path(
                    os.getenv(event_env, ROOT / "outputs/demo-data" / event_default)
                ).absolute(),
                Path(
                    os.getenv(review_env, ROOT / "outputs/demo-data" / review_default)
                ).absolute(),
            )
            for name, event_env, event_default, review_env, review_default in (
                (
                    "incidents",
                    "CCTV_INCIDENTS_DIR",
                    "incidents-draft-v1",
                    "CCTV_INCIDENT_REVIEWS_DIR",
                    "incident-reviews-v1",
                ),
                (
                    "congestion",
                    "CCTV_EVENTS_DIR",
                    "events-v1",
                    "CCTV_EVENT_REVIEWS_DIR",
                    "event-reviews-v1",
                ),
                (
                    "demo",
                    "CCTV_DEMO_EVENTS_DIR",
                    "events-demo-v1",
                    "CCTV_DEMO_REVIEWS_DIR",
                    "events-demo-reviews-v1",
                ),
            )
        }
    )

    dispatch_packages = {
        "simulation": DispatchPackage(
            ROOT / "outputs/demo-data/dispatch-simulation-v2",
            ROOT / "configs/cctv.demands.simulation.json",
        )
    }
    for name, event_name, output, demands in (
        (
            "events_empty",
            "incidents",
            "dispatch-events-empty-v2",
            "configs/cctv.demands.events.json",
        ),
        (
            "review_gate",
            "demo",
            "dispatch-review-gate-v2",
            "outputs/demo-data-review/dispatch-event-request.json",
        ),
    ):
        if event_name in events.packages:
            dispatch_packages[name] = DispatchPackage(
                ROOT / "outputs/demo-data" / output,
                ROOT / demands,
                *events.packages[event_name],
            )
    proposals = dispatch_store or DispatchStore(
        Path(os.getenv("CCTV_MANIFEST", ROOT / "configs/cctv.demo.json")).absolute(),
        Path(
            os.getenv(
                "CCTV_RESOURCE_CONFIG", ROOT / "configs/cctv.resources.simulation.json"
            )
        ).absolute(),
        dispatch_packages,
    )

    def event_sources() -> dict[str, dict]:
        if not runtime.ready:
            return {}
        with closing(runtime.search.connect()) as conn:
            return {
                vid: dict(
                    camera_id=camera,
                    sha256=runtime.videos[vid]["signature"]["sha256"],
                    seek_offset_sec=runtime.videos[vid]["seek_offset_sec"],
                )
                for vid, camera in conn.execute(
                    "SELECT DISTINCT video_id,camera_id FROM cctv_frames"
                )
            }

    def start_traffic() -> None:
        if not runtime.ready:
            traffic.error = "Retrieval assets unavailable"
            return
        with closing(runtime.search.connect()) as conn:
            sources = {
                vid: {
                    "camera_id": camera,
                    "sha256": runtime.videos[vid]["signature"]["sha256"],
                    "seek_offset_sec": runtime.videos[vid]["seek_offset_sec"],
                }
                for vid, camera in conn.execute(
                    "SELECT DISTINCT video_id,camera_id FROM cctv_frames"
                )
            }
        traffic.start(sources)
        overlays.start(sources)

    overlays = OverlayStore(
        overlay_dir
        or Path(
            os.getenv(
                "CCTV_OVERLAY_DIR", ROOT / "outputs/demo-data/tracking-overlay-v2"
            )
        ),
        traffic.tracking,
        overlay_manifest or ROOT / "configs/cctv.demo.json",
        stitched_dir or (
            Path(os.environ["CCTV_STITCHED_DIR"])
            if os.environ.get("CCTV_STITCHED_DIR") else None
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(runtime.start)
        await run_in_threadpool(start_traffic)
        try:
            yield
        finally:
            workflow.closing = True
            workflow.stop()
            if workflow.thread:
                await run_in_threadpool(workflow.thread.join, 15)
            runtime.close()

    app = FastAPI(title="CCTV Keyframe Retrieval", lifespan=lifespan)
    app.state.cctv = runtime
    app.state.traffic = traffic
    app.state.overlays = overlays
    app.state.events = events
    app.state.dispatch = proposals
    from app.cctv_workflow import Workflow, install, read_payload

    previous = None

    def release_workflow() -> None:
        nonlocal previous
        with runtime.lock:
            previous = (
                runtime.index_dir,
                runtime.preview_dir,
                traffic,
                overlays,
                events,
                proposals,
            )
            runtime.close()

    def activate_workflow(run: Path) -> None:
        nonlocal traffic, overlays, events, proposals
        with runtime.lock:
            runtime.close()
            runtime.videos = {}
            runtime.index_dir, runtime.preview_dir = (
                run / "search-index",
                run / "video-preview",
            )
            runtime.start()
            if not runtime.ready:
                raise ValueError("Index chưa sẵn sàng: " + str(runtime.error))
            traffic = TrafficStore(
                run / "traffic-draft-v1",
                run / "tracking-clips",
                run / "roi-draft-previews",
            )
            overlays = OverlayStore(
                run / "tracking-overlay-v2",
                run / "tracking-clips",
                run / "configs/manifest.json",
            )
            events = EventStore(
                {
                    name: (run / folder, run / (name + "-reviews"))
                    for name, folder in (
                        ("congestion", "events-v1"),
                        ("incidents", "incidents-draft-v1"),
                        ("demo", "events-demo-v1"),
                    )
                }
            )
            packages = {
                "simulation": DispatchPackage(
                    run / "dispatch-simulation-v1", run / "configs/simulation.json"
                )
            }
            if (run / "proposal-current.json").exists():
                pointer = json.loads(
                    (run / "proposal-current.json").read_text(encoding="utf-8")
                )
                package = pointer["package_id"]
                packages["review_gate"] = DispatchPackage(
                    run / pointer["output"],
                    run / pointer["demands"],
                    *events.packages[package],
                )
            proposals = DispatchStore(
                run / "configs/manifest.json", run / "configs/resources.json", packages
            )
            start_traffic()
            if not traffic.available() or not overlays.available():
                raise ValueError("Traffic/overlay chưa sẵn sàng; xem nhật ký pipeline.")
            (
                app.state.traffic,
                app.state.overlays,
                app.state.events,
                app.state.dispatch,
            ) = traffic, overlays, events, proposals

    def restore_workflow() -> None:
        nonlocal traffic, overlays, events, proposals
        if previous is not None:
            with runtime.lock:
                runtime.close()
                (
                    runtime.index_dir,
                    runtime.preview_dir,
                    traffic,
                    overlays,
                    events,
                    proposals,
                ) = previous
                runtime.videos = {}
                runtime.start()
                start_traffic()
                (
                    app.state.traffic,
                    app.state.overlays,
                    app.state.events,
                    app.state.dispatch,
                ) = traffic, overlays, events, proposals

    workflow = Workflow(
        workflow_dir
        or (
            (index_dir.parent / "ui-workflow")
            if index_dir
            else ROOT / "outputs/ui-workflow"
        ),
        release_workflow,
        activate_workflow,
        restore_workflow,
        runtime.device,
    )
    app.state.workflow = workflow
    install(app, workflow)

    @app.post("/cctv/workflow/review/{package_id}")
    async def workflow_review(package_id: str, request: Request):
        if request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Chỉ review từ UI ứng dụng.")
        payload = await read_payload(request)
        from pipeline.cctv.event_review import submit_review

        def submit():
            with workflow.lock:
                workflow.idle()
                if workflow.active is None or package_id not in events.packages:
                    raise ValueError(
                        "Chọn bộ dữ liệu đã xử lý qua UI trước khi review."
                    )
                return submit_review(
                    *events.packages[package_id],
                    payload["event_id"],
                    payload["decision"],
                    payload["reviewer"],
                    payload["reason"],
                    payload["expected_revision"],
                ).model_dump(mode="json")

        try:
            return await run_in_threadpool(submit)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/cctv/workflow/proposal/{package_id}")
    async def workflow_proposal(package_id: str, request: Request):
        if request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Chỉ phân bổ từ UI ứng dụng.")
        payload = await read_payload(request)
        from pipeline.cctv.dispatch import EventScenario, propose_dispatch
        from pipeline.cctv.ingest import save_json

        def propose():
            with workflow.lock:
                workflow.idle()
                if workflow.active is None or package_id not in events.packages:
                    raise ValueError("Chọn bộ dữ liệu đã xử lý qua UI trước.")
                run = Path(workflow.get(workflow.active)["run"])
                scenario = EventScenario.model_validate_json(
                    json.dumps(
                        dict(
                            schema_version=1,
                            mode="simulation",
                            kind="event_package",
                            demands=[
                                dict(
                                    demand_id="UI-DEMAND",
                                    event_id=payload["event_id"],
                                    min_officers=payload["min_officers"],
                                    priority=payload["priority"],
                                    required_skills=payload["required_skills"],
                                    max_travel_sec=180.0,
                                )
                            ],
                        )
                    )
                )
                token = uuid.uuid4().hex
                demands, output = "demands-" + token + ".json", "proposal-" + token
                save_json(run / demands, scenario.model_dump(mode="json"))
                result = propose_dispatch(
                    run / "configs/manifest.json",
                    run / "configs/resources.json",
                    run / demands,
                    run / output,
                    *events.packages[package_id],
                )
                proposals.packages["review_gate"] = DispatchPackage(
                    run / output, run / demands, *events.packages[package_id]
                )
                save_json(
                    run / "proposal-current.json",
                    dict(package_id=package_id, demands=demands, output=output),
                )
                return result

        try:
            return await run_in_threadpool(propose)
        except (ValueError, KeyError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc

    app.mount(
        "/static", StaticFiles(directory=ROOT / "static/cctv"), name="cctv-static"
    )

    def require_ready() -> None:
        if not runtime.ready:
            raise HTTPException(
                503, "CCTV chưa sẵn sàng. Kiểm tra /ready và log server."
            )

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static/cctv/index.html")

    @app.get("/health")
    def health():
        return {"status": "running"}

    @app.get("/ready")
    def ready():
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {
                "ready": runtime.ready,
                "frames": runtime.search.index.ntotal if runtime.search else 0,
                "cameras": sorted({row["camera_id"] for row in camera_rows()})
                if runtime.ready
                else [],
                "capabilities": {
                    "text": runtime.ready,
                    "image": runtime.ready,
                    "video": runtime.ready,
                    "traffic": traffic.available(),
                },
                "traffic": traffic.status(),
                "error": runtime.error,
                "benchmark_identity": runtime.benchmark_identity,
                "model_memory": runtime.encoder.memory() if runtime.encoder else {},
            },
            status_code=200 if runtime.ready else 503,
        )

    def camera_rows() -> list[dict]:
        with closing(runtime.search.connect()) as conn:
            return [
                {"camera_id": row[0], "video_count": row[1]}
                for row in conn.execute(
                    "SELECT camera_id,count(DISTINCT video_id) FROM cctv_frames GROUP BY camera_id ORDER BY camera_id"
                )
            ]

    @app.get("/cctv/search")
    def search_text(
        query: str = Query(min_length=1, max_length=500),
        top_k: int = Query(12, ge=1, le=50),
    ):
        require_ready()
        if not query.strip():
            raise HTTPException(422, "Nhập mô tả tìm kiếm.")
        return runtime.text(query.strip(), top_k)

    @app.post("/cctv/search/image")
    async def search_image(request: Request, top_k: int = Query(12, ge=1, le=50)):
        require_ready()
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Ảnh tối đa 8 MiB.")
            payload.extend(chunk)
        return await run_in_threadpool(runtime.image, bytes(payload), top_k)

    @app.get("/cctv/image/{frame_id}")
    def image_asset(frame_id: int):
        require_ready()
        with closing(runtime.search.connect()) as conn:
            row = conn.execute(
                "SELECT file FROM frames WHERE faiss_id=?", (frame_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Không có keyframe này.")
        try:
            return FileResponse(runtime.image_path(row[0]), media_type="image/jpeg")
        except ValueError as exc:
            raise HTTPException(404, "Keyframe không còn khả dụng.") from exc

    @app.get("/cctv/video/{video_id}")
    def video_asset(video_id: str):
        require_ready()
        if video_id not in runtime.videos:
            raise HTTPException(404, "Không có video này.")
        return FileResponse(runtime.videos[video_id]["path"], media_type="video/mp4")

    @app.get("/cctv/traffic")
    def traffic_status():
        return traffic.status()

    @app.get("/cctv/events")
    def event_catalog():
        require_ready()
        return events.catalog(event_sources())

    @app.get("/cctv/events/{package_id}")
    def event_rows(
        package_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        require_ready()
        if package_id not in events.packages:
            raise HTTPException(404, "Không có gói sự kiện này.")
        try:
            rows = events.rows(package_id, event_sources())
        except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
            LOG.warning("Event package unavailable: %s", exc)
            raise HTTPException(
                503, "Gói sự kiện hoặc journal chưa khả dụng; kiểm tra log server."
            ) from exc
        return dict(
            package_id=package_id,
            total=len(rows),
            offset=offset,
            limit=limit,
            rows=rows[offset : offset + limit],
            read_only=True,
            operational_ready=False,
        )

    @app.get("/cctv/dispatch")
    def dispatch_catalog():
        require_ready()
        return proposals.catalog(event_sources())

    @app.get("/cctv/dispatch/{package_id}")
    def dispatch_report(package_id: str):
        require_ready()
        if package_id not in proposals.packages:
            raise HTTPException(404, "Không có gói phân bổ này.")
        try:
            return proposals.report(package_id, event_sources())
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            LOG.warning("Dispatch package unavailable: %s", exc)
            raise HTTPException(
                503,
                "Báo cáo phân bổ thiếu, lỗi hoặc đầu vào đã đổi; kiểm tra log và tạo báo cáo mới.",
            ) from exc

    def require_traffic() -> None:
        require_ready()
        if not traffic.available():
            raise HTTPException(
                503,
                "Thống kê giao thông chưa khả dụng; kiểm tra output và khởi động lại demo.",
            )

    @app.get("/cctv/traffic/windows/{video_id}")
    def traffic_windows(
        video_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=500),
    ):
        require_traffic()
        if video_id not in traffic.rows:
            raise HTTPException(404, "Không có thống kê cho video này.")
        rows = traffic.rows[video_id]
        return {
            "video": traffic.video_rows[video_id],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "rows": [
                dict(row, tracking_overlay=overlays.evidence(row["evidence"]))
                for row in rows[offset : offset + limit]
            ],
            "demo_only": traffic.manifest["demo_only"],
            "thresholds_calibrated": False,
            "operational_ready": False,
        }

    @app.get("/cctv/traffic/overlays")
    def overlay_catalog():
        require_ready()
        return overlays.status()

    @app.get("/cctv/traffic/overlay/{video_id}")
    def overlay_video(video_id: str):
        require_traffic()
        if not overlays.available():
            raise HTTPException(
                503,
                "Video tracking overlay chưa khả dụng; kiểm tra output và khởi động lại demo.",
            )
        if video_id not in overlays.paths:
            raise HTTPException(404, "Không có tracking overlay cho video này.")
        path, checksum = overlays.paths[video_id]
        try:
            if digest(path) != checksum:
                raise ValueError("Overlay checksum changed")
        except (OSError, ValueError) as exc:
            overlays.ready = False
            raise HTTPException(
                503, "Tracking overlay đã thay đổi; kiểm tra pipeline."
            ) from exc
        return FileResponse(path, media_type="video/mp4")

    @app.get("/cctv/traffic/roi/{video_id}")
    def traffic_roi(video_id: str):
        require_traffic()
        if video_id not in traffic.images:
            raise HTTPException(404, "Không có ảnh ROI này.")
        path, checksum = traffic.images[video_id]
        # Small immutable assets are rehashed before serving.
        try:
            if digest(path) != checksum:
                raise ValueError("ROI checksum changed")
        except (OSError, ValueError) as exc:
            traffic.ready, traffic.error = False, str(exc)
            raise HTTPException(
                503, "Ảnh ROI đã thay đổi; kiểm tra pipeline và khởi động lại demo."
            ) from exc
        return FileResponse(path, media_type="image/png")

    return app
