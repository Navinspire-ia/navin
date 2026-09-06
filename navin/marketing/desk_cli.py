"""Marketing desk CLI: same store as Studio #/marketing, Tauri, and the marketing tool.

`navin marketing ...` and `python -m navin.marketing.desk_cli ...` are the same
entry. Vite / the gateway send JSON on stdin (action is argv[1]). A human
in the terminal can pass flags instead:

  navin marketing pipeline --goal "1000 inscriptions"
  navin marketing start --kind daily --hour 9
  navin marketing stop
  navin marketing tick
  navin marketing watch
  navin marketing snapshot

Heartbeat is watch only. Never invent traffic or published posts.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from navin.marketing.errors import MarketingError
from navin.webui.marketing_desk_api import handle_marketing_action

HELP = """Marketing desk - same store as Studio #/marketing, Tauri, and the marketing tool.

  navin marketing snapshot
  navin marketing harvest --site https://example.com
  navin marketing produce --kinds image
  navin marketing ship
  navin marketing seo
  navin marketing social
  navin marketing ads
  navin marketing pipeline --goal "1000 inscriptions" --days 30
  navin marketing start --kind daily --hour 9 --minute 0
  navin marketing stop
  navin marketing schedule --kind weekdays --hour 8 --minute 30
  navin marketing tick
  navin marketing watch

Same commands via python -m navin.marketing.desk_cli ...

Flags for start/schedule: --kind daily|weekdays|weekend|weekly|monthly
  --hour 0-23  --minute 0-59  --weekday 1-7  --day 1-28|last  --tz IANA
  --run-now (cycle once after start)  --force (tick even if paused)

JSON stdin still works (Vite / Tauri fallback):
  echo '{"goal":"1000 inscriptions"}' | navin marketing pipeline
  echo '{"schedule":{"kind":"daily","hour":9}}' | python -m navin.marketing.desk_cli start

The gateway cycles on that calendar while it is up. Heartbeat only watches.
Never invent traffic, spend or published posts.
"""

ALIASES = {
    "pause": "stop",
    "status": "snapshot",
    "social-pack": "ship",
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
        if key in {"--no-generate", "--skip-generate"}:
            body["generate"] = False
            i += 1
            continue
        if not key.startswith("--") or i + 1 >= len(args):
            raise MarketingError(f"unknown marketing flag {key}", status=400)
        name = key[2:].replace("-", "_")
        raw = args[i + 1]
        i += 2
        if name == "schedule":
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise MarketingError("schedule must be JSON", status=400) from exc
            if not isinstance(parsed, dict):
                raise MarketingError("schedule must be an object", status=400)
            body["schedule"] = parsed
            continue
        if name in {"hour", "minute", "weekday", "days", "signups"}:
            try:
                value = int(raw)
            except ValueError as exc:
                raise MarketingError(f"{name} must be an integer", status=400) from exc
            if name in {"hour", "minute", "weekday"}:
                schedule[name] = value
            else:
                body[name] = value
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    token = args[0] if args else "snapshot"
    action = ALIASES.get(token, token)
    if action == "help":
        print(HELP)
        return 0
    rest = args[1:]
    try:
        if rest:
            body = _parse_flags(rest)
        else:
            raw = sys.stdin.read()
            if not raw.strip():
                body = {}
            else:
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    print(json.dumps({"error": "invalid marketing payload"}))
                    return 1
                body = parsed if isinstance(parsed, dict) else {}
        payload = handle_marketing_action(action, body)
    except MarketingError as exc:
        print(json.dumps({"error": exc.message, "status": exc.status}))
        return 2 if exc.status < 500 else 1
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
