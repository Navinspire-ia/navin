# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Whether WSL still starts new sessions, as the Windows engine sees it.

WSL can wedge so that sessions already open keep working while every new
``wsl.exe`` launch hangs (orphaned ``Relay`` processes pile up inside the VM,
microsoft/WSL#41242) or fails at once with ``Wsl/Service/E_UNEXPECTED``. No
test, lint, git or preview command then reaches a project in the distribution,
and only a WSL restart helps. The status bar has to say so plainly instead of
leaving the user with panels that spin and an agent that guesses.

A caller that saw a launch time out or fail calls :func:`suspect`. That is only
a hint (a long ``find`` may time out on a healthy distribution), so the verdict
comes from a liveness check run off the event loop: ``wsl.exe -d <distro>
--exec true`` answers in well under a second when WSL is healthy. While a
distribution is marked down the check is repeated from :func:`status`, so the
notice clears by itself once WSL is restarted. Nothing probes WSL while no
caller suspects it: a periodic probe would keep an idle VM awake.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from navin.utils import wsl
from navin.utils.proc import no_window_kwargs

# A healthy distribution answers `--exec true` in well under a second, a
# stopped one boots in a few seconds. Twenty seconds without an answer is a
# wedged WSL, not a slow one.
CHECK_TIMEOUT_S = 20.0
# Least time between two checks of one distribution, whatever asked for them.
RECHECK_EVERY_S = 30.0
# What the user runs on the Windows side to clear it.
RESTART_COMMAND = "wsl --shutdown"


@dataclass(frozen=True)
class _Down:
    distro: str
    state: str  # "hung": no answer; "error": wsl.exe failed at once
    since: float  # wall clock, for "for N s" in the UI
    detail: str


_lock = threading.Lock()
_down: dict[str, _Down] = {}
_checking: set[str] = set()
_last_check: dict[str, float] = {}


def suspect(distro: str) -> None:
    """A ``wsl.exe`` launch into ``distro`` timed out or failed: check WSL itself."""
    if distro:
        _schedule_check(distro)


def answered(distro: str) -> None:
    """A ``wsl.exe`` launch into ``distro`` answered: it starts sessions again."""
    with _lock:
        cleared = _down.pop(distro.lower(), None)
    if cleared is not None:
        logger.info("WSL {} starts new sessions again", cleared.distro)


def status() -> dict[str, Any] | None:
    """The distribution that no longer starts sessions, for the status bar.

    Never blocks: it reads the last verdict and, while one is down, starts the
    next recovery check in the background.
    """
    with _lock:
        down = sorted(_down.values(), key=lambda item: item.since)
    if not down:
        return None
    for item in down:
        _schedule_check(item.distro)
    first = down[0]
    return {
        "distro": first.distro,
        "state": first.state,
        "sinceSeconds": max(0, int(time.time() - first.since)),
        "detail": first.detail,
        "command": RESTART_COMMAND,
    }


def check_now(distro: str) -> tuple[str, str] | None:
    """Start one empty session in ``distro``: None when it answered, else (state, detail)."""
    exe = wsl.wsl_executable()
    if not exe:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell on the Windows side
            [exe, "-d", distro, "--exec", "true"],
            capture_output=True, timeout=CHECK_TIMEOUT_S, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return "hung", f"wsl.exe gave no answer in {int(CHECK_TIMEOUT_S)} s"
    except OSError as exc:
        return "error", str(exc)
    if out.returncode == 0:
        return None
    return "error", _decode(out.stdout + out.stderr) or f"wsl.exe exited with {out.returncode}"


def _schedule_check(distro: str) -> None:
    key = distro.lower()
    now = time.monotonic()
    with _lock:
        last = _last_check.get(key)
        if key in _checking or (last is not None and now - last < RECHECK_EVERY_S):
            return
        _checking.add(key)
        _last_check[key] = now
    thread = threading.Thread(target=_check, args=(distro,), name="navin-wsl-health", daemon=True)
    thread.start()


def _check(distro: str) -> None:
    key = distro.lower()
    try:
        verdict = check_now(distro)
    except Exception:
        # A check that crashed proves nothing either way: keep the last verdict
        # and let the next one decide, but leave a trace.
        logger.exception("WSL liveness check of {} crashed", distro)
        return
    finally:
        with _lock:
            _checking.discard(key)
    if verdict is None:
        answered(distro)
        return
    state, detail = verdict
    with _lock:
        previous = _down.get(key)
        _down[key] = _Down(
            distro=distro, state=state, detail=detail,
            since=previous.since if previous is not None else time.time(),
        )
    if previous is None:
        logger.warning(
            "WSL {} does not start new sessions ({}): {} on Windows clears it",
            distro, detail, RESTART_COMMAND,
        )


def _decode(raw: bytes) -> str:
    """wsl.exe writes its own messages in UTF-16LE, the distribution in UTF-8."""
    text = raw.decode("utf-16-le", errors="replace") if b"\x00" in raw else raw.decode("utf-8", errors="replace")
    return " ".join(text.replace("\x00", "").split())[:300]


def reset() -> None:
    """Forget every verdict (tests, and a fresh engine)."""
    with _lock:
        _down.clear()
        _checking.clear()
        _last_check.clear()
