"""Inspect the Windows runtime without importing the legacy app or changing data."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PACKAGES = (
    "torch", "torchvision", "numpy", "pillow", "opencv-python-headless",
    "opencv-python", "faiss-cpu", "transformers", "fastapi", "uvicorn",
    "tqdm", "ultralytics", "easyocr", "openai-whisper", "redis",
    "sentence-transformers",
)


def command_result(command: list[str]) -> dict[str, object]:
    """Run a bounded, read-only diagnostic and preserve failures in the report."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=30,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def inspect_environment(data_root: Path | None) -> dict[str, object]:
    """Collect metadata only; do not load models or write to a supplied bundle."""
    packages: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    tools = {name: shutil.which(name) for name in ("git", "ffmpeg", "ffprobe", "nvidia-smi")}
    report: dict[str, object] = {
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "packages": packages,
        "tools": tools,
        "serving_ready": "NOT_TESTED: requires dependencies and a valid serving bundle",
    }
    if tools["nvidia-smi"]:
        report["gpu"] = command_result([
            str(tools["nvidia-smi"]),
            "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader",
        ])
    if packages["torch"]:
        report["torch_cuda"] = command_result([
            sys.executable, "-c",
            "import json, torch; print(json.dumps({'version': torch.__version__, "
            "'cuda_build': torch.version.cuda, 'cuda_available': torch.cuda.is_available()}))",
        ])
    if data_root is not None:
        report["bundle_presence_only"] = {
            "root": str(data_root.resolve()),
            "files": {
                name: (data_root / name).is_file()
                for name in ("serve_manifest.json", "meta.db", "asr_cache.json")
            },
            "faiss_directory": (data_root / "faiss_index").is_dir(),
            "note": "Presence does not validate vectors, mappings, images or model compatibility.",
        }
    return report


def main() -> int:
    """Print diagnostics and optionally save them in the project's output folder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, help="Optional existing bundle; inspected read-only")
    parser.add_argument("--save", action="store_true", help="Save outputs/preflight/environment.json")
    args = parser.parse_args()
    if args.data_root is not None and not args.data_root.is_dir():
        parser.error("--data-root must be an existing directory")
    try:
        report = inspect_environment(args.data_root)
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if args.save:
            output = Path(__file__).resolve().parents[1] / "outputs" / "preflight"
            if args.data_root is not None and output.resolve().is_relative_to(args.data_root.resolve()):
                parser.error("Refusing to write diagnostics inside the supplied data root")
            output.mkdir(parents=True, exist_ok=True)
            temporary = output / "environment.json.tmp"
            temporary.write_text(serialized + "\n", encoding="utf-8")
            temporary.replace(output / "environment.json")
        print(serialized)
        # Diagnostic completion is not a claim that the legacy service is ready.
        return 0
    except (OSError, ValueError) as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
