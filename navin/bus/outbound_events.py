# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Typed outbound events carried by :class:`OutboundMessage`.

The message bus still transports :class:`navin.bus.events.OutboundMessage`
because channels need chat routing fields. Runtime/UI semantics live on the
message's explicit ``event`` field rather than in reserved metadata flags.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from navin.bus.events import OutboundMessage


class OutboundEvent:
    """Marker base for internal outbound runtime events."""


@dataclass(frozen=True)
class ProgressEvent(OutboundEvent):
    content: str = ""
    tool_hint: bool = False
    reasoning: bool = False
    reasoning_delta: bool = False
    reasoning_end: bool = False
    stream_id: str | None = None
    tool_events: list[dict[str, Any]] | None = None
    file_edit_events: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class RetryWaitEvent(OutboundEvent):
    content: str = ""


@dataclass(frozen=True)
class StreamDeltaEvent(OutboundEvent):
    content: str = ""
    stream_id: str | None = None


@dataclass(frozen=True)
class StreamEndEvent(OutboundEvent):
    content: str = ""
    stream_id: str | None = None
    resuming: bool = False


@dataclass(frozen=True)
class StreamedResponseEvent(OutboundEvent):
    pass


@dataclass(frozen=True)
class TurnEndEvent(OutboundEvent):
    latency_ms: int | None = None
    goal_state: dict[str, Any] | None = None
    # Wall time per turn phase (restore/build/run/save...), in ms.
    phase_timings_ms: dict[str, int] | None = None


@dataclass(frozen=True)
class GoalStatusEvent(OutboundEvent):
    status: str
    started_at: float | None = None


@dataclass(frozen=True)
class GoalStateSyncEvent(OutboundEvent):
    goal_state: dict[str, Any]


@dataclass(frozen=True)
class SessionUpdatedEvent(OutboundEvent):
    scope: str | None = None


@dataclass(frozen=True)
class RuntimeModelUpdatedEvent(OutboundEvent):
    model: str | None
    model_preset: str | None = None
    reason: str | None = None
    previous_model: str | None = None
    used_percent: int | None = None


@dataclass(frozen=True)
class ContextCompactedEvent(OutboundEvent):
    kind: str
    messages_archived: int = 0
    tokens_before: int | None = None
    tokens_after: int | None = None


@dataclass(frozen=True)
class CheckpointSavedEvent(OutboundEvent):
    """A restore point was snapshotted; the chat shows it as a divider."""

    name: str
    auto: bool = True


@dataclass(frozen=True)
class BoardUpdatedEvent(OutboundEvent):
    """The project task board changed (human edit or agent tool)."""

    project_path: str | None = None


@dataclass(frozen=True)
class MontageUpdatedEvent(OutboundEvent):
    """Something in the Montage studio changed (timeline, render job, assets).

    Project-scoped like the board: clients showing the same ``project_path``
    refetch what ``kind`` names. ``job`` carries the compact job summary so a
    progress bar can move without a round trip.
    """

    project_path: str | None = None
    kind: str = "timeline"  # "timeline" | "job" | "assets"
    name: str | None = None
    job: dict[str, Any] | None = None


@dataclass(frozen=True)
class MetagraphUpdatedEvent(OutboundEvent):
    """The project dependency graph changed (index refresh or annotate)."""

    project_path: str | None = None
    generation: int = 0
    diff: dict[str, Any] | None = None
    view: str = "files"


@dataclass(frozen=True)
class TerminalOpenRequestedEvent(OutboundEvent):
    """The agent asks the editor UI to open its integrated terminal panel.

    The UI creates the PTY session itself through the normal ``terminal_open``
    flow, so the shell's lifecycle stays bound to the client connection - the
    agent only points at which shell and directory the user asked for.
    """

    shell: str | None = None
    cwd: str | None = None


@dataclass(frozen=True)
class AgentExecEvent(OutboundEvent):
    """Live feed of one agent ``exec`` command for the editor terminal panel.

    Phases: ``start`` (command spawned), ``output`` (delta chunk), ``exit``
    (process finished). The WebUI renders these as read-only terminal tabs,
    the way Cursor surfaces agent commands next to the user's own shells.
    """

    exec_id: str
    phase: str  # "start" | "output" | "exit"
    command: str | None = None
    cwd: str | None = None
    background: bool = False
    data: str | None = None
    exit_code: int | None = None
    # OS sandbox the command runs in ("native", "bwrap"), or None when it
    # runs unconfined. ``sandbox_lifted`` marks a confinement the user lifted
    # for this one command (exec ``unsandboxed=true`` approved in the chat).
    sandbox: str | None = None
    sandbox_lifted: bool = False


