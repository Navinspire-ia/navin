# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool hint formatting for concise, human-readable tool call display."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from navin.utils.path import abbreviate_path

# Registry: tool_name -> (key_args, template, is_path, is_command)
_TOOL_FORMATS: dict[str, tuple[list[str], str, bool, bool]] = {
    "read_file":  (["path", "file_path"],              "read {}",     True,  False),
    "write_file": (["path", "file_path"],              "write {}",    True,  False),
    "edit_file":  (["file_path", "path"],              "edit {}",     True,  False),
    "edit":       (["file_path", "path"],              "edit {}",     True,  False),
    "apply_patch": (["path", "file_path"],             "edit {}",     True,  False),
    "find_files": (["query", "glob", "path"],           "find {}",     False, False),
    "grep":       (["pattern"],                        'grep "{}"',   False, False),
    "exec":       (["command"],                        "$ {}",        False, True),
    "list_exec_sessions": ([],                          "exec sessions", False, False),
    "web_search": (["query"],                          'search "{}"', False, False),
    "web_fetch":  (["url"],                            "fetch {}",    True,  False),
    "list_dir":   (["path"],                           "ls {}",       True,  False),
    "browser":    (["url", "selector", "text", "action"], "browser {}",  False, False),
    "verify":     (["path", "action"],                 "check {}",    False, False),
}

# Quiet verbs for the TUI row. No key=value, no JSON, no full paths.
_TOOL_VERBS: dict[str, str] = {
    "read_file": "read",
    "write_file": "create",
    "edit_file": "edit",
    "edit": "edit",
    "apply_patch": "edit",
    "grep": "grep",
    "glob": "find",
    "find_files": "find",
    "list_dir": "list",
    "exec": "run",
    "shell": "run",
    "exec_command": "run",
    "execute_command": "run",
    "web_search": "search",
    "web_fetch": "fetch",
    "browser": "browse",
    "ask_user": "ask",
    "message": "ask",
    "verify": "check",
    "board": "board",
    "todo": "todo",
    "git": "git",
    "memory": "memory",
    "cron": "cron",
    "spawn": "spawn",
}

# These already have a dedicated card (choice / composer). A tool row is noise.
CARD_ONLY_TOOLS = frozenset({"ask_user", "message"})

# Matches file paths embedded in shell commands, including quoted paths with spaces.
_PATH_IN_CMD_RE = re.compile(
    r'"(?P<double>(?:[A-Za-z]:[/\\]|~/|/)[^"]+)"'
    r"|'(?P<single>(?:[A-Za-z]:[/\\]|~/|/)[^']+)'"
    r"|(?P<bare>(?:[A-Za-z]:[/\\]|~/|(?<=\s)/)[^\s;&|<>\"']+)"
)
_SLEEP_RE = re.compile(r"^sleep\s+(\d+(?:\.\d+)?)$", re.I)
_CD_RE = re.compile(r"^cd\s+(.+)$")
_PYTHON_BIN_RE = re.compile(r"(?:\S+/)?(?:\.venv|venv)/bin/python\d*(?:\.\d+)*")
_SHELL_SPLIT_RE = re.compile(r"\s*(?:&&|;)\s*")
_TRUTHY = {True, "true", "True", "1", 1}


def format_tool_hints(tool_calls: list, max_length: int = 120) -> str:
    """Format tool calls as concise hints with smart abbreviation."""
    if not tool_calls:
        return ""

    formatted = []
    for tc in tool_calls:
        name = getattr(tc, "name", None)
        if not isinstance(name, str) or not name:
            # Degenerate/malformed tool call (e.g. a model emits name=None);
            # skip it instead of raising AttributeError on the whole turn.
            continue
        fmt = _TOOL_FORMATS.get(name)
        if name in {"exec", "shell", "exec_command", "execute_command"}:
            formatted.append(_fmt_exec(tc, max_length))
        elif fmt:
            formatted.append(_fmt_known(tc, fmt, max_length))
        elif name.startswith("mcp_"):
            formatted.append(_fmt_mcp(tc, max_length))
        else:
            formatted.append(_fmt_fallback(tc, max_length))

    hints = []
    for hint in formatted:
        if hints and hints[-1][0] == hint:
            hints[-1] = (hint, hints[-1][1] + 1)
        else:
            hints.append((hint, 1))

    return ", ".join(
        f"{h} \u00d7 {c}" if c > 1 else h for h, c in hints
    )


def _get_args(tc) -> dict:
    """Extract args dict from tc.arguments, handling list/dict/None/empty."""
    if tc.arguments is None:
        return {}
    if isinstance(tc.arguments, list):
        return tc.arguments[0] if tc.arguments else {}
    if isinstance(tc.arguments, dict):
        return tc.arguments
    return {}


def _extract_arg(tc, key_args: list[str]) -> str | None:
    """Extract the first available value from preferred key names."""
    args = _get_args(tc)
    if not isinstance(args, dict):
        return None
    for key in key_args:
        val = args.get(key)
        if isinstance(val, str) and val:
            return val
    for val in args.values():
        if isinstance(val, str) and val:
            return val
    return None


