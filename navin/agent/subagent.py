# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Subagent manager for background task execution."""

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.agent import worktree
from navin.agent.approval import UnattendedGate, bind_approval_gate, reset_approval_gate
from navin.agent.checkpoints import (
    TurnRecorder,
    bind_checkpoint_recorder,
    reset_checkpoint_recorder,
)
from navin.agent.hook import AgentHook, AgentHookContext, CompositeHook
from navin.agent.run_policy import TurnPolicy, current_turn_policy
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from navin.agent.tools.file_state import FileStates
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.outbound_events import (
    SubagentProgressEvent,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.config.schema import AgentDefaults, ToolResultClearing, ToolsConfig
from navin.security.workspace_access import (
    WorkspaceScope,
    bind_live_restrict_to_workspace,
    bind_workspace_scope,
    reset_live_restrict_to_workspace,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.prompt_templates import render_template

# Enough history to answer "what happened to the ones I started" across a few
# turns, small enough that a long session cannot grow it without bound.
#
# Has to cover a full wave with room to spare: this is the safety net for
# results the parent turn could not carry as injections, so a retention smaller
# than the concurrency width would quietly lose the verdict of a subagent that
# really ran. At 400 characters each, a saturated session holds well under a
# megabyte.
_MAX_OUTCOME_HISTORY = 400
_MAX_OUTCOME_SUMMARY = 400
_OUTCOME_FILE_STEM_CHARS = 48

# Waiting spawns one conversation may stack up before spawning is refused
# again. Sized well past any deliberate fan-out: a wave of a few hundred agents
# passes, a model looping on spawn hits the wall long before the process runs
# out of memory.
MAX_QUEUED_SUBAGENTS = 1000
# spawn() returns this prefix when the per-session wall is hit. The spawn
# tool shows that string to the model; Multitask must treat it as a refusal
# so the composer can put the prompt back instead of dropping it.
SPAWN_REFUSED_PREFIX = "Cannot spawn"


def spawn_was_accepted(detail: str) -> bool:
    """Whether ``SubagentManager.spawn`` accepted the task (started or queued)."""
    return isinstance(detail, str) and not detail.startswith(SPAWN_REFUSED_PREFIX)

# How often a still-alive card is republished so the IDE does not look frozen
# on a long tool or a long wait for a worktree. Does not cancel anything:
# killing a 20-minute compile because it was quiet is worse than a silent card.
# 8s is short enough that "Starting..." never looks dead; 60s felt stuck.
_HEARTBEAT_S = 8.0

# Prompt + tool registry stay off the event loop, on a dedicated pool so a
# wave of 50 cannot starve other to_thread work (git, worktrees, the parent
# turn). Eight workers is enough: once prepare returns, the LLM call is async.
_PREPARE_WORKERS = 8
_PREPARE_TIMEOUT_S = 20.0
_PREPARE_EXECUTOR: ThreadPoolExecutor | None = None


def _prepare_executor() -> ThreadPoolExecutor:
    global _PREPARE_EXECUTOR
    if _PREPARE_EXECUTOR is None:
        workers = max(2, min(_PREPARE_WORKERS, (os.cpu_count() or 4) + 2))
        _PREPARE_EXECUTOR = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="navin-subagent-prep",
        )
    return _PREPARE_EXECUTOR


def _outcomes_dir() -> Path:
    """Where finished-subagent history is persisted, one file per session.

    Under the runtime data dir rather than the workspace: the history belongs
    to a conversation, and a conversation can retarget its workspace scope
    mid-life without its subagent record following the folder around.
    """
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("subagents")
# Cap WS fan-out so a chatty subagent does not flood the UI.
_PROGRESS_MIN_INTERVAL_S = 0.45


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    phase: str = "initializing"  # queued | isolating | initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None
    model: str | None = None
    origin_channel: str = "cli"
    origin_chat_id: str = "direct"
    last_progress_at: float = 0.0
    last_status_line: str = ""


@dataclass(slots=True)
class _QueuedSpawn:
    """A spawn accepted but not started, waiting for a slot to free up.

    Everything the parent turn contributed is captured here rather than read
    at start time: by then the turn is over and its contextvars (policy,
    workspace scope) are gone, so a deferred spawn would silently run with
    weaker rules than the one that asked for it.
    """

    task_id: str
    task: str
    label: str
    agent_name: str | None
    origin: dict[str, Any]
    runtime: Any
    origin_message_id: str | None
    workspace_scope: Any
    isolate: bool
    scope_paths: list[str] | None
    parent_metadata: dict[str, Any] | None
    policy: Any
    status: "SubagentStatus"
    session_key: str | None


@dataclass(frozen=True, slots=True)
class SubagentOutcome:
    """What became of a finished subagent, kept after its live status is gone.

    A status is dropped the moment its task ends, so until now the fate of a
    subagent existed only in the announcement it published. If that
    announcement did not fit into the parent turn, the work had simply vanished:
    nothing could say whether it had succeeded, failed, or never run.
    """

    task_id: str
    label: str
    task_description: str
    status: str            # "ok" | "error"
    summary: str
    finished_at: float     # wall clock: an outcome outlives the turn that made it
    session_key: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution - logs tool calls and updates status."""

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        on_progress: Callable[[SubagentStatus], None] | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._on_progress = on_progress

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )
        if self._status is not None and context.tool_calls:
            self._status.phase = "awaiting_tools"
            self._status.tool_events = [
                {"name": tc.name, "status": "running", "detail": ""}
                for tc in context.tool_calls
            ]
            if self._on_progress is not None:
                self._on_progress(self._status)

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)
            self._status.phase = "error"
        elif context.tool_events:
            self._status.phase = "tools_completed"
        else:
            self._status.phase = "final_response"
        if self._on_progress is not None:
            self._on_progress(self._status)


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        *,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        tool_result_clearing: ToolResultClearing | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        image_generation_provider_configs: dict[str, Any] | None = None,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        fail_on_tool_error: bool | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        record_edits: Callable[[str, dict[str, bytes | None]], None] | None = None,
        usage_hooks: list[AgentHook] | None = None,
    ):
        defaults = AgentDefaults()
        self.workspace = workspace
        self.bus = bus
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.tool_result_clearing = tool_result_clearing or ToolResultClearing()
        self.restrict_to_workspace = restrict_to_workspace
        self.image_generation_provider_configs = dict(image_generation_provider_configs or {})
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self.fail_on_tool_error = (
            fail_on_tool_error
            if fail_on_tool_error is not None
            else defaults.fail_on_tool_error
        )
        self.runner = AgentRunner()
        self._usage_hooks = list(usage_hooks or [])
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        self._record_edits = record_edits
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        # Spawns accepted past the limit. Kept in memory only: a running
        # subagent does not survive a gateway restart either, and reviving work
        # that never started while losing work half-done would be the wrong way
        # round.
        self._queued: dict[str, deque[_QueuedSpawn]] = {}
        # Multitask client keys: a timeout retry must not start a second
        # subagent for the same queued prompt.
        self._client_keys: dict[str, str] = {}
        self._task_client_slots: dict[str, str] = {}
        self._client_key_details: dict[str, str] = {}
        # How deep one conversation's queue may get before spawning is refused
        # again. Waiting work is cheap but not free, and without a wall a model
        # looping on spawn would enqueue until the process ran out of memory.
        self.max_queued_subagents = MAX_QUEUED_SUBAGENTS
        # Captured when the first subagent starts: request_drain() is called
        # from the license-sync thread and needs a way back onto this loop.
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        # Outcomes are kept per session: one busy conversation fanning out
        # dozens of subagents must not evict another conversation's history,
        # and a wide fan-out (25+) must stay fully retrievable via
        # spawn(action="results") even when the mid-turn injection cycles
        # could not carry every announcement. This dict is a cache over the
        # files in _outcomes_dir(), hydrated per session on first touch, so the
        # same guarantee holds across a gateway restart.
        self._outcomes: dict[str, deque[SubagentOutcome]] = {}

    def _build_hook(
        self,
        task_id: str,
        status: SubagentStatus | None,
        on_progress: Callable[[SubagentStatus], None] | None,
    ) -> AgentHook:
        """The subagent's own hook, plus whatever accounts for what it spends.

        A subagent drives its own ``AgentRunner``, so the loop's hook chain
        never reaches it. Its tokens are billed like any other turn's and used
        to be recorded nowhere at all, which is what let the usage table sit
        still through an afternoon of subagent work.
        """
        hook: AgentHook = _SubagentHook(task_id, status, on_progress=on_progress)
        if not self._usage_hooks:
            return hook
        return CompositeHook([hook, *self._usage_hooks])

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use.

        The tool set is narrowed by ``Tool._scopes``, not by dropping config
        fields: an omitted field falls back to its schema default, which
        silently re-enables a capability the operator turned off.
        """
        return self.tools_config.model_copy(
            update={"restrict_to_workspace": self.restrict_to_workspace},
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            file_state_store=FileStates(),
            image_generation_provider_configs=self.image_generation_provider_configs,
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        return registry

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        agent: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        isolate: bool = False,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
        client_key: str | None = None,
        *,
        runtime: LLMRuntime,
    ) -> str:
        """Spawn a subagent, or queue it when the machine is already full."""
        self._event_loop = asyncio.get_running_loop()
        self._ensure_watchdog()
        replayed = self.replay_client_spawn(session_key, client_key)
        if replayed:
            return replayed
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        task_id = str(uuid.uuid4())[:8]
        agent_name = (agent or "").strip() or None
        display_label = label or agent_name or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}
        # Captured at spawn time, on purpose: this is the policy of the turn
        # that delegated the work. Delegation must not be an escape hatch from
        # the parent's verify gate or the module denylist.
        inherited_policy = current_turn_policy() or TurnPolicy()

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            model=getattr(runtime, "model", None),
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
        )
        self._task_statuses[task_id] = status

        pending = _QueuedSpawn(
            task_id=task_id,
            task=task,
            label=display_label,
            agent_name=agent_name,
            origin=origin,
            runtime=runtime,
            origin_message_id=origin_message_id,
            workspace_scope=workspace_scope,
            isolate=isolate,
            scope_paths=scope_paths,
            parent_metadata=parent_metadata,
            policy=inherited_policy,
            status=status,
            session_key=session_key,
        )

        # Count this session's subagents, not every session's: the limit exists
        # to keep one turn's fan-in readable, and a global count let a subagent
        # in one conversation block spawning in every other one.
        if self._has_free_slot(session_key):
            self._start(pending)
            self._publish_progress(status, force=True)
            logger.info("Spawned subagent [{}]: {}", task_id, display_label)
            detail = (
                f"Subagent [{display_label}] started (id: {task_id}). "
                "I'll notify you when it completes."
            )
            self.bind_client_spawn(session_key, client_key, task_id, detail)
            return detail

        queue = self._queued.setdefault(session_key or "", deque())
        if len(queue) >= self.max_queued_subagents:
            # The refusal used to be the backpressure. Queuing removed it, so a
            # model looping on spawn could enqueue without end, each entry
            # holding a runtime, a scope and a status. This is the new wall, set
            # far past any real fan-out so only a runaway ever meets it.
            self._task_statuses.pop(task_id, None)
            logger.warning(
                "Refused subagent [{}]: queue for {} is full ({})",
                task_id, session_key or "-", self.max_queued_subagents,
            )
            return (
                f"{SPAWN_REFUSED_PREFIX} subagent: {self.max_concurrent_subagents} are "
                f"running for this conversation and {len(queue)} more are already "
                "queued. Their results arrive on their own, so wait for them "
                "instead of spawning more."
            )

        status.phase = "queued"
        queue.append(pending)
        self._publish_progress(status, force=True)
        ahead = len(self._queued.get(session_key or "", ()))
        logger.info("Queued subagent [{}]: {} (position {})", task_id, display_label, ahead)
        detail = (
            f"Subagent [{display_label}] queued (id: {task_id}), position {ahead}: "
            f"the machine is already running {self.max_concurrent_subagents} of them. "
            "It starts on its own as soon as a slot frees up, and its result "
            "arrives like any other. Do not retry this call."
        )
        self.bind_client_spawn(session_key, client_key, task_id, detail)
        return detail

    def _has_free_slot(self, session_key: str | None) -> bool:
        """Whether one more subagent may run right now for this conversation."""
        if not session_key:
            return len(self._running_tasks) < self.max_concurrent_subagents
        return self._started_count(session_key) < self.max_concurrent_subagents

    def _start(self, pending: _QueuedSpawn) -> None:
        """Turn an accepted spawn into a running task."""
        task_id = pending.task_id
        session_key = pending.session_key
        if pending.status.phase == "queued":
            pending.status.phase = "initializing"
            # The clock starts when the work does, not when it was asked for:
            # a queued wait would otherwise be reported as time spent working.
            pending.status.started_at = time.monotonic()

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                pending.task,
                pending.label,
                pending.origin,
                pending.status,
                pending.runtime,
                pending.origin_message_id,
                pending.workspace_scope,
                pending.isolate,
                pending.agent_name,
                pending.scope_paths,
                pending.parent_metadata,
                pending.policy,
            )
        )
        self._task_statuses[task_id] = pending.status
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            self.forget_client_spawn(task_id)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]
            # The only place a slot is ever given back, for all three endings
            # (finished, failed, cancelled). Anything else would leave a queue
            # waiting on a subagent that is already gone.
            self._drain_queue(session_key)

        bg_task.add_done_callback(_cleanup)

    def _client_slot(self, session_key: str, client_key: str) -> str:
        return f"{session_key}\0{client_key}"

    def replay_client_spawn(
        self, session_key: str | None, client_key: str | None
    ) -> str | None:
        """Return the previous accept message when this client key is live."""
        keys = getattr(self, "_client_keys", None)
        statuses = getattr(self, "_task_statuses", None)
        if not session_key or not client_key or not isinstance(keys, dict):
            return None
        slot = self._client_slot(session_key, client_key.strip())
        task_id = keys.get(slot)
        if not task_id:
            return None
        if isinstance(statuses, dict) and task_id not in statuses:
            keys.pop(slot, None)
            slots = getattr(self, "_task_client_slots", None)
            if isinstance(slots, dict):
                slots.pop(task_id, None)
            return None
        stored = getattr(self, "_client_key_details", None)
        if isinstance(stored, dict) and stored.get(slot):
            return str(stored[slot])
        return f"Subagent started (id: {task_id})."

    def bind_client_spawn(
        self,
        session_key: str | None,
        client_key: str | None,
        task_id: str,
        detail: str,
    ) -> None:
        if not session_key or not client_key or not task_id:
            return
        keys = getattr(self, "_client_keys", None)
        if not isinstance(keys, dict):
            return
        slot = self._client_slot(session_key, client_key.strip())
        keys[slot] = task_id
        slots = getattr(self, "_task_client_slots", None)
        if isinstance(slots, dict):
            slots[task_id] = slot
        details = getattr(self, "_client_key_details", None)
        if details is None:
            self._client_key_details = {slot: detail}
        elif isinstance(details, dict):
            details[slot] = detail

    def forget_client_spawn(self, task_id: str) -> None:
        slots = getattr(self, "_task_client_slots", None)
        keys = getattr(self, "_client_keys", None)
        if not isinstance(slots, dict) or not isinstance(keys, dict):
            return
        slot = slots.pop(task_id, None)
        if not slot:
            return
        keys.pop(slot, None)
        details = getattr(self, "_client_key_details", None)
        if isinstance(details, dict):
            details.pop(slot, None)

    def _ensure_watchdog(self) -> None:
        """Keep one heartbeat task for every in-flight card.

        A long compile or a worktree queue produces no events. Without this
        the IDE shows a spinner that never updates, which reads as an agent
        that will neither finish nor stop.
        """
        task = self._watchdog_task
        if task is not None and not task.done():
            return
        self._watchdog_task = asyncio.create_task(
            self._watchdog_loop(), name="navin-subagent-watchdog"
        )

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_S)
                if not self._running_tasks and not self._queued:
                    continue
                now = time.monotonic()
                for status in list(self._task_statuses.values()):
                    if status.phase in {"done", "error"}:
                        continue
                    last = status.last_progress_at or status.started_at
                    if now - last < _HEARTBEAT_S:
                        continue
                    self._publish_progress(status, force=True)
        except asyncio.CancelledError:
            return

    def request_drain(self) -> None:
        """Re-check every queue. Safe to call from another thread.

        The limit is re-measured periodically off the event loop (license sync,
        resource governor). When it goes back up, nothing else would notice
        until a running subagent happened to finish, so a queue could sit still
        on a machine that had just freed the memory it was waiting for.
        """
        loop = self._event_loop
        if loop is None or loop.is_closed() or not self._queued:
            return
        try:
            loop.call_soon_threadsafe(self._drain_all)
        except RuntimeError:  # loop shut down between the check and the call
            pass

    def _drain_all(self) -> None:
        for session_key in list(self._queued):
            self._drain_queue(session_key or None)

    def _drain_queue(self, session_key: str | None) -> None:
        """Start whatever the freed slot can now carry.

        Called from a done-callback, so it cannot await. The limit is read
        again here rather than trusted from enqueue time: a license sync or the
        resource governor may have lowered it while this queue was waiting.
        """
        queue = self._queued.get(session_key or "")
        while queue and self._has_free_slot(session_key):
            pending = queue.popleft()
            try:
                self._start(pending)
            except Exception:  # noqa: BLE001 - one bad spawn must not strand the rest
                logger.exception("Queued subagent [{}] failed to start", pending.task_id)
                self._task_statuses.pop(pending.task_id, None)
                self.forget_client_spawn(pending.task_id)
                continue
            self._publish_progress(pending.status, force=True)
            logger.info("Started queued subagent [{}]: {}", pending.task_id, pending.label)
        if queue is not None and not queue:
            self._queued.pop(session_key or "", None)

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        isolate: bool = False,
        agent_name: str | None = None,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
        inherited_policy: TurnPolicy | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        policy = inherited_policy or TurnPolicy()
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        def _emit_progress(current: SubagentStatus, *, force: bool = False) -> None:
            self._publish_progress(current, force=force)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)
            _emit_progress(status)

        checkout: worktree.Worktree | None = None
        isolation_note = ""
        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            if isolate:
                # A large repo's ``worktree add`` is gated (4 at a time) and
                # can sit here for minutes. Say so, or the card looks dead
                # while git is doing exactly what isolation asked for.
                status.phase = "isolating"
                self._publish_progress(status, force=True)
                checkout = await asyncio.to_thread(
                    worktree.create,
                    root,
                    task_id,
                    pool_size=self.max_concurrent_subagents,
                )
                if checkout is not None:
                    root = self._isolated_root(root, checkout)
                    # The scope is what the path guard consults. Left pointing at
                    # the original project it would refuse every write the
                    # subagent makes in its own checkout.
                    if workspace_scope is not None:
                        workspace_scope = replace(workspace_scope, project_path=root)
                else:
                    # A log line is not enough here: the parent asked for
                    # isolation and will otherwise keep believing the edits sit
                    # in a checkout waiting to be merged, when they are already
                    # in the user's tree. The announcement has to say so.
                    isolation_note = (
                        "\n\nNote: isolation was requested but this project is "
                        "not a usable git repository (not a repo, or it has no "
                        "commit yet), so the subagent worked in the shared "
                        "working tree. Any edits it made are already in the "
                        "user's tree."
                    )
                    logger.info(
                        "Subagent [{}] could not be isolated; running in the shared tree",
                        task_id,
                    )
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            # Visible before the worker starts: otherwise the card sits on
            # "Working..." while tools/prompt are still being built.
            if status.phase != "initializing":
                status.phase = "initializing"
                self._publish_progress(status, force=True)
            # Tools + prompt both do filesystem and YAML work. Dedicated pool
            # so a wave of 50 cannot stall drain/watchdog or starve git.
            def _prepare() -> tuple[ToolRegistry, str]:
                prepared_tools = self._build_tools(workspace=root, tools_config=cfg)
                prompt = self._build_subagent_prompt(
                    workspace=root,
                    agent_name=agent_name,
                    scope_paths=scope_paths,
                    parent_metadata=parent_metadata,
                )
                return prepared_tools, prompt

            timeout_s = getattr(self, "_prepare_timeout_s", _PREPARE_TIMEOUT_S)
            try:
                tools, system_prompt = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        _prepare_executor(),
                        _prepare,
                    ),
                    timeout=timeout_s,
                )
            except TimeoutError:
                logger.warning(
                    "Subagent [{}] tools/prompt prepare exceeded {}s; "
                    "continuing with a slim prompt so the slot is not stuck",
                    task_id,
                    timeout_s,
                )
                tools = self._build_tools(workspace=root, tools_config=cfg)
                system_prompt = self._fallback_subagent_prompt(root)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            request_token = bind_request_context(RequestContext(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                message_id=origin_message_id,
                session_key=sess_key,
                runtime=runtime,
            ))
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            live_restrict_token = bind_live_restrict_to_workspace(self.restrict_to_workspace)
            # A subagent outlives the parent turn, whose recorder is flushed and
            # abandoned when that turn ends. Writing into it would drop these
            # baselines on the floor, leaving the subagent's edits impossible to
            # review or undo, so this run gets a recorder of its own.
            recorder = TurnRecorder()
            recorder_token = bind_checkpoint_recorder(recorder)
            # A background task must not stop and wait for a human: its parent
            # turn has usually already answered, so the card would appear next to
            # a finished conversation with nothing visibly blocked on it. The
            # refusal names the situation so the subagent reports back instead of
            # trying to get around it.
            approval_token = bind_approval_gate(UnattendedGate(
                "A background subagent cannot ask the user for approval. Report "
                "what you need approved in your result and let the main agent "
                "ask, instead of working around the refusal."
            ))
            try:
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    runtime=runtime,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    tool_result_clearing=self.tool_result_clearing,
                    hook=self._build_hook(task_id, status, _emit_progress),
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
                    # Same batching the parent loop gets. A subagent explores
                    # more than it writes, so running its reads one at a time
                    # re-sent the whole prompt once per file for no reason;
                    # _partition_tool_batches still isolates anything that
                    # declares itself exclusive or concurrency-unsafe.
                    concurrent_tools=True,
                    fail_on_tool_error=self.fail_on_tool_error,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                    # Inherited from the spawning turn: a Build parent's
                    # verify gate and the module denylist apply to delegated
                    # work too, so a subagent cannot finish unverified or use
                    # a tool the module locked out.
                    requires_verify_before_done=policy.requires_verify_before_done,
                    denied_tools=policy.locked_denied_tools,
                    locked_denied_tools=policy.locked_denied_tools,
                    allowed_tools=policy.allowed_tools,
                ))
            finally:
                reset_approval_gate(approval_token)
                reset_checkpoint_recorder(recorder_token)
                # Not for an isolated run: those baselines name paths inside the
                # checkout, so the review panel would offer to undo edits to
                # files the user does not have, and would write into a tree that
                # may be removed a moment later.
                if checkout is None:
                    self._publish_edits(sess_key, recorder)
                if token is not None:
                    reset_workspace_scope(token)
                reset_live_restrict_to_workspace(live_restrict_token)
                reset_request_context(request_token)
            status.stop_reason = result.stop_reason
            # Settled before the announcement, and on every branch: a run that
            # fails halfway is exactly when the parent most needs to be told
            # where the half-finished work is sitting. The two notes are
            # mutually exclusive - one exists only without a checkout, the
            # other only with one.
            note = await self._settle_worktree(checkout) + isolation_note
            checkout = None

            if result.stop_reason == "tool_error":
                status.phase = "error"
                status.error = "tool_error"
                status.tool_events = list(result.tool_events)
                self._publish_progress(status, force=True, done=True)
                await self._announce_result(
                    task_id, label, task,
                    self._format_partial_progress(result) + note,
                    origin, "error", origin_message_id,
                )
            elif result.stop_reason == "error":
                status.phase = "error"
                status.error = result.error or "subagent execution failed"
                self._publish_progress(status, force=True, done=True)
                await self._announce_result(
                    task_id, label, task,
                    (result.error or "Error: subagent execution failed.") + note,
                    origin, "error", origin_message_id,
                )
            else:
                status.phase = "done"
                status.error = None
                self._publish_progress(status, force=True, done=True)
                final_result = result.final_content or "Task completed but no final response was generated."
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(
                    task_id, label, task, final_result + note, origin, "ok", origin_message_id
                )

        except asyncio.CancelledError:
            # Nothing git here on purpose. Every await would raise straight
            # away, leaking the checkout, and the blocking calls would hold up
            # the shutdown that asked for the cancellation. Keeping the tree is
            # the safe answer for an interrupted run anyway.
            status.phase = "error"
            status.error = "cancelled"
            self._publish_progress(status, force=True, done=True)
            if checkout is not None:
                logger.info(
                    "Subagent [{}] cancelled; its checkout is kept at {}",
                    task_id,
                    checkout.path,
                )
                checkout = None
            # The parent turn is often blocked on this result. Dropping it
            # here is how a /stop or a cancelled wave left the orchestrator
            # waiting on work that would never report back.
            try:
                await asyncio.shield(
                    self._announce_result(
                        task_id,
                        label,
                        task,
                        "Cancelled before a result was produced.",
                        origin,
                        "error",
                        origin_message_id,
                    )
                )
            except Exception:  # noqa: BLE001 - shutdown must still propagate
                logger.debug("Subagent [{}] cancel announcement failed", task_id)
            raise
        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            self._publish_progress(status, force=True, done=True)
            note = await self._settle_worktree(checkout) + isolation_note
            checkout = None
            await self._announce_result(
                task_id, label, task, f"Error: {e}{note}", origin, "error", origin_message_id
            )

    @staticmethod
    def _status_line(status: SubagentStatus) -> str:
        """Short human line for the parallel-subagent card."""
        if status.phase == "error":
            err = (status.error or "failed").strip()
            return err[:120] if err else "Failed"
        if status.phase == "done":
            return "Completed"
        if status.phase == "queued":
            return "Queued - waiting for a free slot"
        if status.phase == "isolating":
            return "Preparing isolated checkout…"
        if status.phase == "initializing":
            return "Starting…"
        if status.phase == "final_response":
            return "Writing response…"
        events = status.tool_events or []
        if events:
            last = events[-1] if isinstance(events[-1], dict) else {}
            name = str(last.get("name") or "tool").strip()
            detail = str(last.get("detail") or last.get("status") or "").strip()
            if detail and detail not in {"running", "ok", "error", "done"}:
                line = f"{name}: {detail}"
            else:
                line = f"Running {name}"
            return line[:140]
        if status.iteration:
            return f"Working (iteration {status.iteration})"
        return "Working…"

    def _publish_progress(
        self,
        status: SubagentStatus,
        *,
        force: bool = False,
        done: bool = False,
    ) -> None:
        """Push a SubagentProgressEvent to the WebUI (throttled)."""
        line = self._status_line(status)
        now = time.monotonic()
        if (
            not force
            and not done
            and line == status.last_status_line
            and (now - status.last_progress_at) < _PROGRESS_MIN_INTERVAL_S
        ):
            return
        status.last_status_line = line
        status.last_progress_at = now
        if status.origin_channel != "websocket":
            return
        try:
            self.bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=status.origin_chat_id,
                    event=SubagentProgressEvent(
                        task_id=status.task_id,
                        label=status.label,
                        phase=status.phase,
                        status_line=line,
                        model=status.model,
                        iteration=status.iteration,
                        done=done or status.phase in {"done", "error"},
                        error=status.error,
                        task_description=status.task_description[:240]
                        if status.task_description
                        else None,
                    ),
                )
            )
        except Exception:
            logger.debug("Could not publish subagent progress for [{}]", status.task_id)

    @staticmethod
    def _isolated_root(workspace: Path, checkout: "worktree.Worktree") -> Path:
        """The place in ``checkout`` matching ``workspace`` in the repository.

        A workspace that was a subdirectory has to stay one, or isolation hands
        the subagent a wider tree than it had. The directory may be absent from
        the checkout when it was untracked - a scratch or data folder - and git
        only checks out what it tracks, so it is created rather than skipped:
        falling back to the checkout root would quietly widen the scope, and
        leaving it missing gives every process a cwd that does not exist.
        """
        try:
            offset = workspace.resolve().relative_to(checkout.origin.resolve())
        except (OSError, ValueError):
            # Not below the repository root as far as the filesystem is
            # concerned, usually a symlink resolving elsewhere.
            return checkout.path
        root = checkout.path / offset
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning(
                "worktree: cannot create {} in the checkout; using its root", offset
            )
            return checkout.path
        return root

    async def _settle_worktree(self, checkout: "worktree.Worktree | None") -> str:
        """Keep a checkout that holds work, drop one that does not.

        Returns the note appended to the subagent's result. Deleting a
        subagent's only copy of its work is the worst thing this code could do,
        so removal happens on one condition only - git said, plainly, that
        nothing changed. Anything else keeps the tree, including the case where
        git could not be asked.
        """
        if checkout is None:
            return ""
        try:
            changes = await asyncio.to_thread(checkout.inspect)
        except Exception:
            logger.exception("worktree: could not inspect [{}]", checkout.task_id)
            changes = None

        if changes is None:
            return (
                f"\n\nThis ran in an isolated checkout at {checkout.path}. Git "
                "would not say what changed there, so the checkout was left "
                "alone rather than risk discarding work. Inspect it and remove "
                f"it with `git worktree remove --force {checkout.path}`."
            )
        if changes.empty:
            pooled = await asyncio.to_thread(
                worktree.release,
                checkout,
                pool_size=self.max_concurrent_subagents,
            )
            if pooled:
                logger.debug(
                    "worktree: empty checkout for [{}] returned to the pool",
                    checkout.task_id,
                )
            return ""
        return (
            f"\n\nThis ran in an isolated checkout at {checkout.path}, so none of "
            f"it is in the main working tree yet:\n{changes.render()}\n"
            "Review it there and merge what you want with git, then remove the "
            f"checkout with `git worktree remove --force {checkout.path}`."
        )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
        }
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        self._record_outcome(task_id, label, task, result, status, override)
        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    def _publish_edits(self, session_key: str | None, recorder: TurnRecorder) -> None:
        """Hand a subagent's pre-edit baselines to the review store."""
        if not session_key or not recorder.files or self._record_edits is None:
            return
        try:
            self._record_edits(session_key, recorder.files)
        except Exception:
            # Losing the baselines is bad, but not worth discarding a completed
            # task: the announcement still has to reach the parent.
            logger.exception("Failed to record subagent file baselines")

    @staticmethod
    def _outcome_path(session_key: str) -> Path:
        """One file per session: readable stem, hashed suffix against collisions."""
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", session_key or "default")
        digest = hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()[:12]
        return _outcomes_dir() / f"{stem[:_OUTCOME_FILE_STEM_CHARS]}-{digest}.json"

    @staticmethod
    def _outcomes_from_rows(rows: Any, session_key: str) -> deque[SubagentOutcome]:
        queue: deque[SubagentOutcome] = deque(maxlen=_MAX_OUTCOME_HISTORY)
        if not isinstance(rows, list):
            return queue
        for row in rows:
            if not isinstance(row, dict) or not row.get("task_id"):
                continue
            try:
                queue.append(SubagentOutcome(
                    task_id=str(row["task_id"]),
                    label=str(row.get("label") or ""),
                    task_description=str(row.get("task_description") or ""),
                    status=str(row.get("status") or "ok"),
                    summary=str(row.get("summary") or ""),
                    finished_at=float(row.get("finished_at") or 0.0),
                    session_key=session_key,
                ))
            except (TypeError, ValueError):
                # One unreadable row must not cost the rest of the history.
                continue
        return queue

    @classmethod
    def _read_outcome_file(cls, path: Path) -> tuple[str, deque[SubagentOutcome]] | None:
        """Parse one persisted file, or None when it is missing or unusable."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.warning("Could not read subagent outcomes {}: {}", path, e)
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("Ignoring a corrupt subagent outcome file: {}", path)
            return None
        if not isinstance(payload, dict):
            return None
        session_key = payload.get("session_key")
        if not isinstance(session_key, str):
            return None
        return session_key, cls._outcomes_from_rows(payload.get("outcomes"), session_key)

    def _queue_for(self, session_key: str) -> deque[SubagentOutcome]:
        """The live queue for a session, hydrated from disk on first touch."""
        queue = self._outcomes.get(session_key)
        if queue is not None:
            return queue
        restored = self._read_outcome_file(self._outcome_path(session_key))
        queue = restored[1] if restored is not None else deque(maxlen=_MAX_OUTCOME_HISTORY)
        self._outcomes[session_key] = queue
        return queue

    def _persist_outcomes(self, session_key: str, queue: deque[SubagentOutcome]) -> None:
        """Write the session's history so a gateway restart cannot erase it."""
        from navin.utils.atomic_io import atomic_write_text

        payload = {
            "session_key": session_key,
            "outcomes": [asdict(outcome) for outcome in queue],
        }
        try:
            atomic_write_text(
                self._outcome_path(session_key),
                json.dumps(payload, ensure_ascii=False) + "\n",
            )
        except OSError as e:
            # The in-memory queue still has it, so this turn is unaffected;
            # only a restart would now lose the record.
            logger.warning("Could not persist subagent outcomes for {}: {}", session_key, e)

    def _record_outcome(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        status: str,
        session_key: str | None,
    ) -> None:
        """Remember how a subagent ended, so the answer survives the turn.

        And the process: the history is written to disk on every outcome, so a
        result that never reached its parent turn is still recoverable through
        spawn(action="results") after the gateway restarts.
        """
        summary = " ".join(result.split())
        if len(summary) > _MAX_OUTCOME_SUMMARY:
            summary = summary[: _MAX_OUTCOME_SUMMARY - 1].rstrip() + "…"
        key = session_key or ""
        queue = self._queue_for(key)
        queue.append(SubagentOutcome(
            task_id=task_id,
            label=label,
            task_description=task,
            status=status,
            summary=summary,
            finished_at=time.time(),
            session_key=session_key,
        ))
        self._persist_outcomes(key, queue)

    def _merged_outcomes(self) -> list[SubagentOutcome]:
        """Every session's history, memory first then whatever is only on disk."""
        merged: list[SubagentOutcome] = []
        seen = set(self._outcomes)
        for queue in self._outcomes.values():
            merged.extend(queue)
        try:
            files = sorted(_outcomes_dir().glob("*.json"))
        except OSError:
            files = []
        for path in files:
            restored = self._read_outcome_file(path)
            if restored is None or restored[0] in seen:
                continue
            seen.add(restored[0])
            self._outcomes[restored[0]] = restored[1]
            merged.extend(restored[1])
        return merged

    def recent_outcomes(
        self,
        session_key: str | None = None,
        limit: int | None = None,
    ) -> list[SubagentOutcome]:
        """Finished subagents, most recent first, optionally scoped to a session.

        ``limit=None`` returns everything retained for the scope: the whole
        point of the outcome history is that a result which missed its
        injection window can still be recovered in full via spawn(results),
        including across a gateway restart.
        """
        if session_key is not None:
            matches = list(reversed(self._queue_for(session_key)))
        else:
            merged = self._merged_outcomes()
            merged.sort(key=lambda outcome: outcome.finished_at, reverse=True)
            matches = merged
        if limit is None:
            return matches
        return matches[: max(0, limit)]

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    @staticmethod
    def _parent_context_lines(
        root: Path,
        parent_metadata: dict[str, Any] | None,
    ) -> list[str]:
        """Facts the parent turn saw: focus files and the plan's current step.

        The parent gets these as runtime-context blocks on every turn; the
        subagent used to start blind, re-discovering the files the parent was
        already looking at and the step the board says is running. Never
        raises: context enrichment must not be able to block a spawn.
        """
        lines: list[str] = []
        # Skip the pack when the parent sent no focus: it still walks git
        # dirty + the code index, and a wave of 50 would redo that for nothing.
        if parent_metadata:
            try:
                from navin.agent.context_pack import build_context_pack_lines

                lines.extend(build_context_pack_lines(
                    workspace=root,
                    metadata=dict(parent_metadata),
                ))
            except Exception:
                logger.debug("Subagent context pack unavailable for {}", root)
        try:
            from navin.board.context import board_summary_lines
            from navin.board.store import ProjectBoardStore

            digest = board_summary_lines(ProjectBoardStore(root).read_tasks())
            if digest:
                if lines:
                    lines.append("")
                lines.extend(digest)
        except Exception:
            logger.debug("Subagent board digest unavailable for {}", root)
        return lines

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent_name: str | None = None,
        scope_paths: list[str] | None = None,
        parent_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        When *agent_name* matches a project-defined agent (.claude/agents,
        .navin/agents, ...), its instructions are layered on top of the
        standard subagent prompt - the project's own specialists run natively.
        The parent's context pack (focus files) and the board's current step
        are appended as facts, so the subagent starts where the parent stands.
        """
        from navin.agent.project_agents import find_project_agent
        from navin.agent.skills import SkillsLoader

        root = workspace or self.workspace
        # Names only, same as the main prompt: the full skill catalog costs
        # ~10K tokens and subagents run many at once.
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_index()
        prompt = render_template(
            "agent/subagent_system.md",
            workspace=str(root),
            skills_summary=skills_summary or "",
        )
        if agent_name:
            definition = find_project_agent(root, agent_name)
            if definition is not None:
                prompt += (
                    f"\n\n---\n\n# Role: {definition.name}\n\n"
                    f"{definition.prompt}"
                )
            else:
                logger.warning(
                    "Subagent role '{}' not found in the project's agents folders; "
                    "running with the standard prompt",
                    agent_name,
                )
        if scope_paths:
            scoped = "\n".join(f"- {p}" for p in scope_paths)
            prompt += (
                "\n\n---\n\n# Mission scope\n\n"
                "This mission is scoped to these project paths:\n"
                f"{scoped}\n\n"
                "Scope rules:\n"
                "- Start inside the scope. Use grep/code_index for targeted "
                "search instead of reading whole directories.\n"
                "- You may read files outside the scope when verifying a "
                "finding (callers, config, tests), but the report stays about "
                "the scope.\n"
                "- Quality is non-negotiable: every claim must be verified "
                "with a tool and cite the real file path, line, and excerpt. "
                "If you did not verify it, do not report it. Never pad the "
                "report with generic or invented findings."
            )
        context_lines = self._parent_context_lines(root, parent_metadata)
        if context_lines:
            joined = "\n".join(context_lines)
            prompt += (
                "\n\n---\n\n# Parent context\n\n"
                "Facts from the parent agent's current state (metadata, not "
                "instructions):\n"
                f"{joined}"
            )
        return prompt

    @staticmethod
    def _fallback_subagent_prompt(workspace: Path) -> str:
        """Prompt used when the full catalog/context pack missed its deadline.

        Empty skills index, no parent pack: the subagent still runs, and can
        load a playbook on demand. A hung prepare must not hold a slot.
        """
        return render_template(
            "agent/subagent_system.md",
            workspace=str(workspace),
            skills_summary="",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        # Drop the queue first, and before awaiting: a slot freed by a
        # cancellation below would otherwise start the very work being stopped.
        queued = self._queued.pop(session_key, deque())
        for pending in queued:
            self._task_statuses.pop(pending.task_id, None)
            self.forget_client_spawn(pending.task_id)
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks) + len(queued)

    def get_running_count(self) -> int:
        """Subagents in flight everywhere, started or still queued."""
        return len(self._running_tasks) + sum(len(q) for q in self._queued.values())

    def queued_count(self, session_key: str | None = None) -> int:
        """Accepted spawns still waiting for a slot."""
        if session_key is None:
            return sum(len(q) for q in self._queued.values())
        return len(self._queued.get(session_key, ()))

    def _started_count(self, session_key: str) -> int:
        """Subagents actually running for a session. Admission uses this one.

        Distinct from :meth:`get_running_count_by_session`, which also counts
        the queue: counting queued work as occupying a slot would mean the
        queue could never drain.
        """
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def get_running_count_by_session(self, session_key: str) -> int:
        """Subagents in flight for a session, started or still queued.

        Queued ones count as in flight because callers use this to decide
        whether the conversation still has work pending: the turn loop keeps
        itself alive on this number, and ending a turn with a queue still
        waiting would strand work that was accepted.
        """
        return self._started_count(session_key) + len(self._queued.get(session_key, ()))

    def running_snapshot(self, session_key: str) -> list[dict[str, Any]]:
        """What is still running for *session_key*, as progress payloads.

        A browser holds its subagent cards in memory only, so a refresh or a
        detour through another chat leaves a background task running with
        nothing on screen saying so - which reads as if the request had been
        dropped. Only this manager knows, so it hands out replayable payloads
        shaped like the live event.
        """
        snapshot: list[dict[str, Any]] = []
        for task_id in sorted(self._session_tasks.get(session_key, set())):
            task = self._running_tasks.get(task_id)
            if task is None or task.done():
                continue
            status = self._task_statuses.get(task_id)
            if status is None:
                continue
            snapshot.append({
                "task_id": status.task_id,
                "label": status.label,
                "phase": status.phase,
                "status_line": self._status_line(status),
                "model": status.model,
                "iteration": status.iteration,
                "done": False,
                "task_description": status.task_description[:240] or None,
                "started_ms_ago": max(0, int((time.monotonic() - status.started_at) * 1000)),
            })
        # Queued spawns are shown too, in the order they will start. A card that
        # only appears once a slot frees up looks like a request that was lost.
        for pending in self._queued.get(session_key, ()):
            status = pending.status
            snapshot.append({
                "task_id": status.task_id,
                "label": status.label,
                "phase": status.phase,
                "status_line": self._status_line(status),
                "model": status.model,
                "iteration": 0,
                "done": False,
                "task_description": status.task_description[:240] or None,
                "started_ms_ago": max(0, int((time.monotonic() - status.started_at) * 1000)),
            })
        return snapshot


async def request_running_subagents(
    bus: Any,
    session_key: str,
    *,
    timeout: float = 2.0,
) -> list[dict[str, Any]]:
    """Ask the running loop which subagents *session_key* still has in flight.

    The manager lives in the agent loop, out of reach of the channel, so this
    borrows the runtime-control acknowledgement path the same way the approvals
    replay does. A short timeout on purpose: this runs while a client is
    attaching, and no answer just means no cards.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )

    if not session_key:
        return []
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-subagents",
            chat_id="runtime",
            content=RUNTIME_CONTROL_SUBAGENTS_QUERY,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_SUBAGENTS_QUERY,
                RUNTIME_CONTROL_ACK: ack,
                "session_key": session_key,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return []
    return result if isinstance(result, list) else []


