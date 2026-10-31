# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Automatic updates: check, download in the background, install when idle.

Runs inside the gateway of a packaged build (desktop sidecar or CLI gateway).
At start and every two hours it reads the signed manifest. With
``updates.autoInstall`` on, an offered update is downloaded in the background
and installed only once Navin is idle: no chat turn, no subagent, no command
running, and nobody used Navin for ten minutes. A mandatory update (revoked
version, minimumVersion) waits for the same quiet moment; it never cuts a
mission. Before the installer starts, every session is saved, so an agent
that was waiting picks up after the restart.

Formats the desktop shell installs with the Tauri updater (Windows setup,
macOS app, AppImage) are left to the shell while it reports in; Navin takes
them back when it stops reporting.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.update import activity, service
from navin.update import policy as update_policy


@dataclass
class AutoUpdater:
    """One decision per tick; the thread only calls ``tick`` on a timer."""

    save_sessions: Callable[[], int] | None = None
    check_interval_s: float = update_policy.CHECK_INTERVAL_S
    idle_before_install_s: float = update_policy.IDLE_BEFORE_INSTALL_S
    clock: Callable[[], float] = time.monotonic
    _next_check: float = 0.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    last_action: str = "idle"

    def _settings(self) -> Any:
        from navin.config.loader import load_config

        return load_config().updates

    def tick(self) -> str:
        """Advance the update one step; returns what was done (for tests and logs)."""
        settings = self._settings()
        if not settings.enabled or not settings.auto_check:
            return self._did("disabled")
        now = self.clock()
        if now >= self._next_check:
            self._next_check = now + self.check_interval_s
            try:
                service.check_for_update(force=True)
            except service.UpdateError as exc:
                logger.info("Automatic update check failed: {}", exc)
                return self._did("check-failed")
        status = service.update_status()
        info = status.get("update") or {}
        if not info.get("available") or not info.get("supported"):
            return self._did("nothing")
        if not settings.auto_install:
            return self._did("announce-only")
        kind = str(info.get("installKind") or status.get("installKind") or "")
        if kind == "cli":
            # The terminal may be running this very tree: the CLI announces the
            # update (startup hint, /update) and `navin update` installs it.
            return self._did("announce-only")
        if update_policy.engine_for(kind) != "navin":
            return self._did("shell-installs")
        state = status.get("state")
        if state in {"downloading", "installing", "restarting"}:
            return self._did(str(state))
        if state == "error":
            # Retried at the next check, not every minute against a failing server.
            return self._did("error-wait")
        if state != "ready":
            service.start_update_download()
            return self._did("download-started")
        return self._install_when_idle(info)

    def _install_when_idle(self, info: dict[str, Any]) -> str:
        if activity.busy_reasons():
            return self._did("waiting-busy")
        if activity.idle_for_s() < self.idle_before_install_s:
            return self._did("waiting-idle")
        ready = prepare_for_install(self.save_sessions)
        if not ready["ready"]:
            return self._did("waiting-busy")
        try:
            service.install_update()
        except service.UpdateError as exc:
            logger.warning("Automatic update install failed: {}", exc)
            return self._did("install-failed")
        logger.info("Installing update {} while Navin is idle", info.get("latestVersion"))
        return self._did("installing")

    def _did(self, action: str) -> str:
        self.last_action = action
        return action

    def start(self, every_s: float = 60.0) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            # First check right away: the requirement is "at launch, then periodically".
            while not self._stop.is_set():
                try:
                    self.tick()
                except (service.UpdateError, OSError) as exc:
                    logger.warning("Automatic update step failed: {}", exc)
                self._stop.wait(every_s)

        self._thread = threading.Thread(target=run, name="navin-auto-update", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def prepare_for_install(save_sessions: Callable[[], int] | None) -> dict[str, Any]:
    """Checkpoint before a restart: refuse while work runs, else save sessions.

    Used by the automatic install and by the desktop shell (Tauri engine)
    through /api/settings/update-prepare.
    """
    busy = activity.busy_reasons()
    if busy:
        return {"ready": False, "busy": busy, "checkpointed": 0}
    saved = save_sessions() if save_sessions is not None else 0
    return {"ready": True, "busy": [], "checkpointed": int(saved)}


_INSTANCE: AutoUpdater | None = None
_INSTANCE_LOCK = threading.Lock()
_SAVE_SESSIONS: Callable[[], int] | None = None


def register_session_saver(save: Callable[[], int] | None) -> None:
    """How the running engine writes its sessions to disk before a restart."""
    global _SAVE_SESSIONS
    with _INSTANCE_LOCK:
        _SAVE_SESSIONS = save


def current_save_sessions() -> Callable[[], int] | None:
    """The saver registered by the agent loop, or None in a bare gateway."""
    with _INSTANCE_LOCK:
        return _SAVE_SESSIONS


def start_auto_updater(save_sessions: Callable[[], int] | None = None) -> AutoUpdater | None:
    """Start the background updater once per process, packaged builds only.

    A source checkout cannot replace itself, and tests must never reach the
    update server: only a frozen build (desktop sidecar, packaged CLI) runs it.
    """
    import sys

    global _INSTANCE
    register_session_saver(save_sessions)
    if not getattr(sys, "frozen", False):
        return None
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = AutoUpdater(save_sessions=save_sessions)
            _INSTANCE.start()
        return _INSTANCE
