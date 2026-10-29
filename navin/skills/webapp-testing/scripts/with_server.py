#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Start one or more servers, wait for their ports, run a command, stop everything.

Examples:
    with_server.py --server "npm run dev -- --port 4300" --port 4300 -- python3 smoke.py
    with_server.py --server "python3 api.py" --port 8100 \\
                   --server "npm run dev -- --port 4300" --port 4300 \\
                   --timeout 60 -- pytest tests/e2e

Each --server pairs with the --port given in the same position. The command
after "--" runs once every port accepts a TCP connection. Servers are stopped
with SIGTERM, then SIGKILL after 5 seconds, even when the inner command fails.
The exit code of the inner command is propagated; 124 means a server did not
open its port in time, 2 means bad arguments.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def wait_for_port(port: int, timeout: float, proc: subprocess.Popen) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.25)
    return False


def stop_all(procs: list[subprocess.Popen]) -> None:
    """Stop every server group: SIGTERM, wait 5 s, then SIGKILL.

    A ProcessLookupError means the group already exited between poll() and the
    signal (the race is inherent to signalling); the process is then reaped with
    wait() so the exit is observed, not ignored.
    """
    for proc in reversed(procs):
        if proc.poll() is not None:
            continue
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.wait()
            continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                print(f"[with_server] {proc.args!r} exited while being killed", file=sys.stderr)
            proc.wait()


def parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    if "--" in argv:
        split = argv.index("--")
        own, inner = argv[:split], argv[split + 1 :]
    else:
        own, inner = argv, []
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="%(prog)s --server CMD --port N [--server CMD --port N ...] [--timeout S] -- COMMAND...",
    )
    parser.add_argument("--server", action="append", default=[], metavar="CMD", help="shell command starting a server (repeatable)")
    parser.add_argument("--port", action="append", default=[], type=int, metavar="N", help="port the matching --server must open (repeatable)")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="S", help="seconds to wait for each port (default 30)")
    args = parser.parse_args(own)
    if not args.server:
        parser.error("at least one --server is required")
    if len(args.server) != len(args.port):
        parser.error(f"{len(args.server)} --server but {len(args.port)} --port; they must pair up")
    if not inner:
        parser.error("no command given after '--'")
    return args, inner


def _no_window() -> dict:
    """No console window per child on Windows, through navin's helper when navin is importable."""
    try:
        from navin.utils.proc import no_window_kwargs

        return dict(no_window_kwargs())
    except ImportError:
        return {"creationflags": 0x08000000} if os.name == "nt" else {}


def main(argv: list[str] | None = None) -> int:
    args, inner = parse_args(sys.argv[1:] if argv is None else argv)
    procs: list[subprocess.Popen] = []
    try:
        for cmd, port in zip(args.server, args.port):
            if port_open(port):
                print(f"[with_server] port {port} is already open; refusing to start '{cmd}' on top of it", file=sys.stderr)
                return 124
            print(f"[with_server] starting: {cmd}", file=sys.stderr)
            proc = subprocess.Popen(cmd, shell=True, start_new_session=True, **_no_window())
            procs.append(proc)
            if not wait_for_port(port, args.timeout, proc):
                state = f"exited with {proc.returncode}" if proc.poll() is not None else f"did not open port {port} within {args.timeout:g}s"
                print(f"[with_server] server '{cmd}' {state}", file=sys.stderr)
                return 124
            print(f"[with_server] port {port} ready", file=sys.stderr)
        print(f"[with_server] running: {' '.join(inner)}", file=sys.stderr)
        result = subprocess.run(inner, **_no_window())
        return result.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        stop_all(procs)
        if procs:
            print(f"[with_server] stopped {len(procs)} server(s)", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
