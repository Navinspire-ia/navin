"""Keep a background gateway listening, or start it again.

Never SIGTERM a live gateway because /health was slow. That is what used to
take the WebUI down: a 2s skills or git poll blocked the loop, the probe
timed out, restart() killed the process, and Vite answered HTTP 500.

The rule is: if the port accepts a TCP connection, leave it alone. If the
port is closed and no live child exists, start a new one.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from typing import Any

from navin.process_runtime import ProcessStartOptions

PortProbe = Callable[[str, int, float], bool]


def port_is_open(host: str, port: int, timeout_s: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _reap_children() -> None:
    # Zombies are a POSIX notion; Windows has neither waitpid(-1) nor WNOHANG,
    # and the watchdog loop must not die on its first tick there.
    waitpid = getattr(os, "waitpid", None)
    wnohang = getattr(os, "WNOHANG", None)
    if waitpid is None or wnohang is None:
        return
    try:
        while True:
            pid, _status = waitpid(-1, wnohang)
            if pid == 0:
                break
    except (ChildProcessError, OSError):
        return


def ensure_gateway_health(
    runtime: Any,
    options: ProcessStartOptions,
    *,
    host: str = "127.0.0.1",
    probe_port: PortProbe = port_is_open,
    timeout_s: float = 0.4,
) -> str:
    """Return ``healthy``, ``started``, or a ``*_failed`` reason.

    Does not call ``restart()``. Killing a busy process is worse than a
    slow poll: the UI then loses bootstrap, sessions, and the websocket.
    """
    if probe_port(host, options.port, timeout_s):
        return "healthy"
    _reap_children()
    if runtime.status().running:
        # Child is alive but not bound yet (startup) or still draining.
        return "starting"
    result = runtime.start_background(options)
    if result.ok:
        return "started"
    if result.message.endswith("already_running"):
        return "already_running"
    return "start_failed"


def run_watchdog(
    runtime: Any,
    options: ProcessStartOptions,
    *,
    host: str = "127.0.0.1",
    interval_s: float = 3.0,
    probe_port: PortProbe = port_is_open,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Probe the listen port forever until *should_stop* says otherwise."""
    misses = 0
    while should_stop is None or not should_stop():
        _reap_children()
        if probe_port(host, options.port, 0.4):
            misses = 0
            sleep(interval_s)
            continue
        misses += 1
        if misses < 2:
            sleep(interval_s)
            continue
        action = ensure_gateway_health(
            runtime,
            options,
            host=host,
            probe_port=probe_port,
        )
        misses = 0
        if action == "started":
            sleep(max(interval_s, 5.0))
        else:
            sleep(interval_s)
