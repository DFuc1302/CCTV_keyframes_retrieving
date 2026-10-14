"""One-process Windows demo smoke: start one server, verify HTTP, stop owned server."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from pipeline.cctv.ingest import reject_link
from scripts.verify_cctv_demo import ROOT
from scripts.verify_cctv_demo import run as verify_demo


def reserve_port(port: int) -> socket.socket:
    """Reserve the loopback socket before model loading and pass it to Uvicorn."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        listener.setblocking(False)
        return listener
    except OSError as exc:
        listener.close()
        raise RuntimeError(
            f"Cannot bind 127.0.0.1:{port}. CLIP was not loaded. "
            "Choose a free port or use --existing-server explicitly."
        ) from exc


def wait_ready(
    url: str,
    timeout: float,
    worker: threading.Thread | None,
    errors: list[BaseException],
) -> None:
    """Poll local readiness with bounded requests; fail promptly on startup exit."""
    deadline = time.monotonic() + timeout
    with httpx.Client(
        base_url=url, timeout=1.0, trust_env=False, follow_redirects=False
    ) as client:
        while time.monotonic() < deadline:
            if errors:
                raise RuntimeError(f"Demo startup failed: {errors[0]}")
            if worker is not None and not worker.is_alive():
                raise RuntimeError("Demo server exited before readiness")
            try:
                response = client.get("/ready")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError(f"Demo readiness timed out after {timeout:g}s")


def run(
    output: Path,
    port: int = 8080,
    device: str = "cuda",
    startup_timeout: float = 120.0,
    existing_server: bool = False,
    require_overlays: bool = False,
) -> dict:
    """Verify current demo packages; manage only a server created by this invocation."""
    if not 1024 <= port <= 65535 or device not in ("cuda", "cpu"):
        raise ValueError("Invalid demo port/device")
    if not 1 <= startup_timeout <= 600:
        raise ValueError("Startup timeout must be between 1 and 600 seconds")
    output = output.absolute()
    reject_link(output)
    if not output.is_relative_to(ROOT / "outputs"):
        raise ValueError("Smoke output must be below repository outputs")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a NEW empty output for each live smoke")
    url = f"http://127.0.0.1:{port}"
    listener = None
    server = None
    worker = None
    errors: list[BaseException] = []
    previous_device = os.environ.get("CCTV_DEVICE")
    previous_port = os.environ.get("CCTV_PORT")
    try:
        if not existing_server:
            listener = reserve_port(port)
            os.environ["CCTV_DEVICE"] = device
            os.environ["CCTV_PORT"] = str(port)
            server = uvicorn.Server(
                uvicorn.Config(
                    "app.cctv:create_app",
                    factory=True,
                    host="127.0.0.1",
                    port=port,
                    workers=1,
                    reload=False,
                    access_log=False,
                    timeout_graceful_shutdown=10,
                )
            )

            def serve() -> None:
                try:
                    server.run(sockets=[listener])
                except BaseException as exc:
                    errors.append(exc)

            worker = threading.Thread(
                target=serve, name="cctv-smoke-server", daemon=True
            )
            worker.start()
        print(f"Waiting for CCTV demo readiness: {url}", flush=True)
        wait_ready(url, startup_timeout, worker, errors)
        report = verify_demo(
            ROOT / "configs/cctv.demo.json",
            ROOT / "outputs/demo-data/search-index",
            output,
            url,
            require_overlays,
        )
    finally:
        try:
            if server is not None:
                server.should_exit = True
            if worker is not None:
                worker.join(timeout=15)
                if worker.is_alive():
                    raise RuntimeError(
                        "Owned server did not stop within 15s; CLI process will exit"
                    )
        finally:
            if listener is not None:
                listener.close()
            if not existing_server:
                if previous_device is None:
                    os.environ.pop("CCTV_DEVICE", None)
                else:
                    os.environ["CCTV_DEVICE"] = previous_device
                if previous_port is None:
                    os.environ.pop("CCTV_PORT", None)
                else:
                    os.environ["CCTV_PORT"] = previous_port
    if errors:
        raise RuntimeError(f"Demo server failed: {errors[0]}")
    return dict(
        passed=report["passed"],
        url=url,
        managed_server=not existing_server,
        owned_server_stopped=not existing_server,
        report=str(output / "acceptance_report.json"),
        protected_files=report["protected_files"],
        operational_ready=False,
        limitation="HTTP integration only; no browser playback or real event accuracy validation",
    )


def main() -> int:
    """Run with pinned venv through Test-CctvDemo.ps1; log warnings safely on PS5.1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--existing-server", action="store_true")
    parser.add_argument("--require-overlays", action="store_true")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                run(
                    args.output,
                    args.port,
                    args.device,
                    args.startup_timeout,
                    args.existing_server,
                    args.require_overlays,
                ),
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f"Demo smoke failed: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Demo smoke interrupted; owned server shutdown requested.\n")


if __name__ == "__main__":
    raise SystemExit(main())
