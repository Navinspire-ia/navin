# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Append-only episodic journal: one compact JSON line per finished turn.

File: ``<project>/.navin/memory/episodes.jsonl``. A record keeps what is
needed to find the turn again later (who asked what, in which desk, what
Navin answered, which tools it used) and nothing else: no full transcript,
no tool output, no reasoning. Sizes are capped so the file grows by a few
hundred bytes per turn.

Writes go through one daemon thread fed by a queue, so the agent loop only
pays a ``queue.put``. Failures are logged at debug level and dropped: the
journal is a memory aid, not a ledger, and it must never cost the user a
turn. Reads (``search_episodes``) scan the tail of the file only, bounded
in bytes, so recall stays fast no matter how old the project is.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.helpers import strip_think
from navin.workspace_layout import memory_dir

EPISODES_NAME = "episodes.jsonl"
RECORD_VERSION = 1

MAX_USER_CHARS = 600
MAX_REPLY_CHARS = 900
MAX_ERROR_CHARS = 200
MAX_TOOLS = 24
# Rotate once past this size; the previous generation is kept as ``.1``.
MAX_FILE_BYTES = 24 * 1024 * 1024
# Recall reads at most this many bytes from the end of the current file.
SEARCH_TAIL_BYTES = 2 * 1024 * 1024

_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def episodes_path(workspace: Path | str) -> Path:
    return memory_dir(workspace) / EPISODES_NAME


# --------------------------------------------------------------------------
# Record shape
# --------------------------------------------------------------------------


def _clip(text: str, limit: int) -> str:
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _text_of(content: Any) -> str:
    """Plain text of a chat message content (string or OpenAI parts list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def strip_runtime_context(text: str) -> str:
    """Drop the runtime metadata block the loop appends after the user's words."""
    from navin.runtime_context import RUNTIME_CONTEXT_TAG

    cut = text.find(RUNTIME_CONTEXT_TAG)
    return text if cut < 0 else text[:cut]


def first_user_text(messages: list[dict[str, Any]] | None) -> str:
    """First user line, without the runtime-context suffix."""
    if not messages:
        return ""
    from navin.runtime_context import public_history_message

    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        shown = public_history_message(message)
        text = strip_runtime_context(_text_of(shown.get("content")))
        if text.strip():
            return text
    return ""


def last_user_text(messages: list[dict[str, Any]] | None) -> str:
    """Text of the most recent ``user`` message, scanning from the end.

    The runtime context (project map, date, channel) that travels inside the
    same message is not something the user said, so it is cut off.
    """
    if not messages:
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = strip_runtime_context(_text_of(message.get("content")))
        if text.strip():
            return text
    return ""


def build_episode(
    *,
    channel: str,
    chat_id: str,
    session_key: str | None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    user_text: str,
    reply: str | None,
    tools_used: list[str] | None,
    stop_reason: str | None,
    error: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """One journal record, or ``None`` when there is nothing worth keeping."""
    user = _clip(strip_think(user_text or ""), MAX_USER_CHARS)
    answer = _clip(strip_think(reply or ""), MAX_REPLY_CHARS)
    if not user and not answer:
        return None
    seen: set[str] = set()
    tools: list[str] = []
    for name in tools_used or ():
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            tools.append(name)
            if len(tools) >= MAX_TOOLS:
                break
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))
    record: dict[str, Any] = {
        "v": RECORD_VERSION,
        "ts": stamp,
        "channel": channel or "",
        "chat_id": str(chat_id or ""),
        "session": session_key or "",
        "user": user,
        "reply": answer,
        "tools": tools,
        "stop": stop_reason or "",
    }
    if turn_id:
        record["turn"] = turn_id
    module = (metadata or {}).get("product_module")
    if isinstance(module, str) and module:
        record["module"] = module
    if error:
        record["error"] = _clip(str(error), MAX_ERROR_CHARS)
    return record


# --------------------------------------------------------------------------
# Background writer
# --------------------------------------------------------------------------


