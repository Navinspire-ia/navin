# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run the real PowerShell signing retry policy with a simulated service."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell signing runner")
def test_signing_failures_stop_or_retry_without_exposing_secrets():
    powershell = shutil.which("powershell.exe")
    assert powershell, "Windows builds require PowerShell"
    script = Path(__file__).with_name("windows_signing_regression.ps1")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS signing retry policy" in result.stdout
