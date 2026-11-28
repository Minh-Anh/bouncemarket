"""Runs the end-to-end tests in tests/e2e/, each in a process of its own: each starts the
whole server, and the server's settings and memory belong to one process."""
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.e2e
@pytest.mark.parametrize("module, flags", [
    ("limits", []),
    ("round_log", []),
    ("round_log", ["--timing-column"]),
    ("round_log", ["--no-nudge-columns"]),
])
def test_end_to_end(module, flags):
    result = subprocess.run([sys.executable, "-m", f"tests.e2e.{module}", *flags], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0 and "all checks passed" in result.stdout, result.stdout[-2000:] + result.stderr[-2000:]
