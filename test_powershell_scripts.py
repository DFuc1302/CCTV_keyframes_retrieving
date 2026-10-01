"""Check launch scripts using Windows PowerShell's actual on-disk decoder."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import unittest
from pathlib import Path


class PowerShellScriptTests(unittest.TestCase):
    """Catch UTF-8 without BOM punctuation causing PowerShell 5.1 parse errors."""

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell.exe"),
        "Requires Windows PowerShell",
    )
    def test_demo_smoke_script_parses_from_disk(self) -> None:
        root = Path(__file__).resolve().parent
        command = (
            "$tokens = $null; $errors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "(Join-Path (Get-Location) 'scripts/Test-CctvDemo.ps1'), "
            "[ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell.exe"),
        "Requires Windows PowerShell",
    )
    def test_demo_smoke_busy_port_is_preserved(self) -> None:
        root = Path(__file__).resolve().parent
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(root / "scripts/Test-CctvDemo.ps1"),
                    "-Port",
                    str(port),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertGreaterEqual(listener.fileno(), 0)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Cannot bind 127.0.0.1:{port}", output)
        self.assertIn("CLIP was not loaded", output)
        self.assertNotIn("Loading local CLIP", output)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell.exe"),
        "Requires Windows PowerShell 5.1",
    )
    def test_cctv_launcher_parses_from_disk(self) -> None:
        root = Path(__file__).resolve().parent
        command = (
            "$tokens = $null; $parseErrors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            r"(Join-Path (Get-Location) 'scripts\Start-CctvDemo.ps1'), "
            "[ref]$tokens, [ref]$parseErrors) | Out-Null; "
            "if ($parseErrors.Count) { "
            "$parseErrors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell.exe"),
        "Requires Windows PowerShell 5.1",
    )
    def test_busy_port_fails_before_loading_clip(self) -> None:
        """Occupy a real local port and verify launcher exits before inference."""
        root = Path(__file__).resolve().parent
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(root / "scripts" / "Start-CctvDemo.ps1"),
                    "-Port",
                    str(port),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"Cannot bind 127.0.0.1:{port}", output)
        self.assertIn("CLIP was not loaded", output)
        self.assertNotIn("Loading local CLIP", output)
        self.assertNotIn("Started server process", output)


if __name__ == "__main__":
    unittest.main()
