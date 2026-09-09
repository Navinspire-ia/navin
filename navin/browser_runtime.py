# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prepare Chromium for source and packaged desktop installations on every OS."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from weakref import WeakValueDictionary

from navin.documents._chromium import ConversionError, find_chromium
from navin.python_runtime import packaged, python_command
from navin.utils.proc import (
    detached_no_window_kwargs,
    kill_posix_process_group,
    kill_windows_process_tree,
)
from navin.utils.task_progress import emit_task_progress

_INSTALL_TIMEOUT_S = 300
_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def installed_chromium() -> str | None:
    try:
        return find_chromium()
    except (ConversionError, OSError, RuntimeError):
        return None


async def _install_chromium() -> None:
    # The frozen interpreter includes Playwright and its Node driver. Downloads
    # go to the user's OS cache, never into a signed/read-only application bundle.
    env = dict(os.environ)
    if packaged() and env.get("PLAYWRIGHT_BROWSERS_PATH") == "0":
        env.pop("PLAYWRIGHT_BROWSERS_PATH")
    argv = [*python_command(), "-m", "playwright", "install", "chromium"]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=env, **detached_no_window_kwargs(),
    )
    try:
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), _INSTALL_TIMEOUT_S)
        except TimeoutError as exc:
            raise RuntimeError("Preparing Chromium timed out. Retry when the connection is available.") from exc
        if proc.returncode:
            detail = (output or b"").decode("utf-8", errors="replace")[-3000:].strip()
            raise RuntimeError(f"Could not prepare Chromium (exit {proc.returncode}). {detail}")
    finally:
        if proc.returncode is None:
            if sys.platform == "win32":
                await asyncio.to_thread(kill_windows_process_tree, proc.pid)
            else:
                kill_posix_process_group(proc.pid)
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


async def ensure_chromium() -> str:
    """Reuse an installed browser, or fetch one on the first browser request.

    Concurrent chats share preparation. A failed/canceled download is not cached
    as success, so the next request can recover after a connection failure.
    Playwright itself serializes installations between separate processes.
    """
    existing = await asyncio.to_thread(installed_chromium)
    if existing:
        return existing
    loop = asyncio.get_running_loop()
    lock = _LOCKS.setdefault(id(loop), asyncio.Lock())
    async with lock:
        existing = await asyncio.to_thread(installed_chromium)
        if existing:
            return existing
        await emit_task_progress(label="Preparing the browser", indeterminate=True)
        await _install_chromium()
        existing = await asyncio.to_thread(installed_chromium)
        if not existing:
            raise RuntimeError("Chromium was installed but could not be located. Set NAVIN_CHROMIUM to its executable.")
        await emit_task_progress(label="Browser ready", percent=100)
        return existing
