# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime event bus for agent state notifications.

This bus is separate from :mod:`navin.bus.queue`: message bus events are
user/chat delivery, while runtime events are in-process state notifications
that optional subscribers such as WebUI adapters may render.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.bus.events import InboundMessage


def _title_user_text(msg: InboundMessage) -> str:
    """Prefer the user's original slash line over an expanded workflow brief."""
    meta = getattr(msg, "metadata", None) or {}
    original = meta.get("original_content")
    if isinstance(original, str) and original.strip():
        return original
    return str(getattr(msg, "content", None) or "")


@dataclass(frozen=True)
class RuntimeEventContext:
    """Routing context common to turn-scoped runtime events."""

    channel: str
    chat_id: str
    session_key: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionTurnStarted:
    """A user/system turn has loaded its session and is about to build context."""

    context: RuntimeEventContext
    user_text: str = ""


@dataclass(frozen=True)
class TurnRunStatusChanged:
    """Visible run status changed for a turn."""

    context: RuntimeEventContext
    status: str
    started_at: float | None = None


@dataclass(frozen=True)
class TurnCompleted:
    """A turn has delivered its final user-visible response."""

    context: RuntimeEventContext
    latency_ms: int | None = None
    runtime: Any | None = None
    # Wall time spent in each turn state (restore/build/run/save...), in ms.
    phase_timings_ms: dict[str, int] | None = None


@dataclass(frozen=True)
class GoalStateChanged:
    """A session's sustained-goal state changed."""

    context: RuntimeEventContext
    session_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeModelChanged:
    """The active runtime model/preset changed."""

    model: str
    model_preset: str | None
    reason: str | None = None
    previous_model: str | None = None
    used_percent: int | None = None


@dataclass(frozen=True)
class ModelFailedOver:
    """A turn was answered by a model the user did not pick.

    Distinct from :class:`RuntimeModelChanged`: the selection did not change, so
    the UI must report the substitution without rewriting the chosen model.
    """

    chosen_model: str
    served_model: str


@dataclass(frozen=True)
class ContextCompacted:
    """Part of a session's history was summarized to fit the context window."""

    session_key: str
    kind: str  # "consolidation" (token pressure) | "idle" (auto-compact)
    messages_archived: int = 0
    tokens_before: int | None = None
    tokens_after: int | None = None


@dataclass(frozen=True)
class CheckpointSaved:
    """A restore point (conversation + code) was snapshotted for a session."""

    session_key: str
    name: str
    auto: bool = True


RuntimeEvent = (
    SessionTurnStarted
    | TurnRunStatusChanged
    | TurnCompleted
    | GoalStateChanged
    | RuntimeModelChanged
    | ModelFailedOver
    | ContextCompacted
    | CheckpointSaved
)
RuntimeEventType = (
    type[SessionTurnStarted]
    | type[TurnRunStatusChanged]
    | type[TurnCompleted]
    | type[GoalStateChanged]
    | type[RuntimeModelChanged]
    | type[ModelFailedOver]
    | type[ContextCompacted]
    | type[CheckpointSaved]
)
RuntimeEventHandler = Callable[[Any], Awaitable[None] | None]
_HandlerEntry = tuple[RuntimeEventType | None, RuntimeEventHandler]


class RuntimeEventBus:
    """Small in-process pub/sub bus for runtime state.

    Subscribers run in registration order. ``publish`` awaits async handlers so
    callers can preserve ordering when a runtime event must follow a user
    message. ``publish_nowait`` is available for synchronous call sites.
    """

    def __init__(self) -> None:
        self._handlers: list[_HandlerEntry] = []

    def subscribe(
        self,
        handler: RuntimeEventHandler,
        event_type: RuntimeEventType | None = None,
    ) -> Callable[[], None]:
        entry = (event_type, handler)
        self._handlers.append(entry)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._handlers.remove(entry)

        return _unsubscribe

    async def publish(self, event: RuntimeEvent) -> None:
        for event_type, handler in list(self._handlers):
            if event_type is not None and not isinstance(event, event_type):
                continue
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("runtime event handler failed for {}", type(event).__name__)

    def publish_nowait(self, event: RuntimeEvent) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("dropping runtime event without a running loop: {}", type(event).__name__)
            return
        loop.create_task(self.publish(event))


