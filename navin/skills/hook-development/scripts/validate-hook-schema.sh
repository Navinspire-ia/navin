#!/usr/bin/env bash
# Validate a hooks.json file for Navin.
# Usage: validate-hook-schema.sh <hooks.json>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <hooks.json>" >&2
  exit 2
fi

python3 - "$1" <<'PY'
import json, re, sys
from pathlib import Path

EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop"}
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
path = Path(sys.argv[1])
errors, warnings = [], []
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError) as exc:
    print(f"[CRITICAL] {path}: {exc}")
    sys.exit(1)
table = data.get("hooks", data) if isinstance(data, dict) else None
if not isinstance(table, dict):
    print(f"[CRITICAL] {path}: expected an object of events")
    sys.exit(1)
count = 0
for event, entries in table.items():
    if event not in EVENTS:
        errors.append(f"unknown event {event!r} (Navin dispatches {', '.join(sorted(EVENTS))})")
        continue
    if not isinstance(entries, list):
        errors.append(f"{event}: must be a list")
        continue
    for i, entry in enumerate(entries):
        where = f"{event}[{i}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            errors.append(f"{where}: needs a 'hooks' list")
            continue
        matcher = entry.get("matcher")
        if matcher is not None:
            if event not in TOOL_EVENTS:
                warnings.append(f"{where}: matcher is ignored for {event}")
            elif not isinstance(matcher, str):
                errors.append(f"{where}: matcher must be a string")
            elif matcher not in ("", "*"):
                try:
                    re.compile(matcher)
                except re.error as exc:
                    errors.append(f"{where}: invalid matcher regex: {exc}")
        for j, hook in enumerate(entry["hooks"]):
            h = f"{where}.hooks[{j}]"
            if not isinstance(hook, dict):
                errors.append(f"{h}: must be an object")
                continue
            kind = hook.get("type", "command")
            if kind != "command":
                warnings.append(f"{h}: type {kind!r} is not run by Navin (use 'command')")
                continue
            command = hook.get("command")
            if not isinstance(command, str) or not command.strip():
                errors.append(f"{h}: 'command' is required")
                continue
            if re.search(r"(^|\s)/(home|Users)/", command):
                warnings.append(f"{h}: absolute path; use ${{NAVIN_PLUGIN_ROOT}} or a relative path")
            timeout = hook.get("timeout")
            if timeout is not None and (not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600):
                errors.append(f"{h}: timeout must be a number between 1 and 600")
            elif event in TOOL_EVENTS and isinstance(timeout, (int, float)) and timeout > 30:
                warnings.append(f"{h}: {timeout}s on a tool event slows every call")
            count += 1
for w in warnings:
    print(f"[WARNING] {w}")
for e in errors:
    print(f"[CRITICAL] {e}")
print(f"{path}: {count} command hook(s), {len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
PY
