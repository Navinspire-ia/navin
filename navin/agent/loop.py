# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from navin.agent import context as agent_context
from navin.agent import model_presets as preset_helpers
from navin.agent.appfactory_hint import appfactory_hint_context_provider
from navin.agent.approval import (
    ApprovalBroker,
    ApprovalDecision,
    ApprovalRequest,
    bind_approval_gate,
    reset_approval_gate,
)
from navin.agent.autocompact import AutoCompact
from navin.agent.automation_turns import publish_next_deferred_turn
from navin.agent.checkpoints import (
    CheckpointStore,
    TurnRecorder,
    bind_checkpoint_recorder,
    bind_live_edit_hook,
    reset_checkpoint_recorder,
    reset_live_edit_hook,
)
from navin.agent.choice import (
    ChoiceAnswer,
    ChoiceBroker,
    ChoiceRequest,
    bind_choice_broker,
    choice_closed_fields,
    reset_choice_broker,
)
from navin.agent.context import ContextBuilder
from navin.agent.context_pack import agent_context_pack_provider
from navin.agent.cron_turns import CronTurnCoordinator
from navin.agent.history_media import replay_history_images
from navin.agent.hook import AgentHook, AgentTurnHookFactory
from navin.agent.likely_locations import likely_locations_context_provider
from navin.agent.memory import Consolidator
from navin.agent.model_runtime import ModelRuntimeResolver
from navin.agent.project_runbook import project_runbook_context_provider
from navin.agent.reply_language import reply_language_provider
from navin.agent.request_checklist import (
    explicit_asks,
    request_checklist_context_provider,
    ui_terms,
)
from navin.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentLoopGuard, AgentRunner, AgentRunSpec
from navin.agent.scope_anchor import named_targets, scope_anchor_context_provider
from navin.agent.subagent import SubagentManager
from navin.agent.token_budget import input_budget_for
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.agent.tools.file_state import FileStateStore, bind_file_states, reset_file_states
from navin.agent.tools.message import MessageTool
from navin.agent.tools.registry import ToolRegistry
from navin.agent.tools.self import MyTool
from navin.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from navin.agent.vision_guard import guard_vision_media
from navin.agent.voice_mode import voice_mode_context_provider
from navin.board.context import board_context_provider
from navin.bus.events import INBOUND_META_MODEL_PRESET, InboundMessage, OutboundMessage
from navin.bus.outbound_events import (
    ApprovalClosedEvent,
    ApprovalRequestedEvent,
    ChoiceClosedEvent,
    ChoiceRequestedEvent,
    NotificationEvent,
    RetryWaitEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    outbound_message_for_event,
)
from navin.bus.progress import build_bus_progress_callback
from navin.bus.queue import MessageBus
from navin.bus.runtime_events import (
    CheckpointSaved,
    ContextCompacted,
    GoalStateChanged,
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeEventPublisher,
    ensure_runtime_event_publisher,
)
from navin.command import CommandContext, CommandRouter, register_builtin_commands
from navin.config.schema import AgentDefaults, ModelPresetConfig, ToolResultClearing
from navin.continuity.context import continuity_context_provider
from navin.cron.session_turns import cron_job_id
from navin.cron.spend import crediting_job
from navin.providers.base import LLMProvider
from navin.providers.factory import ProviderSnapshot
from navin.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    RuntimeContextProvider,
    append_runtime_context,
    resolve_runtime_context,
)
from navin.security.workspace_access import (
    WorkspaceScopeResolver,
    bind_live_restrict_to_workspace,
    bind_workspace_scope,
    reset_live_restrict_to_workspace,
    reset_workspace_scope,
)
from navin.session import turn_continuation
from navin.session.automation_turns import automation_history_overrides
from navin.session.goal_state import (
    GOAL_STATE_KEY,
    goal_state_raw,
    goal_state_runtime_lines,
    parse_goal_state,
    runner_wall_llm_timeout_s,
    sustained_goal_active,
)
from navin.session.history_visibility import HIDDEN_HISTORY_META
from navin.session.keys import UNIFIED_SESSION_KEY
from navin.session.manager import (
    Session,
    SessionManager,
    replay_max_messages_for_context,
)
from navin.session.turn_recovery import (
    MAX_RECOVERY_AGE_S,
    PENDING_USER_TURN_KEY,
    RECOVERY_ID_META,
    RECOVERY_KEY,
    TurnRecovery,
    portable_request,
    recovery_pending,
)
from navin.triggers.local_turns import LocalTriggerTurnCoordinator
from navin.utils.audio_transcripts import (
    append_video_soundtracks,
    expand_audio_attachments,
)
from navin.utils.document import extract_documents, reference_non_image_attachments
from navin.utils.document_templates import (
    document_template_context_provider,
    document_toolchain_context_provider,
)
from navin.utils.file_mentions import file_mention_context_provider
from navin.utils.git_state import git_state_context_provider
from navin.utils.helpers import image_placeholder_text
from navin.utils.helpers import truncate_text as truncate_text_fn
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.media_templates import media_template_context_provider
from navin.utils.media_urls import expand_media_urls
from navin.utils.runtime import (
    is_go_ahead,
    session_empty_final_response_message,
    started_background_work,
)
from navin.utils.video_frames import expand_video_attachments, is_video_path

if TYPE_CHECKING:
    from navin.agent.tools.mcp import MCPConnection
    from navin.config.schema import (
        ChannelsConfig,
        ProviderConfig,
        ToolsConfig,
    )
    from navin.cron.service import CronService

# Session-metadata key remembering the chat's explicit model choice so it
# survives client reloads and gateway restarts (Cursor-style sticky pin).
# Size-gated tools a conversation already lifted (see _denied_tools), kept
# so the tool list stays byte-stable from one message to the next.
_STICKY_LIFTED_SESSIONS = 512
_STICKY_LIFTED_TOOLS: OrderedDict[str, frozenset[str]] = OrderedDict()

# Seconds between idle-session archival checks (the TTL itself is in minutes).
_IDLE_SESSION_CHECK_S = 60.0

SESSION_MODEL_PIN_KEY = "_last_model_preset"


def _persist_turn_snapshots(
    checkpoints: Any,
    pending_review: Any,
    session_key: str,
    auto_checkpoint_name: str | None,
    files: dict[str, bytes | None],
    skipped: list[str] | None,
) -> None:
    """End of turn: fold the pre-edit snapshots into the checkpoint and the review.

    Blocking (two fsynced JSON writes): the loop reaches it through
    ``asyncio.to_thread`` only. Each store stays best-effort on its own.
    """
    if (files or skipped) and auto_checkpoint_name:
        with suppress(OSError, ValueError):
            checkpoints.attach_files(
                session_key,
                auto_checkpoint_name,
                files,
                # Exclusion manifest: restore_files reports these as
                # unrestorable instead of silently partial.
                skipped=skipped,
            )
    if files:
        # Pending review: keep the pre-edit baseline so the WebUI can
        # highlight agent changes and accept/reject them.
        with suppress(OSError, ValueError):
            pending_review.merge_turn(session_key, files)
# Sentinel a client sends as ``model_preset`` to say "no pin: let task
# routing choose". It clears the sticky session pin instead of setting one.
AUTO_MODEL_PIN = "auto"
# What the *user* asked for on this message ("" when nothing), captured
# before ``stamp_inbound_runtime`` overwrites ``model_preset`` with the model
# that actually ran. Only this value may become the chat's sticky pin: a
# task-routed model must never be remembered as if the user had picked it.
INBOUND_META_USER_MODEL_PIN = "user_model_pin"


def should_inject_into_active_turn(commands: CommandRouter, raw: str) -> bool:
    """Route a message that arrives while its session already runs a turn.

    True: enqueue for mid-turn injection (plain text and agent-turn commands
    like the composer's mode-routed "/forge salut"). Injection never
    interrupts the running tasks - only /stop (priority tier) does.

    False: dispatch inline as a side-channel command (/status, /model, ...).
    Inline dispatch of an agent-turn command would silently DROP the message,
    because those handlers rewrite the message and return None for the normal
    dispatch path to run the turn - a path that never runs inline.
    """
    from navin.command.builtin import is_agent_turn_command

    if commands.is_dispatchable_command(raw) and not is_agent_turn_command(raw):
        return False
    return True


async def enqueue_pending_followup(
    queue: asyncio.Queue[InboundMessage],
    message: InboundMessage,
    *,
    session_key: str,
) -> None:
    """Preserve FIFO and one session owner when the injection queue is full.

    Starting a second dispatch after ``QueueFull`` changed a follow-up from a
    mid-turn injection into a later turn and made its ordering timing-dependent.
    Waiting for one bounded slot applies backpressure without losing the message
    or creating a competing owner.
    """
    if queue.full():
        logger.warning(
            "Pending queue full for session {}; applying backpressure",
            session_key,
        )
    await queue.put(message)


class TurnState(Enum):
    RESTORE = auto()
    COMPACT = auto()
    COMMAND = auto()
    BUILD = auto()
    RUN = auto()
    SAVE = auto()
    RESPOND = auto()
    DONE = auto()


@dataclass
class StateTraceEntry:
    state: TurnState
    started_at: float
    duration_ms: float
    event: str
    error: str | None = None


async def announce_runtime_context(
    on_progress: Callable[..., Awaitable[None]] | None,
    blocks: list[RuntimeContextBlock] | None,
    turn_id: str | None,
) -> None:
    """Show, as one activity row, the skills or MCP servers the turn starts with.

    Matching skills reach the model silently in the request; without a trace
    the user reasonably concluded they were never used. The project map is
    joined on every message, so announcing it each time was noise: it is no
    longer shown on its own."""
    if on_progress is None or not blocks:
        return
    from navin.agent.tools.skill_catalog import SKILL_SUGGESTION_HEADER
    from navin.utils.progress_events import invoke_on_progress, on_progress_accepts_tool_events

    parts: list[str] = []
    for block in blocks:
        if block.source == "skills":
            first = block.content.splitlines()[0] if block.content else ""
            names = first.removeprefix(SKILL_SUGGESTION_HEADER).strip().rstrip(".")
            if names:
                parts.append(f"skills: {names}")
        elif block.source.startswith("mcp") and block.content.strip():
            parts.append(f"MCP: {block.content.splitlines()[0].strip()}")
    if not parts or not on_progress_accepts_tool_events(on_progress):
        return
    summary = "Context loaded - " + "; ".join(parts)
    with suppress(Exception):
        await invoke_on_progress(
            on_progress, "", tool_hint=False,
            tool_events=[{
                "version": 1, "phase": "end", "call_id": f"context-{turn_id or 'turn'}",
                "name": "context", "arguments": {"label": "; ".join(parts)}, "result": summary,
                "error": None, "files": [], "embeds": [],
            }],
        )


# Stop reasons whose final text the runner never streams (it tail-streams
# only "completed" / "no_progress" answers).
_UNSTREAMED_FINAL_STOP_REASONS = frozenset({
    "error",
    "tool_error",
    "validation_failed",
    "empty_final_response",
    "max_iterations",
})


@dataclass
class TurnContext:
    msg: InboundMessage
    session_key: str
    state: TurnState
    turn_id: str
    runtime: LLMRuntime
    original_user_text: str | None = None
    session: Session | None = None

    history: list[dict[str, Any]] = field(default_factory=list)
    initial_messages: list[dict[str, Any]] = field(default_factory=list)
    request_context: RequestContext | None = None
    runtime_context_blocks: list[RuntimeContextBlock] = field(default_factory=list)

    final_content: str | None = None
    tools_used: list[str] = field(default_factory=list)
    all_messages: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    had_injections: bool = False

    user_persisted_early: bool = False
    save_skip: int = 0

    outbound: OutboundMessage | None = None
    suppress_response: bool = False
    # The final text is a notice the loop wrote itself (never streamed).
    final_is_notice: bool = False

    on_progress: Callable[..., Awaitable[None]] | None = None
    on_stream: Callable[[str], Awaitable[None]] | None = None
    on_stream_end: Callable[..., Awaitable[None]] | None = None
    on_retry_wait: Callable[[str], Awaitable[None]] | None = None

    pending_queue: asyncio.Queue | None = None
    pending_summary: str | None = None

    ephemeral: bool = False
    run_extra_hooks_for_ephemeral: bool = False
    hooks: list[AgentHook] = field(default_factory=list)
    hook_factories: list[AgentTurnHookFactory] = field(default_factory=list)
    turn_scopes: list[AbstractContextManager[Any]] = field(default_factory=list)
    tools: ToolRegistry | None = None

    turn_wall_started_at: float = field(default_factory=time.time)
    visible_run_started_at: float | None = None
    turn_latency_ms: int | None = None

    trace: list[StateTraceEntry] = field(default_factory=list)


def stamp_inbound_runtime(
    owner: Any,
    msg: InboundMessage,
    runtime: Any,
    *,
    role: str | None,
) -> None:
    """Copy the chosen model + task onto the inbound turn.

    Progress and reasoning frames inherit ``msg.metadata``, so the thinking
    strip shows the same identity the answer will persist.
    """
    meta = dict(msg.metadata or {})
    if INBOUND_META_USER_MODEL_PIN not in meta:
        asked = meta.get(INBOUND_META_MODEL_PRESET)
        meta[INBOUND_META_USER_MODEL_PIN] = (
            asked.strip() if isinstance(asked, str) else ""
        )
    if isinstance(role, str) and role.strip():
        meta["task_role"] = role.strip()
        meta["model_route_role"] = role.strip()
    model = str(getattr(runtime, "model", "") or "").strip()
    if model:
        meta["model"] = model
        meta["model_name"] = model
    preset = getattr(runtime, "model_preset", None)
    if isinstance(preset, str) and preset.strip():
        meta["model_preset"] = preset.strip()
        presets_map = getattr(owner, "model_presets", None)
        configured = (
            presets_map.get(preset.strip())
            if isinstance(presets_map, Mapping)
            else None
        )
        label = getattr(configured, "label", None) if configured is not None else None
        if isinstance(label, str) and label.strip():
            meta["model_label"] = label.strip()
    msg.metadata = meta



def _waited_ms(route: dict[str, Any]) -> int | None:
    """Milliseconds a choice or approval card stayed open (None if unknown)."""
    opened = route.get("opened_at")
    if not isinstance(opened, (int, float)) or isinstance(opened, bool):
        return None
    return max(0, int((time.monotonic() - opened) * 1000))


#: Imported lazily by the first turn: ~880 modules (the provider SDK, its
#: HTTP stack, prompt and routing helpers) and the tokenizer, ~1.5-2 s that
#: the first message of every CLI or desktop session waited for before its
#: request left. Loaded while the user is still typing instead.
_FIRST_TURN_MODULES: tuple[str, ...] = (
    "openai",
    "anthropic",
    "navin.agent.model_routes",
    "navin.agent.adaptive_reasoning",
    "navin.agent.user_hooks",
    "navin.agent.hookify",
    "navin.agent.security_guidance",
    "navin.agent.stop_checks",
    "navin.agent.nested_instructions",
    "navin.agent.project_rules",
    "navin.agent.custom_commands",
    "navin.agent.marketplace",
    "navin.agent.media_intent",
    "navin.agent.desk_intent",
    "navin.agent.tool_demand",
    "navin.command.modules",
    "navin.plugins.manager",
    "navin.webui.checkpoints_api",
    "navin.webui.metagraph",
    "navin.session.context_usage_meta",
)


def _prewarm_first_turn() -> None:
    """Import what the first turn needs, off the turn path; failures are moot."""
    import importlib

    for name in _FIRST_TURN_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - the real import reports it in context
            pass
    try:
        from navin.utils.helpers import _get_token_encoding

        _get_token_encoding()
    except Exception:  # noqa: BLE001 - token counting falls back on its own
        pass


