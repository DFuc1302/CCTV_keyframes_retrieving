"""Regression checks for stderr warnings and real child failures on PowerShell."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LoggedPythonTests(unittest.TestCase):
    """Validate the CLI boundary that used to mistake warnings for test failures."""

    def invoke(self, code: str, log: Path) -> subprocess.CompletedProcess[str]:
        runner = Path(__file__).parent / "scripts" / "run_python_logged.py"
        return subprocess.run(
            [sys.executable, str(runner), "--log", str(log), "--", "-c", code],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )

    def test_stderr_warning_is_logged_without_becoming_a_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="logged python ") as folder:
            log = Path(folder) / "log with spaces.txt"
            result = self.invoke(
                "import sys; print('warning: deprecated', file=sys.stderr); "
                "print('kiểm tra UTF-8')", log,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertIn("warning: deprecated", log.read_text(encoding="utf-8"))
            self.assertIn("kiểm tra UTF-8", log.read_text(encoding="utf-8"))

    def test_real_child_failure_keeps_exit_code_and_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "failure.txt"
            result = self.invoke("raise RuntimeError('intentional failure')", log)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, "")
            self.assertIn("RuntimeError: intentional failure", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
