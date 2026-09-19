# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Short terminal previews; the original command output stays available."""

from __future__ import annotations

import re
import shlex

from rich.text import Text

COMMAND_PREVIEW_LINES = 6
GIT_PREVIEW_LINES = 16

_EXIT = re.compile(r"^(?:exit(?: code)?\s*:?|process exited with code)\s*(-?\d+)\s*$", re.I)
_WARNING = re.compile(r"\b([A-Za-z_]\w*Warning):\s*(.+)")
_COUNT = re.compile(r"\b(\d+)\s+(passed|failed|errors?|warnings?|skipped|deselected|xfailed|xpassed)\b", re.I)
_NODE_COUNT = re.compile(r"^(?:[ℹ#]\s*)?(pass|fail|cancelled|skipped)\s+(\d+)\b", re.I)


def command_exit_code(result: object) -> int | None:
    if isinstance(result, dict):
        for key in ("exit_code", "returncode"):
            code = result.get(key)
            if isinstance(code, int) and not isinstance(code, bool):
                return code
    if isinstance(result, str):
        for line in reversed(result.splitlines()):
            match = _EXIT.fullmatch(Text.from_ansi(line).plain.strip())
            if match:
                return int(match[1])
    return None


def is_git_command(name: str, arguments: dict | None) -> bool:
    """Recognize Git after cd/env/wrappers without matching 'echo git'."""
    if name == "git":
        return True
    args = arguments or {}
    command = args.get("command") or args.get("cmd") or ""
    if not isinstance(command, str):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    head = True
    for token in tokens:
        if token and all(char in ";&|()" for char in token):
            head = True
        elif head:
            if re.match(r"\w+=", token) or token in {"env", "command", "sudo"} or token.startswith("-"):
                continue
            if token.replace("\\", "/").rsplit("/", 1)[-1] in {"git", "git.exe"}:
                return True
            head = False
    return False


def output_kind(text: str) -> str:
    """Color explicit outcomes without inferring success from arbitrary prose."""
    clean = text.strip().strip("= ")
    exit_match = _EXIT.fullmatch(clean)
    if exit_match:
        return "success" if int(exit_match[1]) == 0 else "failure"
    node = _NODE_COUNT.match(clean)
    if node:
        if node[1].lower() in {"fail", "cancelled"} and int(node[2]):
            return "failure"
        return "info" if node[1].lower() == "skipped" else "success"
    counts = [(int(count), label.lower()) for count, label in _COUNT.findall(clean)]
    if counts:
        if any(count and label in {"failed", "error", "errors", "xpassed"} for count, label in counts):
            return "failure"
        if any(count and label.startswith("warning") for count, label in counts):
            return "warning"
        if any(count and label == "passed" for count, label in counts):
            return "success"
        return "info"
    if re.search(r"\b(?:\w*Error|Exception):|\berror TS\d+\b", clean, re.I) or re.match(
        r"^(?:FAILED\b|FAIL\b|ERROR\b|fatal:|npm (?:ERR!|error)\b|E\s+\S|[✗×✖])", clean,
    ):
        return "failure"
    if _WARNING.search(clean) or re.match(r"^(?:warning\b|warn\b|npm warn\b|⚠)", clean, re.I):
        return "warning"
    if re.match(r"^(?:PASS\b|OK$|[A-Z][A-Z0-9_]*_OK$|All checks passed\b|[✓✔])", clean):
        return "success"
    return "ctx"


def compact_command_rows(
    rows: list[tuple[int | None, str, str]], *, limit: int = COMMAND_PREVIEW_LINES,
    cache: dict | None = None,
) -> list[tuple[int | None, str, str]]:
    """Keep failures, test totals, repeated warning counts and the exit status."""
    candidates: list[tuple[int, int, str, str]] = []
    warnings: dict[str, tuple[int, int]] = {}
    for index, (_, kind, raw) in enumerate(rows):
        cached = cache.get(raw) if cache is not None else None
        if cached is not None and cached[0] == kind:
            parsed = cached[1]
        else:
            parsed = _command_line(kind, raw)
            if cache is not None:
                cache[raw] = (kind, parsed)
        if parsed is None:
            continue
        priority, semantic, line, warning = parsed
        if warning:
            first, count = warnings.get(line, (index, 0))
            warnings[line] = (first, count + 1)
        else:
            candidates.append((index, priority, semantic, line))
    if cache is not None and len(cache) > max(256, len(rows) * 2):
        retained = {raw for _, _, raw in rows}
        for raw in list(cache):
            if raw not in retained:
                del cache[raw]
    for message, (index, count) in warnings.items():
        prefix = f"(x{count}) " if count > 1 else ""
        candidates.append((index, 80, "warning", prefix + message))
    # Newest ordinary lines win, while an earlier failure outranks log chatter.
    selected = sorted(candidates, key=lambda row: (row[1], row[0]), reverse=True)[:limit]
    selected.sort(key=lambda row: row[0])
    return [(number, kind, line) for number, (_, _, kind, line) in enumerate(selected, 1)]


def _command_line(kind: str, raw: str) -> tuple[int, str, str, bool] | None:
    """Classify a line once, even while a retained log tail keeps growing."""
    line = Text.from_ansi(raw).plain.strip() if "\x1b" in raw else raw.strip()
    if not line or re.fullmatch(r"[=_.\-\s]+", line):
        return None
    warning = _WARNING.search(line)
    if warning and kind != "error":
        message = re.split(r"\s+- for details, see |\s+You can register custom marks", warning[2])[0]
        return 80, "warning", f"{warning[1]}: {message}", True
    if re.match(r"^@pytest\.mark\.|^\S+:\d+:?$|^https?://docs\.pytest\.", line):
        return None
    semantic = "failure" if kind == "error" else output_kind(line)
    priority = 120 if _EXIT.fullmatch(line) else {
        "failure": 100, "warning": 80, "success": 70, "info": 60,
    }.get(semantic, 10)
    return priority, semantic, line.strip("= ") if _COUNT.search(line) else line, False
