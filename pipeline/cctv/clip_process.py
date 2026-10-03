"""Serve local CLIP in an owned spawned process, never in the coordinator."""

from __future__ import annotations

import multiprocessing as mp
import threading
import traceback
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


_SHUTDOWN_WAIT_SECONDS = 5.0


def _serve(connection: Connection, path: Path, device: str) -> None:
    """Load and use Torch only inside the serving process."""
    encoder = None
    try:
        try:
            import torch

            from .clip import ClipEncoder

            encoder = ClipEncoder(path, device=device)
            connection.send(
                (
                    "ready",
                    {
                        "dim": encoder.dim,
                        "gpu": torch.cuda.get_device_name() if device == "cuda" else None,
                        "cuda_build": torch.version.cuda,
                    },
                )
            )
        except Exception:
            connection.send(("error", traceback.format_exc()))
            return

        while True:
            try:
                command, payload = connection.recv()
            except (EOFError, OSError):
                return
            try:
                if command == "text":
                    result = encoder.text(payload)
                elif command == "images":
                    result = encoder.images(payload)
                elif command == "memory":
                    result = encoder.memory()
                elif command == "close":
                    closing_encoder = encoder
                    encoder = None
                    closing_encoder.close()
                    result = None
                else:
                    raise ValueError(f"Unknown CLIP worker command: {command!r}")
            except Exception:
                connection.send(("error", traceback.format_exc()))
            else:
                connection.send(("ok", result))
            if command == "close":
                return
    finally:
        try:
            if encoder is not None:
                encoder.close()
        finally:
            connection.close()


class ProcessClipEncoder:
    """Synchronous CPU IPC around the unchanged local CLIP inference engine."""

    def __init__(self, path: Path, device: str = "cuda") -> None:
        self.device = device
        self._lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._closed = False
        self._shutdown_complete = False
        self._process = None
        context = mp.get_context("spawn")
        self._connection, child_connection = context.Pipe(duplex=True)
        try:
            self._process = context.Process(
                target=_serve,
                args=(child_connection, Path(path), device),
                name="cctv-clip",
                daemon=True,
            )
            self._process.start()
            child_connection.close()
            response = self._connection.recv()
            if not isinstance(response, tuple) or len(response) != 2:
                raise RuntimeError(f"Invalid CLIP startup response: {response!r}")
            status, metadata = response
            if status == "error":
                raise RuntimeError(f"CLIP worker startup failed:\n{metadata}")
            if status != "ready":
                raise RuntimeError(f"Unexpected CLIP startup response: {status!r}")
            self.dim = int(metadata["dim"])
            self.gpu: str | None = metadata["gpu"]
            self.cuda_build: str | None = metadata["cuda_build"]
        except BaseException as exc:
            child_connection.close()
            try:
                self._shutdown(graceful=False)
            except Exception as cleanup_error:
                raise RuntimeError(
                    f"CLIP worker startup failed: {type(exc).__name__}: {exc}; "
                    f"cleanup failed: {cleanup_error}"
                ) from exc
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError(
                f"CLIP worker startup failed ({self._worker_status()}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def _worker_status(self) -> str:
        process = self._process
        if process is None:
            return "not started"
        return f"pid={process.pid}, exitcode={process.exitcode}"

    def _request(self, command: str, payload: Any = None) -> Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("CLIP worker is closed")
            try:
                self._connection.send((command, payload))
                response = self._connection.recv()
                if not isinstance(response, tuple) or len(response) != 2:
                    raise ValueError(f"Invalid CLIP worker response: {response!r}")
                status, result = response
                if status not in {"ok", "error"}:
                    raise ValueError(f"Unexpected CLIP worker response: {status!r}")
            except Exception as exc:
                try:
                    self._shutdown(graceful=False)
                except Exception as cleanup_error:
                    raise RuntimeError(
                        f"CLIP worker {command} IPC failed: {type(exc).__name__}: {exc}; "
                        f"cleanup failed: {cleanup_error}"
                    ) from exc
                raise RuntimeError(
                    f"CLIP worker {command} IPC failed ({self._worker_status()}): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if status == "error":
                raise RuntimeError(f"CLIP worker {command} failed:\n{result}")
            return result

    def text(self, prompt: str) -> np.ndarray:
        """Return the worker's normalized float32 text vector on the CPU."""
        return self._request("text", prompt)

    def images(self, images: list[Image.Image]) -> np.ndarray:
        """Encode PIL images without exposing Torch tensors over IPC."""
        return self._request("images", images)

    def memory(self) -> dict[str, float]:
        """Return the encoder's observed CUDA allocation peaks, or {} on CPU."""
        return self._request("memory")

    def _shutdown(self, *, graceful: bool) -> None:
        # The second lock lets close stop a worker even when an inference holds
        # the request lock indefinitely. No new request starts once closed.
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._closed = True
            locked = self._lock.acquire(timeout=_SHUTDOWN_WAIT_SECONDS)
            process = self._process
            close_requested = False
            forced = False
            close_error = None
            try:
                if process is not None and process.pid is not None:
                    if graceful and locked and process.is_alive():
                        try:
                            self._connection.send(("close", None))
                            close_requested = True
                        except Exception as exc:
                            close_error = (
                                f"CLIP worker close IPC failed: {type(exc).__name__}: {exc}"
                            )
                    elif graceful and locked:
                        close_error = "CLIP worker exited before close"
                    process.join(
                        timeout=_SHUTDOWN_WAIT_SECONDS if close_requested else 0
                    )
                    if process.is_alive():
                        forced = True
                        process.terminate()
                        process.join(timeout=_SHUTDOWN_WAIT_SECONDS)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=_SHUTDOWN_WAIT_SECONDS)
                    if process.is_alive():
                        raise RuntimeError(
                            f"CLIP worker did not exit after termination "
                            f"({self._worker_status()})"
                        )
                    if close_requested and not forced:
                        try:
                            response = self._connection.recv()
                            if not isinstance(response, tuple) or len(response) != 2:
                                raise ValueError(f"Invalid close response: {response!r}")
                            status, result = response
                            if status == "error":
                                close_error = f"CLIP worker close failed:\n{result}"
                            elif status != "ok":
                                close_error = f"Unexpected CLIP close response: {status!r}"
                        except Exception as exc:
                            close_error = (
                                f"CLIP worker close IPC failed: {type(exc).__name__}: {exc}"
                            )
                        if process.exitcode != 0:
                            close_error = close_error or "CLIP worker failed during close"
                    if close_error is not None:
                        raise RuntimeError(
                            f"{close_error} ({self._worker_status()})"
                        )
            finally:
                self._connection.close()
                self._shutdown_complete = (
                    process is None or process.pid is None or not process.is_alive()
                )
                if locked:
                    self._lock.release()

    def close(self) -> None:
        """Wait for worker exit; terminate an owned hung worker before returning."""
        self._shutdown(graceful=True)