def _consume_task_result(task: asyncio.Task) -> None:
    """Retrieve a finished task's outcome so asyncio never logs it as unhandled."""
    if task.done() and not task.cancelled():
        with suppress(Exception):
            task.exception()


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    @property
    def current_iteration(self) -> int:
        return self._current_iteration

    @property
    def tool_names(self) -> list[str]:
        return self.tools.tool_names

    @property
    def provider(self) -> LLMProvider:
        """Provider selected for future turn admissions."""
        return self.runtime_resolver.runtime.provider

    @property
    def model(self) -> str:
        """Model selected for future turn admissions."""
        return self.runtime_resolver.runtime.model

    @property
    def context_window_tokens(self) -> int:
        """Context limit selected for future turn admissions."""
        return self.runtime_resolver.runtime.context_window_tokens

    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        """Configured model presets exposed for selection and display."""
        return self.runtime_resolver.model_presets

    @property
    def model_preset(self) -> str | None:
        return self.runtime_resolver.model_preset

    @model_preset.setter
    def model_preset(self, name: str | None) -> None:
        self.set_model_preset(name)

    def llm_runtime(self) -> LLMRuntime:
        """Resolve the immutable default used to admit the next turn."""
        previous = self.runtime_resolver.runtime
        try:
            runtime = self.runtime_resolver.current(refresh=True)
        except Exception:
            logger.exception("Failed to refresh model runtime")
            return previous
        if (
            runtime.model != previous.model
            or runtime.model_preset != previous.model_preset
            or runtime.snapshot_signature != previous.snapshot_signature
        ):
            self._publish_runtime_selection(runtime)
        return runtime

    def runtime_for_inbound(self, msg: InboundMessage) -> LLMRuntime:
        """The default runtime, or the per-turn preset for this message.

        Resolution order:
        1. Explicit ``model_preset`` in message metadata (Cursor-style pin).
        2. Computer route - the user asks to drive the desktop and the
           ``computer`` tool is on → grounding model mapped to ``computer``.
        3. Vision route - image / video attachments → multimodal analysis model
           (typically Nemotron Nano Omni free) when Settings maps ``vision``.
        4. Workflow auto-route - a leading ``/forge``, ``/blueprint``, studio
           command, etc. mapped via Settings → Models → Task routing.
        4. Composer-mode route - ``composer_mode`` metadata alone
           (plan/review/security/debug from non-WebUI channels) mapped onto
           the same task-routing roles.
        5. Code shell ``dev`` route when no slash/pin.
        6. Loop default.

        Unknown presets fall back to the default rather than failing the turn.
        """
        computer_config_loader = getattr(self, "_computer_config_loader", None)
        if computer_config_loader is not None:
            from navin.agent.tools.computer import refresh_computer_registration

            refresh_computer_registration(self.tools, computer_config_loader, bus=self.bus)

        from navin.agent.model_routes import (
            composer_mode_role,
            infer_task_role,
            product_module_role,
            resolve_computer_route,
            resolve_model_route,
            resolve_route_for_message,
            resolve_vision_route,
            workflow_role_for_content,
        )

        default = self.llm_runtime()
        known = set(self.model_presets)
        known.add("default")
        requested = None
        route_role: str | None = None
        # True when the model was picked by the user (message pin or the
        # chat's sticky pin), as opposed to a system route. A pin is honoured
        # only while that model is still on the subscription allowlist.
        user_pinned = False
        # "auto": the user explicitly handed the choice back to task routing,
        # so the sticky chat pin must not resurrect a previous pick.
        user_auto = False
        meta = (msg.metadata or {}).get(INBOUND_META_MODEL_PRESET)
        if isinstance(meta, str) and meta.strip():
            if meta.strip().lower() == AUTO_MODEL_PIN:
                user_auto = True
            else:
                requested = meta.strip()
                user_pinned = True
        # Desktop control needs a model that can place a click on a screenshot;
        # Settings → Task routing → "computer" names it. Checked before the
        # vision route (a grounding model reads images too, the reverse is not
        # true) and only when the tool is available in this session.
        tools = getattr(self, "tools", None)
        if not requested and tools is not None and "computer" in tools:
            from navin.agent.tools.computer import computer_session_active

            requested = resolve_computer_route(
                msg.content, known_presets=known,
                active_session=computer_session_active(msg.session_key),
            )
            if requested:
                route_role = "computer"
        if not requested:
            media = [
                p for p in (msg.media or []) if isinstance(p, str) and p.strip()
            ]
            requested = resolve_vision_route(
                media, text=msg.content, known_presets=known
            )
            if requested:
                route_role = "vision"
        # Sticky chat pin: the model the user explicitly picked for this chat
        # survives client reloads and gateway restarts. A message that lost
        # its per-message pin (replay, non-UI channel, internal follow-up)
        # must not silently fall back to the global default model.
        if not requested and not user_auto:
            requested = self._session_pinned_preset(msg)
            user_pinned = requested is not None
        if not requested:
            requested = resolve_route_for_message(
                msg.content,
                known_presets=known,
            )
            if requested:
                route_role = workflow_role_for_content(msg.content)
        # Composer mode without a slash prefix (Telegram, CLI, API): the WebUI
        # rewrites plan/review/security/debug into /blueprint etc. itself, but
        # other channels send composer_mode metadata alone. Map it onto the
        # same task-routing role so those turns reach the configured model.
        if not requested:
            mode_role = composer_mode_role(
                (msg.metadata or {}).get("composer_mode")
            )
            if mode_role:
                requested = resolve_model_route(mode_role, known_presets=known)
                if requested:
                    route_role = mode_role
        # Studio shell without a slash/pin: Career → search, Code → dev, etc.
        # Infer-from-text would send "trouve une mission Data Engineer" to `dev`.
        if not requested:
            module_role = product_module_role(
                (msg.metadata or {}).get("product_module")
            )
            if module_role:
                requested = resolve_model_route(module_role, known_presets=known)
                if requested:
                    route_role = module_role
        if not requested:
            inferred = infer_task_role(msg.content)
            if inferred:
                requested = resolve_model_route(inferred, known_presets=known)
                if requested:
                    route_role = inferred
        # Media presets (image / video / music / TTS / STT) are specialty
        # tools: they can never run a chat turn, whatever pinned them.
        if requested and requested != "default":
            presets_map = self.model_presets
            preset_cfg = (
                presets_map.get(requested)
                if isinstance(presets_map, Mapping)
                else None
            )
            modality = (
                (getattr(preset_cfg, "modality", None) or "text").strip().lower()
            )
            if preset_cfg is not None and modality != "text":
                logger.warning(
                    "Preset %r is a %s model and cannot run a chat turn; "
                    "using the default runtime",
                    requested,
                    modality,
                )
                requested = None
                user_pinned = False
        if not requested:
            runtime = default
        elif requested == (default.model_preset or "default"):
            runtime = default
        else:
            try:
                runtime = self.runtime_resolver.resolve_preset(requested)
            except Exception:
                logger.warning(
                    "Requested model preset %r is unknown; using the default runtime",
                    requested,
                )
                runtime = default
                # The pin could not be honoured; what runs now is the default
                # runtime, which the soft budget may clamp normally.
                user_pinned = False
        # Soft budget (managed key only): downgrade tiers + shorten outputs
        # when monthly spend hits reduced / economy / exhausted. A swap is
        # published so the picker badge and a toast follow the live model.
        try:
            from navin.config.loader import load_config_cached
            from navin.usage_mode import (
                apply_usage_mode_to_runtime,
                budget_used_percent,
                current_usage_mode,
            )

            try:
                from navin.license_client import uses_managed_key
                from navin.optional_live import live_modules_available

                if not live_modules_available():
                    def uses_managed_key(_cfg: object) -> bool:
                        return False
            except ImportError:
                def uses_managed_key(_cfg: object) -> bool:
                    return False

            # Shared per-file-version config, read-only below (every inbound).
            cfg = load_config_cached()
            before_model = str(getattr(runtime, "model", "") or "")
            runtime = apply_usage_mode_to_runtime(
                runtime,
                cfg,
                resolve_preset=self.runtime_resolver.resolve_preset,
                known_presets=self.runtime_resolver.model_presets,
                user_pinned=user_pinned,
            )
            after_model = str(getattr(runtime, "model", "") or "")
            after_preset = getattr(runtime, "model_preset", None)
            if (
                after_model
                and after_model != before_model
                and uses_managed_key(cfg)
            ):
                percent = budget_used_percent(cfg, current_usage_mode(cfg))
                if isinstance(after_preset, str) and after_preset.strip():
                    try:
                        key = self._effective_session_key(msg)
                        if key:
                            session = self.sessions.get_or_create(key)
                            session.metadata[SESSION_MODEL_PIN_KEY] = after_preset.strip()
                    except Exception:
                        logger.debug("Soft budget session pin update skipped", exc_info=True)
                self._publish_runtime_selection(
                    runtime,
                    reason="budget",
                    previous_model=before_model,
                    used_percent=percent,
                )
        except Exception:
            logger.warning("Soft budget runtime clamp skipped", exc_info=True)
        display_role = route_role
        if not display_role:
            display_role = infer_task_role(msg.content)
        self._active_task_role = display_role
        stamp_inbound_runtime(self, msg, runtime, role=display_role)
        if display_role:
            # ``route:`` means Task routing swapped the model the user sees in
            # the composer for another one; the UI explains that swap once.
            # ``task:`` only labels the turn (pinned or default model).
            routed = (
                route_role is not None
                and not user_pinned
                and runtime is not default
            )
            try:
                self._runtime_events().runtime_model_changed(
                    str(getattr(runtime, "model", "") or ""),
                    getattr(runtime, "model_preset", None),
                    reason=f"{'route' if routed else 'task'}:{display_role}",
                    previous_model=(
                        str(getattr(default, "model", "") or "") if routed else None
                    ),
                )
            except Exception:
                logger.debug("Task-route model announce skipped", exc_info=True)
        return runtime

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    _RUNTIME_CHECKPOINT_MIN_SAVE_INTERVAL_S = 2.0
    _PENDING_USER_TURN_KEY = PENDING_USER_TURN_KEY
    _SESSION_MODEL_PIN_KEY = SESSION_MODEL_PIN_KEY

    def _session_pinned_preset(self, msg: InboundMessage) -> str | None:
        """The model the user last picked for this chat, or None."""
        try:
            key = self._effective_session_key(msg)
            if not key:
                return None
            value = self.sessions.get_or_create(key).metadata.get(
                SESSION_MODEL_PIN_KEY
            )
        except Exception:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _persist_model_pin(self, session: Session, msg: InboundMessage) -> None:
        """Remember this chat's explicit model choice across restarts."""
        meta = msg.metadata or {}
        if turn_continuation.internal_continuation_inbound(meta):
            return
        # After the runtime stamp, ``model_preset`` names the model that ran
        # (possibly a task route); the user's own request lives in the
        # captured copy. Before the stamp, ``model_preset`` is that request.
        pin = (
            meta[INBOUND_META_USER_MODEL_PIN]
            if INBOUND_META_USER_MODEL_PIN in meta
            else meta.get(INBOUND_META_MODEL_PRESET)
        )
        if not isinstance(pin, str) or not pin.strip():
            return
        pin = pin.strip()
        if pin.lower() == AUTO_MODEL_PIN:
            session.metadata.pop(SESSION_MODEL_PIN_KEY, None)
            return
        if session.metadata.get(SESSION_MODEL_PIN_KEY) != pin:
            session.metadata[SESSION_MODEL_PIN_KEY] = pin

    # Event-driven state transition table.
    # Handlers return an event string; the driver looks up the next state here.
    _TRANSITIONS: dict[tuple[TurnState, str], TurnState] = {
        (TurnState.RESTORE, "ok"): TurnState.COMPACT,
        (TurnState.COMPACT, "ok"): TurnState.COMMAND,
        (TurnState.COMMAND, "dispatch"): TurnState.BUILD,
        (TurnState.COMMAND, "shortcut"): TurnState.DONE,
        (TurnState.BUILD, "ok"): TurnState.RUN,
        (TurnState.RUN, "ok"): TurnState.SAVE,
        (TurnState.SAVE, "ok"): TurnState.RESPOND,
        (TurnState.RESPOND, "ok"): TurnState.DONE,
    }

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        context_window_tokens: int | None = None,
        context_block_limit: int | None = None,
        max_tool_result_chars: int | None = None,
        tool_result_clearing: ToolResultClearing | None = None,
        followup_reasoning_effort: str | None = None,
        fail_on_tool_error: bool | None = None,
        provider_retry_mode: str = "standard",
        tool_hint_max_length: int | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        timezone: str | None = None,
        session_ttl_minutes: int = 0,
        consolidation_ratio: float = 0.5,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        unified_session: bool = False,
        disabled_skills: list[str] | None = None,
        trust_workspace_harness_skills: bool = True,
        tools_config: ToolsConfig | None = None,
        image_generation_provider_config: ProviderConfig | None = None,
        image_generation_provider_configs: dict[str, ProviderConfig] | None = None,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None,
        provider_signature: tuple[object, ...] | None = None,
        model_presets: dict[str, ModelPresetConfig] | None = None,
        model_preset: str | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        model_presets_loader: Callable[[], Mapping[str, ModelPresetConfig]] | None = None,
        runtime_events: RuntimeEventBus | None = None,
        runtime_model_publisher: Callable[[str, str | None], None] | None = None,
        restart_mode: str = "auto",
        local_trigger_store: Any | None = None,
    ):
        from navin.config.schema import ToolsConfig

        _tc = tools_config or ToolsConfig()
        defaults = AgentDefaults()
        self.bus = bus
        self.runtime_events = runtime_events or RuntimeEventBus()
        self.runtime_event_publisher = RuntimeEventPublisher(self.runtime_events)
        self.channels_config = channels_config
        self.restart_mode = restart_mode
        self._runtime_model_publisher = runtime_model_publisher
        self.workspace = workspace
        self.checkpoints = CheckpointStore(workspace)
        from navin.agent.review import PendingReviewStore

        self.pending_review = PendingReviewStore(workspace)
        self.approvals = ApprovalBroker(
            publish=self._publish_approval_request,
            close=self._publish_approval_closed,
            config=_tc.approvals,
        )
        self.choices = ChoiceBroker(
            publish=self._publish_choice_request,
            close=self._publish_choice_closed,
        )
        from navin.agent.credentials import CredentialBroker

        # Secure login / git-account cards (values go UI -> vault, never here).
        self.credentials = CredentialBroker.for_bus(self.bus)
        initial_model = model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        initial_context_window = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.effective_context_window_tokens
        )
        configured_presets = model_presets or {}
        self.runtime_resolver = ModelRuntimeResolver(
            LLMRuntime.capture(
                provider,
                initial_model,
                context_window_tokens=initial_context_window,
                snapshot_signature=provider_signature,
            ),
            model_presets=configured_presets,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
            model_presets_loader=model_presets_loader,
        )
        self.context_block_limit = context_block_limit
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        self.followup_reasoning_effort = (
            followup_reasoning_effort
            if followup_reasoning_effort is not None
            else defaults.followup_reasoning_effort
        )
        self.tool_result_clearing = (
            tool_result_clearing
            if tool_result_clearing is not None
            else defaults.tool_result_clearing
        )
        self.provider_retry_mode = provider_retry_mode
        self.tool_hint_max_length = (
            tool_hint_max_length if tool_hint_max_length is not None
            else defaults.tool_hint_max_length
        )
        self.tools_config = _tc
        self.web_config = _tc.web
        self.exec_config = _tc.exec
        self._image_generation_provider_configs = dict(image_generation_provider_configs or {})
        if (
            image_generation_provider_config is not None
            and "openrouter" not in self._image_generation_provider_configs
        ):
            self._image_generation_provider_configs["openrouter"] = image_generation_provider_config
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.restrict_to_workspace = restrict_to_workspace
        self.workspace_scopes = WorkspaceScopeResolver(
            default_workspace=workspace,
            default_restrict_to_workspace=restrict_to_workspace,
        )
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []
        self._hook_factories: list[AgentTurnHookFactory] = hook_factories or []

        self.context = ContextBuilder(
            workspace,
            timezone=timezone,
            disabled_skills=disabled_skills,
            trust_workspace_harness_skills=trust_workspace_harness_skills,
        )
        self.sessions = session_manager or SessionManager(workspace)
        if self._hook_factories:
            from navin.improvement.skills import resume_learning_in_background
            resume_learning_in_background(workspace, self.sessions)
        self.turn_recovery = TurnRecovery(self.sessions)
        self.tools = ToolRegistry()
        # One file-read/write tracker per logical session. The tool registry is
        # shared by this loop, so tools resolve the active state via contextvars.
        self._file_state_store = FileStateStore()
        self.runner = AgentRunner()
        self.subagents = SubagentManager(
            workspace=workspace,
            bus=bus,
            tools_config=_tc,
            max_tool_result_chars=self.max_tool_result_chars,
            tool_result_clearing=self.tool_result_clearing,
            restrict_to_workspace=restrict_to_workspace,
            image_generation_provider_configs=self._image_generation_provider_configs,
            disabled_skills=disabled_skills,
            trust_workspace_harness_skills=trust_workspace_harness_skills,
            max_iterations=self.max_iterations,
            max_concurrent_subagents=max_concurrent_subagents,
            max_total_concurrent_subagents=max_concurrent_subagents,
            fail_on_tool_error=fail_on_tool_error,
            llm_wall_timeout_for_session=lambda sk: runner_wall_llm_timeout_s(self.sessions, sk),
            record_edits=self.pending_review.merge_turn,
            usage_hooks=[
                hook for hook in self._extra_hooks if hook.accounts_for_usage
            ],
            # A subagent spawned outside a turn (Multitask prompt, resume after
            # a restart) still asks the user through this runtime's cards.
            approval_gate=self.approvals,
            choice_broker=self.choices,
        )
        self._unified_session = unified_session
        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stacks: dict[str, MCPConnection] = {}
        self._mcp_connecting = False
        # Built-in providers first: document-template attachments annotate
        # the turn so the agent uses the template picked in the WebUI.
        self._runtime_context_providers: list[RuntimeContextProvider] = [
            document_template_context_provider,
            document_toolchain_context_provider,
            media_template_context_provider,
            file_mention_context_provider,
            scope_anchor_context_provider,
            request_checklist_context_provider,
            likely_locations_context_provider,
            git_state_context_provider,
            agent_context_pack_provider,
            board_context_provider,
            continuity_context_provider,
            project_runbook_context_provider,
            # One nudge toward native apps, once the web project stands.
            appfactory_hint_context_provider,
            voice_mode_context_provider,
            # The chat's language lives in its session: a restart keeps it.
            reply_language_provider(lambda: self.sessions),
        ]
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        # Gateway only (set by the gateway command): puts the long-lived heap
        # away at idle minutes so the automatic full collections during turns
        # stay short. The TUI has its own collector policy (navin.tui.frames).
        self.heap_keeper: Any = None
        # /stop wall-clock per session: internal continuation slices belonging
        # to a run started before this instant are stale and must be dropped,
        # even when they were already republished to the bus at a slice
        # boundary (the window where /stop finds no active task to cancel).
        self._stop_requested_at: dict[str, float] = {}
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # session_key -> (message preview, monotonic start) of the turn that
        # currently holds the session lock, named in the slow-lock notice.
        self._session_lock_holders: dict[str, tuple[str, float]] = {}
        self._runtime_checkpoint_saved_at: dict[str, float] = {}
        # Checkpoint writes running in worker threads (kept referenced).
        self._background_saves: set[asyncio.Task[Any]] = set()
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        self._loop_guards: dict[str, AgentLoopGuard] = {}
        self._deferred_automation_turns: dict[str, list[InboundMessage]] = {}
        self._cron_turns = CronTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._local_trigger_turns = LocalTriggerTurnCoordinator(
            publish_inbound=self.bus.publish_inbound,
            dispatch=self._dispatch,
            is_running=lambda: self._running,
            deferred_queues=self._deferred_automation_turns,
        )
        self._automation_turn_coordinators = (
            ("cron", self._cron_turns),
            ("local trigger", self._local_trigger_turns),
        )
        # NAVIN_MAX_CONCURRENT_REQUESTS: <=0 means unlimited. Default tracks
        # the plan/config concurrent-agents cap so a wave of parallel parent
        # turns is not gated below the subagent spawn budget.
        _env = os.environ.get("NAVIN_MAX_CONCURRENT_REQUESTS")
        if _env is not None:
            _max = int(_env)
        else:
            _max = (
                max_concurrent_subagents
                if max_concurrent_subagents is not None
                else defaults.max_concurrent_subagents
            )
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.consolidator = Consolidator(
            store=self.context.memory,
            sessions=self.sessions,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            consolidation_ratio=consolidation_ratio,
            unified_session=unified_session,
            on_compacted=self._publish_context_compacted,
        )
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        if model_preset:
            self.set_model_preset(model_preset, publish_update=False)
        self._register_default_tools(provider_snapshot_loader=provider_snapshot_loader)
        self._runtime_vars: dict[str, Any] = {}
        self._current_iteration: int = 0
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    @classmethod
    def from_config(
        cls,
        config: Any,
        bus: MessageBus | None = None,
        **extra: Any,
    ) -> AgentLoop:
        """Create an AgentLoop from config with the common parameter set.

        Extra keyword arguments are forwarded to ``AgentLoop.__init__``,
        allowing callers to override or extend the standard config-derived
        parameters (e.g. ``cron_service``, ``session_manager``).
        """
        from navin.providers.factory import make_provider

        if bus is None:
            bus = MessageBus()
        from navin.plan_limits import effective_concurrent_agents, effective_steps_per_task

        defaults = config.agents.defaults
        provider = extra.pop("provider", None)
        if provider is None:
            try:
                provider = make_provider(config)
            except ValueError as exc:
                from navin.providers.unconfigured import UnconfiguredProvider

                logger.warning("Provider not ready at boot: {}", exc)
                provider = UnconfiguredProvider(reason=str(exc))
        resolved = config.resolve_preset()
        model = extra.pop("model", None) or resolved.model
        context_window_tokens = extra.pop("context_window_tokens", None) or resolved.effective_context_window_tokens
        provider_snapshot_loader = extra.pop("provider_snapshot_loader", None)
        preset_snapshot_loader = extra.pop("preset_snapshot_loader", None) or preset_helpers.make_preset_snapshot_loader(
            config,
            provider_snapshot_loader,
        )
        model_presets_loader = extra.pop("model_presets_loader", None)
        if model_presets_loader is None and provider_snapshot_loader is not None:
            # A disk-refreshing snapshot loader means the config can change at
            # runtime (gateway + WebUI settings); reload presets the same way.
            # Cached per file version: this runs before every turn.
            from navin.config.loader import load_config_cached as _load_config

            def model_presets_loader() -> dict[str, ModelPresetConfig]:
                return preset_helpers.configured_model_presets(_load_config())
        # Zero-setup MCP presets (e.g. debugmcp) must be in config before the
        # agent connects MCP tools - install them from the catalog at startup.
        try:
            from navin.webui.mcp_presets_api import ensure_auto_enabled_mcp_presets

            added_mcp = ensure_auto_enabled_mcp_presets(config)
            if added_mcp:
                try:
                    from navin.config.loader import get_config_path, load_config, save_config

                    path = get_config_path()
                    if path.exists():
                        live = load_config(path)
                        if ensure_auto_enabled_mcp_presets(live):
                            save_config(live, path)
                    else:
                        save_config(config, path)
                except Exception:
                    pass
                from loguru import logger as _logger

                _logger.info("Auto-enabled MCP presets: {}", added_mcp)
        except Exception:
            pass
        max_iterations = effective_steps_per_task(config, defaults.max_tool_iterations)
        max_concurrent_subagents = effective_concurrent_agents(
            config, defaults.max_concurrent_subagents
        )
        if not config.tools.visual_qa.preset:
            config.tools.visual_qa.preset = str(
                (config.model_routes or {}).get("vision") or ""
            )
        return cls(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=model,
            max_iterations=max_iterations,
            max_concurrent_subagents=max_concurrent_subagents,
            context_window_tokens=context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            tool_result_clearing=defaults.tool_result_clearing,
            followup_reasoning_effort=defaults.followup_reasoning_effort,
            fail_on_tool_error=defaults.fail_on_tool_error,
            provider_retry_mode=defaults.provider_retry_mode,
            tool_hint_max_length=defaults.tool_hint_max_length,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            timezone=defaults.timezone,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            trust_workspace_harness_skills=defaults.trust_workspace_harness_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            consolidation_ratio=defaults.consolidation_ratio,
            tools_config=config.tools,
            model_presets=preset_helpers.configured_model_presets(config),
            model_preset=defaults.model_preset,
            restart_mode=config.gateway.restart_mode,
            provider_snapshot_loader=provider_snapshot_loader,
            preset_snapshot_loader=preset_snapshot_loader,
            model_presets_loader=model_presets_loader,
            **extra,
        )

    def _sync_subagent_runtime_limits(self) -> None:
        """Keep subagent runtime limits aligned with mutable loop settings."""
        self.subagents.max_iterations = self.max_iterations

    def _publish_runtime_selection(
        self,
        runtime: LLMRuntime,
        *,
        publish_update: bool = True,
        reason: str | None = None,
        previous_model: str | None = None,
        used_percent: int | None = None,
    ) -> None:
        if not publish_update:
            return
        if self._runtime_model_publisher is not None:
            self._runtime_model_publisher(runtime.model, runtime.model_preset)
        self._runtime_events().runtime_model_changed(
            runtime.model,
            runtime.model_preset,
            reason=reason,
            previous_model=previous_model,
            used_percent=used_percent,
        )

    async def _publish_approval_request(
        self,
        request_id: str,
        request: ApprovalRequest,
        route: dict[str, Any],
    ) -> None:
        """Put the question in front of the user, on the chat that asked."""
        channel = str(route.get("channel") or "")
        text: str | None = None
        if channel != "websocket":
            # Text-only channels (Telegram, WhatsApp, Teams, email...) get a
            # readable request with the reply commands.
            short = request_id[:8]
            lines = [f"Navin needs your approval: {request.action or request.tool}"]
            if request.reason:
                lines.append(f"Why: {request.reason}")
            if request.detail:
                lines.append(f"Detail: {request.detail[:500]}")
            if request.consequence:
                lines.append(f"Impact: {request.consequence}")
            lines.append(f"Reply /approve {short} or /deny {short}")
            text = "\n".join(lines)
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=channel,
                chat_id=str(route.get("chat_id") or ""),
                content=text,
                event=ApprovalRequestedEvent(
                    request_id=request_id,
                    tool=request.tool,
                    action=request.action,
                    reason=request.reason,
                    detail=request.detail,
                    consequence=request.consequence,
                    scope=request.scope,
                    expires_at_ms=route.get("expires_at_ms"),
                    remember_offered=bool(
                        request.scope and self.tools_config.approvals.remember
                    ),
                ),
            )
        )
        try:
            from navin.agent.remote_channels import publish_approval

            # A text channel was already asked above ("/approve <id>"): the
            # remote panel only adds the other chats set for approvals.
            await publish_approval(self.bus, request_id, request, route, origin_asked=channel != "websocket")
        except Exception:  # noqa: BLE001 - the WebUI card is already out
            logger.exception("could not mirror approval request to remote channels")

    async def _publish_choice_request(
        self,
        request_id: str,
        request: ChoiceRequest,
        route: dict[str, Any],
    ) -> None:
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=str(route.get("channel") or ""),
                chat_id=str(route.get("chat_id") or ""),
                event=ChoiceRequestedEvent(
                    request_id=request_id,
                    question=request.question,
                    options=[option.payload() for option in request.options],
                    allow_skip=request.allow_skip,
                    recommended_id=request.recommended.id,
                    expires_at_ms=route.get("expires_at_ms"),
                ),
            )
        )

    async def _publish_choice_closed(
        self,
        request_id: str,
        answer: ChoiceAnswer,
        route: dict[str, Any],
    ) -> None:
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=str(route.get("channel") or ""),
                chat_id=str(route.get("chat_id") or ""),
                event=ChoiceClosedEvent(
                    request_id=request_id,
                    question=str(route.get("question") or ""),
                    waited_ms=_waited_ms(route),
                    **choice_closed_fields(answer),
                ),
                metadata=route.get("turn_metadata") or None,
            )
        )

    async def _publish_approval_closed(
        self,
        request_id: str,
        decision: ApprovalDecision,
        route: dict[str, Any],
    ) -> None:
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=str(route.get("channel") or ""),
                chat_id=str(route.get("chat_id") or ""),
                event=ApprovalClosedEvent(
                    request_id=request_id,
                    allowed=decision.allowed,
                    reason=decision.reason,
                    waited_ms=_waited_ms(route),
                    action=str(route.get("action") or ""),
                ),
            )
        )

    def _publish_context_compacted(self, info: dict[str, Any]) -> None:
        """Relay a Consolidator compaction so channel UIs can surface it."""
        self._runtime_events().context_compacted(
            ContextCompacted(
                session_key=str(info.get("session_key") or ""),
                kind=str(info.get("kind") or "consolidation"),
                messages_archived=int(info.get("messages_archived") or 0),
                tokens_before=info.get("tokens_before"),
                tokens_after=info.get("tokens_after"),
            )
        )

    def set_model_preset(
        self,
        name: str | None,
        *,
        publish_update: bool = True,
    ) -> LLMRuntime:
        """Select a named default runtime for future turns."""
        old_model = self.model
        runtime = self.runtime_resolver.select_preset(name)
        self._publish_runtime_selection(runtime, publish_update=publish_update)
        logger.info(
            "Runtime model switched for next turn: {} -> {}",
            old_model,
            runtime.model,
        )
        return runtime

    def set_runtime_model(self, model: str) -> LLMRuntime:
        """Select a model on the current provider for future turns."""
        return self.runtime_resolver.select_model(model)

    def set_runtime_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Select a context limit for future turns."""
        return self.runtime_resolver.select_context_window(context_window_tokens)

    def _register_default_tools(
        self,
        *,
        provider_snapshot_loader: Callable[..., ProviderSnapshot] | None,
    ) -> None:
        """Register the default set of tools via plugin loader."""
        from navin.agent.tools.context import ToolContext
        from navin.agent.tools.loader import ToolLoader

        ctx = ToolContext(
            config=self.tools_config,
            workspace=str(self.workspace),
            bus=self.bus,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
            sessions=self.sessions,
            provider_snapshot_loader=provider_snapshot_loader,
            image_generation_provider_configs=self._image_generation_provider_configs,
            timezone=self.context.timezone or "UTC",
            workspace_sandbox=self.workspace_scopes.sandbox_status,
            runtime_events=self.runtime_events,
            resolve_model_preset=self.runtime_resolver.resolve_preset,
        )
        loader = ToolLoader()
        registered = loader.load(ctx, self.tools)
        from navin.config.loader import get_config_path, load_config_cached

        computer_config_path = get_config_path()
        # Runs before every turn: cached per file version, read-only.
        self._computer_config_loader = lambda: (
            load_config_cached(computer_config_path).tools.computer
            if computer_config_path.is_file() else self.tools_config.computer
        )

        # MyTool needs runtime state reference - manual registration
        if self.tools_config.my.enable:
            self.tools.register(
                MyTool(runtime_state=self, modify_allowed=self.tools_config.my.allow_set)
            )
            registered.append("my")

        # Installing an MCP server launches a local process, like exec: only
        # offer it where exec is allowed. Needs this loop to hot-reload MCP.
        if getattr(getattr(self.tools_config, "exec", None), "enable", True):
            from navin.agent.tools.mcp_manage import McpManageTool

            self.tools.register(McpManageTool(runtime_state=self, registry=self.tools))
            registered.append("mcp")

        logger.info("Registered {} tools: {}", len(registered), registered)

    async def _connect_mcp(self) -> None:
        """Connect configured MCP servers."""
        await agent_context.connect_mcp(self, self.tools)

    async def _auto_recover_subagents(self) -> None:
        """Resume subagents a previous gateway left at a durable boundary."""
        subagents = getattr(self, "subagents", None)
        if subagents is None or not hasattr(subagents, "auto_recover"):
            return

        def scope_for(session_key: str):
            session = self.sessions.get_or_create(session_key)
            channel, _, _ = session_key.partition(":")
            return self.workspace_scopes.for_turn(
                channel=channel, message_metadata=None,
                session_metadata=getattr(session, "metadata", None),
            )

        try:
            await subagents.auto_recover(runtime=self.llm_runtime(), scope_for=scope_for)
        except Exception:
            logger.exception("Subagent auto-recovery failed")

    async def _connect_mcp_safe(self) -> None:
        """Best-effort MCP connect for startup - never raise into the agent loop."""
        try:
            await self._connect_mcp()
            logger.info("MCP servers connected")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("MCP startup connect failed (tools may reconnect later): {}", e)

    def register_runtime_context_provider(
        self,
        provider: RuntimeContextProvider,
    ) -> None:
        """Register a provider resolved once before each inbound model turn."""
        if provider not in self._runtime_context_providers:
            self._runtime_context_providers.append(provider)

    @staticmethod
    def _runtime_chat_id(msg: InboundMessage) -> str:
        """Return the chat id shown in runtime metadata for the model."""
        return str(msg.metadata.get("context_chat_id") or msg.chat_id)

    async def _build_bus_progress_callback(
        self, msg: InboundMessage
    ) -> Callable[..., Awaitable[None]]:
        """Build a progress callback that publishes to the message bus."""
        return build_bus_progress_callback(self.bus, msg)

    async def _build_retry_wait_callback(
        self, msg: InboundMessage
    ) -> Callable[[str], Awaitable[None]]:
        """Build a retry-wait callback that publishes to the message bus."""

        async def _on_retry_wait(content: str) -> None:
            await self.bus.publish_outbound(
                outbound_message_for_event(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    event=RetryWaitEvent(content=content),
                    metadata=msg.metadata,
                )
            )

        return _on_retry_wait

    def _runtime_events(self) -> RuntimeEventPublisher:
        return ensure_runtime_event_publisher(self)

    async def submit_cron_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._cron_turns.submit(msg)

    async def submit_local_trigger_turn(self, msg: InboundMessage) -> OutboundMessage | None:
        return await self._local_trigger_turns.submit(msg)

    def pending_cron_job_ids_for_session(self, session_key: str) -> set[str]:
        return self._cron_turns.pending_job_ids_for_session(session_key)

    def pending_local_trigger_ids_for_session(self, session_key: str) -> set[str]:
        return self._local_trigger_turns.pending_trigger_ids_for_session(session_key)

    async def _publish_next_deferred_automation_turn(self, session_key: str) -> None:
        await publish_next_deferred_turn(
            deferred_queues=self._deferred_automation_turns,
            publish_inbound=self.bus.publish_inbound,
            session_key=session_key,
        )

    def _persist_user_message_early(
        self,
        msg: InboundMessage,
        session: Session,
        runtime_context_blocks: list[RuntimeContextBlock] | None = None,
        **kwargs: Any,
    ) -> bool:
        """Persist the triggering user message before the turn starts.

        Returns True if the message was persisted.
        """
        if not turn_continuation.should_persist_user_message(msg.metadata):
            return False
        media_paths = [p for p in (msg.media or []) if isinstance(p, str) and p]
        has_text = isinstance(msg.content, str) and msg.content.strip()
        if has_text or media_paths or runtime_context_blocks:
            extra: dict[str, Any] = ({"media": list(media_paths)} if media_paths else {}) | agent_context.session_extra(msg.metadata)
            extra.update(kwargs)
            text = msg.content if isinstance(msg.content, str) else ""
            text_override, automation_extra = automation_history_overrides(msg.metadata)
            if text_override is not None:
                text = text_override
            else:
                # Workflow handlers expand msg.content into an internal brief
                # ("[Build mode] (/forge)..."). Keep the user's original slash
                # line in history so titles, previews, and bubbles stay readable.

                original = (msg.metadata or {}).get("original_content")
                if isinstance(original, str) and original.strip():
                    text = original
            extra.update(automation_extra)
            text, runtime_context_meta = append_runtime_context(
                text,
                runtime_context_blocks or (),
            )
            if runtime_context_meta is not None:
                extra[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            session.add_message("user", text, **extra)
            self._mark_pending_user_turn(session, msg)
            self.turn_recovery.user_persisted(session, msg)
            # Off the loop like checkpoints; the end-of-turn save lands last.
            try:
                task = self.sessions.save_in_background(session)
            except RuntimeError:  # no running event loop (sync callers, tests)
                self.sessions.save(session)
            else:
                if task is not None:
                    self._background_saves.add(task)
                    task.add_done_callback(self._background_saves.discard)
            return True
        return False

    def _module_disabled_skills(self, msg: InboundMessage) -> set[str] | None:
        """Extra skills to hide for the WebUI product module on this turn."""
        from navin.command.modules import (
            PRODUCT_MODULE_METADATA_KEY,
            disabled_skills_for_module,
            normalize_product_module,
        )

        module = normalize_product_module(
            (msg.metadata or {}).get(PRODUCT_MODULE_METADATA_KEY)
        )
        if module is None:
            return None
        return disabled_skills_for_module(module)

    def _preload_skills_for_message(self, msg: InboundMessage) -> list[str] | None:
        """Skill names a workflow brief or product module asked to inject."""
        from navin.command.modules import (
            PRELOAD_SKILLS_METADATA_KEY,
            PRODUCT_MODULE_METADATA_KEY,
            default_preload_skills_for_module,
            extra_preload_skills_for_module,
        )

        raw = (msg.metadata or {}).get(PRELOAD_SKILLS_METADATA_KEY)
        names: list[str] = []
        if isinstance(raw, (list, tuple)):
            names = [str(name).strip() for name in raw if str(name).strip()]
        if raw is None:
            # Plain requests like "push this project to GitHub" or "split this
            # into PRs" load their playbook; workflow briefs choose their own.
            from navin.agent.skill_routing import intent_skill_names

            for name in intent_skill_names(str(getattr(msg, "content", "") or "")):
                if name not in names:
                    names.append(name)
        seen = set(names)
        module = (msg.metadata or {}).get(PRODUCT_MODULE_METADATA_KEY)
        for name in default_preload_skills_for_module(module):
            if name not in seen:
                names.append(name)
                seen.add(name)
        for name in extra_preload_skills_for_module(module):
            if name not in seen:
                names.append(name)
                seen.add(name)
        if "computer" in getattr(self, "tools", ()) and "computer-use" not in seen:
            names.append("computer-use")
        return names or None

    @staticmethod
    def _requires_tool_delivery(msg: InboundMessage | None, metadata: dict | None) -> bool:
        """True when this turn must produce a workspace deliverable via tools."""
        from navin.command.modules import REQUIRES_TOOL_DELIVERY_METADATA_KEY

        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if isinstance(source, dict) and source.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY):
                return True
        return False

    @staticmethod
    def _requires_verify_before_done(
        msg: InboundMessage | None, metadata: dict | None
    ) -> bool:
        """True when Build/Code turns must run verify/lint/tests before done."""
        from navin.command.modules import REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY

        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if isinstance(source, dict) and source.get(
                REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY
            ):
                return True
        return False

    @staticmethod
    def _verify_fail_nudge_limit(
        msg: InboundMessage | None, metadata: dict | None
    ) -> int | None:
        """Consecutive unaddressed validation nudges (None = runner default)."""
        from navin.command.modules import VERIFY_FAIL_NUDGE_LIMIT_METADATA_KEY

        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if not isinstance(source, dict):
                continue
            raw = source.get(VERIFY_FAIL_NUDGE_LIMIT_METADATA_KEY)
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int) and raw >= 0:
                return raw
        return None

    @staticmethod
    def _read_only_tools(
        msg: InboundMessage | None, metadata: dict | None
    ) -> bool:
        """True when Ask (and similar) turns must refuse mutating tools."""
        from navin.command.modules import READ_ONLY_TOOLS_METADATA_KEY

        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if isinstance(source, dict) and source.get(READ_ONLY_TOOLS_METADATA_KEY):
                return True
        return False

    def _adapt_reasoning_effort(
        self,
        runtime: LLMRuntime,
        metadata: dict | None,
        project_path: Any,
    ) -> LLMRuntime:
        """Route reasoning effort by mode and explicit override.

        Explicit message and configuration choices win over mode defaults.
        Automatic effort uses high for Plan and none for Agent turns.
        """
        try:
            from navin.agent.adaptive_reasoning import (
                REASONING_EFFORT_METADATA_KEY,
                adaptive_reasoning_effort,
            )

            override = None
            if isinstance(metadata, dict):
                override = metadata.get(REASONING_EFFORT_METADATA_KEY)
            after_failure = False
            if project_path:
                from navin.quality.verification_log import (
                    DEFAULT_MAX_AGE_S,
                    last_verification,
                )

                last = last_verification(project_path)
                if isinstance(last, dict) and not last.get("ok", True):
                    age = time.time() - float(last.get("ts", 0) or 0)
                    after_failure = 0 <= age <= DEFAULT_MAX_AGE_S
            effort = adaptive_reasoning_effort(
                runtime.generation.reasoning_effort,
                composer_mode=self._composer_mode(None, metadata),
                override=override,
                after_verify_failure=after_failure,
            )
            if effort != runtime.generation.reasoning_effort:
                runtime = runtime.with_generation_overrides(reasoning_effort=effort)
        except Exception:
            logger.debug("adaptive reasoning effort skipped", exc_info=True)
        return runtime

    @staticmethod
    def _composer_mode(
        msg: InboundMessage | None, metadata: dict | None
    ) -> str | None:
        """Composer mode this turn starts in (plan/ask/agent/...), if any."""
        mode: str | None = None
        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if not isinstance(source, dict):
                continue
            raw = source.get("composer_mode")
            if isinstance(raw, str) and raw.strip():
                mode = raw.strip().lower()
        return mode

    @staticmethod
    def _is_heartbeat_metadata(
        msg: InboundMessage | None, metadata: dict | None
    ) -> bool:
        """True when process_direct (or inbound meta) marked this turn as heartbeat."""
        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if not isinstance(source, dict):
                continue
            if source.get("heartbeat"):
                return True
            if str(source.get("session_key") or "").strip().lower() == "heartbeat":
                return True
        return False

    @staticmethod
    def _locked_denied_tools(
        msg: InboundMessage | None, metadata: dict | None
    ) -> frozenset[str]:
        """Module-level denials (product decisions) no mode switch may lift."""
        from navin.command.modules import (
            CODE_DENIED_TOOLS,
            HEARTBEAT_DENIED_TOOLS,
            PRODUCT_MODULE_METADATA_KEY,
            extra_denied_tools_for_module,
            kept_tools_for_module,
            normalize_product_module,
        )

        denied: set[str] = set()
        turn_module: str | None = None
        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if not isinstance(source, dict):
                continue
            module = normalize_product_module(source.get(PRODUCT_MODULE_METADATA_KEY))
            if module is not None:
                turn_module = module
            if module == "code":
                denied.update(CODE_DENIED_TOOLS)
        denied.update(extra_denied_tools_for_module(turn_module))
        from navin.webui.mcp_presets_api import mcp_deny_prefixes

        denied.update(mcp_deny_prefixes(turn_module))
        if turn_module != "marketing":
            denied.add("visual_qa")
        if turn_module != "seo":
            denied.add("seo")
        if turn_module != "ads":
            denied.add("ads")
        heartbeat = AgentLoop._is_heartbeat_metadata(msg, metadata)
        if heartbeat:
            denied.update(HEARTBEAT_DENIED_TOOLS)
        kept = set(kept_tools_for_module(turn_module))
        if heartbeat:
            kept -= set(HEARTBEAT_DENIED_TOOLS)
        denied.difference_update(kept)
        return frozenset(denied)

    @staticmethod
    def _denied_tools(
        msg: InboundMessage | None,
        metadata: dict | None,
        user_text: str | None = None,
        session_metadata: dict | None = None,
        *,
        workspace: str | os.PathLike[str] | None = None,
        session_key: str | None = None,
    ) -> frozenset[str]:
        """Tools refused for this turn (Code module denylist, composer mode, etc.).

        ``workspace`` lets the on-demand gating read the repository: a mobile
        checkout keeps ``mobile``, a web one keeps ``browser``, a project with a
        board keeps ``board``, whatever the turn's text says. ``session_key``
        keeps ``board`` for a conversation that already filed a plan.
        """
        from navin.agent.desk_intent import CODE_WORKBENCH_LIFTABLE_DESKS, desk_tools_for_text
        from navin.agent.media_intent import media_tools_for_text
        from navin.agent.tool_demand import (
            ON_DEMAND_TOOLS,
            exec_sessions_are_open,
            on_demand_tools_for_facts,
            on_demand_tools_for_session,
            on_demand_tools_for_text,
        )
        from navin.agent.tool_surface import denied_tools_for_composer_mode
        from navin.command.modules import (
            ACTIVE_DESKS_METADATA_KEY,
            CODE_DENIED_TOOLS,
            HEARTBEAT_DENIED_TOOLS,
            PRODUCT_MODULE_METADATA_KEY,
            STUDIO_OWNED_TOOLS,
            extra_denied_tools_for_module,
            kept_tools_for_module,
            normalize_product_module,
        )

        denied: set[str] = set()
        turn_module: str | None = None
        # Mode/workflow denials are "liftable": an explicit media request in
        # the user's text re-enables the tool. Module denials (Code) are
        # product decisions and never lifted.
        liftable: set[str] = set()
        # Denials that come from a mode or workflow, never kept lifted.
        policy_liftable: set[str] = set()
        # Tools withheld only to keep the schema small (see below).
        size_gated: set[str] = set()
        composer_mode: str | None = None
        for source in (metadata, getattr(msg, "metadata", None) if msg is not None else None):
            if not isinstance(source, dict):
                continue
            module = normalize_product_module(source.get(PRODUCT_MODULE_METADATA_KEY))
            if module is not None:
                turn_module = module
            if module == "code":
                denied.update(CODE_DENIED_TOOLS)
            extra = source.get("denied_tools")
            if isinstance(extra, (list, tuple, set, frozenset)):
                liftable.update(str(item) for item in extra if item)
                policy_liftable.update(str(item) for item in extra if item)
            mode = source.get("composer_mode")
            if isinstance(mode, str) and mode.strip():
                composer_mode = mode.strip().lower()
        liftable.update(denied_tools_for_composer_mode(composer_mode))
        policy_liftable.update(denied_tools_for_composer_mode(composer_mode))
        denied.update(extra_denied_tools_for_module(turn_module))
        # Outside a product module (CLI, Telegram, plain chat) every desk used
        # to ship its schema on every call: ~9k tokens, and a big prompt is
        # ~2.3s slower per model call even fully cached. Liftable, so the
        # desk's slash command brings it straight back for the turn.
        if turn_module is None:
            liftable.update(STUDIO_OWNED_TOOLS.values())
            size_gated.update(STUDIO_OWNED_TOOLS.values())
        # The Code workbench edits code; the montage and CRM desks (~2k tokens
        # of schema) come back only with /montage or /crm.
        if turn_module == "code":
            liftable.update(CODE_WORKBENCH_LIFTABLE_DESKS)
            size_gated.update(CODE_WORKBENCH_LIFTABLE_DESKS)
        # The heavy multiplexer tools (board, browser, mobile, cron,
        # notebook_edit, write_stdin) are ~4.5k tokens of schema on every step
        # of a turn that fixes a Python function. Withheld here, lifted below
        # by the text, the repository on disk or a live exec session.
        if turn_module in (None, "code"):
            liftable.update(ON_DEMAND_TOOLS)
            size_gated.update(ON_DEMAND_TOOLS)
        from navin.webui.mcp_presets_api import mcp_deny_prefixes

        denied.update(mcp_deny_prefixes(turn_module))
        if turn_module != "marketing":
            denied.add("visual_qa")
        if turn_module != "seo":
            denied.add("seo")
        if turn_module != "ads":
            denied.add("ads")
        heartbeat = AgentLoop._is_heartbeat_metadata(msg, metadata)
        if heartbeat:
            denied.update(HEARTBEAT_DENIED_TOOLS)
        # Asking for an image while the composer sits in Ask or Plan used to strip
        # the generator from the schema, and the model answered with inline SVG.
        # An explicit media request outranks the mode the user happened to be in
        # and workflow denylists (/forge without media); the Code module
        # denylist is a product decision and still wins.
        text = user_text or (msg.content if msg is not None else None)
        if text:
            liftable.difference_update(media_tools_for_text(text))
            liftable.difference_update(desk_tools_for_text(text))
            liftable.difference_update(on_demand_tools_for_text(text))
        if liftable & ON_DEMAND_TOOLS:
            liftable.difference_update(
                on_demand_tools_for_facts(
                    workspace, exec_sessions_open=exec_sessions_are_open()
                )
            )
        if not session_key:
            session_key = getattr(msg, "session_key", None)
        if not session_key and isinstance(metadata, dict):
            session_key = metadata.get("session_key")
        if liftable & ON_DEMAND_TOOLS:
            liftable.difference_update(on_demand_tools_for_session(session_key))
        if "browser" in liftable:
            from navin.agent.tools.browser import browser_session_active

            if browser_session_active(session_key):
                # "Continue" after a live handoff must keep the browser even
                # in a backend-only repository or after context compaction.
                liftable.discard("browser")
        if isinstance(session_metadata, dict):
            open_desks = session_metadata.get(ACTIVE_DESKS_METADATA_KEY)
            if isinstance(open_desks, (list, tuple, set, frozenset)):
                liftable.difference_update(str(item) for item in open_desks if item)
        if session_key and size_gated:
            # A tool lifted once stays for the conversation. Dropping it again
            # on the next message changes the tool list, which every provider
            # puts ahead of the history, so the whole prompt cache was lost
            # twice; keeping one schema costs far less than that re-read.
            sticky = _STICKY_LIFTED_TOOLS.get(session_key, frozenset())
            liftable.difference_update(sticky - policy_liftable)
            lifted = frozenset((size_gated - liftable) | sticky)
            if lifted:
                _STICKY_LIFTED_TOOLS[session_key] = lifted
                _STICKY_LIFTED_TOOLS.move_to_end(session_key)
                while len(_STICKY_LIFTED_TOOLS) > _STICKY_LIFTED_SESSIONS:
                    _STICKY_LIFTED_TOOLS.popitem(last=False)
        denied.update(liftable)
        kept = set(kept_tools_for_module(turn_module))
        if heartbeat:
            kept -= set(HEARTBEAT_DENIED_TOOLS)
        denied.difference_update(kept)
        return frozenset(denied)

    @staticmethod
    def _drop_repeated_runtime_context(
        blocks: list[RuntimeContextBlock],
        history: list[dict[str, Any]],
        *,
        lookback_user_messages: int = 8,
    ) -> list[RuntimeContextBlock]:
        """Skip blocks already present, byte for byte, in the replayed history.

        The project map, board, continuity and an unchanged git status used to
        ride on every user message (~800 tokens each), stored verbatim and
        re-sent on every later request. A repeated block adds nothing the
        model cannot read a few messages up; once compaction drops that copy
        it is no longer found and the block comes back.
        """
        if not blocks or not history:
            return blocks
        texts: list[str] = []
        for message in reversed(history):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                texts.extend(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if len(texts) >= lookback_user_messages:
                break
        if not texts:
            return blocks
        return [
            block for block in blocks
            if not any(block.content in text for text in texts)
        ]

    @staticmethod
    def _load_tools_named_by_text(text: str | None, session_key: str | None) -> bool:
        """Load the deferred tools this message asks for; True = defer the rest.

        Naming a browser, a board, a desk or a media generator is a request to
        use it, so its schema is sent from the first step instead of costing a
        tool_search round trip.
        """
        from navin.agent.tool_deferral import deferral_enabled, mark_loaded

        if not deferral_enabled() or not session_key:
            return False
        if text:
            from navin.agent.desk_intent import desk_tools_for_text
            from navin.agent.media_intent import media_tools_for_text
            from navin.agent.tool_demand import on_demand_tools_for_text

            named = (
                set(media_tools_for_text(text))
                | set(desk_tools_for_text(text))
                | set(on_demand_tools_for_text(text))
            )
            mark_loaded(session_key, sorted(named))
        return True

    @staticmethod
    def _allowed_tools(
        msg: InboundMessage | None,
        metadata: dict | None,
        user_text: str | None = None,
        session_metadata: dict | None = None,
    ) -> frozenset[str] | None:
        """Allowlist for this turn, or None when every registered tool is in play.

        ``/forge`` publishes the code-build surface. An explicit media request
        unions those generators onto it so a landing-page brief that also asks
        for a video still has ``generate_video``. Follow-up messages in the
        same Build session reuse the session copy so the 29k desk schemas
        do not come back the moment the user says "continue".
        """
        from navin.agent.media_intent import media_tools_for_text
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS, CODE_INTERACTION_TOOLS
        from navin.command.modules import ALLOWED_TOOLS_METADATA_KEY

        allowed: set[str] | None = None
        command = ""
        sources: list[Any] = [
            metadata,
            getattr(msg, "metadata", None) if msg is not None else None,
        ]
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw_cmd = source.get("original_command")
            if isinstance(raw_cmd, str) and raw_cmd.strip():
                command = raw_cmd.strip().lower()
            if extra := source.get("custom_command_allowed_tools"):
                # A Markdown command's allowed-tools: this turn only, never
                # persisted, so the next plain message gets every tool back.
                if isinstance(extra, (list, tuple, set, frozenset)):
                    names = {str(item) for item in extra if item}
                    allowed = names if allowed is None else allowed & names
            extra = source.get(ALLOWED_TOOLS_METADATA_KEY)
            if isinstance(extra, (list, tuple, set, frozenset)):
                names = {str(item) for item in extra if item}
                allowed = names if allowed is None else allowed | names
        if allowed is None and isinstance(session_metadata, dict):
            if command in {"", "/forge", "/cruise"}:
                extra = session_metadata.get(ALLOWED_TOOLS_METADATA_KEY)
                if isinstance(extra, (list, tuple, set, frozenset)):
                    allowed = {str(item) for item in extra if item}
                    # Migrate the exact former built-in surface on a resumed
                    # Build session. Explicit per-turn/custom lists stay as
                    # supplied; a saved old default must not hide new tools.
                    if allowed == CODE_BUILD_ALLOWED_TOOLS - CODE_INTERACTION_TOOLS:
                        allowed.update(CODE_INTERACTION_TOOLS)
        if allowed is None:
            return None
        text = user_text or (msg.content if msg is not None else None)
        if text:
            allowed.update(media_tools_for_text(text))
        return frozenset(allowed)

    @staticmethod
    def _desk_tools_opened_by_turn(
        metadata: dict | None,
        user_text: str | None,
    ) -> set[str]:
        """Desk tools this turn put in play, either by text or by its module."""
        from navin.agent.desk_intent import desk_tools_for_text
        from navin.command.modules import (
            PRODUCT_MODULE_METADATA_KEY,
            STUDIO_OWNED_TOOLS,
            normalize_product_module,
        )

        opened = set(desk_tools_for_text(user_text))
        if isinstance(metadata, dict):
            module = normalize_product_module(metadata.get(PRODUCT_MODULE_METADATA_KEY))
            owned = STUDIO_OWNED_TOOLS.get(module or "")
            if owned:
                opened.add(owned)
        return opened

    @staticmethod
    def _forced_media_tool(user_text: str | None) -> str | None:
        """Generator to pin for an unambiguous single-medium request."""
        from navin.agent.media_intent import detect_media_intent

        intent = detect_media_intent(user_text)
        if intent is None or not intent.should_force_tool():
            return None
        return intent.primary_tool or None

    def _build_initial_messages(
        self,
        msg: InboundMessage,
        session: Session,
        history: list[dict[str, Any]],
        pending_summary: str | None,
        include_memory_recent_history: bool = True,
        runtime_context_blocks: list[RuntimeContextBlock] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for the LLM turn."""
        from navin.command.modules import metadata_requests_evidence_only

        scope = self.workspace_scopes.for_message(msg, session.metadata)
        meta = msg.metadata if isinstance(msg.metadata, dict) else {}
        return self.context.build_messages(
            history=history,
            current_message=msg.content,
            skill_names=self._preload_skills_for_message(msg),
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=self._runtime_chat_id(msg),
            sender_id=msg.sender_id,
            session_summary=pending_summary,
            session_metadata=session.metadata,
            workspace=scope.project_path,
            runtime_context_blocks=runtime_context_blocks,
            include_memory_recent_history=include_memory_recent_history,
            session_key=session.key,
            unified_session=self._unified_session,
            extra_disabled_skills=self._module_disabled_skills(msg),
            evidence_only=metadata_requests_evidence_only(meta),
        )

    def _request_context_for_turn(self, ctx: TurnContext) -> RequestContext:
        scope = self.workspace_scopes.for_message(ctx.msg, ctx.session.metadata)
        return RequestContext(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            message_id=ctx.msg.metadata.get("message_id"),
            session_key=ctx.session_key,
            original_user_text=ctx.original_user_text,
            runtime=ctx.runtime,
            metadata=dict(ctx.msg.metadata or {}),
            sender_id=ctx.msg.sender_id,
            turn_id=ctx.turn_id,
            workspace=scope.project_path,
        )

    async def _resolve_runtime_context_for_turn(
        self,
        ctx: TurnContext,
    ) -> list[RuntimeContextBlock]:
        tools = ctx.tools or self.tools
        providers = [
            *tools.get_runtime_context_providers(),
            *self._runtime_context_providers,
        ]
        assert ctx.request_context is not None
        blocks = await resolve_runtime_context(providers, ctx.request_context)
        from navin.command.modules import (
            CONVERSATIONAL_TURN_METADATA_KEY,
            ORIENTATION_CONTEXT_SOURCES,
        )

        if (ctx.msg.metadata or {}).get(CONVERSATIONAL_TURN_METADATA_KEY):
            blocks = [block for block in blocks if block.source not in ORIENTATION_CONTEXT_SOURCES]
        return blocks

    async def _dispatch_command_inline(
        self,
        msg: InboundMessage,
        key: str,
        raw: str,
        dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """Dispatch a command directly from the run() loop and publish the result."""
        ctx = CommandContext(msg=msg, session=None, key=key, raw=raw, loop=self)
        result = await dispatch_fn(ctx)
        if result:
            await self.bus.publish_outbound(result)
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    def mark_stop_requested(self, key: str) -> None:
        """Record a user /stop so stale continuation slices cannot resume work.

        A board or turn-budget continuation republished to the bus at a slice
        boundary is invisible to ``_cancel_active_tasks``; without this marker
        it restarts the very plan the user just stopped.
        """
        self._stop_requested_at[key] = time.time()
        # Durable: a restart must not resume the request the user stopped.
        self.turn_recovery.cancel(key)

    def _is_stale_continuation(self, msg: InboundMessage, key: str) -> bool:
        """True when *msg* is an internal continuation of a run stopped by /stop."""
        if not turn_continuation.internal_continuation_inbound(msg.metadata):
            # Any genuine message starts fresh work: the stop no longer applies.
            self._stop_requested_at.pop(key, None)
            return False
        stop_ts = self._stop_requested_at.get(key)
        if stop_ts is None:
            return False
        started = turn_continuation.internal_continuation_run_started_at(msg.metadata)
        return started is None or started <= stop_ts

    _STOP_GRACE_S = 2.0

    async def _cancel_active_tasks(self, key: str) -> int:
        """Cancel and await all active tasks and subagents for *key*.

        Returns the total number of cancelled tasks + subagents.
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        # Bounded wait: this runs inline in run(), the only inbound reader. A
        # task that unwinds slowly (or swallows one CancelledError) must not
        # freeze every later message, nor make /stop feel unresponsive.
        pending = [t for t in tasks if not t.done()]
        if pending:
            _, still = await asyncio.wait(pending, timeout=self._STOP_GRACE_S)
            for t in still:
                t.cancel()  # second request for tasks that swallowed the first
                t.add_done_callback(_consume_task_result)
        for t in tasks:
            if t.done():
                _consume_task_result(t)
        try:
            sub_cancelled = await asyncio.wait_for(
                self.subagents.cancel_by_session(key), timeout=self._STOP_GRACE_S,
            )
        except asyncio.TimeoutError:
            logger.warning("Subagent cancel for {} still unwinding after stop", key)
            sub_cancelled = 0
        return cancelled + sub_cancelled

    async def _cancel_sustained_goal(self, key: str, msg: InboundMessage) -> bool:
        """Deactivate the session's active sustained goal after a user /stop.

        Cancelling the running task is not enough: while the goal stays
        ``active`` in session metadata, the runner re-injects "continue the
        goal" at the end of any later turn (heartbeat, automation, next user
        message) and the work silently resumes. Returns True when a goal was
        deactivated.
        """
        from datetime import datetime

        session = self.sessions.get_or_create(key)
        goal = parse_goal_state(goal_state_raw(session.metadata))
        if not isinstance(goal, dict) or goal.get("status") != "active":
            return False
        session.metadata[GOAL_STATE_KEY] = {
            **goal,
            "status": "cancelled",
            "ended_at": datetime.now().isoformat(),
            "recap": "Stopped by the user (/stop).",
        }
        turn_continuation.reset_goal_continuation_rounds(session.metadata)
        await self.sessions.save_async(session)
        logger.info("Sustained goal cancelled by /stop for session {}", key)
        await self._runtime_events().bus.publish(
            GoalStateChanged(
                context=RuntimeEventContext(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    session_key=key,
                    metadata=dict(msg.metadata or {}),
                ),
                session_metadata=dict(session.metadata),
            )
        )
        return True

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """Return the session key used for task routing and mid-turn injections."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    @staticmethod
    def _replay_token_budget(runtime: LLMRuntime) -> int:
        """Derive a token budget for session history replay from the context window."""
        if runtime.context_window_tokens <= 0:
            return 0
        budget = input_budget_for(runtime.context_window_tokens, runtime.generation.max_tokens)
        return budget if budget > 0 else max(128, runtime.context_window_tokens // 2)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
        *,
        runtime: LLMRuntime,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        original_user_text: str | None = None,
        pending_queue: asyncio.Queue | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        turn_scopes: list[AbstractContextManager[Any]] | None = None,
        tools: ToolRegistry | None = None,
        request_context: RequestContext | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.

        Returns (final_content, tools_used, messages, stop_reason, had_injections).
        """
        self._sync_subagent_runtime_limits()

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(
            *, limit: int = _MAX_INJECTIONS_PER_TURN, wait: bool = True,
        ) -> list[dict[str, Any]]:
            """Drain follow-up messages from the pending queue.

            With ``wait`` (the end-of-turn drain), when no messages are
            immediately available but sub-agents spawned in this dispatch are
            still running, blocks until at least one result arrives (or
            timeout).  This keeps the runner loop alive so subsequent
            sub-agent completions are consumed in-order rather than
            dispatched separately.

            The mid-turn drains pass ``wait=False``: between two tool batches
            the model has its own work to continue, and parking the whole
            turn for up to five minutes on a subagent that may not be done
            froze exactly the flows spawn exists to parallelise.
            """
            if pending_queue is None:
                return []

            async def _to_user_message(pending_msg: InboundMessage) -> dict[str, Any]:
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = await self._prepare_message_media(content, media)
                    content, media, _ = guard_vision_media(content, media, runtime.model)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                row: dict[str, Any] = {"role": "user", "content": user_content}
                metadata = pending_msg.metadata if isinstance(pending_msg.metadata, dict) else {}
                if (
                    pending_msg.sender_id == "subagent"
                    and metadata.get("injected_event") == "subagent_result"
                ):
                    marker: dict[str, Any] = {"kind": "subagent_result"}
                    task_id = metadata.get("subagent_task_id")
                    if isinstance(task_id, str) and task_id:
                        marker["subagent_task_id"] = task_id
                        row["subagent_task_id"] = task_id
                    row[HIDDEN_HISTORY_META] = marker
                    row["injected_event"] = "subagent_result"
                elif metadata.get("injected_event") == "exec_finished":
                    # A background command's exit, not something the user typed.
                    row[HIDDEN_HISTORY_META] = {
                        "kind": "exec_finished",
                        "exec_session_id": str(metadata.get("exec_session_id") or ""),
                    }
                    row["injected_event"] = "exec_finished"
                return row

            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    queued = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                items.append(await _to_user_message(queued))

            # Block if nothing drained but sub-agents spawned in this dispatch
            # are still alive.  Keeps the runner loop alive so subsequent
            # completions are injected in-order rather than dispatched separately.
            # The wait itself ends as soon as no result is owed any more.
            if (wait
                    and not items
                    and session is not None
                    and self.subagents.get_running_count_by_session(session.key) > 0):
                msg = await self._wait_for_owed_result(session.key, pending_queue)
                if msg is None:
                    return items
                items.append(await _to_user_message(msg))
                while len(items) < limit:
                    try:
                        queued = pending_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    items.append(await _to_user_message(queued))

            return items

        def _background_idle(run_messages: list[dict[str, Any]]) -> bool:
            """Background work was started here and none of it runs any more."""
            if session is None:
                return False
            if pending_queue is not None and not pending_queue.empty():
                return False  # a result is waiting for the next drain
            if self._owed_subagent_results(session.key) > 0:
                return False
            if self._live_background_commands(session.key):
                return False
            return started_background_work(run_messages)

        active_session_key = session.key if session else session_key
        effective_scope = self.workspace_scopes.for_turn(
            channel=channel,
            message_metadata=metadata,
            session_metadata=session.metadata if session is not None else None,
        )
        effective_tools = tools or self.tools
        request_ctx = request_context or RequestContext(
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
            session_key=active_session_key,
            original_user_text=original_user_text,
            runtime=runtime,
            metadata=dict(metadata or {}),
            workspace=effective_scope.project_path,
        )
        file_state_token = bind_file_states(self._file_state_store.for_session(active_session_key))
        request_token = bind_request_context(request_ctx)
        workspace_token = bind_workspace_scope(effective_scope)
        live_restrict_token = bind_live_restrict_to_workspace(self.restrict_to_workspace)
        # An ephemeral turn has no one watching it (a summary, a title, a
        # consolidation), so leaving the gate unbound is what tells a tool to
        # refuse instead of hanging on a question nobody will read.
        approval_token = bind_approval_gate(None if ephemeral else self.approvals)
        choice_token = bind_choice_broker(None if ephemeral else self.choices)
        from navin.agent.credentials import bind_credential_broker, reset_credential_broker

        credential_token = bind_credential_broker(
            None if ephemeral else getattr(self, "credentials", None)
        )
        # Automatic checkpoint: every real user prompt snapshots the
        # conversation, and a bound recorder captures before-modification
        # file states so /checkpoint restore can rewind code too.
        turn_recorder: TurnRecorder | None = None
        recorder_token = None
        live_hook_token = None
        auto_checkpoint_name: str | None = None
        if session is not None and original_user_text and not ephemeral:
            try:
                meta = self.checkpoints.save(
                    session,
                    auto=True,
                    prompt=original_user_text,
                )
                auto_checkpoint_name = meta["name"]
                # Surface the restore point in the chat transcript: the user
                # should see, at the exact spot in the conversation, that this
                # turn can be rewound.
                self._runtime_events().checkpoint_saved(
                    CheckpointSaved(
                        session_key=session.key,
                        name=auto_checkpoint_name or "",
                        auto=True,
                    )
                )
            except OSError as exc:
                logger.warning("Auto checkpoint failed for {}: {}", session.key, exc)
            turn_recorder = TurnRecorder()
            recorder_token = bind_checkpoint_recorder(turn_recorder)
            # Full workspace snapshot (shadow git) so the Dev workbench can
            # one-click restore the pre-turn state. Fire-and-forget daemon
            # thread; never blocks or fails the turn.
            try:
                from navin.webui.checkpoints_api import spawn_pre_turn_checkpoint

                if effective_scope.project_path:
                    spawn_pre_turn_checkpoint(
                        effective_scope.project_path, label=original_user_text
                    )
            except Exception:  # noqa: BLE001 - snapshotting is best-effort
                pass
            # Stream each first-touch edit into the pending-review store while
            # the turn is still running, so open editors highlight agent
            # changes live instead of after the final answer.
            live_session_key = session.key

            def _live_flush(path: str, before: bytes | None) -> None:
                with suppress(OSError, ValueError):
                    self.pending_review.merge_turn(live_session_key, {path: before})

            live_hook_token = bind_live_edit_hook(_live_flush)
        turn_scope_stack = ExitStack()
        # Compute lazily because create_goal may create goal metadata during this run.
        def _goal_continue() -> str | None:
            _goal_lines = goal_state_runtime_lines(session.metadata if session is not None else None)
            if not _goal_lines:
                return None
            return (
                "You have an active sustained goal:\n\n"
                + "\n".join(_goal_lines)
                + "\n\nPlease continue working toward the objective using your tools, "
                "or call update_goal with action='complete' if the work is truly finished. "
                "If you are blocked (missing credential, a decision only the user can "
                "take, the same error again), call update_goal with action='block' and "
                "say what you need instead of retrying."
            )

        session_metadata = session.metadata if session is not None else None
        turn_loop_guard = (
            self._loop_guards.setdefault(active_session_key, AgentLoopGuard())
            if active_session_key and not ephemeral else None
        )
        try:
            for scope in turn_scopes or ():
                turn_scope_stack.enter_context(scope)
            if turn_loop_guard is not None:
                from navin.board.auto_advance import validation_probe

                # Board auto-advance (R3) must not close a step while code
                # validation is pending, the same gate the runner applies to
                # an explicit `board move done`.
                turn_scope_stack.enter_context(validation_probe(
                    lambda: bool(getattr(turn_loop_guard.validation, "pending", False))
                ))
            hook = build_agent_turn_hook(AgentTurnHookSpec(
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                channel=channel,
                chat_id=chat_id,
                message_id=message_id,
                metadata=metadata,
                session_key=active_session_key,
                workspace=effective_scope.project_path,
                tool_hint_max_length=self.tool_hint_max_length,
                on_iteration=lambda iteration: setattr(self, "_current_iteration", iteration),
                registered_hook_factories=self._hook_factories,
                turn_hook_factories=list(hook_factories or []),
                registered_hooks=self._extra_hooks,
                turn_hooks=list(hooks or []),
                ephemeral=ephemeral,
                run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            ))
            runtime = self._adapt_reasoning_effort(
                runtime, metadata, effective_scope.project_path
            )
            from navin.plan_limits import effective_turn_iterations

            goal_on = bool(
                session is not None and sustained_goal_active(session.metadata)
            )
            result = await self.runner.run(AgentRunSpec(
                initial_messages=initial_messages,
                tools=effective_tools,
                runtime=runtime,
                max_iterations=effective_turn_iterations(
                    self.max_iterations, goal_active=goal_on
                ),
                max_tool_result_chars=self.max_tool_result_chars,
                hook=hook,
                error_message="Sorry, I encountered an error calling the AI model.",
                concurrent_tools=True,
                workspace=effective_scope.project_path,
                session_key=session.key if session else None,
                context_block_limit=self.context_block_limit,
                tool_result_clearing=self.tool_result_clearing,
                provider_retry_mode=self.provider_retry_mode,
                progress_callback=on_progress,
                stream_progress_deltas=on_stream is not None,
                retry_wait_callback=on_retry_wait,
                checkpoint_callback=_checkpoint,
                injection_callback=_drain_pending,
                # Sustained goals may legitimately exceed NAVIN_LLM_TIMEOUT_S; idle stall
                # is still capped by NAVIN_STREAM_IDLE_TIMEOUT_S in streaming providers.
                llm_timeout_s=runner_wall_llm_timeout_s(
                    self.sessions,
                    session.key if session is not None else session_key,
                    metadata=session_metadata,
                    message_metadata=metadata,
                ),
                goal_active_predicate=lambda: sustained_goal_active(session.metadata) if session is not None else False,
                background_idle=_background_idle,
                goal_continue_message=_goal_continue,
                requires_tool_delivery=self._requires_tool_delivery(None, metadata),
                scope_targets=tuple(named_targets(original_user_text, effective_scope.project_path)),
                ui_terms=ui_terms(original_user_text),
                explicit_asks=explicit_asks(original_user_text),
                go_ahead=is_go_ahead(original_user_text),
                requires_verify_before_done=self._requires_verify_before_done(
                    None, metadata
                ),
                validate_code_changes=not ephemeral,
                verify_fail_nudge_limit=self._verify_fail_nudge_limit(None, metadata),
                read_only_tools=self._read_only_tools(None, metadata),
                # Plan turns refuse mutating calls at the runner (design-only);
                # a successful set_composer_mode lifts or tightens this mid-turn.
                plan_read_only=self._composer_mode(None, metadata) == "plan",
                composer_mode=self._composer_mode(None, metadata),
                followup_reasoning_effort=self.followup_reasoning_effort,
                conversational_turn=bool(
                    (metadata or {}).get("conversational_turn")
                ),
                denied_tools=self._denied_tools(
                    None,
                    metadata,
                    original_user_text,
                    session.metadata if session is not None else None,
                    workspace=self.workspace,
                    session_key=active_session_key,
                ),
                locked_denied_tools=self._locked_denied_tools(None, metadata),
                allowed_tools=self._allowed_tools(
                    None,
                    metadata,
                    original_user_text,
                    session.metadata if session is not None else None,
                ),
                forced_tool=self._forced_media_tool(original_user_text),
                defer_tools=self._load_tools_named_by_text(
                    original_user_text,
                    session.key if session is not None else None,
                ),
                finalize_on_max_iterations=turn_continuation.should_finalize_on_max_iterations(
                    pending_queue_available=pending_queue is not None and session is not None,
                    session_metadata=session_metadata,
                    message_metadata=metadata,
                    session_key=session.key if session is not None else session_key,
                ),
                # One-shot CLI calls have no dispatcher to consume a next
                # slice. Keep the accepted task in this runner until it ends.
                continue_on_max_iterations=pending_queue is None and not ephemeral,
                loop_guard=turn_loop_guard,
            ))
        finally:
            turn_scope_stack.close()
            if live_hook_token is not None:
                reset_live_edit_hook(live_hook_token)
            if recorder_token is not None:
                reset_checkpoint_recorder(recorder_token)
                if turn_recorder is not None and session is not None and (
                    turn_recorder.files or turn_recorder.skipped
                ):
                    # Both stores fsync a JSON file: 1 to 4 s per turn end on
                    # a WSL or network drive in the CLI freeze log, so the
                    # writes run in a worker thread, never on the shared loop.
                    with suppress(RuntimeError):  # executor already shut down: the turn ends anyway
                        await asyncio.to_thread(
                            _persist_turn_snapshots,
                            self.checkpoints,
                            self.pending_review,
                            session.key,
                            auto_checkpoint_name,
                            turn_recorder.files,
                            turn_recorder.skipped,
                        )
            reset_approval_gate(approval_token)
            reset_choice_broker(choice_token)
            reset_credential_broker(credential_token)
            reset_workspace_scope(workspace_token)
            reset_live_restrict_to_workspace(live_restrict_token)
            reset_request_context(request_token)
            reset_file_states(file_state_token)
        self._last_usage = result.usage
        if session is not None and not ephemeral:
            from navin.board.auto_advance import finish_turn_async as finish_board_turn

            # Turn end of navin/board/auto_advance.py: settles the steps the
            # model moved on from (R3) and may close the step it worked on (R4).
            await finish_board_turn(
                effective_scope.project_path,
                session.key,
                stop_reason=result.stop_reason,
                final_content=result.final_content,
                tool_events=result.tool_events,
                composer_mode=self._composer_mode(None, metadata),
                followup_reasoning_effort=self.followup_reasoning_effort,
                read_only=self._read_only_tools(None, metadata),
                validation_is_pending=bool(
                    turn_loop_guard is not None
                    and getattr(turn_loop_guard.validation, "pending", False)
                ),
                metadata={**(metadata or {}), **(request_ctx.metadata or {})},
                sender_id=request_ctx.sender_id,
                turn_id=request_ctx.turn_id,
                bus=self.bus,
            )
        if session is not None and not ephemeral:
            from navin.command.modules import (
                ACTIVE_DESKS_METADATA_KEY,
                ALLOWED_TOOLS_METADATA_KEY,
                PRELOAD_SKILLS_METADATA_KEY,
                SLIM_SKILL_PRELOAD_METADATA_KEY,
            )
            from navin.session.context_usage_meta import (
                persist_last_context_usage,
                persist_last_preload_skills,
            )

            meta = metadata if isinstance(metadata, dict) else {}
            # A conversation that opened a desk keeps it for its follow-ups:
            # asking about tenders then saying "continue" must not lose the
            # tenders tool, while a chat that never mentioned it never pays
            # for its schema.
            opened = self._desk_tools_opened_by_turn(meta, original_user_text)
            if opened:
                previous = session.metadata.get(ACTIVE_DESKS_METADATA_KEY)
                keep = {str(item) for item in previous or () if item} | opened
                session.metadata[ACTIVE_DESKS_METADATA_KEY] = sorted(keep)
            raw_skills = meta.get(PRELOAD_SKILLS_METADATA_KEY)
            skills: list[str] | None = None
            if isinstance(raw_skills, (list, tuple)):
                skills = [str(s) for s in raw_skills if s]
            persist_last_preload_skills(session.metadata, skills)
            if meta.get(SLIM_SKILL_PRELOAD_METADATA_KEY):
                session.metadata[SLIM_SKILL_PRELOAD_METADATA_KEY] = True
            raw_allowed = meta.get(ALLOWED_TOOLS_METADATA_KEY)
            if isinstance(raw_allowed, (list, tuple, set, frozenset)) and raw_allowed:
                session.metadata[ALLOWED_TOOLS_METADATA_KEY] = [
                    str(item) for item in raw_allowed if item
                ]
            else:
                command = str(meta.get("original_command") or "").strip().lower()
                if command and command not in {"/forge", "/cruise"}:
                    session.metadata.pop(ALLOWED_TOOLS_METADATA_KEY, None)
            profile = getattr(result, "prompt_profile", None)
            profile_sections = (
                profile.get("sections") if isinstance(profile, dict) else None
            )
            profile_tool_count = (
                profile.get("tool_count") if isinstance(profile, dict) else None
            )
            if persist_last_context_usage(
                session.metadata,
                result.usage,
                context_window_tokens=runtime.context_window_tokens,
                model=runtime.model,
                sections=profile_sections if isinstance(profile_sections, dict) else None,
                tool_count=profile_tool_count if isinstance(profile_tool_count, int) else None,
            ):
                await self.sessions.save_async(session)
        if result.stop_reason == "max_iterations":
            logger.info("Tool slice completed for {}", active_session_key or "default")
            should_stream = turn_continuation.should_stream_budget_response(
                stop_reason=result.stop_reason,
                pending_queue_available=pending_queue is not None and session is not None,
                session_metadata=session_metadata,
                message_metadata=metadata,
                session_key=session.key if session is not None else session_key,
                final_content=result.final_content,
            )
            # Push final content through stream so streaming channels (e.g. Telegram)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end and should_stream:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
            if session is not None and not ephemeral and getattr(result, "retryable_error", False):
                delay = self.turn_recovery.schedule(session, retry_after=getattr(result, "retry_after_s", None))
                if delay is not None and on_retry_wait is not None:
                    await on_retry_wait(
                        f"Connection interrupted. Progress saved; resuming automatically in {delay:.0f}s."
                    )
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    def _publish_model_failover(self, chosen_model: str, served_model: str) -> None:
        """Relay a provider-level model substitution so channel UIs can report it."""
        self._runtime_events().model_failed_over(chosen_model, served_model)

    async def run(
        self, *, recovery_channel: str = "websocket", recovery_session_key: str | None = None,
    ) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        # The provider layer builds the failover wrapper without a bus, so it needs
        # a publisher handed to it; otherwise a substitution stays in the logs.
        from navin.providers.model_switch_notice import set_model_switch_publisher

        set_model_switch_publisher(self._publish_model_failover)
        try:
            # MCP cold-start (stdio/npx) can hang for minutes. Never block the
            # inbound loop on it - connect in the background and keep consuming.
            mcp_task = asyncio.create_task(
                self._connect_mcp_safe(),
                name="mcp-startup-connect",
            )
            threading.Thread(
                target=_prewarm_first_turn, name="navin-first-turn-prewarm", daemon=True,
            ).start()
            # Only the gateway (all sessions, websocket runtime) serves chat
            # apps, so only it resumes their interrupted requests.
            external = recovery_session_key is None and recovery_channel == "websocket"
            if external:
                await asyncio.to_thread(self._adopt_interrupted_turns)
                # The gateway owns automatic updates: check at start and every
                # 2 h, download in the background, install only while idle.
                self._start_auto_update()
            recovered = await asyncio.to_thread(
                self.turn_recovery.discover,
                channel=recovery_channel, session_key=recovery_session_key, external=external,
            )
            abandoned, self.turn_recovery.abandoned = self.turn_recovery.abandoned, []
            cut_twice, self.turn_recovery.cut_twice = self.turn_recovery.cut_twice, set()
            for key in abandoned:
                session = self.sessions.get_or_create(key)
                self._restore_runtime_checkpoint(session)
                self._close_interrupted_turn(session, cut_twice=key in cut_twice)
                await self.sessions.save_async(session)
            for key in recovered:
                session = self.sessions.get_or_create(key)
                checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
                if isinstance(checkpoint, dict) and checkpoint.get("phase") == "final_response":
                    self._restore_runtime_checkpoint(session)
                    self.turn_recovery.finish(session)
                    await self.sessions.save_async(session)
            logger.info("Agent loop started")
            recovery_task = asyncio.create_task(
                self._auto_recover_subagents(), name="subagent-auto-recover",
            )
            self._background_tasks.append(recovery_task)
            recovery_task.add_done_callback(
                lambda t: t in self._background_tasks and self._background_tasks.remove(t)
            )

            while self._running:
                active_keys = {
                    key for key, tasks in self._active_tasks.items()
                    if any(not task.done() for task in tasks)
                } | set(self._pending_queues)
                for recovered_msg in self.turn_recovery.take_due(active_keys=active_keys):
                    self._republish_inbound(recovered_msg)
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    self._maybe_check_idle_sessions()
                    continue
                except asyncio.CancelledError:
                    # Preserve real task cancellation so shutdown can complete cleanly.
                    # Only ignore non-task CancelledError signals that may leak from integrations.
                    if not self._running or asyncio.current_task().cancelling():
                        raise
                    continue
                except Exception as e:
                    logger.warning("Error consuming inbound message: {}, continuing...", e)
                    continue

                try:
                    raw = msg.content.strip()
                    effective_key = self._effective_session_key(msg)
                    if await agent_context.handle_runtime_control(self, msg, self.tools):
                        continue
                    if not self.turn_recovery.matches(msg, effective_key):
                        continue
                    # A goal-continuation slice may still be in flight on the bus
                    # when /stop deactivates the goal; running it would resume the
                    # very work the user just stopped. ONLY goal slices are gated
                    # on goal state: board and turn-budget continuations have no
                    # goal and must never be dropped here (that silent drop was
                    # exactly the "stuck at max iterations" hard stop mid-build).
                    if turn_continuation.is_goal_continuation_inbound(msg.metadata):
                        session_metadata = self.sessions.get_or_create(effective_key).metadata
                        if not sustained_goal_active(session_metadata):
                            logger.info(
                                "Dropping stale goal continuation for session {} (goal inactive)",
                                effective_key,
                            )
                            continue
                    # Board and turn-budget continuations have no goal state to
                    # gate on; a /stop marker is the only thing that can tell a
                    # live slice from one that outlived the run it belongs to.
                    if self._is_stale_continuation(msg, effective_key):
                        logger.info(
                            "Dropping stale internal continuation after /stop for session {}",
                            effective_key,
                        )
                        continue
                    if self.commands.is_priority(raw):
                        await self._dispatch_command_inline(
                            msg, effective_key, raw,
                            self.commands.dispatch_priority,
                        )
                        continue
                    deferred = False
                    for label, coordinator in self._automation_turn_coordinators:
                        if coordinator.defer_if_active(
                            msg,
                            session_key=effective_key,
                            active_session_keys=self._pending_queues.keys(),
                        ):
                            logger.info(
                                "Deferred {} turn for active session {}",
                                label,
                                effective_key,
                            )
                            deferred = True
                            break
                    if deferred:
                        continue
                    # If this session already has an active pending queue (i.e. a task
                    # is processing this session), route the message there for mid-turn
                    # injection instead of creating a competing task.
                    if effective_key in self._pending_queues:
                        # Side-channel commands must not be queued for injection;
                        # dispatch them directly (same pattern as priority
                        # commands). Agent-turn commands (mode-routed free text
                        # like "/forge salut") are ordinary user messages: they
                        # join the pending queue below so they are neither dropped
                        # nor allowed to interrupt the running tasks.
                        if not should_inject_into_active_turn(self.commands, raw):
                            await self._dispatch_command_inline(
                                msg, effective_key, raw,
                                self.commands.dispatch,
                            )
                            continue
                        pending_msg = msg
                        if effective_key != msg.session_key:
                            pending_msg = dataclasses.replace(
                                msg,
                                session_key_override=effective_key,
                            )
                        await enqueue_pending_followup(
                            self._pending_queues[effective_key],
                            pending_msg,
                            session_key=effective_key,
                        )
                        logger.info(
                            "Routed follow-up message to pending queue for session {}",
                            effective_key,
                        )
                        continue
                    # Compute the effective session key before dispatching
                    # This ensures /stop command can find tasks correctly when unified session is enabled
                    task = asyncio.create_task(self._dispatch(msg))
                    self._active_tasks.setdefault(effective_key, []).append(task)
                    task.add_done_callback(
                        lambda t, k=effective_key: self._active_tasks.get(k, [])
                        and self._active_tasks[k].remove(t)
                        if t in self._active_tasks.get(k, [])
                        else None
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One bad message (a failing command, a disk error while
                    # loading a session) must never stop the only inbound reader.
                    logger.exception(
                        "Error routing inbound message for session {}; continuing",
                        getattr(msg, "session_key", "?"),
                    )
                    continue
        finally:
            if not mcp_task.done():
                mcp_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await mcp_task
            set_model_switch_publisher(None)
            # MCP stdio transports use AnyIO cancel scopes; close them from the task that opened them.
            await self.close_mcp()

    def _republish_inbound(self, msg: InboundMessage) -> None:
        """Put a message back on the inbound queue without blocking ``run()``.

        ``run()`` is the only reader of that queue, so awaiting a put on a full
        queue from inside it would wait forever.
        """
        inbound = getattr(self.bus, "inbound", None)
        if isinstance(inbound, asyncio.Queue):
            try:
                inbound.put_nowait(msg)
                return
            except asyncio.QueueFull:
                pass
        task = asyncio.create_task(self.bus.publish_inbound(msg))
        self._background_tasks.append(task)
        task.add_done_callback(
            lambda t: t in self._background_tasks and self._background_tasks.remove(t)
        )

    def _parent_turn_gate(self) -> asyncio.Semaphore | None:
        """Gate for top-level conversation turns.

        Parent turns of different conversations always start right away: the
        plan / machine cap on concurrent agents applies to subagents only.
        Gating parent turns made the 3rd or 4th busy conversation sit on
        "preparing the first tokens" until another finished. An operator can
        still cap them explicitly with NAVIN_MAX_CONCURRENT_REQUESTS.
        """
        if os.environ.get("NAVIN_MAX_CONCURRENT_REQUESTS") is None:
            return None
        return self._concurrency_gate

    # After this long on a held session lock the user is told what is running.
    _SESSION_LOCK_NOTICE_S = 30.0

    @asynccontextmanager
    async def _session_turn_lock(
        self, lock: asyncio.Lock, msg: InboundMessage, session_key: str
    ) -> AsyncIterator[None]:
        """Hold the per-session lock, telling the user when the wait is long.

        A turn of the same chat that never finished used to leave the next
        message on a silent spinner for as long as it lasted (34 minutes on a
        plain "hello"): the model was never called, so no stream timeout could
        fire. After ``_SESSION_LOCK_NOTICE_S`` the UI gets one status line
        naming the turn still running and the log records it every window.
        The wait itself is never cut: the session stays strictly serial.
        """
        waited = 0.0
        notified = False
        while True:
            try:
                await asyncio.wait_for(lock.acquire(), timeout=self._SESSION_LOCK_NOTICE_S)
                break
            except asyncio.TimeoutError:
                waited += self._SESSION_LOCK_NOTICE_S
                holder = self._session_lock_holders.get(session_key)
                holder_preview = holder[0] if holder else "unknown turn"
                holder_age = time.monotonic() - holder[1] if holder else waited
                logger.warning(
                    "Session {} still locked after {:.0f}s by a turn running for {:.0f}s: {!r}",
                    session_key, waited, holder_age, holder_preview,
                )
                if notified:
                    continue
                notified = True
                try:
                    callback = await self._build_retry_wait_callback(msg)
                    await callback(
                        "Waiting for the previous turn of this chat to finish "
                        f"(running for {max(1, round(holder_age / 60))} min: "
                        f"{holder_preview!r}). Use /stop to interrupt it."
                    )
                except Exception:
                    logger.debug("session lock wait notice failed", exc_info=True)
        preview = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
        self._session_lock_holders[session_key] = (preview, time.monotonic())
        try:
            yield
        finally:
            self._session_lock_holders.pop(session_key, None)
            lock.release()

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = AgentLoop._parent_turn_gate(self) or nullcontext()

        pending: asyncio.Queue | None = None
        turn_cancelled = False
        dispatch_skipped = False
        try:
            async with self._session_turn_lock(lock, msg, session_key), gate:
                if not self.turn_recovery.matches(msg, session_key):
                    dispatch_skipped = True
                    return
                # Only the task that owns the session lock may publish the
                # active mid-turn injection queue for this session.
                # One full subagent wave (AgentDefaults.max_concurrent_subagents,
                # itself bounded at 1000) plus room for the user typing during it.
                # Overflow is not lost, it falls back to a queued task, but that
                # costs the in-order mid-turn injection.
                pending = asyncio.Queue(maxsize=1056)
                self._pending_queues[session_key] = pending
                try:
                    on_stream = on_stream_end = None
                    if msg.metadata.get("_wants_stream"):
                        # Split one answer into distinct stream segments.
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def on_stream(delta: str) -> None:
                            await self.bus.publish_outbound(
                                outbound_message_for_event(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    event=StreamDeltaEvent(
                                        content=delta,
                                        stream_id=_current_stream_id(),
                                    ),
                                    metadata=msg.metadata,
                                )
                            )

                        async def on_stream_end(
                            *, resuming: bool = False, discarded: bool = False,
                        ) -> None:
                            nonlocal stream_segment
                            await self.bus.publish_outbound(
                                outbound_message_for_event(
                                    channel=msg.channel,
                                    chat_id=msg.chat_id,
                                    event=StreamEndEvent(
                                        stream_id=_current_stream_id(),
                                        resuming=resuming,
                                        discarded=discarded,
                                    ),
                                    metadata=msg.metadata,
                                )
                            )
                            stream_segment += 1

                    # A scheduled turn charges its tokens to the loop that
                    # triggered it, which only this message's metadata knows.
                    with crediting_job(cron_job_id(msg.metadata)):
                        response = await self._process_message(
                            msg, on_stream=on_stream, on_stream_end=on_stream_end,
                            pending_queue=pending,
                        )
                    completed_channel = msg.channel
                    completed_chat_id = msg.chat_id
                    if response is not None:
                        await self.bus.publish_outbound(response)
                        completed_channel = response.channel
                        completed_chat_id = response.chat_id
                        # Light auto-detect: ```html / ```mermaid fences become
                        # Artifact Canvas entries without requiring present_artifact.
                        if (
                            msg.channel == "websocket"
                            and isinstance(response.content, str)
                            and "```" in response.content
                        ):
                            try:
                                from navin.agent.tools.present_artifact import (
                                    present_detected_artifacts,
                                )

                                present_detected_artifacts(
                                    chat_id=msg.chat_id,
                                    text=response.content,
                                    bus=self.bus,
                                )
                            except Exception:
                                logger.debug(
                                    "artifact auto-detect skipped for {}",
                                    msg.chat_id,
                                )
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                    continuing = (
                        turn_continuation.internal_continuation_pending(msg.metadata)
                        or self.turn_recovery.waiting(session_key)
                    )
                    if not continuing:
                        await self._runtime_events().turn_completed(
                            channel=completed_channel,
                            chat_id=completed_chat_id,
                            session_key=session_key,
                            metadata=msg.metadata,
                        )
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, response=response)
                except asyncio.CancelledError:
                    turn_cancelled = True
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=asyncio.CancelledError())
                    logger.info("Task cancelled for session {}", session_key)
                    # Preserve partial context from the interrupted turn so
                    # the user does not lose tool results and assistant
                    # messages accumulated before /stop.  The checkpoint was
                    # already persisted to session metadata by
                    # _emit_checkpoint during tool execution; materializing
                    # it into session history now makes it visible in the
                    # next conversation turn.
                    try:
                        key = self._effective_session_key(msg)
                        session = self.sessions.get_or_create(key)
                        if self._restore_runtime_checkpoint(session):
                            self._clear_pending_user_turn(session)
                            await self.sessions.save_async(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    # A stopped turn is a finished turn for the UI: without this
                    # frame the WebUI kept its Working state from the last tool
                    # event that arrived after Stop, so the composer queued the
                    # next message instead of sending it, and the queue never
                    # drained because no turn_end ever came.
                    try:
                        await asyncio.shield(
                            self._runtime_events().turn_completed(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                session_key=session_key,
                                metadata=msg.metadata,
                            )
                        )
                    except Exception:
                        logger.debug(
                            "Could not publish turn_end for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                except Exception as exc:
                    logger.exception("Error processing message for session {}", session_key)
                    session = self.sessions.get_or_create(session_key)
                    transient = LLMProvider._is_transient_response(
                        LLMProvider._unexpected_error_response(exc)
                    )
                    if transient and self.turn_recovery.schedule(session) is not None:
                        callback = await self._build_retry_wait_callback(msg)
                        await callback("Connection interrupted. Progress saved; automatic retry pending.")
                        return
                    self.turn_recovery.finish(session)
                    # The user saw the error: a restart must not replay it.
                    self._settle_pending_user_turn(session)
                    await self.sessions.save_async(session)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
                    if not turn_continuation.internal_continuation_pending(msg.metadata):
                        await self._runtime_events().turn_completed(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            session_key=session_key,
                            metadata=msg.metadata,
                        )
                    for _, coordinator in self._automation_turn_coordinators:
                        coordinator.complete(msg, error=exc)
                except BaseException as exc:
                    # PyO3 PanicException is BaseException, not Exception. Without
                    # this the UI stays on "Réflexion…" with no turn_end.
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    logger.exception(
                        "Fatal error processing message for session {}", session_key
                    )
                    session = self.sessions.get_or_create(session_key)
                    self.turn_recovery.finish(session)
                    self._settle_pending_user_turn(session)
                    await self.sessions.save_async(session)
                    try:
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="Sorry, I encountered an error.",
                        ))
                        if not turn_continuation.internal_continuation_pending(msg.metadata):
                            await self._runtime_events().turn_completed(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                session_key=session_key,
                                metadata=msg.metadata,
                            )
                    except Exception:
                        logger.exception(
                            "Failed to publish error/turn_end for session {}",
                            session_key,
                        )
                    for _, coordinator in self._automation_turn_coordinators:
                        try:
                            coordinator.complete(msg, error=exc)
                        except Exception:
                            pass
                finally:
                    # Drain any messages still in the pending queue and re-publish
                    # them to the bus so they are processed as fresh inbound messages
                    # rather than silently lost.  Only remove our own queue; a
                    # later task waiting on the lock must not be able to steal
                    # cleanup ownership.
                    queue = None
                    if self._pending_queues.get(session_key) is pending:
                        queue = self._pending_queues.pop(session_key, None)
                    else:
                        queue = pending
                    if queue is not None:
                        leftover = 0
                        dropped = 0
                        while True:
                            try:
                                item = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break
                            # A cancelled turn means the user hit /stop: internal
                            # goal continuations die with the turn, but messages
                            # the user typed meanwhile are kept and replayed in
                            # order as the next turn(s).
                            if turn_cancelled and (
                                turn_continuation.internal_continuation_inbound(
                                    getattr(item, "metadata", None)
                                )
                                or (getattr(item, "metadata", None) or {}).get("injected_event")
                                or getattr(item, "sender_id", "") == "subagent"
                            ):
                                dropped += 1
                                continue
                            await self.bus.publish_inbound(item)
                            leftover += 1
                        if leftover:
                            logger.info(
                                "Re-published {} leftover message(s) to bus for session {}",
                                leftover, session_key,
                            )
                        if dropped:
                            logger.info(
                                "Dropped {} queued message(s) after cancelled turn for session {}",
                                dropped, session_key,
                            )
                    if turn_cancelled:
                        self._loop_guards.pop(session_key, None)
                    if (
                        not turn_continuation.internal_continuation_pending(msg.metadata)
                        and not self.turn_recovery.waiting(session_key)
                    ):
                        self._loop_guards.pop(session_key, None)
                        await self._runtime_events().run_status_changed(
                            msg, session_key, "idle"
                        )
                        self._runtime_events().clear_turn(session_key)
                    await self._publish_next_deferred_automation_turn(session_key)
        finally:
            if pending is None and not dispatch_skipped:
                await self._runtime_events().run_status_changed(
                    msg, session_key, "idle"
                )
                self._runtime_events().clear_turn(session_key)
                await self._publish_next_deferred_automation_turn(session_key)

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        await agent_context.close_mcp(self)

    def _maybe_check_idle_sessions(self) -> None:
        """Idle-session archival check: once a minute, listing sessions off the loop.

        It ran every second the inbound queue was quiet and listed every
        session file on the event loop, which froze the UI and the reply stream
        for seconds under WSL. The TTL is in minutes, so once a minute is plenty.
        """
        now = time.monotonic()
        last = getattr(self, "_idle_check_at", 0.0)
        task = getattr(self, "_idle_check_task", None)
        if now - last < _IDLE_SESSION_CHECK_S or (task is not None and not task.done()):
            return
        self._idle_check_at = now
        active = self._busy_session_keys()
        keeper = self.heap_keeper
        if keeper is not None:
            keeper.tick(idle=not active)

        async def _check() -> None:
            try:
                keys = await asyncio.to_thread(self.auto_compact.expired_candidates, active)
                # The listing took a while: a turn may have started since.
                busy = self._busy_session_keys()
                for key in keys:
                    if key in busy:
                        continue
                    # Loading the session to judge its tail parses the whole
                    # file: off the loop like the listing.
                    if not await asyncio.to_thread(self.auto_compact.check_compactable, key):
                        continue
                    if key in self._busy_session_keys():
                        continue
                    self.auto_compact.archive_if_compactable(
                        key, self._schedule_background, self.llm_runtime, checked=True,
                    )
            except Exception:
                logger.exception("Idle session check failed")

        self._idle_check_task = asyncio.create_task(_check())

    # The end-of-turn wait for a subagent result: how long at most, and how
    # often it checks that a result is still owed at all.
    _OWED_RESULT_WAIT_S = 300.0
    _OWED_RESULT_POLL_S = 0.5

    def _owed_subagent_results(self, session_key: str) -> int:
        """Subagent results this conversation still waits for, queued spawns included."""
        counter = getattr(self.subagents, "pending_result_count", None)
        if not callable(counter):
            counter = self.subagents.get_running_count_by_session
        return counter(session_key)

    async def _wait_for_owed_result(
        self,
        session_key: str,
        queue: asyncio.Queue,
        *,
        timeout: float | None = None,
        poll: float | None = None,
    ) -> InboundMessage | None:
        """Next follow-up while a subagent result is still owed, or None.

        Rechecked at every poll. One blind five-minute wait used to start
        whenever a subagent task was still alive, even when its result had
        already reached the model (an earlier drain took it): the turn then
        sat for nothing. Once nothing is owed, one more poll is still given to
        a result in flight from the bus to this queue, so none is missed.
        """
        deadline = time.monotonic() + (self._OWED_RESULT_WAIT_S if timeout is None else timeout)
        step = self._OWED_RESULT_POLL_S if poll is None else poll
        idle_polls = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Timeout waiting for sub-agent completion in session {}", session_key,
                )
                return None
            try:
                return await asyncio.wait_for(queue.get(), timeout=min(step, remaining))
            except asyncio.TimeoutError:
                if queue.empty() and self._owed_subagent_results(session_key) <= 0:
                    idle_polls += 1
                    if idle_polls >= 2:
                        return None
                else:
                    idle_polls = 0

    @staticmethod
    def _live_background_commands(session_key: str) -> bool:
        """A background command of this conversation (or of no known one) still runs."""
        from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

        return any(
            not row.owner_session_key or row.owner_session_key == session_key
            for row in DEFAULT_EXEC_SESSION_MANAGER.running_snapshot()
        )

    def _busy_session_keys(self) -> set[str]:
        """Sessions that are not idle: a turn, its lock or a subagent is live.

        A chat whose Multitask subagents are still working looks idle by its
        timestamp; compacting it then truncated the history their results
        were about to land in.
        """
        busy = set(self._pending_queues)
        busy.update(
            key for key, tasks in self._active_tasks.items()
            if any(not task.done() for task in tasks)
        )
        busy.update(key for key, lock in self._session_locks.items() if lock.locked())
        subagents = getattr(self, "subagents", None)
        busy.update(getattr(subagents, "_session_tasks", None) or ())
        busy.update(
            key for key, queue in (getattr(subagents, "_queued", None) or {}).items() if queue
        )
        return busy

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_system_message(
        self,
        msg: InboundMessage,
        *,
        runtime: LLMRuntime,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
    ) -> OutboundMessage | None:
        """Process a system inbound message (e.g. subagent announce)."""
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        self._runtime_events().record_turn_runtime(key, runtime)
        if self._restore_runtime_checkpoint(session):
            await self.sessions.save_async(session)
        if self._restore_pending_user_turn(session, superseded=False):
            await self.sessions.save_async(session)

        session, pending = self.auto_compact.prepare_session(session, key)
        if pending:
            logger.info("Memory compact triggered for session {}", key)

        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            runtime=runtime,
            replay_max_messages=replay_max_messages_for_context(
                runtime.context_window_tokens
            ),
        )
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and self._persist_subagent_followup(session, msg):
            logger.debug("Subagent result persisted for session {}", key)
            await self.sessions.save_async(session)
        elif is_subagent and self._subagent_result_already_answered(session, msg):
            # A result the model already answered (a leftover re-published
            # after its turn, a second announcement). A turn on nothing new
            # made the model repeat its previous answer to the user.
            logger.info(
                "Subagent result {} already answered in session {}; no new turn",
                (msg.metadata or {}).get("subagent_task_id") or "(no id)", key,
            )
            return None
        # The result replays as the last user turn (Session.get_history): a
        # trailing assistant turn is dropped by every provider, so the model
        # would never have seen it.
        current_role = "user"
        _hist_kwargs: dict[str, Any] = {
            "max_messages": replay_max_messages_for_context(runtime.context_window_tokens),
            "max_tokens": self._replay_token_budget(runtime),
            "extend_to_user": is_subagent,
            "with_media_refs": True,
            "subagent_results_as_input": True,
        }
        history = replay_history_images(
            session.get_history(**_hist_kwargs), model=runtime.model
        )
        workspace_scope = self.workspace_scopes.for_message(msg, session.metadata)

        from navin.command.modules import metadata_requests_evidence_only

        messages = await asyncio.to_thread(
            self.context.build_messages,
            history=history,
            current_message="" if is_subagent else msg.content,
            channel=channel,
            chat_id=chat_id,
            current_role=current_role,
            sender_id=msg.sender_id,
            session_summary=pending,
            session_metadata=session.metadata,
            workspace=workspace_scope.project_path,
            session_key=key,
            unified_session=self._unified_session,
            evidence_only=metadata_requests_evidence_only(msg.metadata),
        )
        t_wall = time.time()
        final_content, _, all_msgs, stop_reason, _ = await self._run_agent_loop(
            messages, session=session, channel=channel, chat_id=chat_id,
            runtime=runtime,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            original_user_text=None,
            pending_queue=pending_queue,
            hook_factories=hook_factories,
        )
        wall_done = time.time()
        latency_ms = max(0, int((wall_done - t_wall) * 1000))
        if msg.metadata.get("injected_event") == "exec_finished":
            # The announcement of a background command's exit is the model's
            # cue, not a chat turn the user typed; keep it out of the transcript.
            for row in all_msgs[1 + len(history):]:
                if isinstance(row, dict) and row.get("role") == "user":
                    row[HIDDEN_HISTORY_META] = {
                        "kind": "exec_finished",
                        "exec_session_id": str(msg.metadata.get("exec_session_id") or ""),
                    }
                    row["injected_event"] = "exec_finished"
                    break
        self._save_turn(
            session, all_msgs, 1 + len(history), turn_latency_ms=latency_ms,
            model_meta=self._turn_model_meta(msg),
        )
        self._runtime_events().record_turn_latency(key, latency_ms)
        session.enforce_file_cap(
            on_archive=partial(self.context.memory.raw_archive, session_key=key),
            on_earlier=partial(self.sessions.keep_earlier_messages, session),
        )
        self._clear_runtime_checkpoint(session)
        await self.sessions.save_async(session)
        self._schedule_background(
            self.consolidator.maybe_consolidate_by_tokens(
                session,
                runtime=runtime,
                replay_max_messages=replay_max_messages_for_context(
                    runtime.context_window_tokens
                ),
            )
        )
        content = final_content or "Background task completed."
        outbound_metadata: dict[str, Any] = {}
        if channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        if origin_message_id := msg.metadata.get("origin_message_id"):
            outbound_metadata["origin_message_id"] = origin_message_id
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata=outbound_metadata,
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
        ephemeral: bool = False,
        run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        runtime: LLMRuntime | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        if runtime is None:
            runtime = self.runtime_for_inbound(msg)

        if msg.channel == "system":
            return await self._process_system_message(
                msg,
                runtime=runtime,
                session_key=session_key,
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                pending_queue=pending_queue,
                hook_factories=hook_factories,
            )

        key = session_key or msg.session_key
        if not ephemeral and should_inject_into_active_turn(self.commands, msg.content.strip()):
            self.turn_recovery.begin(self.sessions.get_or_create(key), msg, persisted=False)
        t0 = time.time()
        ctx = TurnContext(
            msg=msg,
            session=None,
            session_key=key,
            state=TurnState.RESTORE,
            turn_id=f"{key}:{time.time_ns()}",
            runtime=runtime,
            original_user_text=(
                None
                if turn_continuation.internal_continuation_inbound(msg.metadata)
                else msg.content
            ),
            turn_wall_started_at=t0,
            visible_run_started_at=turn_continuation.internal_continuation_run_started_at(
                msg.metadata,
            ),
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            pending_queue=pending_queue,
            ephemeral=ephemeral,
            run_extra_hooks_for_ephemeral=run_extra_hooks_for_ephemeral,
            hooks=list(hooks or []),
            hook_factories=list(hook_factories or []),
            tools=tools,
        )

        while ctx.state is not TurnState.DONE:
            handler_name = f"_state_{ctx.state.name.lower()}"
            handler = getattr(self, handler_name, None)
            if handler is None:
                raise RuntimeError(f"Missing state handler for {ctx.state}")

            t0 = time.perf_counter()
            try:
                event = await handler(ctx)
            except Exception:
                duration = (time.perf_counter() - t0) * 1000
                ctx.trace.append(
                    StateTraceEntry(
                        state=ctx.state,
                        started_at=t0,
                        duration_ms=duration,
                        event="",
                        error="exception",
                    )
                )
                raise

            duration = (time.perf_counter() - t0) * 1000
            ctx.trace.append(
                StateTraceEntry(
                    state=ctx.state,
                    started_at=t0,
                    duration_ms=duration,
                    event=event,
                )
            )
            logger.debug(
                "[turn {}] State {} took {:.1f}ms -> event {}",
                ctx.turn_id,
                ctx.state.name,
                duration,
                event,
            )

            next_state = self._TRANSITIONS.get((ctx.state, event))
            if next_state is None:
                raise RuntimeError(
                    f"[turn {ctx.turn_id}] No transition from {ctx.state} "
                    f"on event {event!r}"
                )
            ctx.state = next_state

        logger.debug(
            "[turn {}] Turn completed after {} states",
            ctx.turn_id,
            len(ctx.trace),
        )
        return ctx.outbound

    def _turn_model_meta(self, msg: InboundMessage | None = None) -> dict[str, Any]:
        """Model identity for the active turn (history + WebUI labels).

        The turn's own model (pin, task route, vision...) was stamped on the
        inbound message by ``stamp_inbound_runtime``; it wins over the loop
        default, which would otherwise label a Gemini answer with the default
        model's name."""
        stamped = dict(getattr(msg, "metadata", None) or {}) if msg is not None else {}
        if isinstance(stamped.get("model"), str) and stamped["model"].strip():
            out = {
                key: stamped[key]
                for key in ("model", "model_name", "model_preset", "model_label", "task_role", "model_route_role")
                if isinstance(stamped.get(key), str) and stamped[key].strip()
            }
            out.setdefault("model_name", out["model"])
            return out
        out: dict[str, Any] = {}
        model = (self.model or "").strip()
        if model:
            out["model"] = model
            out["model_name"] = model
        preset = self.model_preset
        if preset:
            out["model_preset"] = preset
            configured = self.model_presets.get(preset)
            label = getattr(configured, "label", None) if configured is not None else None
            if isinstance(label, str) and label.strip():
                out["model_label"] = label.strip()
        role = getattr(self, "_active_task_role", None)
        if isinstance(role, str) and role.strip():
            out["task_role"] = role.strip()
            out["model_route_role"] = role.strip()
        return out

    def _assemble_outbound(
        self,
        msg: InboundMessage,
        final_content: str,
        all_msgs: list[dict[str, Any]],
        stop_reason: str,
        had_injections: bool,
        on_stream: Callable[[str], Awaitable[None]] | None,
        *,
        turn_latency_ms: int | None = None,
        final_is_notice: bool = False,
    ) -> OutboundMessage | None:
        """Assemble the final outbound message from turn results."""
        # MessageTool suppression
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        event = None
        meta = dict(msg.metadata or {})
        # Only text the runner actually streamed may be marked as streamed:
        # streaming channels drop such a final. Notices (blank reply with no
        # continuation left, max-iterations fallback, errors) were never
        # streamed, so they must go out as a plain message, exactly once.
        if (
            on_stream is not None
            and not final_is_notice
            and stop_reason not in _UNSTREAMED_FINAL_STOP_REASONS
        ):
            event = StreamedResponseEvent()
        if turn_latency_ms is not None:
            meta["latency_ms"] = int(turn_latency_ms)
        meta.update(self._turn_model_meta(msg))

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            event=event,
            metadata=meta,
        )

    async def _state_restore(self, ctx: TurnContext) -> TurnState:
        """Restore checkpoint / pending user turn; extract documents."""
        msg = ctx.msg

        # Links first: a downloaded video has to exist as a file before
        # _prepare_message_media can sample frames out of it.
        expansion = await expand_media_urls(msg.content, msg.media)
        if expansion.results:
            msg = dataclasses.replace(
                msg, content=expansion.text, media=expansion.media
            )
            ctx.msg = msg

        if msg.media:
            new_content, image_only = await self._prepare_message_media(msg.content, msg.media)
            new_content, image_only, withheld = guard_vision_media(
                new_content, image_only, ctx.runtime.model
            )
            if withheld:
                logger.warning(
                    "Model {} has no image input; {} attachment(s) withheld from the turn",
                    ctx.runtime.model,
                    len(msg.media),
                )
            ctx.msg = dataclasses.replace(msg, content=new_content, media=image_only)
            msg = ctx.msg

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Session is already fetched by the caller (_process_message) but
        # ensure it exists in case this handler is invoked independently.
        if ctx.session is None:
            ctx.session = self.sessions.get_or_create(ctx.session_key)
        # A genuine user message restarts checkpoint numbering. Internal
        # slices retain it so a long run keeps getting coherence checkpoints.
        if not turn_continuation.internal_continuation_inbound(msg.metadata):
            turn_continuation.reset_budget_continuation_rounds(ctx.session.metadata)
            self._loop_guards.pop(ctx.session_key, None)
        self._persist_model_pin(ctx.session, msg)
        await self._runtime_events().session_turn_started(msg, ctx.session_key)
        # Tell the WebUI the turn is live as soon as it is accepted - not only
        # once BUILD finishes and the model call starts. Without this the first
        # message of a new chat can sit silent for a long time with no clock.
        if not turn_continuation.internal_continuation_inbound(msg.metadata):
            if ctx.visible_run_started_at is None:
                ctx.visible_run_started_at = time.time()
            await self._runtime_events().run_status_changed(
                msg,
                ctx.session_key,
                "running",
                started_at=ctx.visible_run_started_at,
            )
            # Publishing only queues the frame; RESTORE through BUILD then
            # runs without yielding, so it reached the socket after BUILD.
            # One yield lets the outbound dispatcher send it right away.
            await asyncio.sleep(0)
        await self._maybe_request_product_module(ctx)
        self.workspace_scopes.persist_message_scope(ctx.session, msg)

        if self._restore_runtime_checkpoint(ctx.session):
            await self.sessions.save_async(ctx.session)
        if ctx.msg.metadata.get(RECOVERY_ID_META):
            self._clear_pending_user_turn(ctx.session)
        elif self._restore_pending_user_turn(ctx.session):
            await self.sessions.save_async(ctx.session)

        return "ok"

    async def _maybe_request_product_module(self, ctx: TurnContext) -> None:  # noqa: ARG002
        """Do not yank Tchat onto Code.

        Guessing "this is a coding turn" from the first sentence used to
        navigate the shell to ``#/code``. On a new chat with no project that
        looked like the message had vanished: the thread left Tchat, the Code
        gate asked for a folder, and the user had to pick a project before
        anything answered. New chat stays in Tchat. Code opens when the user
        opens it.
        """
        return

    async def _prepare_message_media(
        self, content: str, media: list[str]
    ) -> tuple[str, list[str]]:
        # Videos first: both branches below only keep image paths, so an mp4
        # that has not become frames by now never reaches the model. The
        # soundtrack note follows the frames note so the model reads "what it
        # sees" then "what it hears" about the same clip.
        videos = [item for item in media if isinstance(item, str) and is_video_path(item)]
        content, media = expand_video_attachments(content, media)
        if videos:
            content = await append_video_soundtracks(content, videos)
        # Audio files become text the same way; nothing downstream can read them.
        content, media = await expand_audio_attachments(content, media)
        if self._should_extract_document_text():
            return extract_documents(content, media)
        return reference_non_image_attachments(content, media)

    def _should_extract_document_text(self) -> bool:
        if self.channels_config is None:
            return True
        return self.channels_config.extract_document_text

    async def _state_compact(self, ctx: TurnContext) -> str:
        ctx.session, pending = self.auto_compact.prepare_session(ctx.session, ctx.session_key)
        ctx.pending_summary = pending
        return "ok"

    async def _state_command(self, ctx: TurnContext) -> str:
        raw = ctx.msg.content.strip()
        _, automation_metadata = automation_history_overrides(ctx.msg.metadata)
        is_user_turn = (
            ctx.original_user_text is not None
            and not automation_metadata
            and ctx.msg.channel != "system"
            and ctx.msg.sender_id != "subagent"
        )
        cmd_ctx = CommandContext(
            msg=ctx.msg,
            session=ctx.session,
            key=ctx.session_key,
            raw=raw,
            loop=self,
            runtime=ctx.runtime,
            is_user_turn=is_user_turn,
            turn_scopes=ctx.turn_scopes,
        )
        result = await self.commands.dispatch(cmd_ctx)
        if result is None and is_user_turn:
            result = await self._apply_user_extensions(ctx, raw)
        if result is not None:
            ctx.outbound = result
            # Shortcut commands skip BUILD and SAVE, so we must persist the
            # turn here so WebUI history hydration after _turn_end sees the
            # message.  Mark messages with _command so get_history can filter
            # them out of LLM context.  /new is excluded because it
            # intentionally clears the session.
            if cmd_ctx.raw.lower() != "/new":
                ctx.user_persisted_early = self._persist_user_message_early(
                    ctx.msg, ctx.session, _command=True
                )
                ctx.session.add_message(
                    "assistant", result.content, _command=True
                )
                self._clear_pending_user_turn(ctx.session)
                self.turn_recovery.finish(ctx.session)
                await self.sessions.save_async(ctx.session)
            return "shortcut"
        return "dispatch"

    async def _apply_user_extensions(self, ctx: TurnContext, raw: str) -> OutboundMessage | None:
        """Markdown slash commands, then SessionStart / UserPromptSubmit hooks.

        Built-in router commands always win: a custom command is expanded only
        when no built-in handles the text. Returns a reply when a hook refuses
        the message, else None after rewriting ``ctx.msg`` in place.
        """
        from navin.agent.custom_commands import build_command_turn, find_custom_command
        from navin.agent.user_hooks import prompt_hooks

        session_meta = ctx.session.metadata if ctx.session is not None else {}
        try:
            workspace = self.workspace_scopes.for_message(ctx.msg, session_meta).project_path
        except Exception:  # noqa: BLE001
            workspace = self.workspace
        workspace = Path(workspace) if workspace else None
        if raw.startswith("/") and not self.commands.is_dispatchable_command(raw):
            token, _, args = raw.partition(" ")
            command = await asyncio.to_thread(find_custom_command, workspace, token)
            if command is not None:
                content, meta = await asyncio.to_thread(build_command_turn, command, args, workspace)
                ctx.msg.content = content
                ctx.msg.metadata = {**dict(ctx.msg.metadata or {}), **meta, "original_content": raw}
        history = ctx.session.messages if ctx.session is not None else []
        new_session = not any(m.get("role") == "user" for m in history)
        outcome = await prompt_hooks(workspace, ctx.session_key, ctx.msg.content, new_session=new_session)
        notices = [m for m in outcome.system_messages if m]
        if outcome.blocked:
            content = f"Message refused by a UserPromptSubmit hook: {outcome.reason}"
            if notices:
                content += "\n\n" + "\n".join(notices)
            return OutboundMessage(
                channel=ctx.msg.channel,
                chat_id=ctx.msg.chat_id,
                content=content,
            )
        if notices:
            # Shown once on_progress exists (_state_build), never sent to the model.
            ctx.msg.metadata = {**dict(ctx.msg.metadata or {}), "hook_system_messages": notices}
        if outcome.additional_context:
            ctx.msg.content = f"{ctx.msg.content}\n\n[Hook context]\n{outcome.additional_context}"
        return None

    async def _state_build(self, ctx: TurnContext) -> str:
        from navin.session.context_usage_meta import persist_last_preload_skills

        persist_last_preload_skills(
            ctx.session.metadata,
            self._preload_skills_for_message(ctx.msg),
        )
        replay_max_messages = replay_max_messages_for_context(
            ctx.runtime.context_window_tokens
        )
        if not ctx.ephemeral:
            if ctx.on_progress is None:
                ctx.on_progress = await self._build_bus_progress_callback(ctx.msg)
            on_progress = ctx.on_progress

            async def _compacting_notice() -> None:
                from navin.utils.progress_events import invoke_on_progress
                from navin.utils.task_progress import build_agent_ui_blob

                await invoke_on_progress(
                    on_progress, "", tool_hint=False,
                    agent_ui=build_agent_ui_blob(
                        {
                            "label": "Compacting context…",
                            "indeterminate": True,
                            "phase": "compacting",
                        }
                    ),
                )

            # Only what the prompt cannot fit without: proactive archiving
            # already runs after each turn (_state_save), and its model calls
            # here used to hold the first token for 20-45s.
            await self.consolidator.maybe_consolidate_by_tokens(
                ctx.session,
                runtime=ctx.runtime,
                replay_max_messages=replay_max_messages,
                urgent_only=True,
                on_start=_compacting_notice if on_progress else None,
            )
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        _hist_kwargs: dict[str, Any] = {
            "max_messages": replay_max_messages,
            "max_tokens": self._replay_token_budget(ctx.runtime),
            "extend_to_user": False,
            "with_media_refs": True,
            "subagent_results_as_input": True,
        }
        ctx.history = replay_history_images(
            ctx.session.get_history(**_hist_kwargs), model=ctx.runtime.model
        )
        self._runtime_events().record_turn_runtime(
            ctx.session_key,
            ctx.runtime,
        )

        ctx.request_context = self._request_context_for_turn(ctx)
        ctx.runtime_context_blocks = self._drop_repeated_runtime_context(
            await self._resolve_runtime_context_for_turn(ctx), ctx.history
        )
        ctx.initial_messages = await asyncio.to_thread(
            self._build_initial_messages,
            ctx.msg,
            ctx.session,
            ctx.history,
            ctx.pending_summary,
            include_memory_recent_history=not ctx.ephemeral,
            runtime_context_blocks=ctx.runtime_context_blocks,
        )
        ctx.user_persisted_early = self._persist_user_message_early(
            ctx.msg,
            ctx.session,
            runtime_context_blocks=ctx.runtime_context_blocks,
        )
        if not ctx.ephemeral:
            self.turn_recovery.begin(ctx.session, ctx.msg)

        if ctx.on_progress is None:
            ctx.on_progress = await self._build_bus_progress_callback(ctx.msg)
        await announce_runtime_context(ctx.on_progress, ctx.runtime_context_blocks, ctx.turn_id)
        hook_notices = (ctx.msg.metadata or {}).get("hook_system_messages")
        if hook_notices:
            from navin.agent.user_hooks import announce_hook_messages

            await announce_hook_messages(ctx.on_progress, list(hook_notices), ctx.turn_id)
        if ctx.on_retry_wait is None:
            ctx.on_retry_wait = await self._build_retry_wait_callback(ctx.msg)
        if ctx.msg.metadata.get(RECOVERY_ID_META) is not None:
            # A recovered turn is running again: retire the "retrying" warning
            # the previous attempt left in the notification centre instead of
            # letting it outlive the recovery.
            await self.bus.publish_outbound(
                outbound_message_for_event(
                    channel=ctx.msg.channel,
                    chat_id=ctx.msg.chat_id,
                    event=NotificationEvent(
                        title="Connection restored",
                        level="success",
                        key=f"retry:{ctx.msg.chat_id}",
                        clear=True,
                    ),
                    metadata=ctx.msg.metadata,
                )
            )

        return "ok"

    async def _state_run(self, ctx: TurnContext) -> str:
        if ctx.visible_run_started_at is None:
            ctx.visible_run_started_at = time.time()
        await self._runtime_events().run_status_changed(
            ctx.msg,
            ctx.session_key,
            "running",
            started_at=ctx.visible_run_started_at,
        )
        result = await self._run_agent_loop(
            ctx.initial_messages,
            runtime=ctx.runtime,
            on_progress=ctx.on_progress,
            on_stream=ctx.on_stream,
            on_stream_end=ctx.on_stream_end,
            on_retry_wait=ctx.on_retry_wait,
            session=ctx.session,
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            message_id=ctx.msg.metadata.get("message_id"),
            metadata=ctx.msg.metadata,
            session_key=ctx.session_key,
            original_user_text=ctx.original_user_text,
            pending_queue=ctx.pending_queue,
            ephemeral=ctx.ephemeral,
            run_extra_hooks_for_ephemeral=ctx.run_extra_hooks_for_ephemeral,
            hooks=ctx.hooks,
            hook_factories=ctx.hook_factories,
            turn_scopes=ctx.turn_scopes,
            tools=ctx.tools,
            request_context=ctx.request_context,
        )
        final_content, tools_used, all_msgs, stop_reason, had_injections = result
        ctx.final_content = final_content
        ctx.tools_used = tools_used
        ctx.all_messages = all_msgs
        ctx.stop_reason = stop_reason
        ctx.had_injections = had_injections
        if self.turn_recovery.waiting(ctx.session_key):
            ctx.suppress_response = True
            return "ok"
        await turn_continuation.maybe_continue_turn(ctx)
        return "ok"

    async def _state_save(self, ctx: TurnContext) -> str:
        turn_continuation.prepare_save_boundary(ctx)

        if (
            (ctx.final_content is None or not ctx.final_content.strip())
            and not ctx.suppress_response
        ):
            ctx.final_content = session_empty_final_response_message(ctx.session_key)
            ctx.final_is_notice = True

        latency_started_at = (
            ctx.visible_run_started_at
            if turn_continuation.internal_continuation_inbound(ctx.msg.metadata)
            and ctx.visible_run_started_at is not None
            else ctx.turn_wall_started_at
        )
        ctx.turn_latency_ms = max(0, int((time.time() - latency_started_at) * 1000))
        self._save_turn(
            ctx.session, ctx.all_messages, ctx.save_skip,
            turn_latency_ms=ctx.turn_latency_ms,
            model_meta=self._turn_model_meta(ctx.msg),
        )
        self._runtime_events().record_turn_latency(
            ctx.session_key,
            ctx.turn_latency_ms,
        )
        if not ctx.ephemeral:
            ctx.session.enforce_file_cap(
                on_archive=partial(self.context.memory.raw_archive, session_key=ctx.session_key),
                on_earlier=partial(self.sessions.keep_earlier_messages, ctx.session),
            )
            self._schedule_background(
                self.consolidator.maybe_consolidate_by_tokens(
                    ctx.session,
                    runtime=ctx.runtime,
                    replay_max_messages=replay_max_messages_for_context(
                        ctx.runtime.context_window_tokens
                    ),
                )
            )
        self._clear_pending_user_turn(ctx.session)
        self._clear_runtime_checkpoint(ctx.session)
        if (
            not self.turn_recovery.waiting(ctx.session_key)
            and not turn_continuation.internal_continuation_pending(ctx.msg.metadata)
        ):
            self.turn_recovery.finish(ctx.session)
            self._loop_guards.pop(ctx.session_key, None)
        # Threaded write that still lands: save_async falls back to a
        # synchronous save if the worker fails, unlike a checkpoint.
        await self.sessions.save_async(ctx.session)
        return "ok"

    async def _state_respond(self, ctx: TurnContext) -> str:
        # RESTORE..SAVE are complete in the trace here; publish the per-phase
        # wall times so the WebUI and transcripts can show where a turn went.
        phase_timings = self._phase_timings_ms(ctx)
        if phase_timings:
            self._runtime_events().record_turn_phase_timings(
                ctx.session_key,
                phase_timings,
            )
        if ctx.suppress_response:
            ctx.outbound = None
            return "ok"
        ctx.outbound = self._assemble_outbound(
            ctx.msg,
            ctx.final_content,
            ctx.all_messages,
            ctx.stop_reason,
            ctx.had_injections,
            ctx.on_stream,
            turn_latency_ms=ctx.turn_latency_ms,
            final_is_notice=ctx.final_is_notice,
        )
        if ctx.outbound is not None and phase_timings:
            ctx.outbound.metadata["phase_timings_ms"] = phase_timings
        if ctx.ephemeral and ctx.outbound is not None:
            ctx.outbound.metadata["_stop_reason"] = ctx.stop_reason
        return "ok"

    @staticmethod
    def _phase_timings_ms(ctx: TurnContext) -> dict[str, int]:
        """Aggregate the state trace into ``{phase: wall_ms}``.

        A phase can run more than once in a turn (e.g. COMMAND dispatch), so
        durations are summed per state name.
        """
        timings: dict[str, int] = {}
        for entry in ctx.trace:
            name = entry.state.name.lower()
            timings[name] = timings.get(name, 0) + int(entry.duration_ms)
        return timings

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        should_truncate_text: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(
        self,
        session: Session,
        messages: list[dict],
        skip: int,
        *,
        turn_latency_ms: int | None = None,
        model_meta: dict[str, Any] | None = None,
    ) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime

        declared_tool_call_ids = {
            str(tc["id"])
            for m in session.messages
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls") or []
            if isinstance(tc, dict) and tc.get("id")
        }
        last_assistant_idx: int | None = None
        for m in messages[skip:]:
            entry = dict(m)
            internal_meta = entry.pop("_meta", None)
            runtime_context_meta = (
                internal_meta.get(RUNTIME_CONTEXT_MESSAGE_META)
                if isinstance(internal_meta, dict)
                else None
            )
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages - they poison session context
            if role == "tool":
                tool_call_id = entry.get("tool_call_id")
                if not tool_call_id or str(tool_call_id) not in declared_tool_call_ids:
                    # Undeclared tool results corrupt future provider requests.
                    logger.warning(
                        "Dropping orphaned tool result {} from session {} during persistence",
                        tool_call_id or "(missing id)",
                        session.key,
                    )
                    continue
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        # Preserve the tool_call/result pair after block filtering.
                        filtered = [
                            {"type": "text", "text": "[tool result omitted during persistence]"}
                        ]
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content)
                    if not filtered:
                        continue
                    entry["content"] = filtered
                if isinstance(runtime_context_meta, dict):
                    entry[RUNTIME_CONTEXT_HISTORY_META] = runtime_context_meta
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
            if role == "assistant":
                last_assistant_idx = len(session.messages) - 1
                declared_tool_call_ids.update(
                    str(tc["id"])
                    for tc in entry.get("tool_calls") or []
                    if isinstance(tc, dict) and tc.get("id")
                )
        if turn_latency_ms is not None and last_assistant_idx is not None:
            session.messages[last_assistant_idx]["latency_ms"] = int(turn_latency_ms)
        if last_assistant_idx is not None:
            for key, value in (model_meta or self._turn_model_meta()).items():
                session.messages[last_assistant_idx][key] = value
            # Persist turn usage next to the model id so Settings can attribute
            # requests/tokens per model (and rebuild from history later).
            usage = getattr(self, "_last_usage", None)
            if isinstance(usage, dict):
                prompt = int(usage.get("prompt_tokens") or 0)
                completion = int(usage.get("completion_tokens") or 0)
                total = int(usage.get("total_tokens") or 0) or (prompt + completion)
                if total > 0 or prompt > 0 or completion > 0:
                    session.messages[last_assistant_idx]["usage"] = {
                        "prompt_tokens": max(0, prompt),
                        "completion_tokens": max(0, completion),
                        "cached_tokens": max(0, int(usage.get("cached_tokens") or 0)),
                        "total_tokens": max(0, total),
                    }
        session.updated_at = datetime.now()

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups before prompt assembly so history stays durable.

        Returns True if a new entry was appended; False if the follow-up was
        deduped (same ``subagent_task_id`` already in session) or carries no
        content worth persisting.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    @staticmethod
    def _subagent_result_already_answered(session: Session, msg: InboundMessage) -> bool:
        """True when a subagent result brings nothing new to the model.

        Empty, or already in the history (in either persisted shape) with a
        model answer after it. A stored result with no answer yet still gets
        its turn: the model has not seen it.
        """
        if not msg.content:
            return True
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if not task_id:
            return False
        messages = session.messages
        for index in range(len(messages) - 1, -1, -1):
            row = messages[index]
            if row.get("injected_event") != "subagent_result" or row.get("subagent_task_id") != task_id:
                continue
            return any(
                later.get("role") == "assistant"
                and later.get("injected_event") != "subagent_result"
                and (later.get("content") or later.get("tool_calls"))
                for later in messages[index + 1:]
            )
        return False

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata.

        Saving rewrites the whole session file, and the runner checkpoints two
        to three times per tool iteration, so an unthrottled save made a long
        turn quadratic in transcript length. The payload is always kept in
        memory; only the write to disk is rate limited. Crash recovery is the
        sole consumer of a mid-turn checkpoint, and the end of a turn saves
        unconditionally, so the worst case is losing the last few seconds of
        tool progress after a hard kill.
        """
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        now = time.monotonic()
        last = self._runtime_checkpoint_saved_at.get(session.key, 0.0)
        if now - last < self._RUNTIME_CHECKPOINT_MIN_SAVE_INTERVAL_S:
            return
        self._runtime_checkpoint_saved_at[session.key] = now
        # Written from a worker thread: the JSON of a long transcript took
        # 20-40 ms on the event loop at every step. Writes stay ordered, so
        # the end-of-turn save always lands last.
        try:
            task = self.sessions.save_in_background(session)
        except RuntimeError:  # no running event loop (sync callers, tests)
            self.sessions.save(session)
            return
        if task is not None:
            self._background_saves.add(task)
            task.add_done_callback(self._background_saves.discard)

    def _mark_pending_user_turn(self, session: Session, msg: InboundMessage | None = None) -> None:
        """Remember an accepted request until its turn ends.

        The marker keeps what a restart needs to resume the request itself
        (the user is never asked to send it again).
        """
        marker: dict[str, Any] = {"at": time.time()}
        if msg is not None:
            meta = msg.metadata or {}
            resumable = msg.channel != "system" and not meta.get("heartbeat")
            marker["resumable"] = resumable
            if resumable:
                marker["request"] = portable_request(msg)
        session.metadata[self._PENDING_USER_TURN_KEY] = marker

    def _settle_pending_user_turn(self, session: Session) -> None:
        """Keep the marker for history repair but never resume this request."""
        marker = session.metadata.get(self._PENDING_USER_TURN_KEY)
        if marker:
            session.metadata[self._PENDING_USER_TURN_KEY] = {
                **(marker if isinstance(marker, dict) else {}), "resumable": False,
            }

    def _adopt_interrupted_turns(self) -> list[str]:
        """Startup: track chat-app requests cut by a restart so they resume.

        Desktop/CLI requests already carry a recovery record. A request from
        another channel only left the pending-turn marker; turn it into a
        recovery record (user message already in history) unless the user
        stopped it, it failed for good, or it is too old.
        """
        adopted: list[str] = []
        now = time.time()
        for row in self.sessions.list_sessions():
            key = row.get("key") if isinstance(row, dict) else None
            session = self.sessions.peek(key) if key else None
            if session is None:
                continue
            marker = session.metadata.get(self._PENDING_USER_TURN_KEY)
            if not isinstance(marker, dict) or RECOVERY_KEY in session.metadata:
                continue
            request = marker.get("request")
            at = marker.get("at")
            if (
                not marker.get("resumable")
                or marker.get("stopped")
                or not isinstance(request, dict)
                or request.get("channel") in {"websocket", "cli", "system"}
                or not isinstance(at, int | float)
                or now - float(at) > MAX_RECOVERY_AGE_S
                or not session.messages
            ):
                continue
            if self.turn_recovery.adopt(session, request, started_at=float(at)):
                adopted.append(key)
        return adopted

    def _start_auto_update(self) -> None:
        """Register what the update must wait for, then start its cycle.

        A restart for an update waits until no turn, lock or subagent of this
        loop is live and no background command runs (navin/update/activity).
        """
        from navin.agent.tool_demand import exec_sessions_are_open
        from navin.update import activity
        from navin.update.auto import start_auto_updater

        activity.register_busy_probe("agents", lambda: len(self._busy_session_keys()))
        activity.register_busy_probe("subagents", self.subagents.get_running_count)
        activity.register_busy_probe("commands", lambda: int(exec_sessions_are_open()))
        start_auto_updater(save_sessions=self.save_cached_sessions)

    def save_cached_sessions(self) -> int:
        """Write every session held in memory, before a restart. Returns the count."""
        saved = 0
        for session in self.sessions.cached_sessions():
            self.sessions.save(session, fsync=True)
            saved += 1
        return saved

    _INTERRUPTED_TURN_NOTE = "This request was interrupted by a restart before it finished."
    _STOPPED_TURN_NOTE = "Stopped before a reply was produced."
    _CUT_TWICE_TURN_NOTE = (
        "This task stopped twice in a row, so it was not resumed automatically. "
        "Send a message to continue it."
    )

    def _close_interrupted_turn(self, session: Session, *, cut_twice: bool = False) -> None:
        """Answer a request that will not be resumed with a short neutral note.

        ``cut_twice``: the turn was already resumed once after a restart and
        cut again. The note is always written then, even after tool results,
        so the user sees why the work did not pick up on its own.
        """
        from datetime import datetime

        marker = session.metadata.get(self._PENDING_USER_TURN_KEY)
        stopped = isinstance(marker, dict) and bool(marker.get("stopped"))
        if cut_twice and session.messages and not stopped:
            session.messages.append(
                {
                    "role": "assistant",
                    "content": self._CUT_TWICE_TURN_NOTE,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()
        elif session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": self._STOPPED_TURN_NOTE if stopped else self._INTERRUPTED_TURN_NOTE,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()
        self._clear_pending_user_turn(session)

    def _clear_pending_user_turn(self, session: Session) -> None:
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        # The next turn must be free to checkpoint immediately.
        self._runtime_checkpoint_saved_at.pop(session.key, None)
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": (
                        "Error: Task interrupted before this tool returned a confirmed result. "
                        "Its outcome is unknown. Inspect the current state before retrying "
                        "a mutation; the action may already have succeeded."
                    ),
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session, *, superseded: bool = True) -> bool:
        """Close a turn that only persisted the user message before crashing.

        A request that a restart is about to resume is left alone unless a new
        user message ``superseded`` it; otherwise a neutral note keeps the
        history well formed. The user is never asked to resend anything.
        """
        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False
        if not superseded and recovery_pending(session.metadata):
            return False
        self._close_interrupted_turn(session)
        return True

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        ephemeral: bool = False,
        _run_extra_hooks_for_ephemeral: bool = False,
        hooks: list[AgentHook] | None = None,
        hook_factories: list[AgentTurnHookFactory] | None = None,
        tools: ToolRegistry | None = None,
        persist_user_message: bool = True,
        runtime: LLMRuntime | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        metadata: dict[str, Any] = {}
        if not persist_user_message:
            metadata[turn_continuation.SKIP_USER_PERSIST_META] = True
        if session_key == "heartbeat":
            metadata["heartbeat"] = True
        msg = InboundMessage(
            channel=channel, sender_id=sender_id, chat_id=chat_id,
            content=content, media=media or [], metadata=metadata,
        )
        # Share the dispatch lock so direct calls serialize with bus turns.
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        try:
            async with lock:
                kwargs: dict[str, Any] = {
                    "session_key": session_key,
                    "on_progress": on_progress,
                    "on_stream": on_stream,
                    "on_stream_end": on_stream_end,
                    "ephemeral": ephemeral,
                }
                if _run_extra_hooks_for_ephemeral:
                    kwargs["run_extra_hooks_for_ephemeral"] = True
                if hooks is not None:
                    kwargs["hooks"] = hooks
                if hook_factories is not None:
                    kwargs["hook_factories"] = hook_factories
                if tools is not None:
                    kwargs["tools"] = tools
                if runtime is not None:
                    kwargs["runtime"] = runtime
                while True:
                    try:
                        response = await self._process_message(msg, **kwargs)
                    except Exception as exc:
                        session = self.sessions.get_or_create(session_key)
                        transient = LLMProvider._is_transient_response(
                            LLMProvider._unexpected_error_response(exc)
                        )
                        if ephemeral or not transient or self.turn_recovery.schedule(session) is None:
                            self.turn_recovery.finish(session)
                            await self.sessions.save_async(session)
                            raise
                        response = None
                    if ephemeral or not self.turn_recovery.waiting(session_key):
                        return response
                    # The one-shot CLI has no inbound worker. Keep the same
                    # accepted request alive here until its transport recovers.
                    while self.turn_recovery.waiting(session_key):
                        due = self.turn_recovery.due.get(session_key, time.time())
                        remaining = max(0.0, due - time.time())
                        if remaining:
                            if on_progress is not None:
                                await on_progress(
                                    f"Connection interrupted. Resuming automatically in {remaining:.0f}s."
                                )
                            await asyncio.sleep(min(remaining, 30.0))
                            continue
                        resumed = self.turn_recovery.take_due(active_keys=set(), only_key=session_key)
                        if resumed:
                            msg = resumed[0]
                            break
                        return response
                    else:
                        return response
        finally:
            self._loop_guards.pop(session_key, None)
            await self._runtime_events().run_status_changed(msg, session_key, "idle")
            self._runtime_events().clear_turn(session_key)
