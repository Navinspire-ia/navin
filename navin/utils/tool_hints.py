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

# How much of a tool/message we keep on screen and on the clipboard.
MAX_TRANSCRIPT_LINES = 5000
# Default open preview: a Codex-sized hunk, not a 80-row wall.
PREVIEW_OPEN_LINES = 32
PREVIEW_MIN_WIDTH = 72
# Codex-style washes: readable text on a clear green / red bar.
PREVIEW_ADD_INK = "#E8FFEF"
PREVIEW_ADD_BG = "#0F6B38"
PREVIEW_DEL_INK = "#FFE8E8"
PREVIEW_DEL_BG = "#8B2222"
PREVIEW_CTX_INK = "#E8E8E8"
PREVIEW_CTX_BG = "#1C1C1C"
MAX_TRANSCRIPT_CHARS = 400_000
# One TUI row: long enough for a real grep/run, short enough for WT.
TOOL_LINE_LIMIT = 160

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
_HEREDOC_OPEN_RE = re.compile(r"""<<-?\s*(['\"]?)(\w+)\1\s*$""")
_EMPTY_OUTPUT_RE = re.compile(
    r"^\((?:no output(?: yet)?|[\w.:-]+ completed with no output)\)$",
    re.I,
)
_DIFF_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
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


def split_heredoc(command: str) -> tuple[str, str]:
    """Split ``python - <<'PY'\\ncode\\nPY`` into ``('python -', 'code')``."""
    raw = (command or "").replace("\r\n", "\n")
    if "<<" not in raw:
        return raw.strip(), ""
    first, sep, rest = raw.partition("\n")
    opened = _HEREDOC_OPEN_RE.search(first)
    if not opened:
        return raw.strip(), ""
    tag = opened.group(2)
    head = first[: opened.start()].strip()
    if not sep:
        return head, ""
    body: list[str] = []
    for line in rest.splitlines():
        if line.strip() == tag:
            break
        body.append(line)
    return head, "\n".join(body)


def humanize_shell_command(cmd: str, max_len: int = 88) -> str:
    """Turn a raw exec string into the action a human can read at a glance.

    ``cd .../db-migration && .venv/bin/python migration-v2.py``
    becomes ``in db-migration · python migration-v2.py``.
    ``sleep 120 && tail -5 .../run.log`` becomes ``wait 2 min · tail -5 .../run.log``.
    """
    head, body = split_heredoc(cmd)
    source = head if body or _HEREDOC_OPEN_RE.search(head) else cmd
    raw = " ".join((source or "").strip().split())
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
    action = re.sub(r"\s+-\s*$", "", action)
    action = action.strip() or raw
    if body or _HEREDOC_OPEN_RE.search(head):
        action = action.split()[0] if action.split() else "python"
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


# Consecutive reads/searches fold under one TUI header, like Cursor's Explored.
_EXPLORE_VERBS = frozenset({"read", "list", "grep", "find", "search"})
_EDIT_VERBS = frozenset({"edit", "create"})
_EXPLORE_OP = {
    "read": "Read",
    "list": "List",
    "grep": "Search",
    "find": "Search",
    "search": "Search",
}


def tool_cluster_kind(name: str) -> str:
    """``explore`` or ``edit``. Empty string means a standalone row (run, git)."""
    verb = tool_verb(name)
    if verb in _EXPLORE_VERBS:
        return "explore"
    if verb in _EDIT_VERBS:
        return "edit"
    return ""


