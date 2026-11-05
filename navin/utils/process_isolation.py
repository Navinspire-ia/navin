# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep one out-of-memory kill from stopping every Navin session at once.

Under WSL every process started from a terminal lives in systemd's
``init.scope``, and systemd applies ``OOMPolicy=stop`` to it: when the kernel
OOM killer kills any process in there, systemd stops the whole scope. On
2026-11-05 the WSL VM filled up, the kernel killed one headless Chromium
process (68 MB), and systemd sent SIGTERM to every terminal and every
navin-cli at once, then SIGKILL 90 s later. The cgroup kept its ``oom_kill``
count, so each restart of the distribution was stopped again about 90 s
after it began, until WSL itself was shut down.

A process in a scope of its own with ``OOMPolicy=continue`` loses only the
process the kernel killed (checked on this machine: a memory-capped child was
killed, its shell and the user's manager kept running).
:func:`isolate_current_process` moves a CLI or a gateway there when it
starts from ``init.scope``; :func:`start_scope` does the same for a process
tree Navin starts (a browser), optionally under a memory cap the kernel
enforces inside that scope only. Both ask the user's systemd manager through
``busctl``; without systemd (macOS, Windows, a container) nothing changes and
the reason is returned. ``NAVIN_PROCESS_SCOPE=0`` turns it off. A CLI or a
gateway only moves when that manager outlives login sessions (lingering on):
under WSL it otherwise stops when the first terminal closes, with everything
in it.

:func:`raise_oom_score` makes the kernel pick a child (an agent command, a
browser) before the CLI that started it when the whole machine runs out.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from loguru import logger

from navin.utils.host_env import host_tool_env
from navin.utils.proc import no_window_kwargs

_OFF = frozenset({"0", "false", "no", "off"})
INIT_SCOPE = "/init.scope"
_BUSCTL_TIMEOUT_S = 5
_CONFIRM_TIMEOUT_S = 1.0
_CONFIRM_STEP_S = 0.02
# Characters systemd accepts in a unit name; anything else becomes "-".
_UNIT_CHARS = re.compile(r"[^A-Za-z0-9:_.\-]")
_MAX_OOM_SCORE_ADJ = 1000

# (property name, D-Bus signature, busctl value tokens)
Property = tuple[str, str, tuple[str, ...]]


@dataclass(frozen=True)
class ScopeResult:
    """Where the processes ended up: ``unit`` when moved, else ``reason``."""

    unit: str = ""
    reason: str = ""

    @property
    def moved(self) -> bool:
        return bool(self.unit)


def enabled() -> bool:
    return os.environ.get("NAVIN_PROCESS_SCOPE", "").strip().lower() not in _OFF


def cgroup_of(pid: int | str = "self") -> str | None:
    """The cgroup v2 path of a process; None when it has none or is gone."""
    try:
        with open(f"/proc/{pid}/cgroup", encoding="ascii", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        logger.debug("cgroup of {} is unreadable: {}", pid, exc)
        return None
    for line in lines:
        if line.startswith("0::"):
            return line[3:].strip() or "/"
    return None


def scope_name(prefix: str) -> str:
    """A valid transient unit name for ``prefix``."""
    return _UNIT_CHARS.sub("-", prefix).strip("-")[:200] + ".scope"


def _user_bus_reachable() -> bool:
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    return bool(runtime_dir) and os.path.exists(os.path.join(runtime_dir, "bus"))


def user_manager_persists() -> bool:
    """The user's systemd manager outlives login sessions (lingering is on).

    Without lingering, logind stops the manager, and every scope in it, when
    the user's last login session ends. Under WSL that session is one
    terminal (on 2026-11-05: pts/1), so a CLI moved there would die when that
    tab closes, even though it runs in another one; in ``init.scope`` it
    does not.
    """
    import pwd

    try:
        name = pwd.getpwuid(os.getuid()).pw_name
    except KeyError as exc:
        logger.debug("no user name for uid {}: {}", os.getuid(), exc)
        return False
    return os.path.exists(os.path.join("/var/lib/systemd/linger", name))


def _property_args(properties: Sequence[Property]) -> list[str]:
    args = [str(len(properties))]
    for name, signature, values in properties:
        args += [name, signature, *values]
    return args


def start_transient_scope_command(
    busctl: str, unit: str, properties: Sequence[Property],
) -> list[str]:
    """``busctl`` call creating ``unit`` with ``properties`` (no auxiliary units)."""
    return [
        busctl, "--user", f"--timeout={_BUSCTL_TIMEOUT_S}", "call",
        "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager", "StartTransientUnit",
        "ssa(sv)a(sa(sv))", unit, "fail", *_property_args(properties), "0",
    ]


def _unavailable() -> str:
    """Why no scope can be asked for here, or "" when it can."""
    if not sys.platform.startswith("linux"):
        return "not Linux"
    if not enabled():
        return "turned off (NAVIN_PROCESS_SCOPE=0)"
    if shutil.which("busctl") is None:
        return "busctl not found (no systemd)"
    if not _user_bus_reachable():
        return "no systemd user manager (no session bus)"
    return ""


def _wait_moved(pid: int, unit: str) -> bool:
    deadline = time.monotonic() + _CONFIRM_TIMEOUT_S
    while True:
        current = cgroup_of(pid)
        if current is None or current.endswith("/" + unit):
            # Gone in the meantime, or moved: either way nothing is left to do.
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_CONFIRM_STEP_S)


def start_scope(
    prefix: str,
    pids: Sequence[int],
    *,
    description: str,
    memory_max: int = 0,
    memory_swap_max: int | None = None,
    oom_policy: str = "continue",
) -> ScopeResult:
    """Put ``pids`` in a new transient scope of the user's systemd manager.

    ``memory_max`` (bytes, 0 for none) is enforced by the kernel inside the
    scope: past it the kernel kills the largest process of the scope only.
    Children started later by those processes land in the same scope.
    """
    reason = _unavailable()
    if reason:
        return ScopeResult(reason=reason)
    if not pids:
        return ScopeResult(reason="no process to move")
    unit = scope_name(prefix)
    properties: list[Property] = [
        ("PIDs", "au", (str(len(pids)), *(str(int(pid)) for pid in pids))),
        ("OOMPolicy", "s", (oom_policy,)),
        ("CollectMode", "s", ("inactive-or-failed",)),
        ("Description", "s", (description,)),
    ]
    if memory_max > 0:
        properties.append(("MemoryMax", "t", (str(int(memory_max)),)))
        if memory_swap_max is not None:
            properties.append(("MemorySwapMax", "t", (str(max(0, int(memory_swap_max))),)))
    command = start_transient_scope_command(shutil.which("busctl") or "busctl", unit, properties)
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            timeout=_BUSCTL_TIMEOUT_S + 1, env=host_tool_env(), **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return ScopeResult(reason=f"systemd did not answer within {_BUSCTL_TIMEOUT_S} s")
    except OSError as exc:
        return ScopeResult(reason=f"busctl could not run: {exc}")
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        last = detail[-1] if detail else f"exit code {done.returncode}"
        return ScopeResult(reason=f"systemd refused the scope: {last}")
    if not _wait_moved(int(pids[0]), unit):
        return ScopeResult(reason=f"systemd created {unit} but the process did not move into it")
    return ScopeResult(unit=unit)


def isolate_current_process(role: str) -> ScopeResult:
    """Move this process out of WSL's ``init.scope`` into a scope of its own.

    Only ``init.scope`` is left: a desktop session already starts each
    terminal app in a scope of its own, and an SSH session's processes must
    keep surviving a logout the way they did.
    """
    if not sys.platform.startswith("linux"):
        return ScopeResult(reason="not Linux")
    current = cgroup_of()
    if current is None:
        return ScopeResult(reason="no cgroup v2 information")
    if current != INIT_SCOPE:
        return ScopeResult(reason=f"already in {current}")
    if not user_manager_persists():
        # A browser may die with that manager (it is relaunched); a CLI must not.
        return ScopeResult(reason="the user's systemd manager stops with the last login session (lingering is off)")
    pid = os.getpid()
    return start_scope(f"navin-{role}-{pid}", [pid], description=f"Navin {role} (pid {pid})")


def isolation_message(result: ScopeResult) -> str:
    if result.moved:
        return f"moved to {result.unit} (OOMPolicy=continue)"
    return f"not isolated: {result.reason}"


def prepare_process(role: str, report: Callable[[str], object] | None = None) -> threading.Thread | None:
    """Start-of-process protections for a CLI or a gateway.

    Native memory tuning runs now, before the process starts its threads;
    the move to a scope of its own runs in a thread (a ``busctl`` call, a few
    milliseconds to a few tens) so the first screen is not delayed. Returns
    that thread, or None off Linux.
    """
    from navin.utils import native_memory, wsl_guard

    tuned = native_memory.tune_malloc()
    native_memory.start_trimmer()
    # From Windows: the WSL distributions running now; inside WSL as root:
    # this one. Once per distribution and per Navin version, in a thread.
    wsl_guard.start_in_background(role)
    if not sys.platform.startswith("linux"):
        return None

    def isolate() -> None:
        message = isolation_message(isolate_current_process(role))
        logger.info("Process isolation ({}): {}; memory: {}", role, message, tuned)
        if report is not None:
            report(f"{message}; memory: {tuned}")

    thread = threading.Thread(target=isolate, name="navin-process-scope", daemon=True)
    thread.start()
    return thread


def raise_oom_score(pid: int, value: int) -> bool:
    """Make the kernel's OOM killer pick ``pid`` (and what it starts next) first.

    Only ever raised: a lower value needs a privilege Navin does not have,
    and a process that already asked for more (Chromium does) keeps it.
    True when the value was written.
    """
    if value <= 0 or not sys.platform.startswith("linux"):
        return False
    path = f"/proc/{int(pid)}/oom_score_adj"
    wanted = min(_MAX_OOM_SCORE_ADJ, int(value))
    try:
        with open(path, encoding="ascii") as handle:
            current = int(handle.read().strip() or "0")
        if current >= wanted:
            return False
        with open(path, "w", encoding="ascii") as handle:
            handle.write(str(wanted))
    except (OSError, ValueError) as exc:
        # The process may have exited between the spawn and this write.
        logger.debug("oom_score_adj of {} left as it was: {}", pid, exc)
        return False
    return True


def wsl_oom_policy() -> str | None:
    """``OOMPolicy`` of systemd's ``init.scope`` under WSL; None elsewhere or unknown."""
    if not sys.platform.startswith("linux") or not os.environ.get("WSL_DISTRO_NAME"):
        return None
    systemctl = shutil.which("systemctl")
    if systemctl is None or cgroup_of(1) is None:
        return None
    try:
        done = subprocess.run(
            [systemctl, "show", "init.scope", "-p", "OOMPolicy", "--value"],
            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
            timeout=_BUSCTL_TIMEOUT_S, env=host_tool_env(), **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("systemctl show init.scope failed: {}", exc)
        return None
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


# Only init.scope, where WSL keeps every terminal, is changed: the default for
# the machine's other services stays as it was. Checked on systemd 255 (a
# [Scope] drop-in is applied) and 262 (init.scope picks it up on daemon-reload).
WSL_OOM_FIX = (
    "sudo mkdir -p /etc/systemd/system/init.scope.d && "
    "printf '[Scope]\\nOOMPolicy=continue\\n' | "
    "sudo tee /etc/systemd/system/init.scope.d/90-navin-oom.conf && sudo systemctl daemon-reload"
)
LINGER_FIX = "sudo loginctl enable-linger $USER"
