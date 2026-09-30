"""Check launch scripts using Windows PowerShell's actual on-disk decoder."""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path


class PowerShellScriptTests(unittest.TestCase):
    """Catch UTF-8 without BOM punctuation causing PowerShell 5.1 parse errors."""

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


if __name__ == "__main__":
    unittest.main()
