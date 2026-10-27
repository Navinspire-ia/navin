# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bridge between the Textual UI and the Navin agent engine.

The runtime owns the message bus, the ``AgentLoop`` task and the outbound
consumer. It translates bus traffic into a small set of UI-facing callbacks so
the widgets never touch engine internals directly.

Everything here reuses the exact wiring of ``navin agent`` (see
``navin/cli/commands.py``): same ``AgentLoop.from_config`` call, same cron store,
same hook factories, same ``_wants_stream`` metadata. Nothing in the engine is
modified.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from navin.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_APPROVAL_DECISION,
    RUNTIME_CONTROL_CHOICE_ANSWER,
    RUNTIME_CONTROL_CREDENTIAL_ANSWER,
    InboundMessage,
    OutboundMessage,
)
from navin.bus.outbound_events import (
    ApprovalClosedEvent,
    ApprovalRequestedEvent,
    CheckpointSavedEvent,
    ChoiceClosedEvent,
    ChoiceRequestedEvent,
    ContextCompactedEvent,
    CredentialClosedEvent,
    CredentialRequestedEvent,
    FilePreviewOpenRequestedEvent,
    NotificationEvent,
    OutboundEvent,
    ProgressEvent,
    RetryWaitEvent,
    RuntimeModelUpdatedEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    SubagentProgressEvent,
    TeamMessageEvent,
    TurnEndEvent,
    WorktreeMergeEvent,
    outbound_event_from_message,
)

