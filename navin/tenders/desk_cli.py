# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tenders desk CLI: same store as Studio #/tenders, Tauri, and the tenders tool.

`navin tenders ...` and `python -m navin.tenders.desk_cli ...` are the same
entry. Vite / the gateway send JSON on stdin (action is argv[1]). A human
in the terminal can pass flags instead:

  navin tenders start --kind daily --hour 9
  navin tenders stop
  navin tenders schedule --kind weekdays --hour 8 --minute 30
  navin tenders tick
  navin tenders watch
  navin tenders snapshot

Heartbeat is follow/watch only. The desk loop hunts official notices.
Never send a buyer mail from the loop or from heartbeat.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from navin.tenders.errors import TenderError
from navin.webui.tenders_api import handle_tenders_action

HELP = """Tenders desk - same store as Studio #/tenders, Tauri, and the tenders tool.

  navin tenders snapshot
  navin tenders start --kind daily --hour 9 --minute 0
  navin tenders stop
  navin tenders schedule --kind weekdays --hour 8 --minute 30
  navin tenders tick
  navin tenders watch

Same commands via python -m navin.tenders.desk_cli ...

Flags for start/schedule: --kind daily|weekdays|weekend|weekly|monthly
  --hour 0-23  --minute 0-59  --weekday 1-7  --day 1-28|last  --tz IANA
  --run-now (hunt once after start)  --force (tick even if paused)

JSON stdin still works (Vite / Tauri fallback):
  echo '{"schedule":{"kind":"daily","hour":9}}' | navin tenders start
  echo '{"schedule":{"kind":"daily","hour":9}}' | python -m navin.tenders.desk_cli start

The gateway hunts on that calendar while it is up. Heartbeat only follows.
Never send a buyer mail from the loop or from heartbeat.
"""

ALIASES = {
    "pause": "stop",
    "status": "snapshot",
    "follow": "watch",
    "follow-up": "watch",
    "help": "help",
    "-h": "help",
    "--help": "help",
}


def _parse_flags(args: list[str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    schedule: dict[str, Any] = {}
    i = 0
    while i < len(args):
        key = args[i]
        if key in {"--run-now", "--run_now"}:
            body["run_now"] = True
            i += 1
            continue
        if key == "--force":
            body["force"] = True
            i += 1
            continue
        if key == "--no-force":
            body["force"] = False
            i += 1
            continue
        if not key.startswith("--") or i + 1 >= len(args):
            raise TenderError(f"unknown tenders flag {key}", status=400)
        name = key[2:].replace("-", "_")
        raw = args[i + 1]
        i += 2
        if name == "schedule":
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise TenderError("schedule must be JSON", status=400) from exc
            if not isinstance(parsed, dict):
                raise TenderError("schedule must be an object", status=400)
            body["schedule"] = parsed
            continue
        if name in {"hour", "minute", "weekday"}:
            try:
                schedule[name] = int(raw)
            except ValueError as exc:
                raise TenderError(f"{name} must be an integer", status=400) from exc
            continue
        if name == "day":
            schedule[name] = raw if raw.strip().lower() == "last" else int(raw)
            continue
        if name in {"kind", "tz"}:
            schedule[name] = raw
            continue
        body[name] = raw
    if schedule and "schedule" not in body:
        body["schedule"] = schedule
    return body


def _stdin_body() -> dict[str, Any]:
    """JSON from Vite / Tauri / a pipe. Never block on an interactive TTY."""
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise TenderError("invalid tenders payload", status=400) from exc
    return parsed if isinstance(parsed, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    token = args[0] if args else "snapshot"
    action = ALIASES.get(token, token)
    if action == "help":
        print(HELP)
        return 0
    rest = args[1:]
    try:
        body = _parse_flags(rest) if rest else _stdin_body()
        payload = handle_tenders_action(action, body)
    except TenderError as exc:
        if exc.message == "invalid tenders payload":
            print(json.dumps({"error": exc.message}))
            return 1
        print(json.dumps({"error": exc.message, "status": exc.status}))
        return 2 if exc.status < 500 else 1
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
