#!/usr/bin/env python3
"""PreToolUse hook (matcher: exec): steer slow shell searches to ripgrep.

Reads the hook payload as JSON on stdin. When the shell command starts with a
plain ``grep`` (not in a pipe) or ``find <dir> -name``, it exits 2 and prints
one advice line per rule on stderr, which refuses the call and tells the agent
what to run instead. Anything else exits 0.

Register it in hooks.json::

    {"PreToolUse": [{"matcher": "exec", "hooks": [
      {"type": "command", "command": "python3 ${NAVIN_PLUGIN_ROOT}/hooks/scripts/shell-advice.py"}]}]}
"""

from __future__ import annotations

import json
import re
import sys

RULES = (
    (re.compile(r"^\s*grep\b(?!.*\|)"), "Prefer 'rg <pattern>' over grep: faster and respects .gitignore."),
    (re.compile(r"^\s*find\s+\S+\s+-i?name\b"), "Prefer 'rg --files -g <glob>' over 'find -name'."),
)
SHELL_TOOLS = {"exec", "shell", "bash"}


def advice(command: str) -> list[str]:
    return [text for pattern, text in RULES if pattern.search(command)]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError as exc:
        print(f"shell-advice: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1  # a non-blocking failure: the call goes on
    if not isinstance(data, dict) or str(data.get("tool_name", "")).lower() not in SHELL_TOOLS:
        return 0
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or "")
    issues = advice(command)
    if not issues:
        return 0
    for line in issues:
        print(f"- {line}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
