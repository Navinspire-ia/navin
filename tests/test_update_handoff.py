# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run the detached updater handshake, including startup failures, for real."""

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from navin.update import service


@pytest.fixture
def update_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(service, "_STATE", {"state": "idle", "progress": 0})
    monkeypatch.setattr(service, "_schedule_shutdown", Mock())
    return tmp_path


@pytest.mark.skipif(os.name == "nt", reason="Real POSIX helper process")
def test_helper_must_acknowledge_before_gateway_hands_off(update_home):
    script = service._UPDATE_HANDOFF + 'printf installed > "$NAVIN_UPDATE_PROCEED.done"'
    service._start_desktop_updater(["/bin/sh", "-c", script], timeout=5)
    root = next((update_home / ".navin/updates").iterdir())
    assert (root / "ready").read_text() == "ready"
    assert (root / "proceed").read_text() == "proceed"
    # The same shell must survive the handoff and run the install phase.
    import time

    deadline = time.monotonic() + 5
    while not (root / "proceed.done").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (root / "proceed.done").read_text() == "installed"


@pytest.mark.skipif(os.name == "nt", reason="Real POSIX helper process")
def test_failed_helper_does_not_close_navin_and_keeps_diagnostics(update_home, monkeypatch):
    artifact = update_home / "download.AppImage"
    artifact.write_text("download")
    service._STATE.update(path=str(artifact), update={"installKind": "linux-appimage", "latestVersion": "2.0.5"})
    monkeypatch.setattr(service, "_install_appimage", lambda *a, **kw: service._start_desktop_updater(
        ["/bin/sh", "-c", "echo 'cannot stage application' >&2; exit 7"], timeout=5,
    ))
    with pytest.raises(service.UpdateError, match="code 7"):
        service.install_update()
    service._schedule_shutdown.assert_not_called()
    assert service.update_status()["state"] == "error"
    root = next((update_home / ".navin/updates").iterdir())
    assert "cannot stage application" in (root / "updater.log").read_text()
    assert not (root / "proceed").exists()


def test_silent_helper_timeout_cannot_authorize_an_install_later(update_home, monkeypatch):
    process = Mock(poll=Mock(return_value=None))
    monkeypatch.setattr(service.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(service.UpdateError, match="did not become ready"):
        service._start_desktop_updater(["helper"], timeout=0)
    process.terminate.assert_called_once()
    assert not list(update_home.rglob("proceed"))


def test_relaunch_drops_previous_appimage_and_desktop_identity(monkeypatch):
    stale = {
        "APPIMAGE": "/old/Navin.AppImage", "APPDIR": "/tmp/.mount_old",
        "ARGV0": "old", "NAVIN_DESKTOP_PID": "1234",
        "NAVIN_DESKTOP_APP": "/old/Navin.AppImage", "NAVIN_INSTALL_KIND": "linux-appimage",
    }
    for key, value in stale.items():
        monkeypatch.setenv(key, value)
    assert not set(stale) & service._updater_environment().keys()


@pytest.mark.skipif(os.name == "nt", reason="Real POSIX preparation failure")
def test_swap_preparation_failure_keeps_running_app_alive(update_home):
    target = update_home / "Navin.AppImage"
    target.write_text("original")
    # Use a real child PID: preparation must fail before any stop instruction.
    app = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)
    try:
        with pytest.raises(service.UpdateError, match="exited before installation"):
            service._install_posix_app(
                service._FILE_SWAP_INSTALL_SCRIPT,
                artifact=update_home / "missing", target=target,
                pid=app.pid, desktop_pid=0,
            )
        assert app.poll() is None
        assert target.read_text() == "original"
    finally:
        import signal

        os.killpg(app.pid, signal.SIGTERM)
        app.wait(timeout=5)
