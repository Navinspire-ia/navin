# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool hint formatting for concise, human-readable tool call display."""

from __future__ import annotations

import contextlib
import posixpath
import re
import time
from functools import lru_cache
from itertools import count, repeat
from pathlib import Path
from typing import Any

from rich.cells import cell_len, get_character_cell_size
from rich.cells import chop_cells as _rich_chop_cells
from rich.text import Text

from navin.utils.command_output import align_appended, is_git_command, output_kind
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
    "metagraph": "graph",
    "code_index": "index",
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
# Visual rows one source line may wrap to in a preview before it is cut.
PREVIEW_MAX_WRAPPED_ROWS = 12
# Hard ceiling on what one preview paint may wrap and highlight, whatever the
# caller asks for: a full view of a huge log shows its head and says so, and
# the UI thread never spends seconds on one card. Plain text paints ~30k
# rows a second; zero-width-heavy text is ten times slower, hence the clock.
PREVIEW_MAX_PAINT_ROWS = 8000
PREVIEW_MAX_PAINT_CHARS = 600_000
PREVIEW_MAX_PAINT_SECONDS = 0.35
PREVIEW_MAX_WIDTH = 1000
# A single background with readable change colors and explicit +/- markers.
PREVIEW_ADD_INK = "#E8E8E8"
PREVIEW_ADD_BG = "#20392B"
PREVIEW_DEL_INK = "#E8E8E8"
PREVIEW_DEL_BG = "#48251F"
PREVIEW_CTX_INK = "#E8E8E8"
PREVIEW_CTX_BG = "#181A1D"
MAX_TRANSCRIPT_CHARS = 400_000
# One TUI row: long enough for a real grep/run, short enough for WT.
TOOL_LINE_LIMIT = 160


