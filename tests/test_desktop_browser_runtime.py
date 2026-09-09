# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""First-use browser preparation and OS-specific desktop distribution paths."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from navin import browser_runtime as runtime
from navin.agent.tools import browser
from navin.documents import _chromium


@pytest.mark.parametrize("relative", [
    "chrome-linux64/chrome",
    "chrome-linux/chrome",
    "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
    "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chrome-win64/chrome.exe",
    "chrome-win/chrome.exe",
])
def test_chromium_cache_layout_on_each_os(tmp_path, relative):
    binary = tmp_path / "cache/ms-playwright/chromium-1228" / relative
    binary.parent.mkdir(parents=True)
    binary.touch()
    with (
        patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}, clear=True),
        patch.object(_chromium.Path, "home", return_value=tmp_path / "home"),
        patch.object(_chromium.shutil, "which", return_value=None),
        patch.object(_chromium, "_MAC_CHROMIUM_APPS", ()),
        patch.object(_chromium, "_windows_install_candidates", return_value=[]),
    ):
        assert _chromium.find_chromium() == str(binary)


@pytest.mark.skipif(os.name == "nt", reason="macOS application directory semantics")
def test_macos_browser_installed_only_for_the_current_user(tmp_path):
    relative = "Applications/Navin Test Chromium.app/Contents/MacOS/Chromium"
    binary = tmp_path / relative
    binary.parent.mkdir(parents=True)
    binary.touch()
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.object(_chromium.Path, "home", return_value=tmp_path),
        patch.object(_chromium.shutil, "which", return_value=None),
        patch.object(_chromium, "_MAC_CHROMIUM_APPS", ("/" + relative,)),
    ):
        assert _chromium.find_chromium() == str(binary)


@pytest.mark.asyncio
async def test_existing_browser_needs_no_download():
    with patch.object(runtime, "installed_chromium", return_value="existing"), \
         patch.object(runtime, "_install_chromium", new_callable=AsyncMock) as install:
        assert await runtime.ensure_chromium() == "existing"
        install.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_chats_prepare_the_browser_once():
    state = {"path": None}

    async def download():
        await asyncio.sleep(0)
        state["path"] = "prepared"

    with patch.object(runtime, "installed_chromium", side_effect=lambda: state["path"]), \
         patch.object(runtime, "_install_chromium", side_effect=download) as install:
        assert await asyncio.gather(runtime.ensure_chromium(), runtime.ensure_chromium()) == ["prepared", "prepared"]
        install.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_failure_can_be_retried():
    state = {"path": None, "attempts": 0}

    async def download():
        state["attempts"] += 1
        if state["attempts"] == 1:
            raise RuntimeError("connection lost")
        state["path"] = "prepared"

    with patch.object(runtime, "installed_chromium", side_effect=lambda: state["path"]), \
         patch.object(runtime, "_install_chromium", side_effect=download):
        with pytest.raises(RuntimeError, match="connection lost"):
            await runtime.ensure_chromium()
        assert await runtime.ensure_chromium() == "prepared"
        assert state["attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
async def test_packaged_installer_uses_argv_and_a_writable_cache(platform):
    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"installed", None)))
    executable = "/Application Folder/Navin" if platform != "win32" else r"C:\Program Files\Navin\navin-cli.exe"
    with (
        patch.object(runtime.sys, "platform", platform),
        patch.object(runtime, "packaged", return_value=True),
        patch.object(runtime, "python_command", return_value=[executable, "python"]),
        patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "0"}),
        patch.object(runtime, "detached_no_window_kwargs", return_value={"creationflags": 8}),
        patch.object(runtime.asyncio, "create_subprocess_exec", return_value=proc) as start,
    ):
        await runtime._install_chromium()
    assert start.call_args.args == (executable, "python", "-m", "playwright", "install", "chromium")
    assert "PLAYWRIGHT_BROWSERS_PATH" not in start.call_args.kwargs["env"]
    assert start.call_args.kwargs["creationflags"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
async def test_canceling_preparation_reaps_the_installer_process_tree(platform):
    proc = SimpleNamespace(returncode=None, pid=314, kill=Mock(), wait=AsyncMock(),
                           communicate=AsyncMock(side_effect=asyncio.CancelledError))
    with (
        patch.object(runtime.sys, "platform", platform),
        patch.object(runtime.asyncio, "create_subprocess_exec", return_value=proc),
        patch.object(runtime, "kill_windows_process_tree") as windows_kill,
        patch.object(runtime, "kill_posix_process_group") as posix_kill,
    ):
        with pytest.raises(asyncio.CancelledError):
            await runtime._install_chromium()
    (windows_kill if platform == "win32" else posix_kill).assert_called_once_with(314)
    (posix_kill if platform == "win32" else windows_kill).assert_not_called()
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_failure_reports_the_underlying_error():
    proc = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"DNS unavailable", None)))
    with patch.object(runtime.asyncio, "create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="DNS unavailable"):
            await runtime._install_chromium()


@pytest.mark.asyncio
async def test_browser_prepares_missing_chromium_and_retries_the_original_launch():
    page = Mock()
    context = Mock(new_page=AsyncMock(return_value=page))
    ready_browser = Mock(new_context=AsyncMock(return_value=context))
    launcher = AsyncMock(side_effect=[RuntimeError("Executable doesn't exist"), ready_browser])
    session = browser._BrowserSession(browser.BrowserToolConfig())
    session._pw = SimpleNamespace(chromium=SimpleNamespace(launch=launcher))
    with (
        patch.dict(sys.modules, {"playwright.async_api": SimpleNamespace(async_playwright=Mock())}),
        patch.object(browser, "_installed_chromium", return_value=None),
        patch.object(runtime, "ensure_chromium", return_value="/Application Folder/Chromium") as prepare,
    ):
        assert await session.ensure_page() is page
    prepare.assert_awaited_once()
    assert launcher.call_args.kwargs["executable_path"] == "/Application Folder/Chromium"
    assert launcher.call_args.kwargs["headless"] is True


@pytest.mark.asyncio
async def test_an_explicit_browser_path_is_not_replaced_or_downloaded():
    session = browser._BrowserSession(browser.BrowserToolConfig(executable_path="requested-browser"))
    launcher = AsyncMock(side_effect=RuntimeError("Executable doesn't exist"))
    session._pw = SimpleNamespace(chromium=SimpleNamespace(launch=launcher))
    with (
        patch.dict(sys.modules, {"playwright.async_api": SimpleNamespace(async_playwright=Mock())}),
        patch.object(runtime, "ensure_chromium", new_callable=AsyncMock) as prepare,
    ):
        with pytest.raises(RuntimeError, match="Executable"):
            await session.ensure_page()
        prepare.assert_not_awaited()
    assert launcher.call_args.kwargs["executable_path"] == "requested-browser"


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [None, "requested-browser"])
async def test_browser_use_uses_the_same_prepared_browser(explicit):
    from navin.agent.tools.browser_use_bridge import create_browser_use_session

    session = SimpleNamespace(start=AsyncMock())
    profile = Mock()
    with (
        patch.dict(sys.modules, {
            "browser_use": SimpleNamespace(BrowserSession=Mock(return_value=session)),
            "browser_use.browser.profile": SimpleNamespace(BrowserProfile=profile),
        }),
        patch.object(runtime, "ensure_chromium", return_value="prepared-browser") as prepare,
    ):
        assert await create_browser_use_session(executable_path=explicit) is session
        assert profile.call_args.kwargs["executable_path"] == (explicit or "prepared-browser")
        assert prepare.await_count == (0 if explicit else 1)
        session.start.assert_awaited_once()
