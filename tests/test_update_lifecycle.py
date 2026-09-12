# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Regressions for the update button's download and installer hand-off."""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path
from unittest import mock

import pytest

from navin.update import notice, service


@pytest.fixture
def update_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(service, "_STATE", {"state": "idle", "progress": 0})
    monkeypatch.setattr(service, "_CACHE", (0.0, None))
    monkeypatch.setattr(service, "notify", mock.Mock())
    monkeypatch.setattr(service, "_schedule_shutdown", mock.Mock())
    return tmp_path


def release(payload: bytes = b"new application", kind: str = "windows-setup") -> dict:
    return {
        "available": True,
        "supported": True,
        "configured": True,
        "currentVersion": "1.0.0",
        "latestVersion": "9.9.9",
        "installKind": kind,
        "channel": "stable",
        "artifact": {
            "url": "https://updates.example/v9.9.9/application",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }


def stream_payload(payload: bytes):
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.iter_bytes.return_value = [payload]
    return response


def test_bad_download_reports_error_instead_of_downloading_forever(update_home, monkeypatch):
    info = release(b"expected")
    monkeypatch.setattr(service, "check_for_update", lambda: info)
    monkeypatch.setattr(service.httpx, "stream", lambda *a, **kw: stream_payload(b"tampered"))

    with pytest.raises(service.UpdateError, match="checksum"):
        service.download_update()

    status = service.update_status()
    assert status["state"] == "error"
    assert "checksum" in status["error"]
    assert not status.get("path")


def test_download_keeps_the_release_that_matches_its_bytes(update_home, monkeypatch):
    info = release()
    service._STATE["update"] = {**info, "latestVersion": "1.1.0"}
    monkeypatch.setattr(service, "check_for_update", lambda: info)
    monkeypatch.setattr(service.httpx, "stream", lambda *a, **kw: stream_payload(b"new application"))

    service.download_update()

    status = service.update_status()
    assert status["state"] == "ready"
    assert status["update"] == info
    assert Path(status["path"]).read_bytes() == b"new application"


@pytest.mark.parametrize("state", ["downloading", "installing", "restarting", "ready"])
def test_version_checks_preserve_an_update_in_progress(update_home, monkeypatch, state):
    info = release()
    path = update_home / "verified.exe"
    path.write_bytes(b"new application")
    service._STATE.update(state=state, update=info, path=str(path), progress=54)
    monkeypatch.setattr(service, "_update_config", lambda: ("https://updates.example", "stable", ""))
    monkeypatch.setattr(service, "_fetch_manifest", lambda *a: {})
    monkeypatch.setattr(service, "_release_info", lambda *a, **kw: info.copy())

    service.check_for_update(force=True)

    status = service.update_status()
    assert status["state"] == state
    assert status["progress"] == 54
    assert status["path"] == str(path)


@pytest.mark.parametrize("kind", ["windows-setup", "windows-portable"])
def test_desktop_helper_runs_outside_the_installation(update_home, monkeypatch, kind):
    installed = update_home / "installed" / "Navin"
    tree = installed / "navin-dist"
    tree.mkdir(parents=True)
    exe = tree / "navin.exe"
    exe.write_bytes(b"engine")
    helper = tree / "NavinUpdater.exe"
    helper.write_bytes(b"helper")
    desktop = installed / "Navin.exe"
    desktop.write_bytes(b"window")
    artifact = update_home / "download.exe"
    artifact.write_bytes(b"new application")
    service._STATE.update(state="ready", path=str(artifact), update=release(kind=kind))
    monkeypatch.setattr(service.sys, "executable", str(exe))
    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setattr(service, "_desktop_app", lambda: desktop)
    monkeypatch.setattr(service, "_desktop_pid", lambda: 9876)
    popen = mock.Mock()
    monkeypatch.setattr(service.subprocess, "Popen", popen)

    result = service.install_update()

    assert result["state"] == "restarting"
    command = popen.call_args.args[0]
    running_helper = Path(command[0])
    assert not running_helper.is_relative_to(installed)
    assert running_helper.read_bytes() == b"helper"
    assert not Path(popen.call_args.kwargs["cwd"]).is_relative_to(installed)
    assert command[command.index("--target") + 1] == str(desktop)
    assert command[command.index("--restart") + 1] == str(desktop)
    service._schedule_shutdown.assert_called_once()


def test_installer_launch_failure_is_visible_and_keeps_the_app_open(update_home, monkeypatch):
    artifact = update_home / "download.AppImage"
    artifact.write_bytes(b"new application")
    service._STATE.update(state="ready", path=str(artifact), update=release(kind="linux-appimage"))
    monkeypatch.setattr(service, "_install_appimage", mock.Mock(side_effect=OSError("Permission denied")))

    with pytest.raises(service.UpdateError, match="Permission denied"):
        service.install_update()

    assert service.update_status()["state"] == "error"
    assert "Permission denied" in service.update_status()["error"]
    service._schedule_shutdown.assert_not_called()


def test_second_install_click_does_not_start_a_second_installer(update_home, monkeypatch):
    artifact = update_home / "download.AppImage"
    artifact.write_bytes(b"new application")
    service._STATE.update(state="ready", path=str(artifact), update=release(kind="linux-appimage"))
    install = mock.Mock()
    monkeypatch.setattr(service, "_install_appimage", install)

    assert service.install_update()["state"] == "restarting"
    assert service.install_update()["state"] == "restarting"

    install.assert_called_once()
    service._schedule_shutdown.assert_called_once()


def test_background_download_returns_before_the_file_is_ready(update_home, monkeypatch):
    info = release()
    started = threading.Event()
    finish = threading.Event()
    calls = []

    def slow_stream(*args, **kwargs):
        calls.append(1)
        started.set()
        assert finish.wait(5)
        return stream_payload(b"new application")

    monkeypatch.setattr(service, "check_for_update", lambda: info)
    monkeypatch.setattr(service.httpx, "stream", slow_stream)
    try:
        status = service.start_update_download()
        assert status["state"] == "downloading"
        assert started.wait(2)
        assert service.start_update_download()["state"] == "downloading"
    finally:
        finish.set()
        worker = getattr(service, "_DOWNLOAD_WORKER", None)
        if worker is not None:
            worker.join(5)

    assert calls == [1]
    assert service.update_status()["state"] == "ready"


def test_cli_does_not_reuse_a_desktop_update_notice(update_home, monkeypatch):
    monkeypatch.setattr(service, "_update_config", lambda: ("https://updates.example", "stable", ""))
    monkeypatch.setattr(service, "_install_kind", lambda: "windows-setup")
    notice._write_cache(release())
    monkeypatch.setattr(service, "_install_kind", lambda: "cli")
    monkeypatch.setattr(service, "check_for_update", lambda **kw: release(kind="cli"))

    info = notice.latest_update_info()

    assert info["installKind"] == "cli"


def test_changing_release_channel_invalidates_the_notice_cache(update_home, monkeypatch):
    monkeypatch.setattr(service, "_update_config", lambda: ("https://updates.example", "stable", ""))
    notice._write_cache(release())
    monkeypatch.setattr(service, "_update_config", lambda: ("https://updates.example", "beta", ""))
    assert notice._read_cache() is None


def test_updater_does_not_relaunch_with_the_old_bundles_libraries(update_home, monkeypatch):
    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/old/navin/_internal")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/old/navin/_internal:/system/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/lib")
    env = service._updater_environment()
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert "_PYI_PARENT_PROCESS_LEVEL" not in env
    assert env["LD_LIBRARY_PATH"] == "/system/lib"
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.skipif(os.name == "nt", reason="Exercises the POSIX macOS hand-off script")
@pytest.mark.parametrize("copy_fails", [False, True])
def test_macos_handoff_preserves_a_working_bundle_on_copy_failure(update_home, monkeypatch, copy_fails):
    commands = update_home / "commands"
    commands.mkdir()
    image = update_home / "disk image"
    (image / "Navin.app").mkdir(parents=True)
    (image / "Navin.app" / "version").write_text("new")
    target = update_home / "Applications" / "Navin.app"
    target.mkdir(parents=True)
    (target / "version").write_text("old")
    opened = update_home / "opened"
    scripts = {
        "hdiutil": 'if [ "$1" = attach ]; then shift; while [ "$#" -gt 0 ]; do '
            'if [ "$1" = -mountpoint ]; then cp -R "$NAVIN_UPDATE_TEST_IMAGE/Navin.app" "$2/"; exit; '
            'fi; shift; done; fi; exit 0',
        "ditto": 'cp -R "$1" "$2"; ' + ("exit 1" if copy_fails else "exit 0"),
        "xattr": "exit 0",
        "open": 'cat "$2/version" >> "$NAVIN_UPDATE_TEST_OPENED"',
    }
    for name, script in scripts.items():
        command = commands / name
        command.write_text("#!/bin/sh\n" + script + "\n")
        command.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("NAVIN_UPDATE_TEST_IMAGE", str(image))
    monkeypatch.setenv("NAVIN_UPDATE_TEST_OPENED", str(opened))
    finished = subprocess.Popen(["/bin/true"])
    finished.wait()

    result = subprocess.run([
        "/bin/sh", "-c", service._MACOS_INSTALL_SCRIPT, "navin-updater",
        str(finished.pid), "0", str(image), str(target), str(target) + ".old",
    ], capture_output=True, text=True, timeout=10)

    assert result.returncode == (1 if copy_fails else 0), result.stderr
    version = "old" if copy_fails else "new"
    assert (target / "version").read_text() == version
    assert opened.read_text() == version
    assert not Path(str(target) + ".new").exists()


def test_terminal_does_not_report_up_to_date_when_the_server_failed(update_home, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from navin.tui.app import NavinApp

    host = SimpleNamespace(_update_info={}, _note=mock.AsyncMock())
    monkeypatch.setattr(service, "check_for_update", mock.Mock(side_effect=service.UpdateError("Server unavailable")))
    asyncio.run(NavinApp.action_update(host))
    host._note.assert_awaited_once()
    text, level = host._note.call_args.args
    assert "Server unavailable" in text
    assert "up to date" not in text
    assert level == "error"
