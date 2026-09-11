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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from navin.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_APPROVAL_DECISION,
    RUNTIME_CONTROL_CHOICE_ANSWER,
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
    NotificationEvent,
    OutboundEvent,
    ProgressEvent,
    RetryWaitEvent,
    RuntimeModelUpdatedEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    SubagentProgressEvent,
    TurnEndEvent,
    outbound_event_from_message,
)

UiCallback = Callable[["UiEvent"], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# UI-facing events (engine events, flattened for widgets)
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True)
class UiReasoning(UiEvent):
    text: str
    end: bool = False


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


@dataclass(frozen=True)
class UiFileEdit(UiEvent):
    path: str
    kind: str = ""
    added: int = 0
    removed: int = 0


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
class UiEngineError(UiEvent):
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


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


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
        self._closed = False
        self._license_sync: Any = None

    # -- properties -------------------------------------------------------

    @property
    def session_key(self) -> str:
        return f"{self.channel}:{self.chat_id}"

    @property
    def turn_active(self) -> bool:
        return self.status.turn_active

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
        with contextlib.suppress(Exception):
            from navin.index.warmer import schedule_warm

            schedule_warm(config.workspace_path)

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
                TokenUsageHook(timezone_name=config.agents.defaults.timezone),
                CronSpendHook(cron),
                *self._managed_usage_hooks(config),
            ],
            provider=snapshot.provider,
            provider_snapshot_loader=load_provider_snapshot_allowing_unconfigured,
        )
        self._refresh_status()
        self._subscribe_runtime_events()
        from navin.optional_live import live_modules_available

        try:
            if not live_modules_available():
                raise ImportError("live account disabled")
            from navin.license_sync import start_license_sync

            self._license_sync = start_license_sync(lambda: self.agent_loop)
        except ImportError:
            self._license_sync = None
        self._loop_task = asyncio.create_task(self.agent_loop.run(), name="navin-tui-agent-loop")
        self._consumer_task = asyncio.create_task(self._consume_outbound(), name="navin-tui-outbound")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._license_sync is not None:
            with contextlib.suppress(Exception):
                from navin.license_sync import stop_license_sync

                stop_license_sync(self._license_sync)
            self._license_sync = None
        if self._unsubscribe_runtime:
            with contextlib.suppress(Exception):
                self._unsubscribe_runtime()
        if self.agent_loop is not None:
            with contextlib.suppress(Exception):
                self.agent_loop.stop()
        if self._consumer_task:
            self._consumer_task.cancel()
        tasks = [t for t in (self._loop_task, self._consumer_task) if t]
        if tasks:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=8)
        if self.agent_loop is not None:
            with contextlib.suppress(Exception):
                await self.agent_loop.close_mcp()
        with contextlib.suppress(Exception):
            from navin.cli.commands import _close_agent_subprocesses

            await _close_agent_subprocesses()

    # -- outbound ---------------------------------------------------------

    async def _emit(self, event: UiEvent) -> None:
        try:
            result = self._on_event(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - UI failures must not kill the engine
            logger.exception("TUI event handler failed for {}", type(event).__name__)

    def _is_ours(self, msg: OutboundMessage) -> bool:
        if msg.channel == "system":
            return True
        return msg.channel == self.channel and msg.chat_id == self.chat_id

    async def _consume_outbound(self) -> None:
        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                if self._is_ours(msg):
                    await self._dispatch(msg)
            except Exception:  # noqa: BLE001
                logger.exception("TUI failed to dispatch outbound message")

    async def _dispatch(self, msg: OutboundMessage) -> None:
        event = outbound_event_from_message(msg)
        if isinstance(event, StreamDeltaEvent):
            self._streamed_this_turn = True
            await self._emit(UiStreamDelta(msg.content or ""))
            return
        if isinstance(event, StreamEndEvent):
            await self._emit(UiStreamEnd(resuming=event.resuming))
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
        if isinstance(event, SubagentProgressEvent):
            await self._emit(
                UiSubagent(
                    event.task_id,
                    event.label,
                    event.phase,
                    event.status_line,
                    event.model,
                    event.iteration,
                    event.done,
                    event.error,
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
        if self.status.turn_active:
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
                    )
                )
        if event.file_edit_events:
            for payload in event.file_edit_events:
                if not isinstance(payload, dict):
                    continue
                await self._emit(
                    UiFileEdit(
                        path=str(payload.get("path") or ""),
                        kind=str(payload.get("kind") or payload.get("op") or ""),
                        added=int(payload.get("added") or payload.get("lines_added") or 0),
                        removed=int(payload.get("removed") or payload.get("lines_removed") or 0),
                    )
                )
        text = (msg.content or "").strip()
        if not text:
            return
        if event.tool_hint:
            if ch is not None and not getattr(ch, "send_tool_hints", True):
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
        from navin.bus.runtime_events import RuntimeModelChanged, TurnCompleted

        async def _handler(event: Any) -> None:
            if isinstance(event, RuntimeModelChanged):
                self._refresh_status()
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
                if row.get("context_window"):
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

    def preset_details(self) -> list[dict[str, str]]:
        """Model presets as rows for the picker."""
        rows: list[dict[str, str]] = []
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
                "label": "Default (agents.defaults)",
            }
        )
        presets = getattr(loop, "model_presets", None) if loop is not None else None
        if not presets:
            presets = getattr(self.config, "model_presets", {}) or {}
        for name in sorted(presets):
            preset = presets[name]
            rows.append(
                {
                    "name": name,
                    "model": str(getattr(preset, "model", "") or ""),
                    "provider": str(getattr(preset, "provider", "") or ""),
                    "label": str(getattr(preset, "label", "") or ""),
                }
            )
        return rows

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
        """Fill missing names from the first user line and persist them."""
        loop = self.agent_loop
        if loop is None:
            return
        from navin.cognition.episodes import first_user_text
        from navin.session.webui_turns import apply_provisional_title
        from navin.tui.session_labels import session_display_title

        for row in self.session_rows():
            if session_display_title(row) != "Untitled chat":
                continue
            key = str(row.get("key") or "")
            if not key:
                continue
            session = loop.sessions.peek(key)
            if session is None:
                continue
            if apply_provisional_title(session, first_user_text(session.messages)):
                loop.sessions.save(session)

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        loop = self.agent_loop
        if loop is None:
            return []
        try:
            session = loop.sessions.get_or_create(self.session_key)
        except Exception:  # noqa: BLE001
            return []
        from navin.cognition.episodes import strip_runtime_context
        from navin.runtime_context import public_history_message

        out: list[dict[str, Any]] = []
        for msg in session.messages[-limit:]:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in {"user", "assistant"} or msg.get("injected_event"):
                continue
            # What the user typed, without the runtime-context block the loop
            # appends for the model (project map, date, channel...).
            shown = public_history_message(msg)
            content = shown.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if not isinstance(content, str):
                continue
            if role == "user":
                content = strip_runtime_context(content)
            if not content.strip():
                continue
            out.append({"role": role, "content": content.rstrip(), "metadata": msg})
        return out

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
        self.status.turn_active = False
        self._turn_started_at = None
        self._refresh_status()

    async def send(self, text: str, *, model_preset: str | None = None) -> None:
        if self.bus is None:
            raise RuntimeError("runtime not started")
        text = text.strip()
        if not text:
            return
        metadata: dict[str, Any] = {"_wants_stream": True}
        if model_preset and model_preset != "default":
            from navin.bus.events import INBOUND_META_MODEL_PRESET

            metadata[INBOUND_META_MODEL_PRESET] = model_preset
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
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.channel,
                sender_id="user",
                chat_id=self.chat_id,
                content=text,
                metadata=metadata,
            )
        )

    async def send_command(self, text: str) -> None:
        """Send a slash command that must not be counted as a user turn."""
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

    def set_model_preset(self, name: str | None) -> None:
        """Switch the runtime preset for future turns (same as ``/model``)."""
        if self.agent_loop is None:
            return
        self.agent_loop.model_preset = None if name in (None, "", "default") else name
        self._refresh_status()

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
