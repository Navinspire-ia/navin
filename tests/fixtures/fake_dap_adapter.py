#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Minimal DAP adapter used by Phase 5 unit tests (stdio, Content-Length)."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path


def _read_message() -> dict | None:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header += chunk
    length = 0
    for line in header.decode("utf-8", errors="replace").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write(message: dict) -> None:
    raw = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    sys.stdout.buffer.flush()


_seq = 1
_thread_id = 1
_stop_soon: threading.Event | None = None


def _event(event: str, body: dict | None = None) -> None:
    global _seq
    _seq += 1
    _write({"seq": _seq, "type": "event", "event": event, "body": body or {}})


def _respond(req: dict, *, body: dict | None = None, success: bool = True) -> None:
    global _seq
    _seq += 1
    _write(
        {
            "seq": _seq,
            "type": "response",
            "request_seq": req.get("seq"),
            "success": success,
            "command": req.get("command"),
            "body": body or {},
        }
    )


def main() -> None:
    global _stop_soon
    _stop_soon = threading.Event()
    launched = False
    while True:
        msg = _read_message()
        if msg is None:
            break
        if msg.get("type") != "request":
            continue
        command = msg.get("command")
        if command == "initialize":
            _respond(
                msg,
                body={
                    "supportsConfigurationDoneRequest": True,
                    "supportsEvaluateForHovers": True,
                },
            )
            _event("initialized")
        elif command == "setBreakpoints":
            args = msg.get("arguments") or {}
            bps = args.get("breakpoints") or []
            _respond(
                msg,
                body={
                    "breakpoints": [
                        {"line": bp.get("line"), "verified": True} for bp in bps
                    ]
                },
            )
        elif command == "launch":
            launched = True
            _respond(msg, body={})
            _event("thread", {"reason": "started", "threadId": _thread_id})
        elif command == "configurationDone":
            _respond(msg, body={})
            if launched:
                # Simulate hitting a breakpoint shortly after start.
                def _hit() -> None:
                    time.sleep(0.05)
                    _event(
                        "stopped",
                        {
                            "reason": "breakpoint",
                            "threadId": _thread_id,
                            "allThreadsStopped": True,
                        },
                    )

                threading.Thread(target=_hit, daemon=True).start()
        elif command == "stackTrace":
            source_path = str(Path.cwd() / "app.py")
            _respond(
                msg,
                body={
                    "stackFrames": [
                        {
                            "id": 10,
                            "name": "main",
                            "line": 3,
                            "column": 1,
                            "source": {
                                "name": "app.py",
                                "path": source_path,
                            },
                        }
                    ],
                    "totalFrames": 1,
                },
            )
        elif command == "scopes":
            _respond(
                msg,
                body={
                    "scopes": [
                        {
                            "name": "Locals",
                            "variablesReference": 100,
                            "expensive": False,
                        }
                    ]
                },
            )
        elif command == "variables":
            _respond(
                msg,
                body={
                    "variables": [
                        {
                            "name": "x",
                            "value": "42",
                            "type": "int",
                            "variablesReference": 0,
                        }
                    ]
                },
            )
        elif command == "evaluate":
            expr = (msg.get("arguments") or {}).get("expression") or ""
            _respond(msg, body={"result": f"eval({expr})", "type": "str"})
        elif command in {"continue", "next", "stepIn", "stepOut"}:
            _respond(msg, body={"allThreadsContinued": True})
            _event("continued", {"threadId": _thread_id})
            if command == "continue":

                def _rehit() -> None:
                    time.sleep(0.05)
                    _event(
                        "stopped",
                        {
                            "reason": "breakpoint",
                            "threadId": _thread_id,
                        },
                    )

                threading.Thread(target=_rehit, daemon=True).start()
        elif command == "disconnect":
            _respond(msg, body={})
            _event("terminated")
            break
        else:
            _respond(msg, body={})


if __name__ == "__main__":
    main()