class RuntimeEventPublisher:
    """Convenience publisher for turn-scoped runtime events.

    Agent code should decide when state transitions happen; this helper owns
    the mechanics of building event contexts and carrying per-turn metadata.
    """

    def __init__(self, bus: RuntimeEventBus | None = None) -> None:
        self.bus = bus or RuntimeEventBus()
        self._turn_latency_ms: dict[str, int] = {}
        self._turn_runtime: dict[str, Any] = {}
        self._turn_phase_timings: dict[str, dict[str, int]] = {}

    @staticmethod
    def _context(
        *,
        channel: str,
        chat_id: str,
        session_key: str,
        metadata: dict[str, Any] | None,
    ) -> RuntimeEventContext:
        return RuntimeEventContext(
            channel=channel,
            chat_id=chat_id,
            session_key=session_key,
            metadata=dict(metadata or {}),
        )

    def record_turn_runtime(self, session_key: str, runtime: Any) -> None:
        self._turn_runtime[session_key] = runtime

    def record_turn_latency(self, session_key: str, latency_ms: int | None) -> None:
        if latency_ms is not None:
            self._turn_latency_ms[session_key] = int(latency_ms)

    def record_turn_phase_timings(
        self,
        session_key: str,
        timings: dict[str, int] | None,
    ) -> None:
        if timings:
            self._turn_phase_timings[session_key] = dict(timings)

    def clear_turn(self, session_key: str) -> None:
        self._turn_latency_ms.pop(session_key, None)
        self._turn_runtime.pop(session_key, None)
        self._turn_phase_timings.pop(session_key, None)

    async def session_turn_started(
        self,
        msg: InboundMessage,
        session_key: str,
    ) -> None:
        await self.bus.publish(
            SessionTurnStarted(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    session_key=session_key,
                    metadata=msg.metadata,
                ),
                user_text=_title_user_text(msg),
            )
        )

    async def run_status_changed(
        self,
        msg: InboundMessage,
        session_key: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        await self.bus.publish(
            TurnRunStatusChanged(
                context=self._context(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    session_key=session_key,
                    metadata=msg.metadata,
                ),
                status=status,
                started_at=started_at,
            )
        )

    async def turn_completed(
        self,
        *,
        channel: str,
        chat_id: str,
        session_key: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        await self.bus.publish(
            TurnCompleted(
                context=self._context(
                    channel=channel,
                    chat_id=chat_id,
                    session_key=session_key,
                    metadata=metadata,
                ),
                latency_ms=self._turn_latency_ms.pop(session_key, None),
                runtime=self._turn_runtime.pop(session_key, None),
                phase_timings_ms=self._turn_phase_timings.pop(session_key, None),
            )
        )

    def runtime_model_changed(
        self,
        model: str,
        model_preset: str | None,
        *,
        reason: str | None = None,
        previous_model: str | None = None,
        used_percent: int | None = None,
    ) -> None:
        self.bus.publish_nowait(
            RuntimeModelChanged(
                model=model,
                model_preset=model_preset,
                reason=reason,
                previous_model=previous_model,
                used_percent=used_percent,
            )
        )

    def model_failed_over(self, chosen_model: str, served_model: str) -> None:
        self.bus.publish_nowait(
            ModelFailedOver(chosen_model=chosen_model, served_model=served_model)
        )

    def context_compacted(self, event: ContextCompacted) -> None:
        self.bus.publish_nowait(event)

    def checkpoint_saved(self, event: CheckpointSaved) -> None:
        self.bus.publish_nowait(event)


def ensure_runtime_event_publisher(owner: Any) -> RuntimeEventPublisher:
    """Return an owner's runtime publisher, creating missing state lazily."""
    publisher = getattr(owner, "runtime_event_publisher", None)
    if isinstance(publisher, RuntimeEventPublisher):
        return publisher

    bus = getattr(owner, "runtime_events", None)
    if not isinstance(bus, RuntimeEventBus):
        bus = RuntimeEventBus()
        owner.runtime_events = bus

    publisher = RuntimeEventPublisher(bus)
    owner.runtime_event_publisher = publisher
    return publisher