class _Writer:
    """Single daemon thread appending lines; the caller only enqueues."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Path, str] | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._max_bytes = MAX_FILE_BYTES

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="navin-episodes", daemon=True
            )
            self._thread.start()

    def enqueue(self, path: Path, line: str) -> None:
        self._ensure_thread()
        self._queue.put((path, line))

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until every line queued so far is on disk (tests, shutdown)."""
        if self._thread is None or not self._thread.is_alive():
            return True
        done = threading.Event()
        self._queue.put(done)
        return done.wait(timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            path, line = item
            try:
                self._append(path, line)
            except Exception as exc:  # never let the journal hurt a turn
                logger.debug("episode journal write skipped {}: {}", path, exc)

    def _append(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.stat(path).st_size >= self._max_bytes:
                os.replace(path, path.with_name(f"{path.stem}.1{path.suffix}"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


_WRITER = _Writer()


def append_episode(workspace: Path | str, record: dict[str, Any]) -> None:
    """Queue one record for the background writer. Returns immediately."""
    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        logger.debug("episode record not serializable: {}", exc)
        return
    _WRITER.enqueue(episodes_path(workspace), line)


def flush_episodes(timeout: float = 2.0) -> bool:
    return _WRITER.flush(timeout)


atexit.register(_WRITER.flush, 0.5)


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def tokenize(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _read_tail_lines(path: Path, max_bytes: int) -> list[str]:
    try:
        size = os.stat(path).st_size
    except OSError:
        return []
    if size == 0:
        return []
    with open(path, "rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            data = handle.read()
            cut = data.find(b"\n")
            data = data[cut + 1 :] if cut >= 0 else b""
        else:
            data = handle.read()
    return data.decode("utf-8", errors="replace").splitlines()


def iter_episodes(workspace: Path | str, *, max_bytes: int = SEARCH_TAIL_BYTES) -> list[dict[str, Any]]:
    """Most recent records first, from the tail of the current file."""
    records: list[dict[str, Any]] = []
    for line in reversed(_read_tail_lines(episodes_path(workspace), max_bytes)):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def score_episode(record: dict[str, Any], tokens: list[str], phrase: str) -> int:
    """Distinct query tokens found in the record, plus a bonus for the whole phrase."""
    haystack = " ".join(
        (
            str(record.get("user", "")),
            str(record.get("reply", "")),
            " ".join(str(t) for t in record.get("tools", []) or []),
            str(record.get("module", "")),
            str(record.get("error", "")),
        )
    ).lower()
    score = sum(1 for token in tokens if token in haystack)
    if score and phrase and len(phrase) >= 6 and phrase in haystack:
        score += 2
    return score


def search_episodes(
    workspace: Path | str,
    query: str,
    *,
    limit: int = 5,
    max_bytes: int = SEARCH_TAIL_BYTES,
) -> list[tuple[int, dict[str, Any]]]:
    """Top ``limit`` matching records as ``(score, record)``, best first.

    Ties keep the most recent record first, which is what "like last time"
    usually means.
    """
    tokens = tokenize(query)
    if not tokens:
        return []
    phrase = _WS_RE.sub(" ", query.lower()).strip()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for recency, record in enumerate(iter_episodes(workspace, max_bytes=max_bytes)):
        score = score_episode(record, tokens, phrase)
        if score > 0:
            scored.append((score, -recency, record))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(score, record) for score, _recency, record in scored[: max(1, limit)]]


def format_episode(record: dict[str, Any]) -> str:
    stamp = str(record.get("ts", ""))[:16].replace("T", " ")
    head = [stamp or "?", str(record.get("channel") or "?")]
    module = record.get("module")
    if module:
        head.append(str(module))
    lines = [" | ".join(head)]
    user = record.get("user")
    if user:
        lines.append(f"User: {user}")
    reply = record.get("reply")
    if reply:
        lines.append(f"Navin: {reply}")
    tools = record.get("tools") or []
    if tools:
        lines.append("Tools: " + ", ".join(str(t) for t in tools))
    error = record.get("error")
    if error:
        lines.append(f"Error: {error}")
    return "\n".join(lines)