async def request_multitask_spawn(
    bus: Any,
    session_key: str,
    prompt: str,
    *,
    label: str = "",
    client_key: str = "",
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Ask the running loop to spawn a subagent for a queued composer prompt.

    This is the Multitask path: a prompt the user queued while a turn was
    running is dispatched to a parallel subagent instead of waiting its turn.
    Same ack pattern as the subagents replay query; the loop side holds the
    SubagentManager, the model runtime, and the session's workspace scope.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_MULTITASK_SPAWN,
    )

    if not session_key or not prompt.strip():
        return {"ok": False, "error": "a session and a prompt are required"}
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-multitask",
            chat_id="runtime",
            content=RUNTIME_CONTROL_MULTITASK_SPAWN,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_MULTITASK_SPAWN,
                RUNTIME_CONTROL_ACK: ack,
                "session_key": session_key,
                "prompt": prompt,
                "label": label,
                "client_key": (client_key or "").strip()[:80],
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        # The control is already on the inbound bus. The loop may still
        # spawn after we give up, so callers must not retry the same prompt.
        return {
            "ok": False,
            "error": "the agent loop did not answer in time",
            "timed_out": True,
        }
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid answer"}


async def handle_multitask_spawn(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: spawn a subagent for a queued prompt."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_MULTITASK_SPAWN,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_MULTITASK_SPAWN:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)

    def _answer(payload: dict[str, Any]) -> None:
        if isinstance(ack, asyncio.Future) and not ack.done():
            ack.set_result(payload)

    manager = getattr(state, "subagents", None)
    session_key = metadata.get("session_key")
    prompt = str(metadata.get("prompt") or "").strip()
    label = str(metadata.get("label") or "").strip()
    client_key = str(metadata.get("client_key") or "").strip()[:80] or None
    if not isinstance(manager, SubagentManager) or not isinstance(session_key, str) or not prompt:
        _answer({"ok": False, "error": "multitask spawn is not available"})
        return True
    try:
        # No limit check here: the manager owns it and queues the overflow, so
        # a busy conversation slows down instead of rejecting the prompt.
        channel, _, chat_id = session_key.partition(":")
        session = state.sessions.get_or_create(session_key)
        session_metadata = getattr(session, "metadata", None)
        scope = state.workspace_scopes.for_turn(
            channel=channel,
            message_metadata=None,
            session_metadata=session_metadata,
        )
        runtime = state.llm_runtime()
        detail = await manager.spawn(
            task=prompt,
            runtime=runtime,
            label=label or None,
            origin_channel=channel or "websocket",
            origin_chat_id=chat_id or "direct",
            session_key=session_key,
            workspace_scope=scope,
            parent_metadata=(
                dict(session_metadata) if isinstance(session_metadata, dict) else None
            ),
            # Cursor-style isolation: each Multitask prompt gets its own git
            # worktree so parallel tasks cannot trample each other's edits.
            # On a non-git project the manager falls back to the shared tree
            # and says so in the announcement.
            isolate=True,
            client_key=client_key,
        )
        if not spawn_was_accepted(detail):
            _answer({"ok": False, "error": detail})
        else:
            _answer({"ok": True, "detail": detail})
    except Exception as exc:  # noqa: BLE001 - the control path must always answer
        logger.exception("Multitask spawn failed")
        _answer({"ok": False, "error": str(exc)[:300]})
    return True


async def handle_subagents_query(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: answer a reconnecting client's question."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_SUBAGENTS_QUERY,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_SUBAGENTS_QUERY:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    manager = getattr(state, "subagents", None)
    session_key = metadata.get("session_key")
    running: list[dict[str, Any]] = []
    if isinstance(manager, SubagentManager) and isinstance(session_key, str):
        try:
            running = manager.running_snapshot(session_key)
        except Exception:
            # A replay is a convenience: never let it take down the loop's
            # control path, which also carries approval answers.
            logger.exception("Could not snapshot running subagents")
            running = []
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(running)
    return True
