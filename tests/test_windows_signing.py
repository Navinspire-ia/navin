# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run the real PowerShell signing retry policy with a simulated service."""

import shutil
import subprocess
from pathlib import Path

import pytest


def _windows_powershell():
    """Windows PowerShell binary, also present under WSL interop on dev machines."""
    return shutil.which("powershell.exe")


@pytest.mark.skipif(_windows_powershell() is None, reason="Windows PowerShell not available (Windows or WSL interop)")
def test_signing_failures_stop_or_retry_without_exposing_secrets():
    powershell = _windows_powershell()
    assert powershell, "Windows builds require PowerShell"
    script = Path(__file__).with_name("windows_signing_regression.ps1")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS signing retry policy" in result.stdout
