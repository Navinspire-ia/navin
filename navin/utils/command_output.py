# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Short terminal previews; the original command output stays available."""

from __future__ import annotations

import re
import shlex
from bisect import insort
from collections import deque
from itertools import islice
from operator import itemgetter

from rich.text import Text

COMMAND_PREVIEW_LINES = 6
GIT_PREVIEW_LINES = 16

_EXIT = re.compile(r"^(?:exit(?: code)?\s*:?|process exited with code)\s*(-?\d+)\s*$", re.I)
_WARNING = re.compile(r"\b([A-Za-z_]\w*Warning):\s*(.+)")
_COUNT = re.compile(r"\b(\d+)\s+(passed|failed|errors?|warnings?|skipped|deselected|xfailed|xpassed)\b", re.I)
_NODE_COUNT = re.compile(r"^(?:[ℹ#]\s*)?(pass|fail|cancelled|skipped)\s+(\d+)\b", re.I)
# A grep/rg hit or context line (``466-``, ``466:``, ``path:466:``) followed by
# source code: file content that happens to name an exception, not an outcome.
_SOURCE_LOCATION = re.compile(r"^(?:[\w./\\-]+:)?\d+[-:]\s*")
_SOURCE_CODE = re.compile(
    r"^(?:except\b|catch\b|raise\b|throw\b|class\b|(?:async\s+)?def\b|elif\b|return\b"
    r"|#|//|/\*|\*\s|\"\"\"|''')"
)


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


def is_exit_line(text: str) -> bool:
    """The ``Exit code: N`` status line of a finished command."""
    clean = Text.from_ansi(text).plain if "\x1b" in text else text
    return bool(_EXIT.fullmatch(clean.strip().strip("= ")))


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
    if _SOURCE_CODE.match(_SOURCE_LOCATION.sub("", clean, count=1)):
        # ``except Exception:`` printed by grep, cat or sed is code, not a failure.
        return "ctx"
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
    if isinstance(cache, CompactRowsCache):
        cache.update(rows)
        return cache.select(limit)
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


def align_appended(old: list, new: list, tries: int = 4) -> tuple[int, int] | None:
    """How a live log moved since the last paint: ``(dropped, kept)``.

    ``new[:kept] == old[dropped:dropped + kept]`` and at most the last old
    item was rewritten (a partial line that grew, a progress redraw). Any
    match is valid, since items are processed independently; None means
    the log changed some other way and needs a full recompute.
    """
    if not old or not new:
        return None
    first = new[0]
    start = 0
    for _ in range(tries):
        try:
            dropped = old.index(first, start)
        except ValueError:
            return None
        for kept in (len(old) - dropped, len(old) - dropped - 1):
            if 0 < kept <= len(new) and new[:kept] == old[dropped:dropped + kept]:
                return dropped, kept
        start = dropped + 1
    return None


class CompactRowsCache:
    """``compact_command_rows`` for one live log, updated with its new rows.

    Each paint classifies only the rows appended since the last one and
    forgets the rows that scrolled out of the retained tail, so a 5000-line
    log costs its new lines, not 5000 regex passes, ten times a second.
    """

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._raws: list[str] = []
        self._kinds: list[str] = []
        # Absolute position of the first retained row.
        self._base = 0
        self._parsed: deque[tuple[int, str, str, bool] | None] = deque()
        # priority -> positions of non-warning candidates, oldest first.
        self._levels: dict[int, deque[int]] = {}
        # warning message -> positions, and (first position, message) sorted.
        self._warnings: dict[str, deque[int]] = {}
        self._firsts: list[tuple[int, str]] = []

    def __len__(self) -> int:
        return len(self._parsed)

    def update(self, rows: list[tuple[int | None, str, str]]) -> None:
        raws = list(map(itemgetter(2), rows))
        kinds = list(map(itemgetter(1), rows))
        head = dropped = kept = 0
        # A finished command may put its error above the streamed output:
        # a few new leading rows, then the same log.
        for head in range(min(8, len(raws))) if self._raws else ():
            match = align_appended(self._raws, raws[head:] if head else raws)
            if match is not None and kinds[head:head + match[1]] == self._kinds[match[0]:match[0] + match[1]]:
                dropped, kept = match
                break
        else:
            self.clear()
            head = 0
        for _ in range(dropped):
            self._pop_head()
        while len(self._parsed) > kept:
            self._pop_tail()
        for kind, raw in zip(kinds[head + kept:], raws[head + kept:]):
            self._push(kind, raw)
        for index in range(head - 1, -1, -1):
            self._push_head(kinds[index], raws[index])
        self._raws, self._kinds = raws, kinds

    def select(self, limit: int) -> list[tuple[int | None, str, str]]:
        """The same rows as a full ``compact_command_rows`` pass."""
        picked: list[tuple[int, str | None]] = []
        priorities = set(self._levels)
        if self._firsts:
            priorities.add(80)
        for priority in sorted(priorities, reverse=True):
            need = limit - len(picked)
            if need <= 0:
                break
            group = [(position, None) for position in islice(reversed(self._levels.get(priority, ())), need)]
            if priority == 80:
                group += list(islice(reversed(self._firsts), need))
                group.sort(key=itemgetter(0), reverse=True)
            picked += group[:need]
        picked.sort(key=itemgetter(0))
        selected: list[tuple[int | None, str, str]] = []
        for number, (position, message) in enumerate(picked, 1):
            if message is None:
                _, semantic, line, _ = self._parsed[position - self._base]
                selected.append((number, semantic, line))
            else:
                count = len(self._warnings[message])
                selected.append((number, "warning", (f"(x{count}) " if count > 1 else "") + message))
        return selected

    def _push(self, kind: str, raw: str) -> None:
        position = self._base + len(self._parsed)
        parsed = _command_line(kind, raw)
        self._parsed.append(parsed)
        if parsed is None:
            return
        priority, _, line, warning = parsed
        if not warning:
            self._levels.setdefault(priority, deque()).append(position)
        elif line in self._warnings:
            self._warnings[line].append(position)
        else:
            # The newest row has the highest position: the list stays sorted.
            self._warnings[line] = deque([position])
            self._firsts.append((position, line))

    def _push_head(self, kind: str, raw: str) -> None:
        self._base -= 1
        position = self._base
        parsed = _command_line(kind, raw)
        self._parsed.appendleft(parsed)
        if parsed is None:
            return
        priority, _, line, warning = parsed
        if not warning:
            self._levels.setdefault(priority, deque()).appendleft(position)
            return
        positions = self._warnings.setdefault(line, deque())
        if positions:
            self._firsts.remove((positions[0], line))
        positions.appendleft(position)
        # The oldest row now: this message's first, before every other.
        self._firsts.insert(0, (position, line))

    def _pop_head(self) -> None:
        parsed = self._parsed.popleft()
        self._base += 1
        if parsed is None:
            return
        priority, _, line, warning = parsed
        if not warning:
            self._levels[priority].popleft()
            return
        positions = self._warnings[line]
        positions.popleft()
        # The oldest row was this message's first occurrence, and the oldest.
        del self._firsts[0]
        if positions:
            insort(self._firsts, (positions[0], line))
        else:
            del self._warnings[line]

    def _pop_tail(self) -> None:
        parsed = self._parsed.pop()
        if parsed is None:
            return
        priority, _, line, warning = parsed
        if not warning:
            self._levels[priority].pop()
            return
        positions = self._warnings[line]
        positions.pop()
        if not positions:
            # Its only occurrence was the newest row: the newest first, too.
            del self._warnings[line]
            self._firsts.pop()
