# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Apply the WSL out-of-memory guard where Navin can, remind where it cannot.

``wsl_oom_guard.sh`` (next to this file, so in every build) gives systemd's
init.scope OOMPolicy=continue: under WSL every terminal lives there, and with
the default one out-of-memory kill stopped all of them (2026-11-05, eight
navin-cli). It needs root. Where Navin has that right, it applies it once
per distribution and per Navin version:

- the Linux packages run it as their install, upgrade and removal scripts;
- on Windows, the desktop and the CLI run it with ``wsl.exe -u root`` in
  every distribution running at start (no Windows administrator needed,
  and a stopped distribution is never started for it);
- inside WSL, a Navin running as root applies it at start.

Elsewhere the CLI says it once per version, and ``navin doctor --install``
applies it with sudo. ``NAVIN_WSL_OOM_GUARD=0`` turns all of it off.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils import wsl as wsl_paths
from navin.utils import wsl_health
from navin.utils.host_env import host_tool_env
from navin.utils.proc import no_window_kwargs

SCRIPT_NAME = "wsl_oom_guard.sh"
LINE_PREFIX = "navin-wsl-oom-guard: "
APPLIED_FILE = "applied.json"
REMINDER_FILE = "reminder.json"
_OFF = frozenset({"0", "false", "no", "off"})
_LIST_TIMEOUT_S = 15
_RUN_TIMEOUT_S = 60
# A password typed at the sudo prompt: give the person time.
_SUDO_TIMEOUT_S = 300
REMINDER = (
    "WSL: one out-of-memory kill here stops every terminal and every navin-cli at once. "
    "Run `navin doctor --install` once to prevent it (it asks for sudo)."
)


@dataclass(frozen=True)
class GuardResult:
    """One answer of the guard: ``state`` is its own word (applied, needed...)."""

    state: str
    detail: str = ""

    @property
    def protected(self) -> bool:
        return self.state in ("applied", "present", "protected")


def enabled() -> bool:
    return os.environ.get("NAVIN_WSL_OOM_GUARD", "").strip().lower() not in _OFF


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)  # absent on Windows
    return geteuid is not None and geteuid() == 0


def guard_script() -> Path | None:
    """The bundled script, None in an installation that does not ship it."""
    path = Path(__file__).with_name(SCRIPT_NAME)
    return path if path.is_file() else None


def parse_line(text: str) -> GuardResult | None:
    """The guard's own line in what a run printed, None when it never ran."""
    for raw in text.splitlines():
        line = raw.strip().strip("\x00").strip()
        if line.startswith(LINE_PREFIX):
            state, _, detail = line[len(LINE_PREFIX):].partition(": ")
            return GuardResult(state.strip(), detail.strip())
    return None


def _decode(raw: bytes) -> str:
    """wsl.exe writes its own messages in UTF-16LE, the distribution in UTF-8."""
    if b"\x00" in raw:
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    return raw.decode("utf-8", errors="replace")


