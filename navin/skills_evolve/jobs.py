# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Draft jobs: queued during a turn, run after it, never inside it (S2.1).

The turn hook only ever calls ``enqueue_draft_job``: one line appended to
``.navin/skills-draft/queue.jsonl`` and a ``queue.put``. A single daemon
thread (``_Runner``) drains the file and runs the corridor, one job at a
time, at low priority. ``drain_jobs`` does the same synchronously for the
CLI (``navin agi run``) and for tests, which switch the thread off with
``configure(auto_thread=False)``.

With the project flag off, ``enqueue_draft_job`` returns ``False`` before
touching the disk: no folder, no file, no thread.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from loguru import logger

from navin.skills_evolve.author import DraftBrief
from navin.skills_evolve.drafts import journal, now_stamp
from navin.skills_evolve.paths import queue_path
from navin.skills_evolve.settings import read_settings

_MAX_QUEUE_BYTES = 512 * 1024
_MAX_PENDING = 20


def _read_queue(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as stream:
            lines = stream.read(_MAX_QUEUE_BYTES).splitlines()
    except OSError:
        return []
    jobs: list[dict[str, Any]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("brief"), dict):
            jobs.append(data)
    return jobs


def _write_queue(path: Path, jobs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(job, ensure_ascii=False, separators=(",", ":")) + "\n" for job in jobs)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def pending_jobs(workspace: Path | str) -> list[dict[str, Any]]:
    return _read_queue(queue_path(workspace))


class _Runner:
    """One daemon thread for the whole process; the queue carries workspaces."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Path | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.auto_thread = True
        self._busy = threading.Lock()
        self._workspaces: set[Path] = set()

    def configure(self, *, auto_thread: bool) -> None:
        self.auto_thread = auto_thread

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def kick(self, workspace: Path) -> None:
        if not self.auto_thread:
            return
        self._ensure_thread()
        with self._lock:
            self._workspaces.add(workspace)
        self._queue.put(workspace)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        if not self.alive:
            return True
        done = threading.Event()
        self._queue.put(done)
        return done.wait(timeout)

    def _ensure_thread(self) -> None:
        if self.alive:
            return
        with self._lock:
            if self.alive:
                return
            self._thread = threading.Thread(
                target=self._run, name="navin-skills-evolve", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        _lower_priority()
        while True:
            try:
                item = self._queue.get(timeout=30)
            except queue.Empty:
                if self.auto_thread:
                    with self._lock:
                        workspaces = tuple(self._workspaces)
                    for workspace in workspaces:
                        if pending_jobs(workspace):
                            self._queue.put(workspace)
                        else:
                            with self._lock:
                                self._workspaces.discard(workspace)
                continue
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            if not self.auto_thread:
                continue
            try:
                drain_jobs(item)
            except Exception as exc:  # noqa: BLE001 - the corridor must never crash the host
                logger.warning("skills-evolve job runner error for {}: {}", item, exc)

    def drain_lock(self) -> threading.Lock:
        return self._busy


_RUNNER = _Runner()


def _lower_priority() -> None:
    try:
        os.nice(5)
    except (OSError, AttributeError):
        pass


def configure(*, auto_thread: bool) -> None:
    """Tests and CLIs switch the daemon thread off and drain by hand."""
    _RUNNER.configure(auto_thread=auto_thread)


def runner_alive() -> bool:
    return _RUNNER.alive


def resume_jobs(workspace: Path) -> None:
    if read_settings(workspace).feature("draft") and pending_jobs(workspace):
        _RUNNER.kick(workspace)


def wait_idle(timeout: float = 30.0) -> bool:
    return _RUNNER.wait_idle(timeout)


def enqueue_draft_job(workspace: Path | str, brief: DraftBrief | dict[str, Any], *, source: str = "hook") -> bool:
    """Queue one draft job. ``False`` (and no disk write) when the flag is off."""
    workspace = Path(workspace)
    if not read_settings(workspace).feature("draft"):
        return False
    payload = brief.as_dict() if isinstance(brief, DraftBrief) else dict(brief)
    name = payload.get("name")
    path = queue_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path) + ".lock", timeout=2):
            jobs = _read_queue(path)
            if any(job.get("brief", {}).get("name") == name for job in jobs):
                return False
            if len(jobs) >= _MAX_PENDING:
                logger.debug("skills-evolve queue full for {}", workspace)
                return False
            jobs.append({"id": uuid.uuid4().hex, "ts": now_stamp(), "source": source, "brief": payload})
            _write_queue(path, jobs)
    except (OSError, Timeout) as exc:
        logger.debug("skills-evolve queue write skipped: {}", exc)
        return False
    journal(workspace, "queued", name=name, source=source)
    _RUNNER.kick(workspace)
    return True


def drain_jobs(workspace: Path | str, deps: Any | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = queue_path(workspace)
    if not read_settings(workspace).feature("draft") or not path.is_file():
        return []
    try:
        # A crashed process releases this OS lock; its pending job stays on disk.
        with FileLock(str(path) + ".drain.lock", timeout=0):
            return _drain_jobs(workspace, deps, limit=limit)
    except Timeout:
        return []


def _drain_jobs(workspace: Path | str, deps: Any | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Run every pending job of ``workspace`` now, in this thread.

    Jobs are removed from the queue one by one as they complete, so a crash
    mid-run leaves the remaining ones for the next drain.
    """
    from navin.skills_evolve.pipeline import run_pipeline

    workspace = Path(workspace)
    results: list[dict[str, Any]] = []
    if not read_settings(workspace).feature("draft"):
        return results
    path = queue_path(workspace)
    with _RUNNER.drain_lock():
        jobs = _read_queue(path)
        if not jobs:
            return results
        budget = len(jobs) if limit is None else max(0, min(limit, len(jobs)))
        for job in jobs[:budget]:
            if not read_settings(workspace).feature("draft"):
                break
            if job.get("not_before", 0) > time.time():
                continue
            name = job.get("brief", {}).get("name")
            retry = False
            try:
                result = run_pipeline(workspace, job["brief"], deps)
                results.append(result.as_dict())
                retry = result.status == "retry"
            except Exception as exc:  # noqa: BLE001 - one bad job must not block the rest
                logger.warning("skills-evolve job {} failed: {}", name, exc)
                journal(workspace, "error", name=name, reason=str(exc)[:200])
                results.append({"name": name, "status": "error", "reason": str(exc)[:200]})
                retry = True
            with FileLock(str(path) + ".lock", timeout=2):
                current = _read_queue(path)
                def matches(item):
                    return item.get("id") == job["id"] if job.get("id") else item == job
                if retry:
                    for item in current:
                        if matches(item):
                            item["retries"] = int(item.get("retries", 0)) + 1
                            item["not_before"] = time.time() + min(3600, 60 * 2 ** min(item["retries"], 6))
                else:
                    current = [item for item in current if not matches(item)]
                if current:
                    _write_queue(path, current)
                else:
                    path.unlink(missing_ok=True)
    return results
