# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A one-line "a newer navin exists" hint for the terminal entry points.

``navin-cli`` and ``navin gateway`` are the places a CLI user actually looks.
The desktop app has its toast; the terminal had nothing, so a user who
installed with ``curl https://navin.live/install`` never learned a new
version existed. This module answers one question, cheaply and safely: is
there something newer for this install, and how does the user get it?

It never raises, never blocks the caller (run it from a thread) and asks
the update server at most once a day per machine: the answer is cached in
``~/.navin/update-check.json`` and reused by every navin process until it
ages out or the running version changes (which means the update happened).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin import __version__

_CACHE_TTL_S = 24 * 60 * 60


def _cache_path() -> Path:
    return Path.home() / ".navin" / "update-check.json"


def _read_cache() -> dict[str, Any] | None:
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        fresh = time.time() - float(raw.get("at") or 0) < _CACHE_TTL_S
    except (TypeError, ValueError):
        return None
    if not fresh or raw.get("currentVersion") != __version__:
        return None
    info = raw.get("info")
    return info if isinstance(info, dict) else None


def _write_cache(info: dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"at": time.time(), "currentVersion": __version__, "info": info}),
            encoding="utf-8",
        )
    except OSError:
        pass


def latest_update_info(*, force: bool = False) -> dict[str, Any] | None:
    """The updater's answer for this install, from the daily cache or the server.

    ``None`` when this build is not updated by the signed pipeline (source
    checkout), has no update server, or the server could not be reached.
    """
    from navin.update import service

    kind = service._install_kind()
    if kind in {"source", "unsupported"}:
        return None
    if not force:
        cached = _read_cache()
        if cached is not None:
            return cached
    try:
        if not service.updates_configured():
            return None
        info = service.check_for_update(force=True)
    except Exception:  # noqa: BLE001 - a hint must never become an error
        return None
    _write_cache(info)
    return info


def update_notice(*, force: bool = False) -> str | None:
    """A short sentence to show the user, or ``None`` when there is nothing to say."""
    info = latest_update_info(force=force)
    if not info or not info.get("available"):
        return None
    latest = str(info.get("latestVersion") or "").strip()
    if not latest:
        return None
    head = f"navin {latest} is available (you have {__version__})."
    if not info.get("supported"):
        reason = str(info.get("reason") or "").strip()
        return f"{head} {reason}".strip()
    if info.get("installKind") == "cli":
        return f"{head} Update with: navin update"
    # A desktop install: its own window carries the Install button.
    return f"{head} Open Navin and use Settings > Updates to install it."


def notice_in_background(callback: Callable[[str], None]) -> threading.Thread:
    """Compute the notice on a daemon thread and hand it to ``callback`` if any.

    The callback runs on that thread; UI code should marshal back to its own
    loop (Textual: ``call_from_thread``).
    """

    def run() -> None:
        try:
            text = update_notice()
        except Exception:  # noqa: BLE001
            return
        if text:
            try:
                callback(text)
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=run, name="navin-update-notice", daemon=True)
    thread.start()
    return thread
