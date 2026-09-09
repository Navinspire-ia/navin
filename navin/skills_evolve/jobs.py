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
from pathlib import Path
from typing import Any

from loguru import logger

from navin.skills_evolve.author import DraftBrief
from navin.skills_evolve.drafts import journal, now_stamp
from navin.skills_evolve.paths import queue_path
from navin.skills_evolve.settings import read_settings

_MAX_QUEUE_BYTES = 512 * 1024
_MAX_PENDING = 20


def _read_queue(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
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

    def configure(self, *, auto_thread: bool) -> None:
        self.auto_thread = auto_thread

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def kick(self, workspace: Path) -> None:
        if not self.auto_thread:
            return
        self._ensure_thread()
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
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
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
    jobs = _read_queue(path)
    if any(job.get("brief", {}).get("name") == name for job in jobs):
        return False
    if len(jobs) >= _MAX_PENDING:
        logger.debug("skills-evolve queue full for {}", workspace)
        return False
    jobs.append({"ts": now_stamp(), "source": source, "brief": payload})
    try:
        _write_queue(path, jobs)
    except OSError as exc:
        logger.debug("skills-evolve queue write skipped: {}", exc)
        return False
    journal(workspace, "queued", name=name, source=source)
    _RUNNER.kick(workspace)
    return True


def drain_jobs(workspace: Path | str, deps: Any | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
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
        for _ in range(budget):
            job = jobs.pop(0)
            _write_queue(path, jobs)
            try:
                result = run_pipeline(workspace, job["brief"], deps)
                results.append(result.as_dict())
            except Exception as exc:  # noqa: BLE001 - one bad job must not block the rest
                name = job.get("brief", {}).get("name")
                logger.warning("skills-evolve job {} failed: {}", name, exc)
                journal(workspace, "error", name=name, reason=str(exc)[:200])
                results.append({"name": name, "status": "error", "reason": str(exc)[:200]})
        if not jobs:
            try:
                path.unlink()
            except OSError:
                pass
    return results
