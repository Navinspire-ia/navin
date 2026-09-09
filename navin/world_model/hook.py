# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-turn hook: one trajectory line after each tool call (S3.1).

``create_world_model_hook`` is a turn hook factory registered next to the
episodic journal hook. It answers ``None`` for every turn where the world
model must stay silent: no project, ephemeral turn, heartbeat, or the flag
off. That path is one ``os.stat``.

When it does return a hook, the hook does almost nothing on the turn's
thread: ``before_execute_tool`` notes the clock, ``after_execute_tool`` /
``on_execute_tool_error`` copy the arguments and the head of the result and
hand them to a recorder thread. Normalizing, hashing, scrubbing, classing
and writing all happen there, after the call already answered. ``after_run``
kicks the training runner (a ``queue.put``) when the corridor is on.

The recorder also keeps the live A/B honest: with ``advise`` on and the
gates open, it predicts each call from the same inputs the head will see
(the prediction is deterministic, so "before" and "after" agree) and
compares with the observed class. That feeds the kill switch.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
)
from navin.providers.base import ToolCallRequest
from navin.world_model.journal import append_trajectory, project_salt
from navin.world_model.settings import WorldModelSettings, read_settings
from navin.world_model.trajectory import (
    MAX_OBS_SCAN_CHARS,
    SessionHistory,
    build_trajectory,
    observation_text,
)

_HEARTBEAT_KEY = "heartbeat"
_MAX_SEEN_PER_SESSION = 64


def _is_heartbeat(context: AgentTurnHookContext) -> bool:
    if (context.session_key or "").strip().lower() == _HEARTBEAT_KEY:
        return True
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    return bool(metadata.get(_HEARTBEAT_KEY))


def _read_only(tool: Any, params: Any) -> bool | None:
    if tool is None:
        return None
    checker = getattr(tool, "call_read_only", None)
    try:
        if callable(checker):
            return bool(checker(params))
        return bool(getattr(tool, "read_only", False))
    except Exception:  # noqa: BLE001 - a tool's opinion must not break the journal
        return None


@dataclass(slots=True)
class _Pending:
    workspace: Path
    settings: WorldModelSettings
    tool: str
    arguments: Any
    result_head: str
    is_error: bool
    is_timeout: bool
    read_only: bool | None
    duration_ms: int | None
    session: str
    turn: str | None
    advise_open: bool


