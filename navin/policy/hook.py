"""Per-turn hook of policy learning: count the turn, keep the live A/B honest.

``create_policy_hook`` is a turn hook factory registered next to the world
model hook. It answers ``None`` for every turn where policy learning must
stay silent: no project, ephemeral turn, heartbeat, flag off, or neither
``train`` nor an open ``steer`` gate. That path is one ``os.stat``.

The hook writes **no trajectory**: chat turns are never training data (S4.1,
the reward comes from evals only). On the turn's thread it does two cheap
things:

* ``after_run``: one counter increment, and a ``queue.put`` when
  ``train_every`` turns went by (the job then runs in a child process);
* with ``steer`` open: before each tool call, remember what the head would
  have proposed for the current state; after it, compare with the tool
  really called. The comparison itself runs on the recorder thread and feeds
  the kill switch (S4.4).
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext, AgentTurnHookContext
from navin.policy.settings import PolicySettings, read_settings
from navin.policy.trajectory import STOP, intent_of
from navin.providers.base import ToolCallRequest
from navin.world_model.trajectory import (
    HISTORY,
    MAX_OBS_SCAN_CHARS,
    classify_observation,
    observation_text,
)

_HEARTBEAT_KEY = "heartbeat"


def _is_heartbeat(context: AgentTurnHookContext) -> bool:
    if (context.session_key or "").strip().lower() == _HEARTBEAT_KEY:
        return True
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    return bool(metadata.get(_HEARTBEAT_KEY))


@dataclass(slots=True)
class _Pending:
    workspace: Path
    intent: str
    prev: tuple[str, ...]
    action: str
    result_head: str
    is_error: bool
    is_timeout: bool


class _Recorder:
    """One daemon thread comparing what the head proposed with what happened."""

    def __init__(self) -> None:
        self._queue: queue.Queue[_Pending | threading.Event | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ensure_thread(self) -> None:
        if self.alive:
            return
        with self._lock:
            if self.alive:
                return
            self._thread = threading.Thread(target=self._run, name="navin-policy-recorder", daemon=True)
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
            except Exception as exc:  # noqa: BLE001 - never let the sidecar hurt anything
                logger.debug("policy live record skipped: {}", exc)

    @staticmethod
    def _record(item: _Pending) -> None:
        from navin.policy.steerer import live_tracker, suggest_next

        suggestion = suggest_next(item.workspace, intent=item.intent, prev=item.prev)
        if suggestion is None or not suggestion.action:
            return
        live_tracker().record(item.workspace, suggestion=suggestion, actual=item.action)


_RECORDER = _Recorder()


def flush_recorder(timeout: float = 2.0) -> bool:
    from navin.policy.journal import flush as flush_writer

    ok = _RECORDER.flush(timeout)
    return flush_writer(timeout) and ok


def recorder_alive() -> bool:
    return _RECORDER.alive


class PolicyHook(AgentHook):
    """Count the turn; when steer is open, feed the live A/B. Never raises."""

    __slots__ = ("_workspace", "_settings", "_steer_open", "_intent", "_prev")

    def __init__(self, *, workspace: Path, settings: PolicySettings, steer_open: bool, user_text: str | None) -> None:
        super().__init__()
        self._workspace = workspace
        self._settings = settings
        self._steer_open = steer_open
        self._intent: str | None = intent_of(user_text) if (steer_open and user_text) else None
        self._prev: tuple[str, ...] = ()

    def _intent_key(self) -> str:
        """The request's intent, read once from the bound request context."""
        if self._intent is None:
            from navin.agent.tools.context import current_request_context

            ctx = current_request_context()
            self._intent = intent_of(getattr(ctx, "original_user_text", None) if ctx is not None else None)
        return self._intent

    def _submit(self, tool_call: ToolCallRequest, outcome: Any, *, is_error: bool) -> None:
        if not self._steer_open:
            return
        action = str(tool_call.name or "")
        head = observation_text(outcome)[:MAX_OBS_SCAN_CHARS]
        _RECORDER.submit(
            _Pending(
                workspace=self._workspace,
                intent=self._intent_key(),
                prev=self._prev,
                action=action,
                result_head=head,
                is_error=is_error,
                is_timeout=isinstance(outcome, TimeoutError),
            )
        )
        cls = classify_observation(action, TimeoutError(head) if isinstance(outcome, TimeoutError) else head, is_error=is_error)
        self._prev = (*self._prev, f"{action}:{cls}")[-HISTORY:]

    async def after_execute_tool(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, result: Any) -> None:
        self._submit(tool_call, result, is_error=False)

    async def on_execute_tool_error(self, context: AgentHookContext, tool_call: ToolCallRequest, tool: Any, params: Any, error: Any) -> None:
        self._submit(tool_call, error, is_error=True)

    async def after_run(self, context: AgentRunHookContext) -> None:
        if self._steer_open:
            # The final answer is an action too: did the head expect "stop" here?
            _RECORDER.submit(
                _Pending(
                    workspace=self._workspace,
                    intent=self._intent_key(),
                    prev=self._prev,
                    action=STOP,
                    result_head="",
                    is_error=False,
                    is_timeout=False,
                )
            )
        if self._settings.train:
            from navin.policy.jobs import note_turn

            note_turn(self._workspace)

    async def on_error(self, context: AgentRunHookContext) -> None:
        self._prev = ()


def create_policy_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Turn hook factory: the policy hook, or ``None`` when it must stay off."""
    workspace = context.workspace
    if workspace is None or context.ephemeral or _is_heartbeat(context):
        return None
    settings = read_settings(workspace)
    if not settings.enabled or not (settings.train or settings.steer):
        return None
    steer_open = False
    if settings.steer:
        from navin.policy.steerer import steer_open as gate_open

        steer_open = gate_open(workspace)
    if not settings.train and not steer_open:
        return None
    metadata = context.metadata if isinstance(context.metadata, dict) else {}
    user_text = metadata.get("user_text") if isinstance(metadata.get("user_text"), str) else None
    return PolicyHook(workspace=Path(workspace), settings=settings, steer_open=steer_open, user_text=user_text)