UiCallback = Callable[["UiEvent"], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# UI-facing events (engine events, flattened for widgets)
# ---------------------------------------------------------------------------


class _FileLog:
    """Minimal ``logger.warning`` sink writing to an open file (CLI freeze log)."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def warning(self, fmt: str, *args: Any) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with contextlib.suppress(Exception):
            self._handle.write(f"{stamp} | {fmt.format(*args)}\n")


@dataclass(frozen=True)
class UiEvent:
    """Base type for everything the runtime hands to the UI."""


@dataclass(frozen=True)
class UiTurnStarted(UiEvent):
    text: str


@dataclass(frozen=True)
class UiStreamDelta(UiEvent):
    text: str


@dataclass(frozen=True)
class UiStreamEnd(UiEvent):
    resuming: bool = False
    # The segment's partial text was abandoned by a mid-stream retry.
    discarded: bool = False


@dataclass(frozen=True)
class UiReasoning(UiEvent):
    text: str
    end: bool = False


@dataclass(frozen=True)
class UiFilePreview(UiEvent):
    """The agent asked to show a file to the user in the terminal."""

    path: str


@dataclass(frozen=True)
class UiProgress(UiEvent):
    text: str
    tool_hint: bool = False


@dataclass(frozen=True)
class UiToolEvent(UiEvent):
    call_id: str
    name: str
    phase: str  # start | output | end | error
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    output: str | None = None
    label: str | None = None
    percent: float | None = None
    #: Usual duration of this run or command from earlier runs, in seconds.
    expected_s: float | None = None
    output_mode: str = "delta"


@dataclass(frozen=True)
class UiFileEdit(UiEvent):
    path: str
    kind: str = ""
    added: int = 0
    removed: int = 0
    diff: str = ""
    call_id: str = ""
    tool: str = "edit_file"
    phase: str = "end"
    error: str | None = None
    truncated: bool = False
    binary: bool = False

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> UiFileEdit:
        from navin.utils.file_edit_events import file_edit_details

        return cls(**file_edit_details(payload))


def _file_edit_diff_text(payload: dict[str, Any]) -> str:
    """Compatibility helper for callers that only need the diff body."""
    from navin.utils.file_edit_events import file_edit_details

    return file_edit_details(payload)["diff"]


@dataclass(frozen=True)
class UiAssistantMessage(UiEvent):
    """A complete assistant/side-channel message (non streamed)."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    streamed: bool = False
    render_as: str = "markdown"


@dataclass(frozen=True)
class UiTurnEnd(UiEvent):
    latency_ms: int | None = None


@dataclass(frozen=True)
class UiModelUpdated(UiEvent):
    model: str | None
    model_preset: str | None
    reason: str | None = None
    used_percent: int | None = None


@dataclass(frozen=True)
class UiContextCompacted(UiEvent):
    kind: str
    messages_archived: int
    tokens_before: int | None
    tokens_after: int | None


@dataclass(frozen=True)
class UiCheckpointSaved(UiEvent):
    name: str
    auto: bool


@dataclass(frozen=True)
class UiNotification(UiEvent):
    title: str
    level: str = "info"
    detail: str | None = None


@dataclass(frozen=True)
class UiTeamMessage(UiEvent):
    """One Collaboration Mode exchange, kept in the transcript (team journal)."""

    kind: str
    sender: dict[str, str]
    target: dict[str, str] | None
    content: str
    status: str | None = None


# Terminal face of each teammate avatar (the desktop draws the full bot).
TEAM_GLYPHS = {
    "visor": "▣", "round": "◉", "antenna": "Ψ", "square": "■", "cyclops": "◎",
    "ears": "▲", "dome": "◒", "crest": "♛", "navin": "◆", "user": "●",
}


def team_face(identity: dict[str, str] | None) -> str:
    """``◉ Salma`` in the teammate's color, as Textual markup."""
    if not identity:
        return ""
    from rich.markup import escape

    color = identity.get("color") or ""
    glyph = TEAM_GLYPHS.get(identity.get("avatar") or "", "◉")
    name = escape(identity.get("name") or "?")
    if len(color) == 7 and color.startswith("#"):
        return f"[b {color}]{glyph} {name}[/]"
    return f"[b]{glyph} {name}[/b]"


@dataclass(frozen=True)
class UiRetryWait(UiEvent):
    text: str


@dataclass(frozen=True)
class UiSubagent(UiEvent):
    task_id: str
    label: str
    phase: str
    status_line: str
    model: str | None
    iteration: int
    done: bool
    error: str | None
    started_ms_ago: int | None = None
    tokens: int | None = None
    task_description: str | None = None
    steps: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class UiApprovalRequested(UiEvent):
    request_id: str
    tool: str
    action: str
    reason: str
    detail: str
    consequence: str
    scope: str
    remember_offered: bool
    expires_at_ms: int | None


@dataclass(frozen=True)
class UiApprovalClosed(UiEvent):
    request_id: str
    allowed: bool
    reason: str


@dataclass(frozen=True)
class UiChoiceRequested(UiEvent):
    request_id: str
    question: str
    options: list[dict[str, Any]]
    allow_skip: bool
    recommended_id: str
    expires_at_ms: int | None


@dataclass(frozen=True)
class UiChoiceClosed(UiEvent):
    request_id: str
    option_id: str
    skipped: bool


@dataclass(frozen=True)
class UiCredentialRequested(UiEvent):
    request_id: str
    kind: str
    host: str
    names: list[str]
    reason: str


@dataclass(frozen=True)
class UiWorktreeMerge(UiEvent):
    """A Multitask subagent's changes were merged, or need the user's decision."""

    task_id: str
    label: str
    status: str
    files: list[str]
    conflicts: list[str]
    reason: str = ""
    branch: str = ""
    resolve_prompt: str = ""


@dataclass(frozen=True)
class UiCredentialClosed(UiEvent):
    request_id: str
    status: str


@dataclass(frozen=True)
class UiEngineError(UiEvent):
    text: str


@dataclass(frozen=True)
class UiDisplayError(UiEvent):
    text: str


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------


@dataclass
class RuntimeStatus:
    model: str = ""
    model_preset: str = "default"
    presets: list[str] = field(default_factory=list)
    provider: str = ""
    workspace: str = ""
    session_key: str = ""
    context_window: int = 0
    context_used: int = 0
    billed_tokens_session: int = 0
    #: Schemas the model sees: the last turn's real count when one was
    #: persisted, else what a plain turn in this session would send.
    tool_count: int = 0
    #: Every registered tool. Larger than ``tool_count`` because desks and
    #: on-demand tools are withheld until a turn names them.
    tool_registry_count: int = 0
    turn_active: bool = False
    last_latency_ms: int | None = None
    turns: int = 0


def tools_label(sent: int, loaded: int) -> str:
    """One line for the sidebar and welcome card: tools in the prompt vs loaded."""
    if loaded > sent > 0:
        return f"{sent} of {loaded} tools in prompt"
    return f"{sent or loaded} tools in prompt"


def sent_tool_count(loop: Any, session_metadata: dict | None) -> int:
    """Schemas a plain turn in this session would send, after the loop's gating.

    Mirrors the dispatch path: no product module, no composer mode, no text,
    so desks and on-demand tools are withheld unless the repository or the
    session's open desks bring them back.
    """
    from navin.agent.loop import AgentLoop
    from navin.agent.tool_surface import filter_tool_definitions

    definitions = list(loop.tools.get_definitions())
    meta = {"_wants_stream": True}
    denied = AgentLoop._denied_tools(
        None, meta, None, session_metadata, workspace=getattr(loop, "workspace", None)
    )
    allowed = AgentLoop._allowed_tools(None, meta, None, session_metadata)
    return len(filter_tool_definitions(definitions, denied, allowed=allowed))


def split_session_id(session_id: str) -> tuple[str, str]:
    if ":" in session_id:
        channel, chat_id = session_id.split(":", 1)
        return channel or "cli", chat_id or "direct"
    return "cli", session_id or "direct"


# Opening the sessions picker must stay fast: fix up at most this many
# untitled sessions per call (each fixup may load a full session file).
MAX_SESSION_TITLE_FIXUPS = 10


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class WorkspaceSwitchError(RuntimeError):
    """The engine could not open the other project folder; it stays on the previous one."""


class TuiRuntime:
    """Own the engine for one TUI process.

    Lifecycle: ``await start()`` once the Textual loop is running, then
    ``send()`` / ``stop_turn()`` / ``approve()`` / ``answer_choice()``,
    finally ``await close()``.
    """

    def __init__(
        self,
        config: Any,
        *,
        session_id: str = "cli:direct",
        on_event: UiCallback,
    ) -> None:
        self.config = config
        self.channel, self.chat_id = split_session_id(session_id)
        self._on_event = on_event
        self.bus: Any = None
        self.agent_loop: Any = None
        self._loop_task: asyncio.Task | None = None
        self._consumer_task: asyncio.Task | None = None
        self._unsubscribe_runtime: Callable[[], None] | None = None
        self.status = RuntimeStatus(workspace=str(config.workspace_path))
        self.status.session_key = self.session_key
        self._turn_started_at: float | None = None
        self._streamed_this_turn = False
        # Set by the TUI while a Live voice call is on (navin/tui/live_voice.py).
        self.voice_mode = False
        self._closed = False
        self._display_error_reported = False
        self._license_sync: Any = None
        from navin.tui.exit_summary import CliUsageHook

        self.usage = CliUsageHook()
        self.worked_seconds = 0.0
        self.shutdown_warnings: list[str] = []

    async def list_subagents(self) -> list[dict[str, Any]]:
        """Agents of the conversation on screen, not of every chat on this key.

        Every CLI starts on ``cli:direct`` whatever the project, and /new keeps
        the key: the journal held under it the agents of every project and of
        every earlier conversation, and F8 listed them all.
        """
        manager = getattr(self.agent_loop, "subagents", None)
        if manager is None:
            return []
        workspaces, since = await asyncio.to_thread(self._conversation_scope, manager)
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await asyncio.to_thread(
                manager.list_agents, self.session_key, offset=offset, limit=200,
                workspaces=workspaces, since=since,
            )
            rows.extend(page)
            if len(page) < 200:
                return rows
            offset += len(page)

    def _conversation_scope(self, manager: Any) -> tuple[set[str], float | None]:
        """Project folders and start time of the current conversation.

        The folders are the ones a subagent of this chat records: the turn's
        workspace scope, else the engine workspace. The start is the last /new
        (None for a conversation never restarted).
        """
        from navin.session.manager import CONVERSATION_STARTED_KEY

        loop = self.agent_loop
        workspaces = {str(manager.workspace)}
        session = loop.sessions.get_or_create(self.session_key)
        metadata = getattr(session, "metadata", None) or {}
        scopes = getattr(loop, "workspace_scopes", None)
        if scopes is not None:
            scope = scopes.for_turn(channel=self.channel, message_metadata=None, session_metadata=metadata)
            workspaces.add(str(scope.project_path))
        started = metadata.get(CONVERSATION_STARTED_KEY)
        since = float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else None
        return workspaces, since

    async def subagent_history(self, task_id: str, after: int = 0) -> dict[str, Any]:
        manager = getattr(self.agent_loop, "subagents", None)
        if manager is None:
            return {"agent": None, "events": [], "next_cursor": after, "has_more": False}
        return await asyncio.to_thread(manager.agent_history, self.session_key, task_id, after=after, limit=100)

    async def subagent_operation(self, task_id: str, operation: str) -> dict[str, Any]:
        from navin.agent.subagent import request_subagent_operation

        if self.bus is None:
            return {"ok": False, "error": "Runtime is not started."}
        self._ensure_tasks()
        return await request_subagent_operation(self.bus, self.session_key, operation, task_id)

    async def multitask_spawn(
        self, prompt: str, *, label: str = "", client_key: str = "", timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Run ``prompt`` in a parallel subagent, the webui Multitask path.

        Same runtime control (RUNTIME_CONTROL_MULTITASK_SPAWN) the desktop
        sends, so the subagent is isolated the same way, its progress arrives
        as SubagentProgressEvent rows and its result lands in this chat.
        """
        from navin.agent.subagent import request_multitask_spawn

        if self.bus is None:
            return {"ok": False, "error": "Runtime is not started."}
        self._ensure_tasks()
        return await request_multitask_spawn(
            self.bus, self.session_key, prompt,
            label=label, client_key=client_key, timeout=timeout,
        )

    async def merge_action(self, task_id: str, action: str) -> dict[str, Any]:
        """Resolve / keep / discard a Multitask merge card for this chat."""
        from navin.agent.worktree_merge import apply_action

        return await asyncio.to_thread(apply_action, task_id, action, session_key=self.session_key)

    async def replay_subagents(self) -> None:
        manager = getattr(self.agent_loop, "subagents", None)
        if manager is None:
            return
        for row in manager.running_snapshot(self.session_key):
            await self._emit(UiSubagent(
                row["task_id"], row["label"], row["phase"], row["status_line"], row.get("model"),
                row.get("iteration", 0), row.get("done", False), row.get("error"),
                started_ms_ago=row.get("started_ms_ago"), tokens=row.get("tokens"),
                task_description=row.get("task_description"), steps=row.get("steps"),
            ))

    # -- properties -------------------------------------------------------

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"

    @property
    def turn_active(self) -> bool:
        return self.status.turn_active

    @property
    def turn_elapsed_s(self) -> float:
        started = self._turn_started_at
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    # -- lifecycle --------------------------------------------------------

    @staticmethod
    def _managed_usage_hooks(config: Any) -> list[Any]:
        from navin.optional_live import live_modules_available

        if not live_modules_available():
            return []
        try:
            from navin.license_client import ManagedUsageHook
        except ImportError:
            return []
        return [ManagedUsageHook(config)]

    async def start(self) -> None:
        # Sends that arrive while the engine is still being built wait for it.
        self._start_done = asyncio.Event()
        try:
            await self._start()
        finally:
            self._start_done.set()

    async def _wait_for_start(self) -> None:
        done = getattr(self, "_start_done", None)
        if self.bus is None and done is not None:
            await done.wait()

    async def _build_engine_off_loop(self, attempts: int = 3) -> None:
        """Build the engine in a worker thread, retrying an import deadlock.

        The UI thread keeps importing its own modules while the worker builds
        the engine. Two threads importing the same eager package in opposite
        orders make Python abort one of them with "deadlock detected by
        _ModuleLock", or hand it a half-initialized module ("cannot import
        name X"); the other finishes, so building again then succeeds.
        """
        for attempt in range(1, attempts + 1):
            try:
                await asyncio.to_thread(self._build_engine)
                return
            except ImportError as exc:
                if not _concurrent_import_race(exc) or attempt == attempts:
                    raise
                logger.warning("Engine import deadlock (attempt {}): {}; retrying", attempt, exc)
                await asyncio.sleep(0.05 * attempt)

    async def _start(self) -> None:
        self._install_blocking_pool()
        # Imports, templates, provider snapshot and the tool registry take
        # about a second of plain Python: build them off the UI loop so the
        # first frame and typing never wait for the engine.
        await self._build_engine_off_loop()
        self._enable_interaction()
        await asyncio.to_thread(self._adopt_session_model_pin)
        await asyncio.to_thread(self._refresh_status)
        self._subscribe_runtime_events()
        from navin.optional_live import live_modules_available

        try:
            if not live_modules_available():
                raise ImportError("live account disabled")
            from navin.license_sync import start_license_sync

            self._license_sync = start_license_sync(lambda: self.agent_loop)
        except ImportError:
            self._license_sync = None
        self._ensure_tasks()
        self._start_freeze_watch()
        # Windows: same background ripgrep and Git installs as the desktop gateway.
        from navin.utils.path_env import ensure_git, ensure_ripgrep

        ensure_ripgrep()
        ensure_git()

    def _build_engine(self) -> None:
        """Worker thread: construct the bus, cron service and agent loop."""
        from navin.agent.hooks import DEFAULT_HOOK_FACTORIES
        from navin.agent.loop import AgentLoop
        from navin.bus.queue import MessageBus
        from navin.config.paths import is_default_workspace
        from navin.cron.service import CronService
        from navin.cron.spend import CronSpendHook
        from navin.providers.factory import (
            build_provider_snapshot_allowing_unconfigured,
            load_provider_snapshot_allowing_unconfigured,
        )
        from navin.providers.image_generation import image_gen_provider_configs
        from navin.utils.helpers import sync_workspace_templates
        from navin.webui.token_usage import TokenUsageHook

        config = self.config
        sync_workspace_templates(config.workspace_path)

        self.bus = MessageBus()
        if is_default_workspace(config.workspace_path):
            with contextlib.suppress(Exception):
                from navin.cli.commands import _migrate_cron_store

                _migrate_cron_store(config)
        cron = CronService(config.workspace_path / "cron" / "jobs.json")
        snapshot = build_provider_snapshot_allowing_unconfigured(config)

        self.agent_loop = AgentLoop.from_config(
            config,
            self.bus,
            cron_service=cron,
            image_generation_provider_configs=image_gen_provider_configs(config),
            hook_factories=list(DEFAULT_HOOK_FACTORIES),
            hooks=[
                self.usage,
                TokenUsageHook(timezone_name=config.agents.defaults.timezone),
                CronSpendHook(cron),
                *self._managed_usage_hooks(config),
            ],
            provider=snapshot.provider,
            provider_snapshot_loader=load_provider_snapshot_allowing_unconfigured,
        )
        # Warm the code index only now: its daemon thread imports navin.index
        # while this one still loaded the tools that import it too, and the
        # two met a half-initialized module ("cannot import name
        # 'RefreshStats' from 'navin.index.store'").
        with contextlib.suppress(Exception):
            from navin.index.warmer import schedule_warm

            schedule_warm(config.workspace_path)

    def _install_blocking_pool(self) -> None:
        """Give ``asyncio.to_thread`` a pool sized for waiting, not computing.

        The CLI runs the agent on the UI loop, so session saves, DNS lookups,
        prefs writes, board work and verify's lint/test runs (up to 600s each)
        all share one default executor. Python sizes it ``min(32, cpu + 4)``:
        ten threads on six cores. Two sessions running verify could hold every
        thread and queue the rest, including the saves the UI waits on. Reuse
        the gateway's sizing; threads only start when there is work for them.
        """
        if getattr(self, "_blocking_pool", None) is not None:
            return
        with contextlib.suppress(Exception):
            from concurrent.futures import ThreadPoolExecutor

            from navin.webui.blocking_pool import blocking_pool_size

            pool = ThreadPoolExecutor(
                max_workers=blocking_pool_size(),
                thread_name_prefix="navin-cli-blocking",
            )
            asyncio.get_running_loop().set_default_executor(pool)
            self._blocking_pool = pool

    def _start_freeze_watch(self) -> None:
        """Record every CLI freeze with the code that caused it.

        The terminal UI hides navin logs (they would corrupt the screen), so
        a frozen CLI used to leave no trace. The watch writes the blocking
        stack to ~/.navin/logs/cli-freeze.log, and faulthandler dumps every
        thread there if the process dies hard.
        """
        with contextlib.suppress(Exception):
            import faulthandler

            from navin.config.paths import get_logs_dir
            from navin.webui.stall_watch import LoopLagMonitor

            existing = getattr(self, "_freeze_file", None)
            if existing is None or existing.closed:
                # Opened once per process: a project switch restarts the watch only.
                path = get_logs_dir() / "cli-freeze.log"
                path.parent.mkdir(parents=True, exist_ok=True)
                self._freeze_file = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
                faulthandler.enable(self._freeze_file, all_threads=True)
            # No gc.freeze() after a slow full collection: it also froze the
            # widgets alive at that moment, and once windowed out of the chat
            # they could never be collected (2.2 M objects and +240 MB after
            # 30 probe turns). The loaded chat is frozen once, on history
            # paint, and thawed on session switch.
            self._loop_lag = LoopLagMonitor(_FileLog(self._freeze_file))
            self._loop_lag.start()

    def _enable_interaction(self) -> None:
        """This runtime has a card renderer, including for restored CLI chats."""
        for name in ("approvals", "choices", "credentials"):
            broker = getattr(self.agent_loop, name, None)
            if broker is not None:
                broker.enable_channel(self.channel)

    def _ensure_tasks(self) -> None:
        """Keep the chat usable after a consumer exits; never silently drop input."""
        if self._closed or self.agent_loop is None:
            return
        for attribute, factory, name in (
            ("_loop_task", self._run_agent_loop, "navin-tui-agent-loop"),
            ("_consumer_task", self._consume_outbound, "navin-tui-outbound"),
        ):
            task = getattr(self, attribute)
            if task is None or task.done():
                task = asyncio.create_task(factory(), name=name)
                task.add_done_callback(self._runtime_task_finished)
                setattr(self, attribute, task)

    def _run_agent_loop(self):
        # The loop defaults to resuming Desktop (websocket) turns only. The CLI
        # must resume its own chat: a turn cut by a crash or a closed terminal
        # restarts here instead of being lost.
        try:
            return self.agent_loop.run(recovery_channel=self.channel, recovery_session_key=self.session_key)
        except TypeError:
            # Embedders and test doubles whose run() takes no recovery options.
            return self.agent_loop.run()

    async def _resume_interrupted_turn(self) -> None:
        recovery = getattr(self.agent_loop, "turn_recovery", None)
        if recovery is None:
            return
        with contextlib.suppress(Exception):
            await asyncio.to_thread(recovery.discover, channel=self.channel, session_key=self.session_key)

    def _runtime_task_finished(self, task: asyncio.Task) -> None:
        if self._closed:
            return
        error = "cancelled" if task.cancelled() else str(task.exception() or "stopped unexpectedly")

        async def report() -> None:
            if self._closed or task not in (self._loop_task, self._consumer_task):
                return
            self._finish_turn({})
            await self._emit(UiEngineError(
                f"Chat connection stopped: {error}. Send a message to reconnect."
            ))

        asyncio.create_task(report())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        lag = getattr(self, "_loop_lag", None)
        if lag is not None:
            self._loop_lag = None
            with contextlib.suppress(Exception):
                await lag.stop()
        self.worked_seconds += self.turn_elapsed_s
        self._turn_started_at = None
        if self._license_sync is not None:
            with contextlib.suppress(Exception):
                from navin.license_sync import stop_license_sync

                stop_license_sync(self._license_sync)
            self._license_sync = None
        if self._unsubscribe_runtime:
            with contextlib.suppress(Exception):
                self._unsubscribe_runtime()
        loop = self.agent_loop
        if loop is not None:
            with contextlib.suppress(Exception):
                loop.stop()
        tasks = [t for t in (self._loop_task, self._consumer_task) if t and not t.done()]
        for active in getattr(loop, "_active_tasks", {}).values():
            tasks.extend(t for t in active if not t.done())
        subagents = getattr(loop, "subagents", None)
        # A freed slot must not launch a queued child while we are quitting.
        getattr(subagents, "_queued", {}).clear()
        tasks.extend(t for t in getattr(subagents, "_running_tasks", {}).values() if not t.done())
        # Stop dispatch and active work together, before waiting on cleanup.
        # The agent's cancellation handler persists its latest checkpoint.
        for task in tasks:
            task.cancel()
        from navin.tui.shutdown import drain

        # AgentLoop.run owns MCP cleanup in its finally block. Calling it again
        # here can wait twice and enter an AnyIO scope from the wrong task.
        if loop is not None and self._loop_task is None:
            tasks.append(asyncio.create_task(loop.close_mcp(), name="cli-mcp-close"))
        from navin.cli.commands import _close_agent_subprocesses

        tasks.append(asyncio.create_task(_close_agent_subprocesses(), name="cli-subprocess-close"))
        if not await drain(tasks, timeout=2.0):
            self.shutdown_warnings.append("Background cleanup exceeded 2 seconds; cancellation requested.")
        if loop is not None:
            sessions = getattr(loop, "sessions", None)
            if sessions is not None:
                # Turns already save atomically. Sync the files actually
                # written, without serializing every history opened in memory.
                try:
                    await asyncio.to_thread(sessions.flush_saved)
                except OSError as exc:
                    self.shutdown_warnings.append(f"Could not sync saved history to disk: {exc}")

    # -- project switch ---------------------------------------------------

    def running_work(self) -> int:
        """Turns and subagents still running in this engine."""
        loop = self.agent_loop
        active = getattr(loop, "_active_tasks", None) or {}
        turns = sum(1 for tasks in active.values() for task in tasks if not task.done())
        subagents = getattr(getattr(loop, "subagents", None), "_running_tasks", None) or {}
        return turns + sum(1 for task in subagents.values() if not task.done())

    async def _stop_engine(self) -> None:
        """Close this engine and leave the runtime ready to :meth:`start` again."""
        await self.close()
        self._closed = False
        self.bus = None
        self.agent_loop = None
        self._loop_task = None
        self._consumer_task = None
        self._unsubscribe_runtime = None
        self.status.turn_active = False

    async def _start_on(self, workspace: str, session_id: str, preset: str | None) -> None:
        self.config.agents.defaults.workspace = workspace
        os.chdir(self.config.workspace_path)
        self.channel, self.chat_id = split_session_id(session_id)
        self.status.workspace = str(self.config.workspace_path)
        self.status.session_key = self.session_key
        await self.start()
        if preset and self.agent_loop is not None:
            self.agent_loop.model_preset = preset
        await asyncio.to_thread(self._refresh_status)

    async def switch_workspace(self, workspace: str | Path, session_id: str) -> None:
        """Move the engine to another project folder without restarting the CLI.

        Reopening the CLI there took seconds: the process exit, every import
        again and a new screen. The modules are loaded already, so only the
        engine (sessions, tools, cron, index) is rebuilt on the new folder, and
        the working directory follows it, as ``navin-cli <folder>`` does. If
        the new folder fails, the engine comes back on the previous one and
        :class:`WorkspaceSwitchError` says why.
        """
        target = Path(workspace).expanduser()
        if not target.is_dir():
            raise WorkspaceSwitchError(f"Not a folder: {target}")
        previous = str(self.config.agents.defaults.workspace)
        previous_session = self.session_key
        preset = getattr(self.agent_loop, "model_preset", None)
        await self._stop_engine()
        try:
            await self._start_on(str(target.resolve()), session_id, preset)
        except Exception as exc:
            await self._stop_engine()
            try:
                await self._start_on(previous, previous_session, preset)
            except Exception as again:
                raise WorkspaceSwitchError(
                    f"Could not open {target}: {exc}. The engine did not restart on {previous} either: {again}"
                ) from again
            raise WorkspaceSwitchError(f"Could not open {target}: {exc}") from exc

    # -- outbound ---------------------------------------------------------

    async def _emit(self, event: UiEvent) -> None:
        if self._closed:
            return
        try:
            result = self._on_event(event)
            if asyncio.iscoroutine(result):
                await result
            if not isinstance(event, UiDisplayError):
                self._display_error_reported = False
        except Exception:  # noqa: BLE001 - UI failures must not kill the engine
            logger.exception("TUI event handler failed for {}", type(event).__name__)
            if not isinstance(event, UiDisplayError):
                await self._report_display_error()

    async def _report_display_error(self) -> None:
        if self._display_error_reported or self._closed:
            return
        self._display_error_reported = True
        await self._emit(UiDisplayError("A chat update could not be displayed. Work is continuing."))

    def _is_ours(self, msg: OutboundMessage) -> bool:
        if msg.channel == "system":
            return True
        return msg.channel == self.channel and msg.chat_id == self.chat_id

    async def _consume_outbound(self) -> None:
        pending = None
        while True:
            try:
                msg = pending if pending is not None else await self.bus.consume_outbound()
                pending = None
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                if self._is_ours(msg):
                    event = outbound_event_from_message(msg)
                    if isinstance(event, StreamDeltaEvent):
                        chunks = [msg.content or ""]
                        # Drain only adjacent deltas of this stream. A tool,
                        # stream-end or another session is an ordering barrier.
                        for _ in range(255):
                            try:
                                following = self.bus.outbound.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            next_event = outbound_event_from_message(following)
                            if (
                                following.channel != msg.channel or following.chat_id != msg.chat_id
                                or not isinstance(next_event, StreamDeltaEvent)
                                or next_event.stream_id != event.stream_id
                            ):
                                pending = following
                                break
                            chunks.append(following.content or "")
                        content = "".join(chunks)
                        msg = replace(msg, content=content, event=replace(event, content=content))
                    await self._dispatch(msg)
                # Queue.get() and mounted widget callbacks can both complete
                # without yielding. Give keyboard and navigation a turn even
                # when several tasks continuously fill the bus.
                await asyncio.sleep(0)
            except Exception:  # noqa: BLE001
                logger.exception("TUI failed to dispatch outbound message")
                await self._report_display_error()

    async def _dispatch(self, msg: OutboundMessage) -> None:
        observer = getattr(self, "share_observer", None)
        if observer is not None and msg.channel == self.channel:
            try:
                await observer.on_outbound(msg)
            except Exception:  # noqa: BLE001 - a guest never breaks the host chat
                logger.exception("TUI live share failed to forward an event")
        event = outbound_event_from_message(msg)
        if isinstance(event, StreamDeltaEvent):
            self._streamed_this_turn = True
            await self._emit(UiStreamDelta(msg.content or ""))
            return
        if isinstance(event, StreamEndEvent):
            await self._emit(UiStreamEnd(resuming=event.resuming, discarded=event.discarded))
            return
        if isinstance(event, StreamedResponseEvent):
            await self._emit(
                UiAssistantMessage(
                    text=msg.content or "",
                    metadata=dict(msg.metadata or {}),
                    streamed=self._streamed_this_turn,
                    render_as=str((msg.metadata or {}).get("render_as") or "markdown"),
                )
            )
            self._finish_turn(msg.metadata)
            return
        if isinstance(event, TurnEndEvent):
            self._finish_turn({"latency_ms": event.latency_ms})
            return
        if isinstance(event, ProgressEvent):
            await self._dispatch_progress(msg, event)
            return
        if isinstance(event, RetryWaitEvent):
            await self._emit(UiRetryWait(msg.content or event.content))
            return
        if isinstance(event, RuntimeModelUpdatedEvent):
            self._refresh_status()
            await self._emit(
                UiModelUpdated(event.model, event.model_preset, event.reason, event.used_percent)
            )
            return
        if isinstance(event, ContextCompactedEvent):
            await self._emit(
                UiContextCompacted(
                    event.kind, event.messages_archived, event.tokens_before, event.tokens_after
                )
            )
            return
        if isinstance(event, CheckpointSavedEvent):
            await self._emit(UiCheckpointSaved(event.name, event.auto))
            return
        if isinstance(event, NotificationEvent):
            await self._emit(UiNotification(event.title, event.level, event.detail))
            return
        if isinstance(event, WorktreeMergeEvent):
            await self._emit(UiWorktreeMerge(
                event.task_id, event.label, event.status, list(event.files),
                list(event.conflicts), event.reason, event.branch, event.resolve_prompt,
            ))
            return
        if isinstance(event, TeamMessageEvent):
            await self._emit(UiTeamMessage(
                event.kind, dict(event.sender),
                dict(event.target) if event.target else None,
                event.content, event.status,
            ))
            return
        if isinstance(event, SubagentProgressEvent):
            await self._emit(
                UiSubagent(
                    event.task_id,
                    # A teammate's row carries its face, like in the desktop.
                    f"{TEAM_GLYPHS.get(event.member.get('avatar') or '', '◉')} {event.label}"
                    if event.member else event.label,
                    event.phase,
                    event.status_line,
                    event.model,
                    event.iteration,
                    event.done,
                    event.error,
                    started_ms_ago=event.started_ms_ago,
                    tokens=event.tokens,
                    task_description=event.task_description,
                    steps=event.steps,
                )
            )
            return
        if isinstance(event, ApprovalRequestedEvent):
            await self._emit(
                UiApprovalRequested(
                    event.request_id,
                    event.tool,
                    event.action,
                    event.reason,
                    event.detail,
                    event.consequence,
                    event.scope,
                    event.remember_offered,
                    event.expires_at_ms,
                )
            )
            return
        if isinstance(event, ApprovalClosedEvent):
            await self._emit(UiApprovalClosed(event.request_id, event.allowed, event.reason))
            return
        if isinstance(event, ChoiceRequestedEvent):
            await self._emit(
                UiChoiceRequested(
                    event.request_id,
                    event.question,
                    list(event.options or []),
                    event.allow_skip,
                    event.recommended_id,
                    event.expires_at_ms,
                )
            )
            return
        if isinstance(event, ChoiceClosedEvent):
            await self._emit(UiChoiceClosed(event.request_id, event.option_id, event.skipped))
            return
        if isinstance(event, CredentialRequestedEvent):
            await self._emit(
                UiCredentialRequested(
                    event.request_id, event.kind, event.host, list(event.names or []), event.reason
                )
            )
            return
        if isinstance(event, CredentialClosedEvent):
            await self._emit(UiCredentialClosed(event.request_id, event.status))
            return
        if isinstance(event, FilePreviewOpenRequestedEvent):
            await self._emit(UiFilePreview(path=str(event.path)))
            return
        if isinstance(event, OutboundEvent):
            # Desktop-only UI events (board, montage, preview, editor...) are
            # irrelevant in a terminal; ignore them silently.
            return
        # Plain message: side-channel reply (/help, /model ...) or a final
        # non-streamed answer.
        if msg.content:
            await self._emit(
                UiAssistantMessage(
                    text=msg.content,
                    metadata=dict(msg.metadata or {}),
                    streamed=False,
                    render_as=str((msg.metadata or {}).get("render_as") or "markdown"),
                )
            )
        tasks = getattr(self.agent_loop, "_active_tasks", {}).get(self.session_key, [])
        if self.status.turn_active and not any(not task.done() for task in tasks):
            # A side-channel command can answer while the main task continues.
            # Its reply must not remove Working; the engine emits TurnCompleted.
            self._finish_turn(msg.metadata)

    async def _dispatch_progress(self, msg: OutboundMessage, event: ProgressEvent) -> None:
        ch = getattr(self.agent_loop, "channels_config", None)
        if event.reasoning_end:
            await self._emit(UiReasoning("", end=True))
            return
        if event.reasoning or event.reasoning_delta:
            if ch is not None and not getattr(ch, "show_reasoning", True):
                return
            await self._emit(UiReasoning(msg.content or ""))
            return
        if event.tool_events:
            for payload in event.tool_events:
                if not isinstance(payload, dict):
                    continue
                phase = str(payload.get("phase") or "")
                args = payload.get("arguments")
                await self._emit(
                    UiToolEvent(
                        call_id=str(payload.get("call_id") or ""),
                        name=str(payload.get("name") or payload.get("tool") or ""),
                        phase=phase or ("output" if payload.get("output") else "start"),
                        arguments=args if isinstance(args, dict) else {},
                        result=payload.get("result"),
                        error=payload.get("error"),
                        output=payload.get("output") if isinstance(payload.get("output"), str) else None,
                        label=payload.get("label") if isinstance(payload.get("label"), str) else None,
                        percent=payload.get("percent") if isinstance(payload.get("percent"), (int, float)) else None,
                        expected_s=(
                            payload.get("test_expected_s")
                            if isinstance(payload.get("test_expected_s"), (int, float))
                            else None
                        ),
                        output_mode=str(payload.get("output_mode") or "delta"),
                    )
                )
        if event.file_edit_events:
            for payload in event.file_edit_events:
                if not isinstance(payload, dict):
                    continue
                await self._emit(UiFileEdit.from_payload(payload))
        text = (msg.content or "").strip()
        if not text:
            agent_ui = (msg.metadata or {}).get("_agent_ui")
            if isinstance(agent_ui, dict) and agent_ui.get("kind") == "task_progress":
                label = str((agent_ui.get("data") or {}).get("label") or "").strip()
                if label.startswith("Compacting context"):
                    # Long model-backed summarization: say so instead of a
                    # silent "Working…" under a 100% context gauge.
                    await self._emit(UiProgress(label, tool_hint=True))
            return
        if event.tool_hint:
            if ch is not None and not getattr(ch, "send_tool_hints", True):
                return
            # The rows above already say it; a second "· read_file(...)" line
            # under them only repeats the chain (the plain CLI skips it too).
            if event.tool_events:
                return
            await self._emit(UiProgress(text, tool_hint=True))
            return
        if ch is not None and not getattr(ch, "send_progress", True):
            return
        await self._emit(UiProgress(text))

    def _finish_turn(self, metadata: Any) -> None:
        if not self.status.turn_active:
            return
        latency = None
        if isinstance(metadata, dict) and metadata.get("latency_ms") is not None:
            with contextlib.suppress(Exception):
                latency = int(metadata["latency_ms"])
        if latency is None and self._turn_started_at is not None:
            latency = int((time.monotonic() - self._turn_started_at) * 1000)
        self.status.turn_active = False
        self.worked_seconds += self.turn_elapsed_s
        self.status.last_latency_ms = latency
        self.status.turns += 1
        self._turn_started_at = None
        self._refresh_status()
        asyncio.get_running_loop().create_task(self._emit(UiTurnEnd(latency)))

    # -- runtime events (model changes from other paths) ------------------

    def _subscribe_runtime_events(self) -> None:
        bus = getattr(self.agent_loop, "runtime_events", None)
        if bus is None:
            return
        from navin.bus.runtime_events import RuntimeModelChanged, SessionTurnStarted, TurnCompleted

        async def _handler(event: Any) -> None:
            if isinstance(event, RuntimeModelChanged):
                self._refresh_status()
            elif isinstance(event, SessionTurnStarted):
                if event.context.session_key == self.session_key and not self.status.turn_active:
                    self.status.turn_active = True
                    self._streamed_this_turn = False
                    self._turn_started_at = time.monotonic()
                    await self._emit(UiTurnStarted(event.user_text))
            elif isinstance(event, TurnCompleted):
                if event.context.session_key == self.session_key and self.status.turn_active:
                    self._finish_turn({"latency_ms": event.latency_ms})

        self._unsubscribe_runtime = bus.subscribe(_handler)

    # -- status -----------------------------------------------------------

    def _refresh_status(self) -> None:
        loop = self.agent_loop
        if loop is None:
            return
        st = self.status
        with contextlib.suppress(Exception):
            st.model = str(loop.model or "")
        with contextlib.suppress(Exception):
            st.model_preset = loop.model_preset or "default"
        with contextlib.suppress(Exception):
            names = set(loop.model_presets)
            st.presets = ["default", *sorted(n for n in names if n != "default")]
        with contextlib.suppress(Exception):
            resolved = self.config.resolve_preset(None if st.model_preset == "default" else st.model_preset)
            st.provider = str(getattr(resolved, "provider", "") or "")
        with contextlib.suppress(Exception):
            st.context_window = int(loop.context_window_tokens or 0)
        with contextlib.suppress(Exception):
            st.tool_registry_count = len(list(loop.tools.get_definitions()))
        sent_from_turn = 0
        with contextlib.suppress(Exception):
            from navin.session.context_usage_meta import last_context_usage_row

            session = loop.sessions.get_or_create(self.session_key)
            row = last_context_usage_row(session.metadata)
            if row:
                st.context_used = int(row.get("prompt_tokens") or 0)
                st.billed_tokens_session = int(row.get("billed_tokens_session") or 0)
                sent_from_turn = int(row.get("tool_count") or 0)
                # The live runtime window wins: a size changed in Settings
                # must show now, not after the next turn.
                if row.get("context_window") and not st.context_window:
                    st.context_window = int(row["context_window"])
            else:
                st.context_used = 0
                st.billed_tokens_session = 0
        # The registry is not what the model pays for: desks and on-demand
        # tools are withheld until a turn names them. Show the real count from
        # the last turn, else what a plain turn would send right now.
        if sent_from_turn > 0:
            st.tool_count = sent_from_turn
        else:
            st.tool_count = st.tool_registry_count
            with contextlib.suppress(Exception):
                session = loop.sessions.get_or_create(self.session_key)
                st.tool_count = sent_tool_count(loop, session.metadata)
        st.session_key = self.session_key
        with contextlib.suppress(Exception):
            session = loop.sessions.get_or_create(self.session_key)
            st.turns = sum(
                1
                for msg in session.messages
                if isinstance(msg, dict) and msg.get("role") == "user" and not msg.get("injected_event")
            )

    def preset_details(self) -> list[dict[str, Any]]:
        """Model presets as rows for the picker."""
        rows: list[dict[str, Any]] = []
        loop = self.agent_loop
        try:
            resolved_default = self.config.resolve_preset("default")
        except Exception:  # noqa: BLE001
            resolved_default = None
        rows.append(
            {
                "name": "default",
                "model": getattr(resolved_default, "model", "") or "",
                "provider": getattr(resolved_default, "provider", "") or "",
                "label": "Default",
                "modality": "text",
            }
        )
        presets = dict(getattr(self.config, "model_presets", {}) or {})
        if loop is not None:
            presets.update(getattr(loop, "model_presets", {}) or {})
        # The agent's presets carry "default" too: it is the row above, once.
        presets.pop("default", None)
        for name in sorted(presets):
            preset = presets[name]
            rows.append(
                {
                    "name": name,
                    "model": str(getattr(preset, "model", "") or ""),
                    "provider": str(getattr(preset, "provider", "") or ""),
                    "label": str(getattr(preset, "label", "") or ""),
                    "modality": getattr(preset, "modality", "text"),
                    "input_modalities": getattr(preset, "input_modalities", None),
                    "enabled": getattr(preset, "enabled", True),
                }
            )
        return rows

    def reasoning_details(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        from navin.tui.models import reasoning_options

        resolved = self.config.resolve_preset(self.status.model_preset)
        options = reasoning_options(resolved.provider or "", resolved.model)
        effort = resolved.reasoning_effort or ""
        if self.agent_loop is not None:
            session = self.agent_loop.sessions.get_or_create(self.session_key)
            choices = session.metadata.get("tui_reasoning_efforts", {})
            effort = choices.get(self._reasoning_key(resolved), effort)
        values = {value for value, _ in options}
        return (effort if effort in values else ""), options

    def _reasoning_key(self, resolved: Any) -> str:
        return f"{self.status.model_preset}:{resolved.provider}:{resolved.model}"

    def set_reasoning_effort(self, effort: str) -> None:
        if self.agent_loop is None:
            raise RuntimeError("runtime not started")
        _, options = self.reasoning_details()
        if effort not in {value for value, _ in options}:
            raise ValueError("This reasoning level is not supported by the selected model")
        resolved = self.config.resolve_preset(self.status.model_preset)
        session = self.agent_loop.sessions.get_or_create(self.session_key)
        session.metadata.setdefault("tui_reasoning_efforts", {})[self._reasoning_key(resolved)] = effort
        self.agent_loop.sessions.save(session)

    def tool_rows(self) -> list[dict[str, Any]]:
        loop = self.agent_loop
        if loop is None:
            return []
        rows: list[dict[str, Any]] = []
        try:
            defs = list(loop.tools.get_definitions())
        except Exception:  # noqa: BLE001
            defs = []
        for definition in defs:
            fn = definition.get("function", definition) if isinstance(definition, dict) else {}
            name = str(fn.get("name") or "")
            if not name:
                continue
            params = fn.get("parameters") or {}
            props = params.get("properties") if isinstance(params, dict) else {}
            rows.append(
                {
                    "name": name,
                    "description": str(fn.get("description") or "").strip(),
                    "params": sorted(props.keys()) if isinstance(props, dict) else [],
                    "mcp": name.startswith("mcp_") or "__" in name,
                }
            )
        rows.sort(key=lambda r: (r["mcp"], r["name"]))
        return rows

    def session_rows(self) -> list[dict[str, Any]]:
        loop = self.agent_loop
        if loop is None:
            return []
        try:
            rows = loop.sessions.list_sessions()
        except Exception:  # noqa: BLE001
            return []
        return [r for r in rows if isinstance(r, dict)]

    def set_session_title(self, key: str, title: str) -> str:
        loop = self.agent_loop
        if loop is None:
            raise RuntimeError("engine not ready")
        return loop.sessions.set_title(key, title)

    def ensure_session_titles(self) -> None:
        """Fill missing names from the first user line and persist them.

        Bounded on purpose: sessions without a usable list preview are skipped
        (a full load from disk cannot help) and only a handful of untitled
        sessions are fixed up per call, so opening the picker stays fast even
        with hundreds of chats.
        """
        loop = self.agent_loop
        if loop is None:
            return
        from navin.cognition.episodes import first_user_text
        from navin.session.webui_turns import apply_provisional_title
        from navin.tui.session_labels import session_display_title

        fixups = 0
        for row in self.session_rows():
            if session_display_title(row) != "Untitled chat":
                continue
            key = str(row.get("key") or "")
            # A row with a preview already shows something useful in the
            # picker; a full session load cannot improve it visibly.
            if not key or str(row.get("preview") or "").strip():
                continue
            if fixups >= MAX_SESSION_TITLE_FIXUPS:
                break
            fixups += 1
            session = loop.sessions.peek(key)
            if session is None:
                continue
            if apply_provisional_title(session, first_user_text(session.messages)):
                loop.sessions.save(session)

    def history(self, limit: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Visible user/assistant turns for the transcript.

        Returns ``(rows, older_count)``. ``older_count`` is how many visible
        turns sit before this window (0 unless a cap dropped them).
        """
        loop = self.agent_loop
        if loop is None:
            return [], 0
        try:
            session = loop.sessions.get_or_create(self.session_key)
        except Exception:  # noqa: BLE001
            return [], 0
        from navin.tui.history import visible_chat_rows

        return visible_chat_rows(session.messages, limit=limit)

    def history_snapshot(self) -> list[Any]:
        """Stable source for paging, without eagerly formatting old turns."""
        if self.agent_loop is None:
            return []
        try:
            return list(self.agent_loop.sessions.get_or_create(self.session_key).messages)
        except Exception:  # noqa: BLE001 - match history() for unavailable sessions
            return []

    def slash_commands(self) -> list[dict[str, Any]]:
        from navin.command.builtin import BUILTIN_COMMAND_SPECS

        rows = [spec.as_dict() for spec in BUILTIN_COMMAND_SPECS]
        rows.append(
            {
                "command": "/ask",
                "title": "Ask (read-only)",
                "description": "Answer a question without changing files.",
                "icon": "message-circle",
                "arg_hint": "<question>",
                "lifecycle": "agent_turn",
                "accepts_args": True,
            }
        )
        return rows

    # -- inbound ----------------------------------------------------------

    async def switch_session(self, session_id: str) -> None:
        """Point the UI at another session key (no engine restart needed)."""
        self.channel, self.chat_id = split_session_id(session_id)
        self._enable_interaction()
        self.status.turn_active = False
        self._turn_started_at = None
        await asyncio.to_thread(self._adopt_session_model_pin)
        await asyncio.to_thread(self._refresh_status)
        # Opening a chat whose turn was interrupted picks that work up again.
        await self._resume_interrupted_turn()

    async def send(self, text: str, *, model_preset: str | None = None, followup: bool = False) -> None:
        await self._wait_for_start()
        if self.bus is None:
            raise RuntimeError("runtime not started")
        text = text.strip()
        if not text:
            return
        observer = getattr(self, "share_observer", None)
        if observer is not None:
            try:
                await observer.on_user_text(text)
            except Exception:  # noqa: BLE001 - a guest never breaks the host chat
                logger.exception("TUI live share failed to forward a message")
        self._ensure_tasks()
        metadata: dict[str, Any] = {"_wants_stream": True}
        if self.voice_mode:
            # Live call: same "expert colleague on a call" brief as the desktop.
            from navin.agent.voice_mode import VOICE_MODE_METADATA_KEY

            metadata[VOICE_MODE_METADATA_KEY] = True
        sessions = getattr(self.agent_loop, "sessions", None)
        if sessions is not None:
            from navin.agent.adaptive_reasoning import REASONING_EFFORT_METADATA_KEY

            resolved = self.config.resolve_preset(self.status.model_preset)
            session = sessions.get_or_create(self.session_key)
            if self._reasoning_key(resolved) in session.metadata.get("tui_reasoning_efforts", {}):
                metadata[REASONING_EFFORT_METADATA_KEY] = self.reasoning_details()[0]
        if model_preset and model_preset != "default":
            from navin.bus.events import INBOUND_META_MODEL_PRESET

            metadata[INBOUND_META_MODEL_PRESET] = model_preset
        if not followup or not self.status.turn_active:
            self.status.turn_active = True
            self._streamed_this_turn = False
            self._turn_started_at = time.monotonic()
            await self._emit(UiTurnStarted(text))
        try:
            from navin.session.webui_turns import apply_provisional_title

            loop = self.agent_loop
            if loop is not None:
                session = loop.sessions.get_or_create(self.session_key)
                if apply_provisional_title(session, text):
                    loop.sessions.save(session)
        except Exception:  # noqa: BLE001
            pass
        from navin.tui.images import extract_image_paths

        media = extract_image_paths(text, Path(self.workspace) if getattr(self, "workspace", None) else Path.cwd())
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.channel,
                sender_id="user",
                chat_id=self.chat_id,
                content=text,
                media=media,
                metadata=metadata,
            )
        )

    async def send_command(self, text: str) -> None:
        """Send a slash command that must not be counted as a user turn."""
        await self._wait_for_start()
        if self.bus is None:
            raise RuntimeError("runtime not started")
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.channel,
                sender_id="user",
                chat_id=self.chat_id,
                content=text.strip(),
                metadata={"_wants_stream": True},
            )
        )

    async def stop_turn(self) -> None:
        if self.bus is None or not self.status.turn_active:
            return
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.channel,
                sender_id="user",
                chat_id=self.chat_id,
                content="/stop",
            )
        )

    async def approve(self, request_id: str, *, allowed: bool, remember: bool = False) -> None:
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="tui-approval",
                chat_id="runtime",
                content=RUNTIME_CONTROL_APPROVAL_DECISION,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_APPROVAL_DECISION,
                    "request_id": request_id,
                    "allowed": bool(allowed),
                    "remember": bool(remember),
                    "answerer_chats": [self.session_key, self.chat_id],
                },
            )
        )

    async def answer_choice(
        self,
        request_id: str,
        *,
        option_id: str = "",
        skipped: bool = False,
        custom_text: str = "",
    ) -> None:
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="tui-choice",
                chat_id="runtime",
                content=RUNTIME_CONTROL_CHOICE_ANSWER,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CHOICE_ANSWER,
                    "request_id": request_id,
                    "option_id": option_id,
                    "skipped": bool(skipped),
                    "custom_text": custom_text.strip()[:2000],
                },
            )
        )

    async def answer_credential(self, request_id: str, *, status: str, login: str = "") -> None:
        """Release a credential card: a status and a login name, never a value."""
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="tui-credentials",
                chat_id="runtime",
                content=RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CREDENTIAL_ANSWER,
                    "request_id": request_id,
                    "status": status if status in {"saved", "connected", "declined"} else "declined",
                    "login": (login or "").strip()[:80],
                },
            )
        )

    def set_model_preset(self, name: str | None) -> None:
        """Switch the runtime preset for future turns (same as ``/model``).

        The pick is remembered on this chat, so going to another session and
        back, or closing and reopening the CLI, keeps it.
        """
        if self.agent_loop is None:
            return
        self._apply_model_preset(name)
        self._remember_model_pin(name)
        self._refresh_status()

    def _apply_model_preset(self, name: str | None) -> None:
        row = next((row for row in self.preset_details() if row["name"] == (name or "default")), None)
        if row is None or not row.get("enabled", True) or row.get("modality", "text") != "text":
            raise ValueError("Choose an enabled chat model; media models are configured in Settings")
        self.agent_loop.model_preset = None if name in (None, "", "default") else name

    def _remember_model_pin(self, name: str | None) -> None:
        """Store the pick as this chat's sticky pin (the one the engine honours)."""
        sessions = getattr(self.agent_loop, "sessions", None)
        if sessions is None:
            return
        from navin.agent.loop import SESSION_MODEL_PIN_KEY

        session = sessions.get_or_create(self.session_key)
        if name in (None, "", "default"):
            # Back to the default: no pin, so task routing applies again.
            if session.metadata.pop(SESSION_MODEL_PIN_KEY, None) is None:
                return
        elif session.metadata.get(SESSION_MODEL_PIN_KEY) == name:
            return
        else:
            session.metadata[SESSION_MODEL_PIN_KEY] = name
        sessions.save(session)

    def _adopt_session_model_pin(self) -> None:
        """Put back the model picked for this chat.

        A chat without its own pick keeps the model in use, so moving to a
        fresh session does not drop the user's choice either.
        """
        sessions = getattr(self.agent_loop, "sessions", None)
        if sessions is None:
            return
        from navin.agent.loop import AUTO_MODEL_PIN, SESSION_MODEL_PIN_KEY

        pin = sessions.get_or_create(self.session_key).metadata.get(SESSION_MODEL_PIN_KEY)
        if not isinstance(pin, str) or not pin.strip():
            return
        pin = pin.strip()
        try:
            self._apply_model_preset("default" if pin.lower() == AUTO_MODEL_PIN else pin)
        except ValueError:
            logger.info(
                "chat {} pins model {}, no longer an enabled chat model; keeping {}",
                self.session_key,
                pin,
                self.agent_loop.model_preset or "default",
            )

    def reload_from_disk(self, config_path: Path | None = None) -> None:
        """Pick up provider / model / account edits without restarting the TUI."""
        from navin.config.loader import load_config

        self.config = load_config(config_path) if config_path else load_config()
        loop = self.agent_loop
        if loop is not None:
            with contextlib.suppress(Exception):
                loop.runtime_resolver.current(refresh=True)
        self._refresh_status()

    def apply_account_from_disk(self, config_path: Path | None = None) -> None:
        """After connect / refresh / disconnect, use the disk provider now."""
        from navin.config.loader import load_config

        self.config = load_config(config_path) if config_path else load_config()
        from navin.optional_live import live_modules_available

        if live_modules_available():
            with contextlib.suppress(ImportError):
                from navin.license_sync import apply_live_account_runtime

                apply_live_account_runtime()
        loop = self.agent_loop
        if loop is not None:
            with contextlib.suppress(Exception):
                loop.runtime_resolver.current(refresh=True)
        self._refresh_status()

    # -- workspace ----------------------------------------------------------

    @property
    def workspace(self) -> Path:
        return Path(self.config.workspace_path)


def _concurrent_import_race(exc: ImportError) -> bool:
    """An import that lost a race with another thread, not a missing module."""
    text = str(exc)
    return "deadlock detected" in text or (
        "cannot import name" in text and "navin" in text
    ) or "partially initialized module" in text
