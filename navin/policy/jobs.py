"""Training jobs: counted after a turn, run in a child process, never inside.

The turn hook only ever calls ``note_turn``: one counter increment and, when
``train_every`` turns went by, a ``queue.put`` of the workspace. A daemon
thread takes the item and **spawns** ``python -m navin.policy.train_job``
for it (S4.2: separate process, not the gateway's). The thread waits on the
child with a hard timeout, kills it past that, and reads one JSON result.

``run_due`` does the same synchronously for the CLI and for tests; tests
switch the thread off with ``configure(auto_thread=False)`` and may ask for
an in-process run with ``configure(inline=True)``.

With the project flag off, ``note_turn`` returns before touching anything.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.policy.journal import journal
from navin.policy.settings import read_settings

# Do not re-run training for the same project sooner than this.
_MIN_INTERVAL_S = 15 * 60
# Wall-clock ceiling on the child, over its own training timeout.
_CHILD_GRACE_S = 60.0


class _Runner:
    def __init__(self) -> None:
        self._queue: queue.Queue[Path | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._busy = threading.Lock()
        self._last_run: dict[str, float] = {}
        self._turns: dict[str, int] = {}
        self.auto_thread = True
        self.inline = False
        self.last_result: dict[str, Any] | None = None

    def configure(self, *, auto_thread: bool | None = None, inline: bool | None = None) -> None:
        if auto_thread is not None:
            self.auto_thread = auto_thread
        if inline is not None:
            self.inline = inline

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
            self._thread = threading.Thread(target=self._run, name="navin-policy-train", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                self.last_result = run_due(item)
            except Exception as exc:  # noqa: BLE001 - the sidecar must never crash the host
                logger.warning("policy train runner error for {}: {}", item, exc)

    def recently_ran(self, workspace: Path, now: float) -> bool:
        last = self._last_run.get(str(workspace))
        return last is not None and now - last < _MIN_INTERVAL_S

    def mark_ran(self, workspace: Path, now: float) -> None:
        self._last_run[str(workspace)] = now

    def turns(self, workspace: Path) -> int:
        return self._turns.get(str(workspace), 0)

    def add_turn(self, workspace: Path) -> int:
        key = str(workspace)
        self._turns[key] = self._turns.get(key, 0) + 1
        return self._turns[key]

    def reset_turns(self, workspace: Path) -> None:
        self._turns.pop(str(workspace), None)

    def busy_lock(self) -> threading.Lock:
        return self._busy

    def reset(self) -> None:
        self._last_run.clear()
        self._turns.clear()
        self.last_result = None


_RUNNER = _Runner()


def configure(*, auto_thread: bool | None = None, inline: bool | None = None) -> None:
    _RUNNER.configure(auto_thread=auto_thread, inline=inline)


def runner_alive() -> bool:
    return _RUNNER.alive


def wait_idle(timeout: float = 30.0) -> bool:
    return _RUNNER.wait_idle(timeout)


def reset_runner() -> None:
    _RUNNER.reset()


def turns_since_train(workspace: Path | str) -> int:
    return _RUNNER.turns(Path(workspace))


def note_turn(workspace: Path | str) -> bool:
    """One chat turn ended in this project. Cheap; ``True`` when a job was queued."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.feature("train"):
        return False
    count = _RUNNER.add_turn(workspace)
    if count < settings.train_every:
        return False
    if _RUNNER.recently_ran(workspace, time.monotonic()):
        return False
    _RUNNER.kick(workspace)
    return True


def training_due(workspace: Path | str) -> bool:
    settings = read_settings(workspace)
    return settings.feature("train") and _RUNNER.turns(Path(workspace)) >= settings.train_every


# --------------------------------------------------------------------------
# The child process
# --------------------------------------------------------------------------


def spawn_train(
    workspace: Path | str,
    *,
    actor: str = "auto",
    force: bool = False,
    collect: bool = True,
    timeout_s: float = 120.0,
    max_rss_mb: int = 512,
    max_cases: int = 400,
) -> dict[str, Any]:
    """Run one training job in a child process and return its result dict.

    The child is killed past ``timeout_s + grace``; a dead or silent child
    is reported as ``status: error`` and N stays active.
    """
    workspace = Path(workspace)
    command = [
        sys.executable,
        "-m",
        "navin.policy.train_job",
        "--workspace",
        str(workspace),
        "--actor",
        actor,
        "--timeout",
        str(timeout_s),
        "--max-rss-mb",
        str(max_rss_mb),
        "--max-cases",
        str(max_cases),
    ]
    if force:
        command.append("--force")
    if not collect:
        command.append("--no-collect")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NAVIN_GATEWAY")}
    env["NAVIN_POLICY_TRAIN_CHILD"] = "1"
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s + _CHILD_GRACE_S,
            env=env,
            cwd=str(workspace) if workspace.is_dir() else None,
            check=False,
        )
    except subprocess.TimeoutExpired:
        reason = f"training child killed after {timeout_s + _CHILD_GRACE_S:.0f}s"
        journal(workspace, "train_failed", reason=reason, actor=actor)
        return {"status": "budget_exceeded", "reason": reason, "duration_ms": int((time.monotonic() - started) * 1000)}
    except OSError as exc:
        reason = f"training child could not start: {exc}"
        journal(workspace, "train_failed", reason=reason, actor=actor)
        return {"status": "error", "reason": reason}
    line = ""
    for candidate in reversed(completed.stdout.splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate
            break
    if completed.returncode != 0 or not line:
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        reason = f"training child exited {completed.returncode}" + (": " + " | ".join(tail) if tail else "")
        journal(workspace, "train_failed", reason=reason[:500], actor=actor)
        return {"status": "error", "reason": reason[:500], "duration_ms": int((time.monotonic() - started) * 1000)}
    try:
        result = json.loads(line)
    except json.JSONDecodeError:
        reason = "training child answered something that is not JSON"
        journal(workspace, "train_failed", reason=reason, actor=actor)
        return {"status": "error", "reason": reason}
    if not isinstance(result, dict):
        return {"status": "error", "reason": "training child answered a non-object"}
    result.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
    result["process"] = "child"
    # The child wrote the files; this process must not serve a stale adapter.
    from navin.policy.checkpoints import clear_checkpoint_cache

    clear_checkpoint_cache()
    return result


def _train_inline(workspace: Path, *, actor: str, force: bool) -> dict[str, Any]:
    from navin.policy.train import train

    result = train(workspace, actor=actor, force=force).as_dict()
    result["process"] = "inline"
    return result


def run_due(workspace: Path | str, *, force: bool = False, actor: str | None = None) -> dict[str, Any] | None:
    """Train now if due (or ``force``), from this thread, in a child. ``None`` when nothing ran."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return None
    if not settings.train and not force:
        return None
    with _RUNNER.busy_lock():
        now = time.monotonic()
        if not force and (_RUNNER.recently_ran(workspace, now) or not training_due(workspace)):
            return None
        _RUNNER.mark_ran(workspace, now)
        _RUNNER.reset_turns(workspace)
        who = actor or ("human" if force else "auto")
        if _RUNNER.inline:
            return _train_inline(workspace, actor=who, force=force)
        return spawn_train(workspace, actor=who, force=force)