@dataclass(frozen=True)
class AgentBrowserEvent(OutboundEvent):
    """Live view of the agent's headless browser for the Dev workbench.

    Phases: ``start`` (a live session begins), ``frame`` (one JPEG screencast
    frame, base64), ``action`` (a human-readable line describing what the
    agent just did), ``exit`` (the browser session closed). The WebUI renders
    these as a read-only "agent browser" tab, the way Cursor mirrors its
    browser next to the editor.
    """

    browser_id: str
    phase: str  # "start" | "frame" | "action" | "exit"
    url: str | None = None
    title: str | None = None
    action: str | None = None
    data: str | None = None  # base64 JPEG for phase="frame"
    width: int | None = None
    height: int | None = None
    user_control: bool | None = None


@dataclass(frozen=True)
class EditorOpenRequestedEvent(OutboundEvent):
    """The agent asks the editor UI to open a file or reveal a folder.

    The UI does the actual opening through its normal Dev workbench flow, so
    what the user sees is exactly what a click in the explorer produces: a
    file opens as a tab (optionally scrolled to a line), a folder expands in
    the tree.
    """

    path: str
    kind: str = "file"  # "file" | "folder"
    line: int | None = None


@dataclass(frozen=True)
class ComposerModeRequestedEvent(OutboundEvent):
    """The agent asks the WebUI to switch the composer turn mode.

    Modes: ``ask`` | ``plan`` | ``agent`` | ``review`` | ``security`` | ``debug`` | ``montage``.
    The client updates the mode menu + shell accent color (same as a manual
    pick) and persists the choice.
    """

    mode: str


@dataclass(frozen=True)
class ProductModuleRequestedEvent(OutboundEvent):
    """Ask the WebUI to open a product module for this turn.

    Distinct from :class:`ComposerModeRequestedEvent`: that one tints the composer
    within the current surface, this one changes which surface is on screen (the
    Code workbench, with the chat beside it). Modules are the ids in
    ``navin.command.modules.VALID_PRODUCT_MODULES``.
    """

    module: str


@dataclass(frozen=True)
class SubagentProgressEvent(OutboundEvent):
    """Live status for a background subagent (parallel Task-style cards).

    Emitted when a subagent starts, updates phase/tools, or finishes so the
    WebUI can show stacked cards (label, status line, model) like Cursor.
    """

    task_id: str
    label: str
    phase: str
    status_line: str
    model: str | None = None
    iteration: int = 0
    done: bool = False
    error: str | None = None
    task_description: str | None = None
    # How long the subagent has been running, for a replay after a refresh:
    # the client cannot infer it from the frame's arrival time, and a task that
    # started ten minutes ago must not look like it just began.
    started_ms_ago: int | None = None


@dataclass(frozen=True)
class PreviewOpenRequestedEvent(OutboundEvent):
    """The agent asks the Dev workbench to open Preview (web) or Mobile.

    ``kind="web"`` loads the local URL in the Preview iframe. ``kind="mobile"``
    switches to the Mobile tab and starts the Android device mirror when ready.
    """

    kind: str = "web"  # "web" | "mobile"
    url: str | None = None


@dataclass(frozen=True)
class FilePreviewOpenRequestedEvent(OutboundEvent):
    """The agent asks the WebUI to open a workspace file in File Preview.

    Used for studio deliverables (RiskLens HTML reports, etc.) so the user
    sees the file immediately with download / PDF export, outside the Dev editor.
    """

    path: str


@dataclass(frozen=True)
class ArtifactUpsertEvent(OutboundEvent):
    """Create or update a chat artifact shown in the Artifact Canvas."""

    artifact: dict[str, Any]


@dataclass(frozen=True)
class ArtifactSelectEvent(OutboundEvent):
    """Focus one artifact in the Artifact Canvas."""

    artifact_id: str


@dataclass(frozen=True)
class ApprovalRequestedEvent(OutboundEvent):
    """A tool has paused and is waiting for the user to allow or refuse.

    Unlike every other event here, something is blocked on the answer: the tool
    call stays suspended until the decision arrives or the request times out.
    ``request_id`` is what the answer must quote to reach the right waiter.
    """

    request_id: str
    tool: str
    action: str
    reason: str
    detail: str = ""
    consequence: str = ""
    scope: str = ""
    expires_at_ms: int | None = None
    remember_offered: bool = True


@dataclass(frozen=True)
class ChoiceRequestedEvent(OutboundEvent):
    """The agent paused because the next step is a real fork.

    ``request_id`` is what the answer must quote. ``options`` is two to four
    paths; exactly one should be recommended.
    """

    request_id: str
    question: str
    options: list[dict[str, Any]]
    allow_skip: bool = True
    recommended_id: str = ""
    expires_at_ms: int | None = None


@dataclass(frozen=True)
class ChoiceClosedEvent(OutboundEvent):
    """A pending choice is over, so its card can go away."""

    request_id: str
    option_id: str = ""
    skipped: bool = False