def describe_explore_step(
    name: str,
    arguments: dict | None,
    *,
    limit: int = 72,
) -> str:
    """``Read useAccount.ts`` or ``Search NavinClient in navin-client.ts``."""
    verb = tool_verb(name)
    label = _EXPLORE_OP.get(verb, verb.title() if verb else "Tool")
    args = arguments if isinstance(arguments, dict) else {}
    path = _first_path(args)
    file_name = _path_name(path) if path else ""
    query = ""
    for key in ("pattern", "query", "glob"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            query = " ".join(val.split())
            break
    if verb in {"grep", "find", "search"}:
        if query and file_name:
            text = f"{label} {query} in {file_name}"
        elif query:
            text = f"{label} {query}"
        else:
            text = f"{label} {file_name}".strip() or label
    else:
        target = file_name or tool_target(args)
        text = f"{label} {target}".strip() if target else label
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


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


def _path_name(value: str) -> str:
    cleaned = value.replace("\\", "/").strip()
    return Path(cleaned).name or cleaned


def _first_path(args: dict[str, Any]) -> str:
    for key in ("path", "file_path", "file", "filename", "paths"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    edits = args.get("edits")
    if isinstance(edits, list):
        for item in edits:
            if isinstance(item, dict):
                for key in ("path", "file_path", "file"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
    return ""


def _usable_shell_label(text: str) -> bool:
    stripped = (text or "").strip().strip("'\"`·- ")
    if len(stripped) < 2:
        return False
    if stripped.lower() in {"shell", "command", "true", ":", "."}:
        return False
    return True


def clip_transcript(
    text: str,
    *,
    max_lines: int = MAX_TRANSCRIPT_LINES,
    max_chars: int = MAX_TRANSCRIPT_CHARS,
) -> str:
    """Keep a copyable / displayable blob: up to 5000 lines, not a 80-line stub."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return ""
    lines = raw.splitlines()
    extra_lines = 0
    if len(lines) > max_lines:
        extra_lines = len(lines) - max_lines
        lines = lines[:max_lines]
    blob = "\n".join(line.rstrip() for line in lines)
    extra_chars = 0
    if len(blob) > max_chars:
        extra_chars = len(blob) - max_chars
        blob = blob[:max_chars].rstrip()
    if extra_lines or extra_chars:
        more = extra_lines or extra_chars
        unit = "lines" if extra_lines else "chars"
        blob = f"{blob.rstrip()}\n… ({more} more {unit})"
    return blob


def _looks_broken_command_head(text: str) -> bool:
    """True when abbreviation ate the verb and left a quoted tail / pipe."""
    stripped = (text or "").lstrip()
    if not stripped:
        return True
    return stripped.startswith(('"', "'", "|", "2>&1"))


def _raw_command_stub(command: str) -> str:
    raw = " ".join((command or "").split())
    chunks = [chunk.strip() for chunk in _SHELL_SPLIT_RE.split(raw) if chunk.strip()]
    kept: list[str] = []
    for index, chunk in enumerate(chunks):
        if _CD_RE.match(chunk) and index < len(chunks) - 1:
            continue
        if _SLEEP_RE.match(chunk):
            continue
        kept.append(_PYTHON_BIN_RE.sub("python", chunk))
    raw = " && ".join(kept) if kept else raw
    return raw.strip() or "command"


def _command_from_args(arguments: dict | None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    command = args.get("command") or args.get("cmd")
    return command.strip() if isinstance(command, str) and command.strip() else ""


def short_run_target(command: str, *, limit: int = TOOL_LINE_LIMIT) -> str:
    """Same shape as an edit target: ``git status``, ``python``, ``pytest  foo.py``."""
    head, body = split_heredoc(command)
    source = head if body or _HEREDOC_OPEN_RE.search((command or "").split("\n", 1)[0]) else command
    raw = _raw_command_stub(source)
    raw = _PYTHON_BIN_RE.sub("python", raw)
    raw = re.sub(r"\s+-\s*$", "", raw).strip()
    tokens = raw.split()
    if not tokens:
        return _command_label(command, limit)
    name = tokens[0]
    if name == "git" and len(tokens) >= 2:
        return "git " + tokens[1]
    if name in {"pytest", "python", "python3"}:
        if body or _HEREDOC_OPEN_RE.search((command or "").split("\n", 1)[0]):
            return "python" if name.startswith("python") else name
        files = [tok for tok in tokens[1:] if not tok.startswith("-") and tok != "--"]
        if len(files) == 1:
            return f"{name} {_path_name(files[0])}"
        if len(files) > 1:
            return f"{name} {len(files)} files"
        return name
    if name in {"rg", "grep"}:
        return _command_label(command, limit)
    pretty = humanize_shell_command(command, max_len=max(limit, 80))
    if _usable_shell_label(pretty) and not _looks_broken_command_head(pretty):
        return pretty if len(pretty) <= limit else pretty[: limit - 1] + "…"
    return _command_label(command, limit)


def _command_label(command: str, limit: int = TOOL_LINE_LIMIT) -> str:
    """Never an empty ``run  "``. Keep the real command, not a 56-char stub."""
    if "<<" in (command or ""):
        short = short_run_target(command, limit=limit)
        if _usable_shell_label(short):
            return short
    pretty = humanize_shell_command(command, max_len=max(limit, 80))
    if _usable_shell_label(pretty) and not _looks_broken_command_head(pretty):
        text = pretty
    else:
        text = _raw_command_stub(command)
        if _looks_broken_command_head(text) or not _usable_shell_label(text):
            fallback = " ".join((command or "").split())
            text = fallback if _usable_shell_label(fallback) else "command"
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def tool_target(arguments: dict | None, *, limit: int = TOOL_LINE_LIMIT) -> str:
    """Short action target: file name, command, or query. No JSON. Never blank."""
    args = arguments if isinstance(arguments, dict) else {}
    command = _command_from_args(args)
    if command:
        return short_run_target(command, limit=limit)
    path = _first_path(args)
    if path:
        return _path_name(path)
    for key in ("pattern", "query", "url", "name", "action", "task"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            if _usable_shell_label(text):
                return text if len(text) <= limit else text[: limit - 1] + "…"
    return ""


def describe_tool_line(
    name: str,
    arguments: dict | None,
    *,
    done: bool = False,
    added: int = 0,
    removed: int = 0,
) -> str:
    """``edit  +38 -14  foo.py`` or ``run  +16  python`` on one line."""
    del done
    verb = tool_verb(name)
    if verb == "run" and added == 0 and removed == 0:
        added, removed = infer_run_stats(arguments)
    target = tool_target(arguments, limit=TOOL_LINE_LIMIT)
    if verb in {"edit", "create"}:
        path = _first_path(arguments if isinstance(arguments, dict) else {})
        if path:
            target = _last_path_bits(path, keep=2)
    suffix = format_diff_suffix(added, removed)
    if target and suffix:
        return f"{verb}  {suffix}  {target}"
    if suffix:
        return f"{verb}  {suffix}"
    return f"{verb}  {target}" if target else verb


def edit_group_key(name: str, arguments: dict | None) -> str:
    """Same file + same verb (edit/create) collapse to one TUI row."""
    if tool_verb(name) not in {"edit", "create"}:
        return ""
    path = _first_path(arguments if isinstance(arguments, dict) else {})
    if not path:
        return ""
    return f"{tool_verb(name)}:{Path(path).name.lower()}"


def format_diff_suffix(added: int, removed: int) -> str:
    """``+38 -14`` when both sides exist, ``+16`` for a create-style add."""
    try:
        plus = max(0, int(added or 0))
        minus = max(0, int(removed or 0))
    except (TypeError, ValueError):
        return ""
    if plus == 0 and minus == 0:
        return ""
    if plus and minus:
        return f"+{plus} -{minus}"
    if plus:
        return f"+{plus}"
    return f"-{minus}"


_DIFF_IN_TEXT_RE = re.compile(r"\(\+(\d+)/-(\d+)\)")


def extract_line_diff(result: Any) -> tuple[int, int]:
    """Pull + / - counts from a tool result or ``(+12/-3)`` text."""
    if isinstance(result, dict):
        added = result.get("added", result.get("lines_added"))
        removed = result.get("removed", result.get("deleted", result.get("lines_removed")))
        try:
            plus = int(added or 0)
            minus = int(removed or 0)
        except (TypeError, ValueError):
            plus, minus = 0, 0
        if plus or minus:
            return max(0, plus), max(0, minus)
        for key in ("output", "content", "text", "message", "summary"):
            val = result.get(key)
            if isinstance(val, str):
                found = extract_line_diff(val)
                if found != (0, 0):
                    return found
        return 0, 0
    if isinstance(result, str):
        plus = minus = 0
        for match in _DIFF_IN_TEXT_RE.finditer(result):
            plus += int(match.group(1))
            minus += int(match.group(2))
        return plus, minus
    return 0, 0


def format_turn_summary(
    rows: list[tuple[str, int, int]],
) -> str:
    """``Edited 4 files, explored 1 file, ran 1 command +38 -14``."""
    edited = 0
    explored = 0
    ran = 0
    added = 0
    removed = 0
    for name, plus, minus in rows:
        verb = tool_verb(name)
        if verb in {"edit", "create"}:
            edited += 1
            added += max(0, plus)
            removed += max(0, minus)
        elif verb == "run":
            ran += 1
        elif verb in {"read", "grep", "find", "list"}:
            explored += 1
        elif verb == "check":
            ran += 1
    bits: list[str] = []
    if edited:
        bits.append(f"Edited {edited} file{'s' if edited != 1 else ''}")
    if explored:
        bits.append(f"explored {explored} file{'s' if explored != 1 else ''}")
    if ran:
        bits.append(f"ran {ran} command{'s' if ran != 1 else ''}")
    if not bits:
        return f"{len(rows)} tool{'s' if len(rows) != 1 else ''}"
    bits[0] = bits[0][0].upper() + bits[0][1:]
    text = ", ".join(bits)
    diff = format_diff_suffix(added, removed)
    return f"{text} {diff}" if diff else text


def _useful_output(text: str) -> str:
    lines: list[str] = []
    for line in (text or "").replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        if not stripped or _EMPTY_OUTPUT_RE.match(stripped):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def _result_text(result: Any, output_lines: list[str] | None) -> str:
    parts: list[str] = []
    if output_lines:
        parts.extend(str(line) for line in output_lines if line)
    preview = _human_result(result) if result is not None else ""
    if preview:
        parts.append(preview)
    return _useful_output("\n".join(parts))


def _count_diff_marks(text: str) -> tuple[int, int]:
    plus = minus = 0
    in_diff = False
    for line in (text or "").splitlines():
        if line.startswith("@@ ") or line.startswith("diff --git "):
            in_diff = True
            continue
        if line.startswith(("+++", "---")):
            continue
        if not in_diff:
            continue
        if line.startswith("+"):
            plus += 1
        elif line.startswith("-"):
            minus += 1
    if plus or minus:
        return plus, minus
    return 0, 0


def infer_run_stats(
    arguments: dict | None,
    output_lines: list[str] | None = None,
    result: Any = None,
) -> tuple[int, int]:
    """Line counts for a run row, same + / - language as edit/create."""
    text = _result_text(result, output_lines)
    plus, minus = _count_diff_marks(text)
    if plus or minus:
        return plus, minus
    plus, minus = extract_line_diff(result)
    if plus or minus:
        return plus, minus
    command = _command_from_args(arguments)
    _, body = split_heredoc(command)
    if body and not text:
        return len(body.splitlines()), 0
    if text:
        n = len([line for line in text.splitlines() if line.strip()])
        return (n, 0) if n else (0, 0)
    return 0, 0


def _looks_like_unified_diff(text: str) -> bool:
    return bool(_DIFF_HUNK_RE.search(text or "") or (text or "").startswith("diff --git "))


def preview_rows(
    name: str,
    arguments: dict | None,
    *,
    result: Any = None,
    error: str | None = None,
    output_lines: list[str] | None = None,
    diff_text: str | None = None,
    limit: int = MAX_TRANSCRIPT_LINES,
) -> list[tuple[int | None, str, str]]:
    """Numbered preview rows: ``(line_no, add|del|ctx, text)``."""
    args = arguments if isinstance(arguments, dict) else {}
    rows: list[tuple[int | None, str, str]] = []
    verb = tool_verb(name)
    extra = (diff_text or "").strip()
    if extra and _looks_like_unified_diff(extra):
        if error:
            for line in error.replace("\r\n", "\n").splitlines():
                if line.strip():
                    rows.append((None, "del", line.rstrip()))
        rows.extend(_rows_from_unified_diff(extra))
        return rows[: max(1, int(limit))]
    path = _first_path(args)
    if path and verb not in {"run", "edit", "create"}:
        rows.append((None, "ctx", abbreviate_path(path, max_len=120)))
    pattern = args.get("pattern") or args.get("query")
    if isinstance(pattern, str) and pattern.strip() and verb != "run":
        rows.append((None, "ctx", pattern.strip()))
    if error:
        for line in error.replace("\r\n", "\n").splitlines():
            if line.strip():
                rows.append((None, "del", line.rstrip()))
    command = _command_from_args(args)
    text = _result_text(result, output_lines)
    _, body = split_heredoc(command)
    if extra:
        text = extra
    if text and _looks_like_unified_diff(text):
        rows.extend(_rows_from_unified_diff(text))
    elif text:
        for index, line in enumerate(text.splitlines(), start=1):
            rows.append((index, "ctx", line))
    elif body:
        for index, line in enumerate(body.splitlines(), start=1):
            rows.append((index, "add", line))
    elif not rows:
        question = args.get("question")
        if isinstance(question, str) and question.strip():
            rows.append((None, "ctx", " ".join(question.split())))
    return rows[: max(1, int(limit))]


def _rows_from_unified_diff(text: str) -> list[tuple[int | None, str, str]]:
    rows: list[tuple[int | None, str, str]] = []
    old = new = 0
    seen_hunk = False
    for line in (text or "").splitlines():
        hunk = _DIFF_HUNK_RE.match(line)
        if hunk:
            old = int(hunk.group("old_start"))
            new = int(hunk.group("new_start"))
            seen_hunk = True
            continue
        if line.startswith(("diff --git ", "index ", "--- ", "+++ ", "\\")):
            continue
        if not seen_hunk:
            if line.strip():
                rows.append((len(rows) + 1, "ctx", line))
            continue
        if line.startswith("+"):
            rows.append((new, "add", line[1:]))
            new += 1
        elif line.startswith("-"):
            rows.append((old, "del", line[1:]))
            old += 1
        elif line.startswith(" "):
            rows.append((new, "ctx", line[1:]))
            old += 1
            new += 1
        elif line.strip():
            rows.append((len(rows) + 1, "ctx", line))
    return rows


def format_preview_line(number: int | None, kind: str, text: str) -> str:
    if number is None:
        mark = {"add": "+", "del": "-", "ctx": ""}.get(kind, "")
        return f"{mark}{text}" if mark else text
    pad = f"{number:>4}"
    mark = {"add": "+", "del": "-", "ctx": " "}.get(kind, " ")
    return f"{pad} {mark}{text}"


def format_preview_markup_line(
    number: int | None,
    kind: str,
    text: str,
    *,
    width: int = 0,
) -> str:
    """Codex-style row: number, +/- , full-width wash, readable code. No bold."""
    plain = format_preview_line(number, kind, text)
    target = max(int(width or 0), PREVIEW_MIN_WIDTH, len(plain))
    payload = plain.replace("[", r"\[").replace("]", r"\]")
    if target > len(plain):
        payload = f"{payload}{' ' * (target - len(plain))}"
    if kind == "add":
        return f"[{PREVIEW_ADD_INK} on {PREVIEW_ADD_BG}]{payload}[/]"
    if kind == "del":
        return f"[{PREVIEW_DEL_INK} on {PREVIEW_DEL_BG}]{payload}[/]"
    return f"[{PREVIEW_CTX_INK} on {PREVIEW_CTX_BG}]{payload}[/]"


def format_tool_preview_markup(
    name: str,
    arguments: dict | None,
    *,
    result: Any = None,
    error: str | None = None,
    output_lines: list[str] | None = None,
    diff_text: str | None = None,
    limit: int = PREVIEW_OPEN_LINES,
    width: int = 0,
) -> str:
    """Numbered preview for the TUI body: data, metadata, add/del backgrounds."""
    rows = preview_rows(
        name,
        arguments,
        result=result,
        error=error,
        output_lines=output_lines,
        diff_text=diff_text,
        limit=limit,
    )
    return "\n".join(
        format_preview_markup_line(*row, width=width) for row in rows
    )


def format_tool_detail(
    name: str,
    arguments: dict | None,
    *,
    result: Any = None,
    error: str | None = None,
    output_lines: list[str] | None = None,
    diff_text: str | None = None,
    limit: int = MAX_TRANSCRIPT_LINES,
) -> str:
    """Click-to-expand preview: numbered lines, + / - like an edit."""
    rows = preview_rows(
        name,
        arguments,
        result=result,
        error=error,
        output_lines=output_lines,
        diff_text=diff_text,
        limit=limit,
    )
    if not rows:
        return ""
    return clip_transcript("\n".join(format_preview_line(*row) for row in rows))


def _human_result(result: Any, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    if result is None:
        return ""
    if isinstance(result, bool):
        return "ok" if result else "failed"
    if isinstance(result, (int, float)) and not isinstance(result, bool):
        return str(result)
    if isinstance(result, str):
        text = result.replace("\r\n", "\n").strip()
        if not text:
            return ""
        return clip_transcript(text, max_chars=limit)
    if isinstance(result, dict):
        bits: list[str] = []
        code = result.get("returncode")
        if isinstance(code, int):
            bits.append("ok" if code == 0 else f"exit {code}")
        if result.get("ok") is True and "ok" not in bits:
            bits.append("ok")
        for key in (
            "output",
            "stdout",
            "stderr",
            "content",
            "text",
            "message",
            "error",
            "summary",
            "diff",
            "result",
            "status",
        ):
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                chunk = _human_result(val, limit=max(80, limit // 2))
                if chunk:
                    bits.append(chunk)
        added = result.get("added")
        removed = result.get("removed")
        if isinstance(added, int) or isinstance(removed, int):
            bits.append(f"+{added or 0}  -{removed or 0}")
        blob = "\n".join(bits)
        return blob if len(blob) <= limit else blob[: limit - 1].rstrip() + "…"
    if isinstance(result, list):
        if not result:
            return ""
        if all(isinstance(item, str) for item in result[:20]):
            return _human_result(
                "\n".join(str(item) for item in result[:MAX_TRANSCRIPT_LINES]), limit
            )
        return f"{len(result)} items"
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
