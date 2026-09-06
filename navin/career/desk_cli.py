"""Career desk CLI: same store as Studio #/career, Tauri, and the career tool.

`navin career ...` and `python -m navin.career.desk_cli ...` are the same
entry. Vite / the gateway send JSON on stdin (action is argv[1]). A human
in the terminal can pass flags instead:

  navin career start --kind daily --hour 9
  navin career stop
  navin career schedule --kind weekdays --hour 8 --minute 30
  navin career tick
  navin career watch
  navin career snapshot

Heartbeat is watch only. Never scrape LinkedIn. Never apply.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from navin.career.errors import CareerError
from navin.webui.career_api import handle_career_action

HELP = """Career desk - same store as Studio #/career, Tauri, and the career tool.

  navin career snapshot
  navin career start --kind daily --hour 9 --minute 0
  navin career stop
  navin career schedule --kind weekdays --hour 8 --minute 30
  navin career tick
  navin career watch

Same commands via python -m navin.career.desk_cli ...

Flags for start/schedule: --kind daily|weekdays|weekend|weekly|monthly
  --hour 0-23  --minute 0-59  --weekday 1-7  --day 1-28|last  --tz IANA
  --run-now (hunt once after start)  --force (tick even if paused)

JSON stdin still works (Vite / Tauri fallback):
  echo '{"schedule":{"kind":"daily","hour":9}}' | navin career start
  echo '{"schedule":{"kind":"daily","hour":9}}' | python -m navin.career.desk_cli start

The gateway hunts on that calendar while it is up. Heartbeat only watches.
Never scrape LinkedIn. Never apply.
"""

ALIASES = {
    "pause": "stop",
    "status": "snapshot",
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
            raise CareerError(f"unknown career flag {key}", status=400)
        name = key[2:].replace("-", "_")
        raw = args[i + 1]
        i += 2
        if name == "schedule":
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise CareerError("schedule must be JSON", status=400) from exc
            if not isinstance(parsed, dict):
                raise CareerError("schedule must be an object", status=400)
            body["schedule"] = parsed
            continue
        if name in {"hour", "minute", "weekday"}:
            try:
                schedule[name] = int(raw)
            except ValueError as exc:
                raise CareerError(f"{name} must be an integer", status=400) from exc
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
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise CareerError("invalid career payload", status=400) from exc
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
        payload = handle_career_action(action, body)
    except CareerError as exc:
        print(json.dumps({"error": exc.message, "status": exc.status}))
        return 2 if exc.status < 500 else 1
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