@dataclass(frozen=True)
class ApprovalClosedEvent(OutboundEvent):
    """A pending request is over, so its card can go away.

    Sent for every ending, including the ones the user did not cause: a timeout,
    a stopped turn, an answer given from another window.
    """

    request_id: str
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class NotificationEvent(OutboundEvent):
    """Something the user should be told about, outside the transcript.

    Carries what the WebUI notification centre needs and nothing else. ``key``
    collapses repeats: give the same key to successive reports of one condition
    (a retry that keeps waiting, a job that keeps failing) so they fold into a
    single entry with a counter instead of scrolling the panel.
    """

    title: str
    level: str = "info"
    detail: str | None = None
    key: str | None = None
    source: str = "session"


def outbound_message_for_event(
    *,
    channel: str,
    chat_id: str,
    event: OutboundEvent,
    content: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> OutboundMessage:
    """Build an :class:`OutboundMessage` for a typed event."""

    return OutboundMessage(
        channel=channel,
        chat_id=chat_id,
        content=_event_content(event) if content is None else content,
        event=event,
        metadata=dict(metadata or {}),
    )


def outbound_event_from_message(msg: OutboundMessage) -> OutboundEvent | None:
    """Return the typed outbound event carried by *msg*, if any."""

    if msg.event is not None:
        return msg.event
    return _legacy_event_from_metadata(msg)


def replace_outbound_event(
    msg: OutboundMessage,
    event: OutboundEvent,
    *,
    content: str | None = None,
) -> OutboundMessage:
    """Return *msg* with a new event and optional content."""

    return replace(
        msg,
        content=_event_content(event) if content is None else content,
        event=event,
    )


def _event_content(event: OutboundEvent) -> str:
    if isinstance(event, ProgressEvent | RetryWaitEvent | StreamDeltaEvent | StreamEndEvent):
        return event.content
    return ""


def _legacy_event_from_metadata(msg: OutboundMessage) -> OutboundEvent | None:
    """Bridge pre-typed outbound metadata flags into typed events.

    New code should set ``OutboundMessage.event`` directly. The fallback keeps
    older in-process extensions and channel plugins from losing runtime events
    while they migrate off reserved metadata flags.
    """

    meta = msg.metadata or {}
    if meta.get("_runtime_model_updated"):
        return RuntimeModelUpdatedEvent(
            model=_metadata_str(meta, "model"),
            model_preset=_metadata_str(meta, "model_preset"),
            reason=_metadata_str(meta, "reason"),
            previous_model=_metadata_str(meta, "previous_model"),
            used_percent=_metadata_int(meta, "used_percent"),
        )
    if meta.get("_goal_state_sync"):
        goal_state = meta.get("goal_state")
        return GoalStateSyncEvent(goal_state if isinstance(goal_state, dict) else {"active": False})
    if meta.get("_goal_status"):
        status = meta.get("goal_status")
        if not isinstance(status, str) or not status:
            return None
        return GoalStatusEvent(
            status=status,
            started_at=_metadata_float(meta, "started_at", "goal_started_at"),
        )
    if meta.get("_turn_end"):
        goal_state = meta.get("goal_state")
        phase_timings = meta.get("phase_timings_ms")
        return TurnEndEvent(
            latency_ms=_metadata_int(meta, "latency_ms"),
            goal_state=goal_state if isinstance(goal_state, dict) else None,
            phase_timings_ms=(
                phase_timings if isinstance(phase_timings, dict) else None
            ),
        )
    if meta.get("_session_updated"):
        return SessionUpdatedEvent(scope=_metadata_str(meta, "_session_update_scope"))
    if meta.get("_retry_wait"):
        return RetryWaitEvent(content=msg.content)
    if meta.get("_stream_end"):
        return StreamEndEvent(
            content=msg.content,
            stream_id=_metadata_str(meta, "_stream_id"),
            resuming=bool(meta.get("_resuming")),
        )
    if meta.get("_stream_delta"):
        return StreamDeltaEvent(
            content=msg.content,
            stream_id=_metadata_str(meta, "_stream_id"),
        )
    if meta.get("_streamed"):
        return StreamedResponseEvent()
    if (
        meta.get("_progress")
        or meta.get("_reasoning_delta")
        or meta.get("_reasoning_end")
        or meta.get("_reasoning")
        or meta.get("_file_edit_events")
        or meta.get("_tool_events")
    ):
        tool_events = meta.get("_tool_events")
        file_edit_events = meta.get("_file_edit_events")
        return ProgressEvent(
            content=msg.content,
            tool_hint=bool(meta.get("_tool_hint")),
            reasoning=bool(meta.get("_reasoning")),
            reasoning_delta=bool(meta.get("_reasoning_delta")),
            reasoning_end=bool(meta.get("_reasoning_end")),
            stream_id=_metadata_str(meta, "_stream_id"),
            tool_events=tool_events if isinstance(tool_events, list) else None,
            file_edit_events=file_edit_events if isinstance(file_edit_events, list) else None,
        )
    return None


def _metadata_str(meta: Mapping[str, Any], key: str) -> str | None:
    value = meta.get(key)
    return value if isinstance(value, str) and value else None


def _metadata_int(meta: Mapping[str, Any], key: str) -> int | None:
    value = meta.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _metadata_float(meta: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return float(value)
    return None
