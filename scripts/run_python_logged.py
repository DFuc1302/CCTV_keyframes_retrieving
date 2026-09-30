"""Capture Python stdout/stderr without PowerShell 5.1 treating warnings as errors."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def run_logged(arguments: list[str], log_path: Path) -> int:
    """Stream both child output channels into a UTF-8 log and keep its exit code."""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log_handle:
        result = subprocess.run(
            [sys.executable, *arguments], stdout=log_handle,
            stderr=subprocess.STDOUT, env=environment, check=False,
        )
    return result.returncode


def main() -> int:
    """Run arguments after '--' with the current virtual environment interpreter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command_arguments = args.arguments
    if command_arguments and command_arguments[0] == "--":
        command_arguments = command_arguments[1:]
    if not command_arguments:
        parser.error("Provide Python arguments after '--'")
    try:
        return run_logged(command_arguments, args.log)
    except OSError as exc:
        # A wrapper failure is reported on stdout; PS checks the nonzero exit code.
        print(f"Cannot run logged Python command: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