class _Recorder:
    """One daemon thread turning pending calls into journal lines, in order."""

    def __init__(self) -> None:
        self._queue: queue.Queue[_Pending | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.history = SessionHistory()
        self._seen: dict[str, dict[str, str]] = {}

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ensure_thread(self) -> None:
        if self.alive:
            return
        with self._lock:
            if self.alive:
                return
            self._thread = threading.Thread(target=self._run, name="navin-world-recorder", daemon=True)
            self._thread.start()

    def submit(self, pending: _Pending) -> None:
        self._ensure_thread()
        self._queue.put(pending)

    def flush(self, timeout: float = 2.0) -> bool:
        if not self.alive:
            return True
        done = threading.Event()
        self._queue.put(done)
        return done.wait(timeout)

    def reset(self) -> None:
        self.history.clear()
        self._seen.clear()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if isinstance(item, threading.Event):
                item.set()
                continue
            try:
                self._record(item)
            except Exception as exc:  # noqa: BLE001 - never let the journal hurt anything
                logger.debug("world-model record skipped: {}", exc)

    def _record(self, item: _Pending) -> None:
        prev = self.history.prev(item.session)
        result: Any = TimeoutError(item.result_head) if item.is_timeout else item.result_head
        trajectory = build_trajectory(
            tool_name=item.tool,
            arguments=item.arguments,
            result=result,
            is_error=item.is_error,
            salt=project_salt(item.workspace),
            duration_ms=item.duration_ms,
            prev=prev,
            session=item.session,
            turn=item.turn,
            read_only=item.read_only,
        )
        if item.advise_open:
            self._track_advice(item, trajectory, prev)
        if item.settings.log:
            append_trajectory(item.workspace, trajectory.as_record())
        self.history.push(item.session, trajectory.tool, trajectory.cls)
        seen = self._seen.setdefault(item.session, {})
        if len(seen) >= _MAX_SEEN_PER_SESSION:
            seen.pop(next(iter(seen)))
        seen[trajectory.args_hash] = trajectory.fingerprint

    def _track_advice(self, item: _Pending, trajectory: Any, prev: list[str]) -> None:
        from navin.world_model import checkpoints as ck
        from navin.world_model.advisor import live_tracker, suggest

        active = ck.active_checkpoint(item.workspace)
        if active is None:
            return
        prediction = active.model.predict(trajectory.tool, trajectory.key, trajectory.args_hash, prev)
        seen_before = self._seen.get(item.session, {}).get(trajectory.args_hash)
        suggestion = suggest(
            prediction,
            tool=trajectory.tool,
            label=trajectory.key.replace("|", " ").strip(),
            threshold=item.settings.confidence_threshold,
            seen_same_answer=item.settings.skip_hint and seen_before is not None,
            read_only=item.read_only is True,
        )
        if suggestion is None:
            return
        actual = trajectory.cls
        if suggestion.kind == "seen":
            # The hit is "same answer again"; the class is beside the point.
            actual = suggestion.cls if seen_before == trajectory.fingerprint else "changed"
        live_tracker().record(item.workspace, suggestion=suggestion, actual_cls=actual)

    def seen_count(self, session: str) -> int:
        return len(self._seen.get(session, {}))


_RECORDER = _Recorder()


def flush_recorder(timeout: float = 2.0) -> bool:
    """Wait for the recorder, then for the writer (tests, shutdown)."""
    from navin.world_model.journal import flush as flush_writer

    ok = _RECORDER.flush(timeout)
    return flush_writer(timeout) and ok


def reset_recorder() -> None:
    _RECORDER.reset()


def recorder_alive() -> bool:
    return _RECORDER.alive


class WorldModelHook(AgentHook):
    """Copy what a tool call saw and hand it to the recorder. Never raises."""

    __slots__ = ("_workspace", "_settings", "_session", "_turn", "_started", "_advise_open")

    def __init__(
        self,
        *,
        workspace: Path,
        settings: WorldModelSettings,
        session_key: str | None,
        turn_id: str | None,
        advise_open: bool,
    ) -> None:
        super().__init__()
        self._workspace = workspace
        self._settings = settings
        self._session = session_key or ""
        self._turn = turn_id
        self._started: dict[str, float] = {}
        self._advise_open = advise_open

    @staticmethod
    def _key(tool_call: ToolCallRequest) -> str:
        call_id = getattr(tool_call, "id", "") or ""
        return f"{call_id}|{tool_call.name}" if call_id else f"{id(tool_call)}|{tool_call.name}"

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        self._started[self._key(tool_call)] = time.monotonic()

    def _submit(self, tool_call: ToolCallRequest, tool: Any, params: Any, outcome: Any, *, is_error: bool) -> None:
        started = self._started.pop(self._key(tool_call), None)
        duration = int((time.monotonic() - started) * 1000) if started is not None else None
        is_timeout = isinstance(outcome, TimeoutError)
        head = observation_text(outcome)[:MAX_OBS_SCAN_CHARS]
        arguments: Any
        if isinstance(params, dict):
            arguments = dict(params)
        else:
            arguments = tool_call.arguments if isinstance(tool_call.arguments, (dict, str)) else params
        _RECORDER.submit(
            _Pending(
                workspace=self._workspace,
                settings=self._settings,
                tool=str(tool_call.name or ""),
                arguments=arguments,
                result_head=head,
                is_error=is_error,
                is_timeout=is_timeout,
                read_only=_read_only(tool, params),
                duration_ms=duration,
                session=self._session,
                turn=self._turn,
                advise_open=self._advise_open,
            )
        )

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        self._submit(tool_call, tool, params, result, is_error=False)

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        self._submit(tool_call, tool, params, error, is_error=True)

    async def after_run(self, context: AgentRunHookContext) -> None:
        self._started.clear()
        if self._settings.train:
            from navin.world_model.jobs import kick

            kick(self._workspace)

    async def on_error(self, context: AgentRunHookContext) -> None:
        self._started.clear()


def create_world_model_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Turn hook factory: the recorder hook, or ``None`` when it must stay off."""
    workspace = context.workspace
    if workspace is None or context.ephemeral or _is_heartbeat(context):
        return None
    settings = read_settings(workspace)
    if not settings.enabled or not (settings.log or settings.advise):
        return None
    advise_open = False
    if settings.advise:
        from navin.world_model.advisor import advice_open

        advise_open = advice_open(workspace)
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    turn_id = metadata.get("turn_id")
    return WorldModelHook(
        workspace=Path(workspace),
        settings=settings,
        session_key=context.session_key,
        turn_id=turn_id if isinstance(turn_id, str) else None,
        advise_open=advise_open,
    )