def activity_palette(dark: bool = True) -> dict[str, str]:
    """Soft semantic accents, with separate inks for exploration and its tools."""
    return {
        "blue": "#5EA8FF" if dark else "#3D82FF",
        "green": "#8FBC8F" if dark else "#2D6A4F",
        "mustard": "#D6BC78" if dark else "#795B13",
        "coral": "#E39B91" if dark else "#914747",
        "lavender": "#C6AFE3" if dark else "#705393",
        "explore": "#5EA8FF" if dark else "#286AC4",
        "tool": "#C3BDA3" if dark else "#6C6042",
        "muted": "#A3A3A3" if dark else "#626262",
    }

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
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@",
    re.MULTILINE,
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
# metagraph and code_index are primary exploration tools: they read the
# project index, so they belong in the Explored cluster, not as standalone rows.
_EXPLORE_VERBS = frozenset({"read", "list", "grep", "find", "search", "graph", "index"})
_EDIT_VERBS = frozenset({"edit", "create"})
_EXPLORE_OP = {
    "read": "Read",
    "list": "List",
    "grep": "Search",
    "find": "Search",
    "search": "Search",
    "graph": "Graph",
    "index": "Index",
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
    for key in ("pattern", "query", "glob", "name"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            query = " ".join(val.split())
            break
    if verb in {"grep", "find", "search", "graph", "index"}:
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
    return redact_command(command.strip()) if isinstance(command, str) and command.strip() else ""


def redact_command(command: str) -> str:
    """Mask explicit credentials in display text without changing execution args."""
    value = r'''(?:'(?:[^']*)'|"(?:[^"\\]|\\.)*"|[^\s;&|]+)'''
    command = re.sub(
        rf"(?i)(\b[\w]*(?:password|passwd|token|secret|api_key|apikey)\s*=\s*){value}",
        r"\1[redacted]", command,
    )
    command = re.sub(
        rf"(?i)(--(?:password|passwd|token|secret|api-key)(?:=|\s+)){value}",
        r"\1[redacted]", command,
    )
    return re.sub(r"(\b[\w+.-]+://[^\s/:]+:)[^\s/@]+(@)", r"\1[redacted]\2", command)


def command_summary(command: str) -> str:
    """Keep script bodies out of the activity heading; details retain the script."""
    command = redact_command(command)
    head = command.splitlines()[0] if command else "command"
    if len(head) > TOOL_LINE_LIMIT:
        head = head[:TOOL_LINE_LIMIT].rstrip() + " [details]"
    if "\n" in command:
        head += f" · {len(command.splitlines())} lines"
    return head


def short_run_target(command: str, *, limit: int = TOOL_LINE_LIMIT) -> str:
    """Same shape as an edit target: ``git status``, ``python``, ``pytest  foo.py``."""
    head, body = split_heredoc(command)
    source = head if body or _HEREDOC_OPEN_RE.search((command or "").split("\n", 1)[0]) else command
    raw = _raw_command_stub(source)
    raw = _PYTHON_BIN_RE.sub("python", raw)
    raw = re.sub(r"\s+-\s*$", "", raw).strip()
    tokens = raw.split()
    if not tokens:
        return _command_label(command, limit, _from_target=True)
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
        return _command_label(command, limit, _from_target=True)
    pretty = humanize_shell_command(command, max_len=max(limit, 80))
    if _usable_shell_label(pretty) and not _looks_broken_command_head(pretty):
        return pretty if len(pretty) <= limit else pretty[: limit - 1] + "…"
    return _command_label(command, limit, _from_target=True)


def _command_label(command: str, limit: int = TOOL_LINE_LIMIT, *, _from_target: bool = False) -> str:
    """Never an empty ``run  "``. Keep the real command, not a 56-char stub."""
    if "<<" in (command or "") and not _from_target:
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
    return f"{tool_verb(name)}:{activity_path_key(path)}"


def activity_path_key(path: str) -> str:
    """Keep the whole path and its case; src/a.py and tests/a.py are distinct."""
    return posixpath.normpath(path.replace("\\", "/")) if path else ""


FILE_OPERATION_LABELS = {
    "create": "Added", "add": "Added", "added": "Added",
    "edit": "Edited", "modify": "Edited", "modified": "Edited",
    "delete": "Deleted", "deleted": "Deleted", "remove": "Deleted",
    "restore": "Reverted", "revert": "Reverted", "reverted": "Reverted",
    "unchanged": "Unchanged",
}


def file_operation_label(operation: str) -> str:
    return FILE_OPERATION_LABELS.get(operation, "Edited")


def is_validation_pending(name: str, error: str | None) -> bool:
    """Recognize current and saved completion prerequisites, never permission errors."""
    if name not in {"board", "update_goal"} or not error:
        return False
    text = error.lstrip()
    if text.startswith((
        "Validation pending: task remains open.",
        "Validation required before closing this work.",
    )):
        return True
    # Older board results used the generic error prefix for these prerequisites.
    text = text.removeprefix("Error: ")
    if name != "board" or not text.startswith("cannot mark ") or " done: " not in text:
        return False
    reason = text.split(" done: ", 1)[1]
    return reason.startswith((
        "validation=", "the task has acceptance criteria but no evidence;",
        "no verification run is recorded for this project.",
        "the last verification run (", "the last passing verification is ",
        "the last verification passed but did not run any tests,",
    ))


def activity_label(
    name: str, arguments: dict | None, *, phase: str = "end",
    added: int = 0, removed: int = 0, operation: str = "", path: str = "",
    counts_known: bool = True, error: str | None = None,
) -> str:
    """Describe actual activity without treating printed output as added code."""
    if phase == "error" and is_validation_pending(name, error):
        target = "Board completion" if name == "board" else "Task completion"
        return f"Validation pending: {target}"
    args = arguments if isinstance(arguments, dict) else {}
    verb = tool_verb(name)
    pending = phase in {"start", "output"}
    if name == "manage_files" and not operation:
        action = str(args.get("action") or "manage")
        labels = {"delete": ("Deleting", "Deleted"), "move": ("Moving", "Moved"), "copy": ("Copying", "Copied"), "mkdir": ("Creating", "Created")}
        label = labels.get(action, ("Managing", "Managed"))[0 if pending else 1]
        if phase in {"error", "cancelled", "interrupted"}:
            label = {"mkdir": "Create directory"}.get(action, action.title())
        paths = args.get("paths") or [path or args.get("path") or "files"]
        target = ", ".join(str(item) for item in paths) if isinstance(paths, list) else str(paths)
        text = f"{label} {target}"
    elif name == "test_run":
        target = args.get("target") or args.get("runner") or "project"
        text = f"{'Testing' if pending else 'Tests'} {target}"
    elif name == "write_stdin":
        # A background command followed up: what was done to it, never the
        # raw tool name ("Write Stdin" on every poll).
        if args.get("terminate"):
            text = "Stopping running command" if pending else "Stopped running command"
        elif args.get("chars"):
            text = "Sending input to running command" if pending else "Sent input to running command"
        else:
            text = "Checking running command" if pending else "Checked running command"
    elif name == "list_exec_sessions":
        text = "Listing running commands" if pending else "Listed running commands"
    elif path or verb in _EDIT_VERBS:
        target = path or _first_path(args) or "files"
        label = "Editing" if pending else file_operation_label(operation)
        if not operation and not pending:
            label = "Edit"
        suffix = f" (+{max(0, added)} -{max(0, removed)})" if counts_known and operation and operation != "unchanged" else ""
        text = f"{label} {target}{suffix}"
    elif verb == "run":
        command = _command_from_args(args) or tool_target(args) or "command"
        label = "Running" if pending else "Ran"
        # One short line: `cd`, interpreter paths and pipes stay in the
        # details (and the copy), never wrapped over two rows of the chat.
        short = short_run_target(command)
        # Env assignments, `timeout N`, `2>&1` and a trailing `| tail` are
        # plumbing: drop them so the row fits one line and `-15` never reads
        # as a deleted-lines count.
        head, sep, rest = short.partition(" · ")
        body = rest if sep else head
        body = re.sub(r"\s*\|\s*(?:tail|head|cat|tee)\b.*$", "", body)
        body = re.sub(r"\s*\d?>&\d|\s*2>/dev/null", "", body)
        body = re.sub(r"^(?:\s*[A-Z_][A-Z0-9_]*=\S+)+\s*", "", body)
        body = re.sub(r"^timeout\s+\S+\s+", "", body).strip() or body
        if len(body) > 72:
            body = body[:71].rstrip() + "…"
        short = f"{head}{sep}{body}" if sep else body
        if "\n" in command and not short.endswith("lines"):
            short += f" · {len(command.splitlines())} lines"
        text = f"{label} {short}"
    elif verb in _EXPLORE_VERBS:
        text = describe_explore_step(name, args, limit=500)
    elif name == "spawn":
        text = _spawn_label(args)
    elif name == "board":
        # A failed or cancelled board call changed nothing: say what was asked.
        text = _board_label(args, pending=pending or phase in {"error", "cancelled", "interrupted"})
    else:
        label = "Checking" if pending and verb == "check" else "Checked" if verb == "check" else verb.title()
        text = f"{label} {tool_target(args)}".rstrip()
    # No "Failed:" on a step: agents retry and probe. Whether the task
    # worked is said once, in the final message.
    if phase in {"cancelled", "interrupted"}:
        text = f"Cancelled: {text}"
    return text


def _first_line(value: Any, limit: int = 72) -> str:
    if not isinstance(value, str):
        return ""
    for line in value.splitlines():
        line = " ".join(line.split()).strip(" #*-")
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def _spawn_label(args: dict[str, Any]) -> str:
    """``Agent Audit the login flow``: who was started, not the raw tool."""
    if args.get("action") == "results":
        return "Agent results"
    name = _first_line(args.get("label")) or _first_line(args.get("task")) or _first_line(args.get("agent"))
    return f"Agent {name}" if name else "Agent"


_BOARD_DONE = {"done", "completed", "complete", "closed"}


def _board_label(args: dict[str, Any], *, pending: bool = False) -> str:
    """Say what changed on the board: ``Completed task T3``, ``Updated plan``.

    While the call runs (or when it did not go through) the label says what
    is asked, not what happened: ``Complete task T3``.
    """
    action = str(args.get("action") or "").strip().lower()
    task = _first_line(args.get("title"), 60) or str(args.get("task_id") or "").strip()
    status = str(args.get("status") or "").strip().lower()
    subject = f" {task}" if task else ""
    if action in {"update", "move"} and status in _BOARD_DONE:
        return f"{'Complete' if pending else 'Completed'} task{subject}"
    if action == "move" and status:
        return f"{'Move' if pending else 'Moved'} task{subject} to {status.replace('_', ' ')}"
    labels = {
        "create": ("Add", "Added", f" task{subject}"),
        "update": ("Update", "Updated", f" task{subject}"),
        "move": ("Move", "Moved", f" task{subject}"),
        "claim": ("Claim", "Claimed", f" task{subject}"),
        "comment": ("Comment", "Commented", f" on task{subject}"),
        "delete": ("Remove", "Removed", f" task{subject}"),
        "get": ("Read", "Read", f" task{subject}"),
        "list": ("Read", "Read", " board"),
        "next": ("Pick", "Picked", " next task"),
        "plan": ("Update", "Updated", " plan"),
        "log": ("Read", "Read", " board log"),
        "sync_github": ("Sync", "Synced", " board with GitHub"),
        "milestone_create": ("Add", "Added", " milestone"),
        "milestone_update": ("Update", "Updated", " milestone"),
        "ledger_get": ("Read", "Read", " mission ledger"),
        "ledger_init": ("Start", "Started", " mission ledger"),
        "ledger_progress": ("Log", "Logged", " mission progress"),
        "ledger_replan": ("Update", "Updated", " mission plan"),
        "ledger_pause": ("Pause", "Paused", " mission"),
        "ledger_resume": ("Resume", "Resumed", " mission"),
    }
    if action.startswith("ledger_") and action not in labels:
        return f"{'Update' if pending else 'Updated'} mission ledger"
    if action in labels:
        asked, done, rest = labels[action]
        return f"{asked if pending else done}{rest}"
    return f"Board {action.replace('_', ' ')}" if action else "Board"


def humanize_tool_error(error: Any, limit: int = 80) -> str:
    """One short reason for a failed step, never a raw error dump."""
    text = display_tool_error(str(error or ""))
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^(?:Error|ERROR|error)\s*:\s*", "", line).strip()
    if not line:
        return ""
    lower = line.lower()
    if lower.startswith("invalid parameters") or "missing required" in lower:
        return "invalid arguments"
    if "timed out" in lower or "timeout" in lower:
        return "timed out"
    if "permission denied" in lower or "not permitted" in lower:
        return "permission denied"
    # Three different failures read "not found". Collapsing them all to the same
    # two words made an edit whose text did not match look like a missing file.
    if "old_text not found" in lower:
        return "text to replace not found"
    if "old_text appears" in lower:
        return "text appears several times"
    if "does not exist" in lower and "file" in lower:
        return "file does not exist"
    if "file not found" in lower or "no such file" in lower:
        return "file not found" if len(line) > limit else line
    if "not found" in lower and len(line) > limit:
        return "not found"
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def humanize_agent_error(error: Any, *, retrying: bool = False) -> str:
    """A calm status for a subagent problem instead of the provider's text."""
    raw = str(error or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if "timed out" in lower or "timeout" in lower or "deadline" in lower:
        reason = "Model timed out"
    elif "rate limit" in lower or re.search(r"\b429\b", lower) or "too many requests" in lower:
        reason = "Rate limited"
    elif "quota" in lower or ("insufficient" in lower and "credit" in lower) or "billing" in lower:
        reason = "Out of credits"
    elif re.search(r"\b40[13]\b", lower) or any(
        mark in lower for mark in ("unauthorized", "forbidden", "api key", "authentication")
    ):
        reason = "Model access refused"
    elif any(mark in lower for mark in ("context length", "context window", "too many tokens", "maximum context")):
        reason = "Context too long"
    elif re.search(r"\b5\d\d\b", lower) or any(
        mark in lower for mark in ("overloaded", "unavailable", "server error")
    ):
        reason = "Model unavailable"
    elif any(mark in lower for mark in ("connection", "network", "unreachable", "reset by peer")):
        reason = "Connection lost"
    elif "cancel" in lower or "interrupted" in lower or "stopped" in lower:
        reason = "Stopped"
    else:
        reason = humanize_tool_error(raw, limit=60) or "Stopped with an error"
        reason = reason[:1].upper() + reason[1:]
    return f"{reason}, retrying" if retrying else reason


def ask_user_answer_text(result: Any) -> str:
    """The user's pick from a saved ask_user result, for the transcript echo."""
    text = str(result or "").strip()
    if not text:
        return ""
    chose = re.match(r'^The user chose "(?P<id>[^"]*)"(?: \((?P<label>[^)]*)\))?', text)
    if chose:
        return chose["label"] or chose["id"]
    if text.startswith("The user did not pick a listed option"):
        lines = text.splitlines()
        return lines[1].strip() if len(lines) > 1 else "custom answer"
    if "skipped" in text.lower().split(" recommended:", 1)[0]:
        return "skipped"
    if "timed out" in text.lower() or "no answer" in text.lower():
        return "no answer"
    return _first_line(text, 80)


_READ_TOTAL_RE = re.compile(r"\(End of file - (\d+) lines total\)")
_EXIT_IN_ERROR_RE = re.compile(r"\bexit(?:ed)?(?: with)?(?: code| status)?\s*:?\s*(-?\d+)\b", re.I)
_READ_WINDOW_RE = re.compile(r"\(Showing lines (\d+)-(\d+) of (\d+)\.")
_READ_NUMBERED_RE = re.compile(r"^\s*\d+\s*[|│:\t]")
_MATCH_TOTAL_RE = re.compile(r"\(total matches: (\d+) in (\d+) files?\)")
_MATCH_LINE_RE = re.compile(r"^(?P<path>[^\s:][^:]*):\d+[:-]")


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def result_summary(
    name: str,
    arguments: dict | None,
    result: Any = None,
    output_lines: list[str] | None = None,
    *,
    phase: str = "end",
    error: str | None = None,
) -> str:
    """One short outcome for a finished row: ``120 lines``, ``7 matches in 3
    files``, ``exit 0 · 42 lines``. Empty when there is nothing worth saying."""
    from navin.utils.command_output import command_exit_code, is_exit_line

    if phase in {"start", "output", "cancelled", "interrupted"}:
        return ""
    del arguments  # the outcome comes from the result, not the request
    verb = tool_verb(name)
    if phase == "error" and is_validation_pending(name, error):
        return ""
    if verb == "run" or name == "test_run":
        text = _result_text(result, output_lines)
        lines = [line for line in text.splitlines() if line.strip() and not is_exit_line(line)]
        code = command_exit_code(result)
        if code is None and phase == "error":
            # "Exit code: 1" / "Command failed with exit code 2" in the error
            # reads as the same "exit N" as a finished command.
            code = command_exit_code(error)
            if code is None:
                found = _EXIT_IN_ERROR_RE.search(Text.from_ansi(str(error or "")).plain)
                code = int(found[1]) if found else None
        bits = []
        if code is not None:
            bits.append(f"exit {code}")
        elif phase == "error":
            reason = humanize_tool_error(error)
            if reason:
                bits.append(reason)
        bits.append(_plural(len(lines), "line") if lines else "no output")
        return " · ".join(bits)
    if phase == "error":
        return humanize_tool_error(error or _human_result(result))
    if verb in _EDIT_VERBS:
        return ""
    text = _human_result(result) if result is not None else ""
    if not text and output_lines:
        text = "\n".join(output_lines)
    if not text.strip():
        return ""
    if verb == "read":
        if text.startswith("[File unchanged since last read"):
            return "unchanged since last read"
        if text.startswith("(Empty file"):
            return "empty file"
        window = _READ_WINDOW_RE.search(text)
        if window:
            return f"lines {window[1]}-{window[2]} of {window[3]}"
        total = _READ_TOTAL_RE.search(text)
        if total:
            return _plural(int(total[1]), "line")
        numbered = sum(1 for line in text.splitlines() if _READ_NUMBERED_RE.match(line))
        return _plural(numbered or len(text.splitlines()), "line")
    if verb in {"grep", "find", "list", "search"}:
        if text.lstrip().startswith(("No matches", "No files found", "No results")):
            return "no matches"
        total = _MATCH_TOTAL_RE.search(text)
        if total:
            return f"{_plural(int(total[1]), 'match')} in {_plural(int(total[2]), 'file')}".replace("matchs", "matches")
        rows = [
            line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith(("(", "[", "Note:"))
        ]
        if not rows:
            return ""
        hits = [_MATCH_LINE_RE.match(line) for line in rows]
        if verb == "grep" and all(hits):
            files = len({hit["path"] for hit in hits if hit})
            return f"{_plural(len(rows), 'match')} in {_plural(files, 'file')}".replace("matchs", "matches")
        noun = {"grep": "file", "find": "file", "list": "entry", "search": "result"}[verb]
        if noun == "entry":
            return f"{len(rows)} entries" if len(rows) != 1 else "1 entry"
        return _plural(len(rows), noun)
    if verb == "fetch":
        return _plural(len([line for line in text.splitlines() if line.strip()]), "line")
    first = _first_line(text, 80)
    return first if len(text.splitlines()) == 1 else ""


def activity_head_text(text: str, *, dark: bool = True) -> Text:
    """Balanced row head: a soft accent on the verb, literal targets.

    The verb keeps one quiet hue per family (explore, edit, run) so the chain
    scans at a glance; paths stay plain (painting every one green made a busy
    turn read like a rainbow) and only diff counts are green/red.
    """
    rendered = Text(text)
    palette = activity_palette(dark)
    label = re.match(r"^[^\w\n]*(?P<action>[A-Za-z][\w-]*):?", text)
    if label:
        action = label["action"].lower()
        if action in {"cancelled", "unchanged"}:
            family = "muted"
        elif action in {"explored", "exploring"}:
            family = "explore"
        elif action in {"edit", "edited", "editing", "edits", "add", "added", "create", "created", "creating", "delete", "deleted", "deleting", "move", "moved", "moving", "copy", "copied", "copying"}:
            family = "lavender"
        elif action in {"run", "ran", "running", "testing", "tests", "completed", "checking", "checked", "validation"}:
            family = "mustard"
        else:
            family = "tool"
        # Include the tree marker in the accent, on the same line as the target.
        rendered.stylize(palette[family], 0, label.end())
        # The status icon keeps its own meaning: green done, red failed.
        prefix = text[: label.start("action")]
        for mark, color in (("✓", "green"), ("×", "coral")):
            index = prefix.find(mark)
            if index >= 0:
                rendered.stylize(palette[color], index, index + 1)
    for match in re.finditer(r"(?<=[( ])\+\d+|(?<= )-\d+(?=[) ]|$)", text):
        color = palette["green"] if match[0].startswith("+") else palette["coral"]
        rendered.stylize(color, match.start(), match.end())
    for match in re.finditer(r"\[[━·]+\]\s+\d+(?:\.\d+)?%", text):
        live = not match[0].endswith(" 100%")
        rendered.stylize(palette["blue"] if live else palette["muted"], match.start(), match.end())
    return rendered


_OUTPUT_STATUS_RE = re.compile(
    r"\b(?P<ok>PASSED|PASS|passed|OK|ok|SUCCESS|succeeded)\b"
    r"|\b(?P<bad>FAILED|FAIL|failed|ERROR|error|Error|Traceback)\b"
    r"|\b(?P<warn>SKIPPED|skipped|XFAIL|xfail|WARNING|warning|warnings)\b"
    r"|(?P<pct>\[\s*\d{1,3}%\])"
    r"|(?P<path>(?<![\w/.-])[\w./-]+\.(?:py|ts|tsx|js|rs|go)(?:::[\w:\[\]-]+)?)"
)


def highlight_output_statuses(rendered: Text, *, dark: bool = True) -> Text:
    """Color test/run statuses in streamed output: pass, fail, skip, %, paths.

    Only adds styles over plain spans; existing diff or markup styles stay.
    """
    palette = activity_palette(dark)
    colors = {
        "ok": palette["green"], "bad": palette["coral"], "warn": palette["mustard"],
        "pct": palette["muted"], "path": palette["blue"],
    }
    plain = rendered.plain
    if len(plain) > 200_000:
        return rendered
    for match in _OUTPUT_STATUS_RE.finditer(plain):
        group = match.lastgroup or ""
        style = colors.get(group)
        if style:
            rendered.stylize(f"bold {style}" if group in {"ok", "bad"} else style, match.start(), match.end())
    return rendered


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


def _streamed_tail(output_lines: list[str] | None, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """The newest streamed output, at most ``limit`` characters.

    Lines are capped by count upstream but not by length; joining and
    splitting a few hundred MB of one-line output on the UI thread froze it.
    """
    kept: list[str] = []
    budget = limit
    for line in reversed(output_lines or []):
        line = str(line)
        if len(line) >= budget:
            if budget > 0:
                kept.append(line[:budget])
            break
        kept.append(line)
        budget -= len(line) + 1
    kept.reverse()
    return "\n".join(kept)


def _result_text(result: Any, output_lines: list[str] | None) -> str:
    streamed = _useful_output(_streamed_tail(output_lines))
    preview = _human_result(result) if result is not None else ""
    final = _useful_output(preview)
    # Completion commonly repeats stdout that was already streamed.
    if streamed and final and streamed in final:
        return final
    if final and streamed and final in streamed:
        return streamed
    return "\n".join(part for part in (streamed, final) if part)


def _stream_line_rows(line: Any, strip_ansi: bool) -> tuple[tuple[str, ...], bool]:
    """The preview rows of one streamed line, and whether it may start a diff.

    Same filtering as ``_useful_output`` over the joined tail: blank and
    placeholder lines go, a line holding several breaks gives several rows.
    """
    rows = []
    diff = False
    for part in str(line).replace("\r\n", "\n").splitlines():
        stripped = part.strip()
        if not stripped or _EMPTY_OUTPUT_RE.match(stripped):
            continue
        part = part.rstrip()
        if part.startswith(("@@ ", "diff --git ")) and (part[0] == "d" or _DIFF_HUNK_RE.match(part)):
            diff = True
        if strip_ansi and "\x1b" in part:
            part = Text.from_ansi(part).plain
        rows.append(part)
    return tuple(rows), diff


class StreamRowsCache:
    """Preview rows of one live command, processed once per output line.

    A running command repaints ten times a second while its retained tail
    holds up to 5000 lines; only the lines appended since the last paint
    (and a rewritten last line) are filtered again.
    """

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._strip: bool | None = None
        self._lines: list = []
        # Row texts of every line, flat, with each line's row count, its
        # diff marker and the total characters of the lines.
        self._texts: list[str] = []
        self._counts: list[int] = []
        self._diffs: list[bool] = []
        self._diff_count = 0
        self._chars = 0

    def __len__(self) -> int:
        return len(self._lines)

    def _forget(self, start: int, stop: int | None = None) -> None:
        span = slice(start, stop)
        rows = sum(self._counts[span])
        if stop is None:
            del self._texts[len(self._texts) - rows:]
        else:
            del self._texts[:rows]
        self._diff_count -= sum(self._diffs[span])
        self._chars -= sum(len(str(line)) for line in self._lines[span])
        del self._counts[span], self._diffs[span], self._lines[span]

    def texts(self, output_lines: list, strip_ansi: bool) -> list[str] | None:
        """Row texts of ``_useful_output(_streamed_tail(output_lines))``.

        None when the output may be a unified diff: that layout needs the
        whole text, so the caller recomputes it. The list is shared: read it,
        do not change it.
        """
        if strip_ansi is not self._strip or not output_lines:
            self.clear()
            self._strip = strip_ansi
        lines = list(output_lines)
        match = align_appended(self._lines, lines)
        if match is None:
            self.clear()
            self._strip = strip_ansi
            kept = 0
        else:
            dropped, kept = match
            if dropped:
                self._forget(0, dropped)
            if len(self._lines) > kept:
                self._forget(kept)
        for line in lines[kept:]:
            rows, diff = _stream_line_rows(line, strip_ansi)
            self._texts.extend(rows)
            self._counts.append(len(rows))
            self._diffs.append(diff)
            self._diff_count += diff
            self._chars += len(str(line))
        self._lines = lines
        if self._diff_count:
            return None
        if self._chars + len(lines) - 1 < MAX_TRANSCRIPT_CHARS:
            return self._texts
        # Mirror ``_streamed_tail``: the newest lines that fit, and the head
        # of the line that overflowed.
        budget = MAX_TRANSCRIPT_CHARS
        start, head = len(lines), ""
        for index in range(len(lines) - 1, -1, -1):
            size = len(str(lines[index]))
            if size >= budget:
                head = str(lines[index])[:budget] if budget > 0 else ""
                break
            start = index
            budget -= size + 1
        texts: list[str] = []
        if head:
            rows, diff = _stream_line_rows(head, strip_ansi)
            if diff:
                return None
            texts.extend(rows)
        texts.extend(self._texts[sum(self._counts[:start]):])
        return texts


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
    include_run_output: bool = False,
    cache: StreamRowsCache | None = None,
) -> list[tuple[int | None, str, str]]:
    """Numbered preview rows: ``(line_no, add|del|ctx, text)``.

    ``cache`` (one per tool call) makes repeated calls on a growing live
    log cost its new lines only.
    """
    args = arguments if isinstance(arguments, dict) else {}
    rows: list[tuple[int | None, str, str]] = []
    verb = tool_verb(name)
    extra = (diff_text or "").strip("\n")
    if extra and _looks_like_unified_diff(extra):
        if error:
            for line in display_tool_error(error).splitlines():
                if line.strip():
                    rows.append((None, "error", line.rstrip()))
        rows.extend(_rows_from_unified_diff(extra))
        return rows[: max(1, int(limit))]
    path = _first_path(args)
    if path and verb not in {"run", "edit", "create"} and name != "manage_files":
        rows.append((None, "ctx", abbreviate_path(path, max_len=120)))
    pattern = args.get("pattern") or args.get("query")
    if isinstance(pattern, str) and pattern.strip() and verb != "run":
        rows.append((None, "ctx", pattern.strip()))
    if error:
        for line in display_tool_error(error).splitlines():
            if line.strip():
                kind = "ctx" if is_validation_pending(name, error) else "error"
                rows.append((None, kind, line.rstrip()))
    metadata_count = len(rows)
    command = _command_from_args(args)
    run_is_git = is_git_command(name, args)
    row_limit = max(1, int(limit))
    if (cache is not None and output_lines and result is None and not error and not extra
            and verb not in {"graph", "index"} and (verb != "run" or run_is_git or include_run_output)):
        # A live command: the streamed tail is the whole text, and only its
        # new lines need filtering (same rows as the full pass below).
        texts = cache.texts(output_lines, verb == "run" or name in {"test_run", "git"})
        if texts:
            rows.extend(zip(count(1), repeat("ctx"), texts))
            if verb == "run" and include_run_output and row_limit >= MAX_TRANSCRIPT_LINES and len(rows) > row_limit:
                metadata = rows[:min(metadata_count, row_limit - 1)]
                return metadata + rows[-(row_limit - len(metadata)):]
            return rows[:row_limit]
    elif cache is not None and len(cache):
        cache.clear()
    text = _result_text(result, output_lines)
    if error:
        # A failure may also contain useful stdout. Remove only a duplicate
        # error and the agent's retry suffix, not the rest of that output.
        text = display_tool_error(text).replace(display_tool_error(error), "").strip()
    _, body = split_heredoc(command)
    if extra:
        text = extra
    if text and name in _SESSION_TOOLS:
        text = strip_session_notes(text)
    if text and _looks_like_unified_diff(text):
        rows.extend(_rows_from_unified_diff(text))
    elif verb in {"graph", "index"} and text:
        # metagraph / code_index results read like a graph answer: render a
        # compact strip (chain joined by arrows, hubs/deps as short rows)
        # instead of numbered transcript lines.
        rows.extend(_graph_compact_rows(text))
    elif verb == "run" and not run_is_git and not include_run_output:
        # Journal off for command runs: only git commands print their output.
        pass
    elif text:
        for index, line in enumerate(text.splitlines(), start=1):
            if verb == "run" or name in {"test_run", "git"}:
                line = Text.from_ansi(line).plain if "\x1b" in line else line
            rows.append((index, "ctx", line))
    elif body and not error:
        for index, line in enumerate(body.splitlines(), start=1):
            rows.append((index, "ctx", line))
    elif not rows:
        question = args.get("question")
        if isinstance(question, str) and question.strip():
            rows.append((None, "ctx", " ".join(question.split())))
    if (verb == "run" and include_run_output and output_lines
            and row_limit >= MAX_TRANSCRIPT_LINES and len(rows) > row_limit
            and not _looks_like_unified_diff(text)):
        # At the retention limit, an error or final result must not push
        # the newest stdout lines out of the copyable activity.
        metadata = rows[:min(metadata_count, row_limit - 1)]
        return metadata + rows[-(row_limit - len(metadata)):]
    return rows[:row_limit]


_SESSION_TOOLS = frozenset({"exec", "write_stdin"})
# Lines a background command session adds for the model, not for the user.
_SESSION_NOTE_RE = re.compile(
    r"^(?:Process running\. session_id: \S+|You will be told when it finishes\b.*"
    r"|Still running after \d+s\. The command was not killed\b.*"
    r"|Elapsed: [\d.]+s|STDERR:|Stdin closed\.|\(no output yet\))$"
)


def strip_session_notes(text: str) -> str:
    """The command's own output, without the session bookkeeping lines."""
    return "\n".join(
        line for line in text.splitlines() if not _SESSION_NOTE_RE.match(line.strip())
    ).strip("\n")


def _graph_compact_rows(text: str, max_rows: int = 8) -> list[tuple[int | None, str, str]]:
    """Compact preview rows for metagraph / code_index answers."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows: list[tuple[int | None, str, str]] = []
    if lines and lines[0].startswith("Path ("):
        steps = [line for line in lines[1:] if line]
        rows.append((None, "ctx", " → ".join(steps) if steps else lines[0]))
        return rows[:max_rows]
    for line in lines[:max_rows]:
        rows.append((None, "ctx", line))
    return rows


def display_tool_error(error: str) -> str:
    """Hide agent retry instructions while preserving the actionable failure."""
    return re.sub(
        r"\n\s*\[(?:No tool was executed\.|Analyze the error above).*\]\s*$",
        "", error.replace("\r\n", "\n"), flags=re.DOTALL,
    ).strip()


def _rows_from_unified_diff(text: str) -> list[tuple[int | None, str, str]]:
    rows: list[tuple[int | None, str, str]] = []
    old = new = 0
    seen_hunk = False
    for line in (text or "").splitlines():
        hunk = _DIFF_HUNK_RE.match(line)
        if hunk:
            if seen_hunk:
                rows.append((None, "meta", "    ⋮"))
            old = int(hunk.group("old_start"))
            new = int(hunk.group("new_start"))
            seen_hunk = True
            continue
        if line.startswith("diff --git "):
            rows.append((None, "meta", line.removeprefix("diff --git ")))
            seen_hunk = False
            continue
        if line.startswith(("index ", "--- ", "+++ ", "\\")):
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
        mark = {"add": "+", "del": "-"}.get(kind, "")
        return f"{mark}{text}" if mark else text
    pad = f"{number:>4}"
    mark = {"add": "+", "del": "-", "ctx": " "}.get(kind, " ")
    return f"{pad} {mark}{text}"


@lru_cache(maxsize=64)
def _preview_lexer(filename: str):
    from pygments.lexers import get_lexer_for_filename
    from pygments.util import ClassNotFound

    try:
        return get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None


def _highlight_preview_source(code: str, filename: str, dark: bool) -> Text:
    """Restrained syntax colors, sharing the same five activity accents."""
    from pygments.token import Token

    source = Text(code)
    lexer = _preview_lexer(filename) if filename else None
    if lexer is None:
        return source
    palette = activity_palette(dark)
    for start, token, value in lexer.get_tokens_unprocessed(code):
        accent = None
        if token in Token.Comment:
            accent = "muted"
        elif token in Token.Literal.String:
            accent = "green"
        elif token in Token.Keyword or token in Token.Literal.Number:
            accent = "mustard"
        elif token in Token.Name.Function or token in Token.Name.Builtin:
            accent = "blue"
        elif token in Token.Name.Class or token in Token.Operator:
            accent = "lavender"
        if accent:
            source.stylize(palette[accent], start, start + len(value))
    return source


def safe_cell_text(text: str) -> str:
    """Drop leading zero-width characters (see ``_patch_rich_graphemes``)."""
    index = _leading_zero_width(text)
    return text[index:] if index else text


def _leading_zero_width(text: str) -> int:
    index = 0
    while index < len(text) and (text[index] in "\u200d\ufe0f" or not get_character_cell_size(text[index])):
        index += 1
    return index


def _patch_rich_graphemes() -> None:
    """rich 14.3.1 ``split_graphemes`` never advances on a zero-width character
    (combining mark, U+200B, U+FE0F...) with no span before it, and ``cell_len``
    raises on a trailing ZWJ. Every width helper (chop_cells, set_cell_size,
    Textual wrapping) then spins forever on the UI thread and the whole CLI
    freezes. This is the fixed rich 14.3.4 algorithm, installed over the old one."""
    import rich.cells as cells

    if getattr(cells.split_graphemes, "_navin_safe", False):
        return
    special = {"\u200d", "\ufe0f"}

    def split_graphemes(text: str, unicode_version: str = "auto"):
        cell_table = cells.load_cell_table(unicode_version)
        size_of = cells.get_character_cell_size
        count = len(text)
        index = 0
        last_measured: str | None = None
        total = 0
        spans: list[tuple[int, int, int]] = []
        while index < count:
            character = text[index]
            if character in special:
                if not spans:
                    spans.append((index, index + 1, 0))
                    index += 1
                    continue
                start, _end, size = spans[-1]
                if character == "\u200d":
                    index += 2 if index < count - 1 else 1
                    spans[-1] = (start, index, size)
                else:
                    index += 1
                    if last_measured and last_measured in cell_table.narrow_to_wide:
                        last_measured = None
                        size += 1
                        total += 1
                    spans[-1] = (start, index, size)
                continue
            width = size_of(character, unicode_version)
            if width:
                last_measured = character
                spans.append((index, index + 1, width))
                total += width
            elif spans:
                start, _end, size = spans[-1]
                spans[-1] = (start, index + 1, size)
            else:
                spans.append((index, index + 1, 0))
            index += 1
        return spans, total

    def cell_len_text(text: str, unicode_version: str) -> int:
        if cells._is_single_cell_widths(text):
            return len(text)
        if "\u200d" not in text and "\ufe0f" not in text:
            return sum(cells.get_character_cell_size(c, unicode_version) for c in text)
        return cells.split_graphemes(text, unicode_version)[1]

    split_graphemes._navin_safe = True  # type: ignore[attr-defined]
    cells.split_graphemes = split_graphemes
    cells._cell_len = cell_len_text
    with contextlib.suppress(Exception):
        cells.cached_cell_len.cache_clear()


_patch_rich_graphemes()


def chop_cells(text: str, width: int) -> list[str]:
    return _rich_chop_cells(safe_cell_text(text), width)


def format_preview_markup_line(
    number: int | None,
    kind: str,
    text: str,
    *,
    width: int = 0,
    dark: bool = True,
    filename: str = "",
    compact: bool = False,
) -> str:
    """One flat diff: tinted changes, quiet numbers and readable syntax."""
    text = str(text)
    target = min(PREVIEW_MAX_WIDTH, max(1, int(width or PREVIEW_MIN_WIDTH)))
    # Cut before tab expansion, zero-width stripping and the cache key, so a
    # multi-MB line costs the same as a short one (see the cut below).
    keep = target * PREVIEW_MAX_WRAPPED_ROWS * 2 + 1
    cut = len(text) > keep
    if cut:
        text = text[:keep]
    return _preview_markup_line(number, kind, text, target, dark, filename, compact, cut)


@lru_cache(maxsize=8192)
def _preview_markup_line(
    number: int | None,
    kind: str,
    text: str,
    target: int,
    dark: bool,
    filename: str,
    compact: bool,
    cut: bool,
) -> str:
    prefix = format_preview_line(number, kind, "")
    if kind in {"success", "warning", "failure", "info"} and not text.lstrip().startswith(tuple("ℹ✓✔✗×✖⚠")):
        prefix += {"success": "✓ ", "warning": "! ", "failure": "✗ ", "info": "ℹ "}[kind]
    code = safe_cell_text(text.expandtabs(4))
    # A single huge line (minified JSON, a base64 blob, a long model answer)
    # used to be wrapped and highlighted in full on the UI thread, freezing
    # the terminal for seconds. Show its head only; copying keeps the source.
    max_chars = target * PREVIEW_MAX_WRAPPED_ROWS
    if len(code) > max_chars or cut:
        code = code[:max_chars] + " ..."
    gutter = cell_len(prefix)
    if compact:
        clipped = Text(code)
        clipped.truncate(max(1, target - gutter), overflow="ellipsis")
        code = clipped.plain
    if 0 < gutter < target:
        # Keep the source number on the first visual line and repeat the
        # change marker on continuations. Copying still uses the source line.
        continuation = " " * (gutter - 1) + prefix[-1]
        chunks = chop_cells(code, target - gutter) or [""]
        lines = [(prefix if index == 0 else continuation) + chunk for index, chunk in enumerate(chunks)]
    else:
        lines = chop_cells(prefix + code, target) or [""]
    palette = {
        "add": (PREVIEW_ADD_INK, PREVIEW_ADD_BG) if dark else ("#20242A", "#E5F0E8"),
        "del": (PREVIEW_DEL_INK, PREVIEW_DEL_BG) if dark else ("#20242A", "#F5E6E3"),
        "ctx": (PREVIEW_CTX_INK, PREVIEW_CTX_BG) if dark else ("#20242A", "#F5F5F5"),
        "meta": ("#BDBDBD", PREVIEW_CTX_BG) if dark else ("#575D66", "#F5F5F5"),
        # Error text is output, not a verdict: same ink as context.
        "error": (PREVIEW_CTX_INK, PREVIEW_CTX_BG) if dark else ("#20242A", "#F5F5F5"),
        **{
            kind: (activity_palette(dark)[accent], PREVIEW_CTX_BG if dark else "#F5F5F5")
            for kind, accent in {"success": "green", "warning": "mustard", "failure": "coral", "info": "blue"}.items()
        },
    }
    ink, background = palette.get(kind, palette["ctx"])
    accents = activity_palette(dark)
    source = _highlight_preview_source(code, filename, dark)
    rendered = []
    offset = 0
    for line in lines:
        row = Text(line, style=f"{ink} on {background}")
        if number is not None and 0 < gutter < target:
            row.stylize(accents["muted"], 0, len(f"{number:>4}"))
            if kind in {"add", "del"}:
                row.stylize(accents["green" if kind == "add" else "coral"], gutter - 1, gutter)
            length = len(line) - gutter
            for span in source[offset:offset + length].spans:
                row.stylize(span.style, gutter + span.start, gutter + span.end)
            offset += length
        row.pad_right(max(0, target - cell_len(line)))
        rendered.append(row.markup)
    return "\n".join(rendered)


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
    dark: bool = True,
    include_run_output: bool = False,
    rows: list[tuple[int | None, str, str]] | None = None,
    compact: bool = False,
) -> str:
    """Numbered preview for the TUI body: data, metadata, add/del backgrounds."""
    if rows is None:
        rows = preview_rows(
            name,
            arguments,
            result=result,
            error=error,
            output_lines=output_lines,
            diff_text=diff_text,
            limit=limit,
            include_run_output=include_run_output,
        )
    else:
        rows = rows[:max(1, int(limit))]
    filename = _first_path(arguments or {}) if diff_text or any(row[1] in {"add", "del"} for row in rows) else ""
    command_rows = tool_verb(name) == "run" or name in {"test_run", "git"}
    target = min(PREVIEW_MAX_WIDTH, max(1, int(width or PREVIEW_MIN_WIDTH)))
    per_row_chars = target * PREVIEW_MAX_WRAPPED_ROWS
    painted: list[str] = []
    painted_rows = painted_chars = 0
    deadline = time.perf_counter() + PREVIEW_MAX_PAINT_SECONDS
    for index, (number, kind, text) in enumerate(rows):
        if painted and (
            painted_rows >= PREVIEW_MAX_PAINT_ROWS
            or painted_chars >= PREVIEW_MAX_PAINT_CHARS
            or time.perf_counter() > deadline
        ):
            # Whatever the caller asked for, one paint stays bounded. The
            # rest is still copyable from the activity.
            hidden = len(rows) - index
            painted.append(format_preview_markup_line(
                None, "meta", f"{hidden} more line{'s' if hidden != 1 else ''} not shown. Copy for the full output.",
                width=width, dark=dark,
            ))
            break
        if command_rows and kind == "ctx":
            kind = output_kind(text[:per_row_chars])
        line = format_preview_markup_line(number, kind, text, width=width, dark=dark, filename=filename, compact=compact)
        painted.append(line)
        painted_rows += line.count("\n") + 1
        painted_chars += min(len(text), per_row_chars)
    return "\n".join(painted)


def format_tool_detail(
    name: str,
    arguments: dict | None,
    *,
    result: Any = None,
    error: str | None = None,
    output_lines: list[str] | None = None,
    diff_text: str | None = None,
    limit: int = MAX_TRANSCRIPT_LINES,
    include_run_output: bool = False,
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
        include_run_output=include_run_output,
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
        code = result.get("exit_code", result.get("returncode"))
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
        if isinstance(code, int) and not isinstance(code, bool):
            bits.append(f"exit {code}")
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