def format_seconds(value: object) -> str:
    """300 -> '5 min', 30 -> '30s'. Empty when the value is not a duration."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    if seconds < 60:
        whole = int(seconds)
        return f"{whole}s" if seconds == whole else f"{seconds:g}s"
    mins = int(round(seconds / 60))
    return f"{mins} min"


def _last_path_bits(path: str, keep: int = 2) -> str:
    cleaned = path.strip().strip("'\"")
    parts = [part for part in re.split(r"[\\/]", cleaned) if part and part != "~"]
    if not parts:
        return cleaned
    if len(parts) <= keep:
        return "/".join(parts)
    if keep == 1:
        return parts[-1]
    return ".../" + "/".join(parts[-keep:])


def humanize_shell_command(cmd: str, max_len: int = 88) -> str:
    """Turn a raw exec string into the action a human can read at a glance.

    ``cd .../db-migration && .venv/bin/python migration-v2.py``
    becomes ``in db-migration · python migration-v2.py``.
    ``sleep 120 && tail -5 .../run.log`` becomes ``wait 2 min · tail -5 .../run.log``.
    """
    raw = " ".join((cmd or "").strip().split())
    if not raw:
        return "shell"
    chunks = [chunk.strip() for chunk in _SHELL_SPLIT_RE.split(raw) if chunk.strip()]
    cwd = ""
    waits: list[str] = []
    action = chunks[-1] if chunks else raw
    for index, chunk in enumerate(chunks):
        cd_match = _CD_RE.match(chunk)
        if cd_match and index < len(chunks) - 1:
            cwd = cd_match.group(1).strip().strip("'\"")
            continue
        sleep_match = _SLEEP_RE.match(chunk)
        if sleep_match:
            pretty = format_seconds(sleep_match.group(1))
            if pretty:
                waits.append(f"wait {pretty}")
            continue
        action = chunk
    action = _PYTHON_BIN_RE.sub("python", action)
    action = action.strip() or raw
    action = _abbreviate_command(action, max_len=max(36, max_len - 18))
    bits: list[str] = []
    if cwd:
        bits.append("in " + _last_path_bits(cwd, keep=1))
    bits.extend(waits)
    bits.append(action)
    text = " · ".join(bits)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def exec_flags(arguments: dict) -> str:
    """Human labels for exec kwargs (not ``background=true timeout=300``)."""
    flags: list[str] = []
    if arguments.get("background") in _TRUTHY:
        flags.append("background")
    timeout = arguments.get("timeout")
    if timeout in (None, "", 0, "0"):
        timeout = arguments.get("timeout_s")
    pretty = format_seconds(timeout) if timeout not in (None, "", 0, "0") else ""
    if pretty:
        flags.append(pretty)
    return " · ".join(flags)


def tool_verb(name: str) -> str:
    """One word for the row: read, edit, grep, create."""
    base = (name or "").strip().lower()
    if not base:
        return "tool"
    if base in _TOOL_VERBS:
        return _TOOL_VERBS[base]
    for key, verb in _TOOL_VERBS.items():
        if base.startswith(key):
            return verb
    if base.endswith("_file"):
        return base[:-5].replace("_", " ") or "file"
    return base.replace("_", " ")


def tool_target(arguments: dict | None, *, limit: int = 36) -> str:
    """File name or short query. Never a JSON blob or a home path dump."""
    args = arguments if isinstance(arguments, dict) else {}
    for key in ("path", "file_path", "file", "filename"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return Path(val.replace("\\", "/")).name or val.strip()
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.strip():
                return Path(first.replace("\\", "/")).name
    for key in ("pattern", "query", "command", "cmd", "url", "name"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            return text if len(text) <= limit else text[: limit - 1] + "…"
    return ""


def describe_tool_line(
    name: str,
    arguments: dict | None,
    *,
    done: bool,
) -> str:
    """Running: ``read  foo.py``. Done: ``read``. No args dump."""
    verb = tool_verb(name)
    if done:
        return verb
    target = tool_target(arguments)
    return f"{verb}  {target}" if target else verb


def format_tool_detail(
    name: str,
    arguments: dict | None,
    *,
    result: Any = None,
    error: str | None = None,
    output_lines: list[str] | None = None,
) -> str:
    """Click-to-expand text. Sentences and paths, never a JSON dump."""
    args = arguments if isinstance(arguments, dict) else {}
    lines: list[str] = []
    path = ""
    for key in ("path", "file_path", "file", "filename"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            path = val.strip()
            break
        if isinstance(val, list) and val and isinstance(val[0], str):
            path = val[0].strip()
            break
    if path:
        lines.append(abbreviate_path(path, max_len=72))
    pattern = args.get("pattern") or args.get("query")
    if isinstance(pattern, str) and pattern.strip():
        lines.append(pattern.strip())
    command = args.get("command") or args.get("cmd")
    if isinstance(command, str) and command.strip():
        flags = exec_flags(args)
        pretty = humanize_shell_command(command, max_len=88)
        lines.append(f"{pretty} · {flags}" if flags else pretty)
    url = args.get("url")
    if isinstance(url, str) and url.strip():
        lines.append(url.strip())
    question = args.get("question")
    if isinstance(question, str) and question.strip():
        lines.append(" ".join(question.split()))
    if error:
        lines.append(error.splitlines()[0][:240])
    elif output_lines:
        snippet = [line for line in output_lines[-8:] if line.strip()]
        lines.extend(snippet[:6])
    else:
        preview = _human_result(result)
        if preview:
            lines.append(preview)
    return "\n".join(lines).strip()


def _human_result(result: Any, limit: int = 240) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return ""
        first = text.splitlines()[0].strip()
        if len(text.splitlines()) > 1:
            first = first[: max(limit - 2, 8)] + " …"
        return first if len(first) <= limit else first[: limit - 1] + "…"
    if isinstance(result, dict):
        for key in ("output", "content", "text", "message", "error"):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return _human_result(val, limit)
        return ""
    if isinstance(result, list):
        return f"{len(result)} items" if result else ""
    return ""


def describe_tool_headline(
    name: str,
    arguments: dict | None,
    max_len: int = 96,
) -> tuple[str, str]:
    """Plain headline + flags for a tool row. No ``key=value`` dump."""
    args = arguments if isinstance(arguments, dict) else {}
    base = (name or "").lower()
    if base in {"exec", "shell", "exec_command", "execute_command"}:
        command = args.get("command") or args.get("cmd") or ""
        headline = humanize_shell_command(str(command), max_len=max_len)
        return headline, exec_flags(args)

    class _Proxy:
        def __init__(self) -> None:
            self.name = name
            self.arguments = args

    fmt = _TOOL_FORMATS.get(name) or _TOOL_FORMATS.get(base)
    if fmt:
        return _fmt_known(_Proxy(), fmt, max_len), ""
    if name.startswith("mcp_"):
        return _fmt_mcp(_Proxy(), max_len), ""
    return _fmt_fallback(_Proxy(), max_len), ""


def _fmt_exec(tc, max_length: int = 80) -> str:
    args = _get_args(tc)
    command = args.get("command") or args.get("cmd") or ""
    headline = humanize_shell_command(str(command) if command else "", max_len=max_length)
    flags = exec_flags(args if isinstance(args, dict) else {})
    text = f"$ {headline}"
    return f"{text} · {flags}" if flags else text


def _fmt_known(tc, fmt: tuple, max_length: int = 40) -> str:
    """Format a registered tool using its template."""
    if not fmt[0] and "{}" not in fmt[1]:
        return fmt[1]
    val = _extract_arg(tc, fmt[0])
    if val is None:
        return tc.name
    if fmt[2]:  # is_path
        val = abbreviate_path(val, max_len=max_length)
    elif fmt[3]:  # is_command
        val = _abbreviate_command(val, max_len=max_length)
    return fmt[1].format(val)


def _abbreviate_command(cmd: str, max_len: int = 40) -> str:
    """Abbreviate paths in a command string, then truncate."""
    path_max = max(max_len // 2, 25)

    def _replace_path(match: re.Match[str]) -> str:
        if match.group("double") is not None:
            return f'"{abbreviate_path(match.group("double"), max_len=path_max)}"'
        if match.group("single") is not None:
            return f"'{abbreviate_path(match.group('single'), max_len=path_max)}'"
        return abbreviate_path(match.group("bare"), max_len=path_max)

    abbreviated = _PATH_IN_CMD_RE.sub(_replace_path, cmd)
    if len(abbreviated) <= max_len:
        return abbreviated
    return abbreviated[:max_len - 1] + "\u2026"


def _fmt_mcp(tc, max_length: int = 40) -> str:
    """Format MCP tool as server::tool."""
    name = tc.name
    if "__" in name:
        parts = name.split("__", 1)
        server = parts[0].removeprefix("mcp_")
        tool = parts[1]
    else:
        rest = name.removeprefix("mcp_")
        parts = rest.split("_", 1)
        server = parts[0] if parts else rest
        tool = parts[1] if len(parts) > 1 else ""
    if not tool:
        return name
    args = _get_args(tc)
    val = next((v for v in args.values() if isinstance(v, str) and v), None)
    if val is None:
        return f"{server}::{tool}"
    return f'{server}::{tool}("{abbreviate_path(val, max_length)}")'


def _fmt_fallback(tc, max_length: int = 40) -> str:
    """Original formatting logic for unregistered tools."""
    args = _get_args(tc)
    val = next(iter(args.values()), None) if isinstance(args, dict) else None
    if not isinstance(val, str):
        return tc.name
    return f'{tc.name}("{abbreviate_path(val, max_length)}")' if len(val) > max_length else f'{tc.name}("{val}")'
