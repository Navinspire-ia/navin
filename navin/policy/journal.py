"""Append-only files of policy learning, written by one background thread.

Same recipe as ``navin.world_model.journal``: the caller only pays a
``queue.put``; a single daemon thread appends the line, rotating the file
past ``MAX_FILE_BYTES`` (the previous generation is kept as ``.1``). Failures
are logged at debug level and dropped: the journal is training data, not a
ledger.

Two things are different from S3 and matter:

* the writer is fed by the **eval runner** after an episode, never by a
  chat turn. A chat turn does not even import this module;
* ``append_step`` refuses any record that is not eval-sourced with a 0/1
  reward. There is no path by which a chat signal becomes a reward.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from navin.policy.paths import journal_path, live_path, trajectories_path
from navin.policy.trajectory import now_stamp, record_is_valid

MAX_FILE_BYTES = 24 * 1024 * 1024
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
            self._thread = threading.Thread(target=self._run, name="navin-policy-journal", daemon=True)
            self._thread.start()

    def enqueue(self, path: Path, line: str) -> None:
        self._ensure_thread()
        self._queue.put((path, line))

    def flush(self, timeout: float = 2.0) -> bool:
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
            except Exception as exc:  # noqa: BLE001 - never let the journal hurt anything
                logger.debug("policy write skipped {}: {}", path, exc)

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
        logger.debug("policy record not serializable: {}", exc)
        return None


def append_step(workspace: Path | str, record: dict[str, Any]) -> bool:
    """Queue one eval step. Returns False (and writes nothing) for anything
    that is not an eval-sourced record with a 0/1 reward."""
    if not record_is_valid(record):
        logger.debug("policy step refused: not an eval record")
        return False
    line = _dumps(record)
    if line is None:
        return False
    _WRITER.enqueue(trajectories_path(workspace), line)
    return True


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


def read_steps(workspace: Path | str, *, limit: int = MAX_TRAIN_ROWS) -> list[dict[str, Any]]:
    """Oldest first, current generation only, capped at ``limit`` rows."""
    rows = _parse_rows(_read_lines(trajectories_path(workspace)), validate=True)
    return rows[-max(1, limit) :] if len(rows) > limit else rows


def recent_steps(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Most recent first, reading only the tail of the file."""
    rows = _parse_rows(_read_lines(trajectories_path(workspace), max_bytes=512 * 1024), validate=True)
    return list(reversed(rows[-max(1, limit) :]))


def count_steps(workspace: Path | str) -> int:
    """Number of lines in the current generation (a byte scan, no parsing)."""
    path = trajectories_path(workspace)
    try:
        with open(path, "rb") as handle:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))
    except OSError:
        return 0


def count_episodes(rows: list[dict[str, Any]]) -> int:
    return len({str(r.get("episode")) for r in rows})


def read_live(workspace: Path | str, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = _parse_rows(_read_lines(live_path(workspace), max_bytes=256 * 1024), validate=False)
    return rows[-max(1, limit) :]


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
        logger.debug("policy journal skipped {}: {}", path, exc)


def read_journal(workspace: Path | str, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = _parse_rows(_read_lines(journal_path(workspace), max_bytes=256 * 1024), validate=False)
    return rows[-max(1, limit) :]
