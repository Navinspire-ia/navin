# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Append-only files of the world model, written by one background thread.

Same recipe as ``navin.cognition.episodes``: the caller only pays a
``queue.put``; a single daemon thread appends the line, rotating the file
past ``MAX_FILE_BYTES`` (the previous generation is kept as ``.1``). Failures
are logged at debug level and dropped: the journal is training data, not a
ledger, and it must never cost the user a turn.

Three files share the writer: ``trajectories.jsonl`` (one line per tool
call), ``live.jsonl`` (advice outcomes) and ``journal.jsonl`` (events:
trained, activated, rollback...). Reads are synchronous and bounded.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import secrets
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from navin.world_model.paths import (
    journal_path,
    live_path,
    salt_path,
    trajectories_path,
)
from navin.world_model.trajectory import now_stamp, record_is_valid

# Rotate once past this size; the previous generation is kept as ``.1``.
MAX_FILE_BYTES = 24 * 1024 * 1024
# Training reads at most this many rows from the tail of the current file.
MAX_TRAIN_ROWS = 200_000
_MAX_JOURNAL_BYTES = 2 * 1024 * 1024


class _Writer:
    """Single daemon thread appending lines; the caller only enqueues."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Path, str] | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._max_bytes = MAX_FILE_BYTES
        self.appended: dict[str, int] = {}

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ensure_thread(self) -> None:
        if self.alive:
            return
        with self._lock:
            if self.alive:
                return
            self._thread = threading.Thread(target=self._run, name="navin-world-model", daemon=True)
            self._thread.start()

    def enqueue(self, path: Path, line: str) -> None:
        self._ensure_thread()
        self._queue.put((path, line))

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until every line queued so far is on disk (tests, shutdown)."""
        if not self.alive:
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
                key = str(path)
                self.appended[key] = self.appended.get(key, 0) + 1
            except Exception as exc:  # never let the journal hurt a turn
                logger.debug("world-model write skipped {}: {}", path, exc)

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


def _dumps(record: dict[str, Any]) -> str | None:
    try:
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        logger.debug("world-model record not serializable: {}", exc)
        return None


def append_trajectory(workspace: Path | str, record: dict[str, Any]) -> None:
    """Queue one trajectory line. Returns immediately."""
    line = _dumps(record)
    if line is not None:
        _WRITER.enqueue(trajectories_path(workspace), line)


def append_live(workspace: Path | str, record: dict[str, Any]) -> None:
    line = _dumps(record)
    if line is not None:
        _WRITER.enqueue(live_path(workspace), line)


def flush(timeout: float = 2.0) -> bool:
    return _WRITER.flush(timeout)


def writer_alive() -> bool:
    return _WRITER.alive


atexit.register(_WRITER.flush, 0.5)


# --------------------------------------------------------------------------
# Salt
# --------------------------------------------------------------------------

_SALTS: dict[str, str] = {}
_SALT_LOCK = threading.Lock()


def project_salt(workspace: Path | str) -> str:
    """Random per-project salt for the argument hashes, created on first use.

    Only called by the hook once the flag says ``log``: with the flag off no
    ``.navin/world`` folder appears.
    """
    path = salt_path(workspace)
    key = str(path)
    cached = _SALTS.get(key)
    if cached:
        return cached
    with _SALT_LOCK:
        cached = _SALTS.get(key)
        if cached:
            return cached
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if len(value) < 16:
            value = secrets.token_hex(16)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value + "\n", encoding="utf-8")
            except OSError as exc:
                logger.debug("world-model salt not persisted: {}", exc)
        _SALTS[key] = value
        return value


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def _read_lines(path: Path, *, max_bytes: int | None = None) -> list[str]:
    try:
        size = os.stat(path).st_size
    except OSError:
        return []
    if size == 0:
        return []
    with open(path, "rb") as handle:
        if max_bytes is not None and size > max_bytes:
            handle.seek(size - max_bytes)
            data = handle.read()
            cut = data.find(b"\n")
            data = data[cut + 1 :] if cut >= 0 else b""
        else:
            data = handle.read()
    return data.decode("utf-8", errors="replace").splitlines()


def _parse_rows(lines: list[str], *, validate: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if validate and not record_is_valid(record):
            continue
        if isinstance(record, dict):
            rows.append(record)
    return rows


def read_trajectories(workspace: Path | str, *, limit: int = MAX_TRAIN_ROWS) -> list[dict[str, Any]]:
    """Oldest first, current generation only, capped at ``limit`` rows."""
    rows = _parse_rows(_read_lines(trajectories_path(workspace)), validate=True)
    return rows[-max(1, limit):] if len(rows) > limit else rows


def recent_trajectories(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent first, reading only the tail of the file."""
    rows = _parse_rows(_read_lines(trajectories_path(workspace), max_bytes=512 * 1024), validate=True)
    return list(reversed(rows[-max(1, limit):]))


def count_trajectories(workspace: Path | str) -> int:
    """Number of lines in the current generation (a byte scan, no parsing)."""
    path = trajectories_path(workspace)
    try:
        with open(path, "rb") as handle:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))
    except OSError:
        return 0


def read_live(workspace: Path | str, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = _parse_rows(_read_lines(live_path(workspace), max_bytes=256 * 1024), validate=False)
    return rows[-max(1, limit):]


# --------------------------------------------------------------------------
# Event journal (synchronous, tiny)
# --------------------------------------------------------------------------


def journal(workspace: Path | str, event: str, **fields: Any) -> None:
    """Append one event line. Best-effort: a journal problem never stops a job."""
    record = {"ts": now_stamp(), "event": event, **fields}
    path = journal_path(workspace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.stat(path).st_size >= _MAX_JOURNAL_BYTES:
                os.replace(path, path.with_name(f"{path.stem}.1{path.suffix}"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str))
            handle.write("\n")
    except OSError as exc:
        logger.debug("world-model journal skipped {}: {}", path, exc)


def read_journal(workspace: Path | str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = _parse_rows(_read_lines(journal_path(workspace), max_bytes=256 * 1024), validate=False)
    return rows[-max(1, limit):]