def _last_line(text: str, fallback: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else fallback


def _version() -> str:
    from navin import __version__

    return str(__version__)


def _state_dir() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("wsl-guard")


def _read_state(name: str) -> dict[str, Any]:
    path = _state_dir() / name
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # The guard is idempotent: starting over only runs it once more.
        logger.warning("WSL guard state {} unreadable, starting over: {}", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(name: str, data: dict[str, Any]) -> None:
    path = _state_dir() / name
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("WSL guard state {} not saved, it runs again next time: {}", path, exc)
        tmp.unlink(missing_ok=True)


# --- Inside a WSL distribution ------------------------------------------------


def run_here(action: str, *, sudo: str = "") -> GuardResult:
    """Run the guard in this Linux system.

    ``sudo``: "" runs it as this user, "ask" through sudo at this terminal
    (its password prompt shows), "quiet" through ``sudo -n`` (never asks).
    """
    script = guard_script()
    if script is None:
        return GuardResult("error", "the guard script is not in this installation")
    argv = ["sh", str(script), action]
    if sudo == "ask":
        argv = ["sudo", *argv]
    elif sudo == "quiet":
        argv = ["sudo", "-n", *argv]
    ask = sudo == "ask"
    try:
        done = subprocess.run(
            argv,
            stdin=None if ask else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=None if ask else subprocess.PIPE,
            text=True,
            timeout=_SUDO_TIMEOUT_S if ask else _RUN_TIMEOUT_S,
            check=False,
            env=host_tool_env(),
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return GuardResult("error", "no answer in time")
    except OSError as exc:
        return GuardResult("error", f"could not run: {exc}")
    result = parse_line(done.stdout or "")
    if result is not None:
        return result
    output = f"{done.stdout or ''}\n{done.stderr or ''}"
    return GuardResult("error", _last_line(output, f"exit code {done.returncode}"))


def install_here(*, interactive: bool) -> GuardResult:
    """Apply the guard in this Linux system: as root, else through sudo."""
    if _is_root():
        return run_here("install")
    if shutil.which("sudo") is None:
        return GuardResult("failed", "needs root, and sudo is not installed")
    return run_here("install", sudo="ask" if interactive else "quiet")


def reminder_once() -> str:
    """Inside WSL without root: the reminder text, once per Navin version."""
    if not enabled() or not sys.platform.startswith("linux") or not wsl_paths.is_wsl_guest():
        return ""
    if _is_root():
        return ""
    version = _version()
    if _read_state(REMINDER_FILE).get("reminded") == version:
        return ""
    if run_here("status").state != "needed":
        return ""
    _write_state(REMINDER_FILE, {"reminded": version, "at": time.time()})
    return REMINDER


# --- From Windows -------------------------------------------------------------


def _running_distributions(exe: str) -> list[str]:
    """Distributions running now: a stopped one is never started for this."""
    try:
        listed = subprocess.run(
            [exe, "-l", "--running", "-q"],
            capture_output=True,
            timeout=_LIST_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("wsl.exe -l --running failed: {}", exc)
        return []
    if listed.returncode != 0:
        return []
    installed = {name.lower() for name in wsl_paths.distributions()}
    # "There are no running distributions." is a sentence, not a name.
    return [name for name in wsl_paths.parse_distribution_list(listed.stdout) if name.lower() in installed]


def _run_in_distribution(exe: str, distro: str, script: bytes) -> GuardResult:
    """The script goes on stdin: the distribution needs no Navin of its own."""
    argv = [exe, "-d", distro, "-u", "root", "--exec", "sh", "-s", "install"]
    try:
        done = subprocess.run(
            argv,
            input=script,
            capture_output=True,
            timeout=_RUN_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        wsl_health.suspect(distro)
        return GuardResult("error", f"WSL {distro} gave no answer in {_RUN_TIMEOUT_S} s")
    except OSError as exc:
        return GuardResult("error", f"wsl.exe could not run: {exc}")
    result = parse_line(_decode(done.stdout))
    if result is not None:
        return result
    return GuardResult("error", _last_line(_decode(done.stdout + done.stderr), f"exit code {done.returncode}"))


def _announce(distro: str) -> None:
    from navin.bus.notify import notify

    notify(
        title=f"WSL {distro} protected",
        detail="One out-of-memory kill there no longer stops every terminal at once.",
        key=f"wsl-oom-guard:{distro}",
    )


def protect_running_distributions() -> list[tuple[str, GuardResult]]:
    """From Windows: apply the guard in every running distribution, once per version.

    A distribution where wsl.exe itself failed is tried again at the next start.
    """
    script = guard_script()
    exe = wsl_paths.wsl_executable()
    if script is None or exe is None:
        return []
    payload = script.read_bytes().replace(b"\r\n", b"\n")
    version = _version()
    state = _read_state(APPLIED_FILE)
    outcomes: list[tuple[str, GuardResult]] = []
    for distro in _running_distributions(exe):
        if distro.lower().startswith("docker-desktop"):
            # Docker Desktop's own VM: no terminal of the user runs there.
            continue
        known = state.get(distro)
        if isinstance(known, dict) and known.get("version") == version:
            continue
        if wsl_health.refuse_reason(distro):
            continue
        result = _run_in_distribution(exe, distro, payload)
        outcomes.append((distro, result))
        if result.state == "error":
            continue
        state[distro] = {"version": version, "state": result.state, "detail": result.detail, "at": time.time()}
        _write_state(APPLIED_FILE, state)
        if result.state == "applied":
            _announce(distro)
    return outcomes


# --- At the start of a CLI or a gateway ---------------------------------------


def _protect_from_windows(role: str) -> None:
    try:
        outcomes = protect_running_distributions()
    except OSError as exc:
        logger.warning("WSL guard ({}): {}", role, exc)
        return
    for distro, result in outcomes:
        logger.info("WSL guard ({}) in {}: {}: {}", role, distro, result.state, result.detail)


def _apply_as_root(role: str) -> None:
    result = run_here("install")
    logger.info("WSL guard ({}): {}: {}", role, result.state, result.detail)


def start_in_background(role: str) -> threading.Thread | None:
    """At the start of a CLI or a gateway, in a thread: nothing waits for it."""
    if not enabled():
        return None
    if sys.platform == "win32":
        target = _protect_from_windows
    elif sys.platform.startswith("linux") and _is_root() and wsl_paths.is_wsl_guest():
        target = _apply_as_root
    else:
        return None
    thread = threading.Thread(target=target, args=(role,), name="navin-wsl-guard", daemon=True)
    thread.start()
    return thread
