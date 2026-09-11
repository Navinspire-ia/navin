# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Session turn helpers for WebUI-capable WebSocket sessions."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.bus import progress as bus_progress
from navin.bus.events import InboundMessage
from navin.bus.outbound_events import (
    CheckpointSavedEvent,
    ContextCompactedEvent,
    GoalStateSyncEvent,
    GoalStatusEvent,
    NotificationEvent,
    RuntimeModelUpdatedEvent,
    SessionUpdatedEvent,
    TurnEndEvent,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.bus.runtime_events import (
    CheckpointSaved,
    ContextCompacted,
    GoalStateChanged,
    ModelFailedOver,
    RuntimeEventBus,
    RuntimeEventContext,
    RuntimeModelChanged,
    SessionTurnStarted,
    TurnCompleted,
    TurnRunStatusChanged,
)
from navin.providers.base import LLMProvider
from navin.runtime_context import public_history_message
from navin.session.goal_state import goal_state_ws_blob
from navin.session.history_visibility import is_hidden_history_message
from navin.session.manager import Session, SessionManager
from navin.utils.helpers import strip_think, truncate_text
from navin.utils.llm_runtime import LLMRuntime

WEBUI_SESSION_METADATA_KEY = "webui"
WEBUI_TITLE_METADATA_KEY = "title"
WEBUI_TITLE_USER_EDITED_METADATA_KEY = "title_user_edited"
WEBUI_TITLE_PROVISIONAL_METADATA_KEY = "title_provisional"
TITLE_MAX_CHARS = 60
TITLE_GENERATION_MAX_TOKENS = 96
TITLE_GENERATION_REASONING_EFFORT = "none"

# Wall-clock turn start per ``chat_id`` (websocket only). Survives browser refresh while the
# gateway process stays up; cleared on idle/stop and implicitly dropped on restart.
_WEBSOCKET_TURN_WALL_STARTED_AT: dict[str, float] = {}
# Same-process live turns, keyed by session. Leaving the UI must not look like a
# crash: this set is what hydrate checks before it dares to say "interrupted".
_LIVE_TURN_SESSIONS: set[str] = set()

# Session-metadata mirror of the in-memory wall clock. The dict above dies
# with the process, so after a crash mid-turn nothing could tell "the agent is
# still working" apart from "the turn is gone". This key survives on the
# session JSONL: hydrate finds it orphaned (metadata says running, memory says
# nothing) and can surface an explicit "interrupted" state instead of leaving
# the user staring at a spinner that will never resolve.
TURN_RUN_STATE_METADATA_KEY = "turn_run_state"


def persist_turn_run_state(
    sessions: SessionManager,
    session_key: str,
    status: str,
    *,
    started_at: float | None = None,
) -> None:
    """Mirror the turn run flag into session metadata (crash detection)."""
    if status == "running":
        _LIVE_TURN_SESSIONS.add(session_key)
    else:
        _LIVE_TURN_SESSIONS.discard(session_key)
    try:
        session = sessions.get_or_create(session_key)
        if status == "running":
            session.metadata[TURN_RUN_STATE_METADATA_KEY] = {
                "status": "running",
                "started_at": float(started_at) if started_at else time.time(),
            }
        elif TURN_RUN_STATE_METADATA_KEY in session.metadata:
            session.metadata.pop(TURN_RUN_STATE_METADATA_KEY, None)
        else:
            return
        sessions.save(session)
    except Exception:
        logger.debug(
            "could not persist turn run state for {}", session_key, exc_info=True
        )


def restore_websocket_turn_wall(chat_id: str, started_at: float) -> None:
    """Re-attach the in-memory clock after a subscribe that missed it."""
    cid = str(chat_id or "").strip()
    if not cid:
        return
    t0 = float(started_at) if started_at else time.time()
    _WEBSOCKET_TURN_WALL_STARTED_AT[cid] = t0


def _last_visible_role(session: Session | None) -> str | None:
    if session is None:
        return None
    messages = session.messages if isinstance(getattr(session, "messages", None), list) else []
    for raw in reversed(messages):
        if not isinstance(raw, dict):
            continue
        if is_hidden_history_message(raw):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = str(raw.get("content") or "")
        if role == "assistant" and "gateway restarted" in text.lower():
            continue
        return role
    return None


def reconnect_turn_action(
    sessions: SessionManager,
    session_key: str,
    chat_id: str,
) -> tuple[str, float | None]:
    """What to tell a client that just subscribed.

    ``running``: the turn is still executing (user left the chat; keep going).
    ``idle``: the turn finished while they were away; no banner.
    ``interrupted``: the process died mid-turn; ask them to resend.
    ``none``: nothing to replay.
    """
    t0 = websocket_turn_wall_started_at(chat_id)
    if t0 is not None:
        return ("running", t0)
    if session_key in _LIVE_TURN_SESSIONS:
        started = time.time()
        try:
            session = sessions.peek(session_key)
        except Exception:
            session = None
        state = (
            session.metadata.get(TURN_RUN_STATE_METADATA_KEY)
            if session is not None and isinstance(session.metadata, dict)
            else None
        )
        if isinstance(state, dict) and isinstance(state.get("started_at"), int | float):
            started = float(state["started_at"])
        restore_websocket_turn_wall(chat_id, started)
        return ("running", started)
    try:
        session = sessions.peek(session_key)
    except Exception:
        session = None
    state = (
        session.metadata.get(TURN_RUN_STATE_METADATA_KEY)
        if session is not None and isinstance(getattr(session, "metadata", None), dict)
        else None
    )
    from navin.session.turn_recovery import RECOVERY_KEY, recovery_pending

    if session is not None and recovery_pending(session.metadata):
        started = session.metadata[RECOVERY_KEY].get("started_at")
        return ("running", float(started) if isinstance(started, int | float) else time.time())
    if not isinstance(state, dict) or state.get("status") != "running":
        return ("none", None)
    if _last_visible_role(session) == "assistant":
        persist_turn_run_state(sessions, session_key, "idle")
        return ("idle", None)
    interrupted_at = take_interrupted_turn_started_at(sessions, session_key)
    return ("interrupted", interrupted_at)



def take_interrupted_turn_started_at(
    sessions: SessionManager,
    session_key: str,
) -> float | None:
    """Detect a turn the previous gateway process never finished.

    Returns the persisted ``started_at`` (or 0.0 when unknown) exactly once,
    rewriting the state to ``interrupted`` so later hydrates stay quiet.
    Returns None when the last turn ended normally.
    """
    try:
        session = sessions.peek(session_key)
    except Exception:
        return None
    if session is None:
        return None
    state = session.metadata.get(TURN_RUN_STATE_METADATA_KEY)
    if not isinstance(state, dict) or state.get("status") != "running":
        return None
    raw = state.get("started_at")
    started_at = float(raw) if isinstance(raw, int | float) and raw > 0 else 0.0
    session.metadata[TURN_RUN_STATE_METADATA_KEY] = {
        "status": "interrupted",
        "started_at": started_at or None,
        "detected_at": time.time(),
    }
    try:
        sessions.save(session)
    except Exception:
        logger.debug(
            "could not persist interrupted turn state for {}",
            session_key,
            exc_info=True,
        )
    return started_at


def mark_webui_session(session: Session, metadata: dict[str, Any]) -> bool:
    """Persist a WebUI marker only when the inbound websocket frame opted in."""
    if metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    session.metadata[WEBUI_SESSION_METADATA_KEY] = True
    return True


def clean_generated_title(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(title|标题)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("\"'`“”‘’")
    text = strip_think(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip("。.!！?？,，;；:")
    if len(text) > TITLE_MAX_CHARS:
        text = text[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return text


_BAD_TITLE_RE = re.compile(
    r"(?i)\b("
    r"the user said|user said|user wrote|user asked|"
    r"the assistant|assistant replied|assistant said|"
    r"french for|english for|translation of|"
    r"this (chat|conversation|message)|"
    r"here('?s| is) (a |the )?title"
    r")\b"
)


def is_usable_generated_title(title: str | None) -> bool:
    """Reject meta / descriptive titles models invent for greetings."""
    text = (title or "").strip()
    if not text or text.lower().startswith("error"):
        return False
    if _BAD_TITLE_RE.search(text):
        return False
    # Real titles are short labels, not narrative sentences.
    if text.count(" ") >= 10:
        return False
    if text.endswith("…") and ("user" in text.lower() or "assistant" in text.lower()):
        return False
    return True


# "/forge build a site" → "build a site". The composer routes Agent/Plan/…
# free text through workflow slashes; the command itself carries no title
# information. A bare command with no args is left untouched (the "/" guard
# below drops it).
_LEADING_SLASH_COMMAND_RE = re.compile(r"^/[\w-]+\s+")
# Expanded workflow briefs look like "[Build mode] (/forge)\nSkills for…".
_WORKFLOW_BRIEF_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*\(/[\w-]+\)(?:\s|$)")
_WORKFLOW_FOCUS_RE = re.compile(
    r"(?m)^Focus / target given by the user:\s*(.+)$"
)


def strip_leading_slash_command(text: str) -> str:
    """Drop a routing prefix like ``/forge `` so titles reflect the prompt."""
    return _LEADING_SLASH_COMMAND_RE.sub("", (text or "").lstrip(), count=1)


def title_source_from_user_text(user_text: str | None) -> str:
    """Normalize user text for chat titles (strip routing / workflow briefs)."""
    source = (user_text or "").strip()
    if not source:
        return ""
    if _WORKFLOW_BRIEF_HEADER_RE.match(source) and "Skills for this mission" in source:
        focus = _WORKFLOW_FOCUS_RE.search(source)
        if focus:
            return focus.group(1).strip()
        return ""
    return strip_leading_slash_command(source).strip()


def provisional_title_from_user_text(user_text: str | None) -> str:
    """Immediate chat title from the first user message (no LLM round-trip)."""
    source = title_source_from_user_text(user_text)
    title = clean_generated_title(truncate_text(source, TITLE_MAX_CHARS + 40))
    if not title:
        return ""
    if title.startswith("/"):
        return ""
    if _WORKFLOW_BRIEF_HEADER_RE.match(title):
        return ""
    # Short greetings are fine as titles ("Salut") - better than waiting for an
    # LLM that often invents meta copy like "The user said salut...".
    return title


def _fallback_title_from_user_text(user_text: str) -> str:
    """Last-resort title when the LLM returns unusable meta text."""
    title = clean_generated_title(truncate_text(user_text, TITLE_MAX_CHARS + 40))
    if not title or title.startswith("/") or _WORKFLOW_BRIEF_HEADER_RE.match(title):
        return ""
    if not is_usable_generated_title(title):
        return ""
    # Prefer a short label; long prompts stay provisional until a good LLM title.
    if title.count(" ") > 8:
        return ""
    return title


def apply_provisional_title(session: Session, user_text: str | None) -> bool:
    """Set metadata.title from the user message when the chat is still untitled."""
    if session.metadata.get(WEBUI_TITLE_USER_EDITED_METADATA_KEY) is True:
        return False
    current = session.metadata.get(WEBUI_TITLE_METADATA_KEY)
    provisional = session.metadata.get(WEBUI_TITLE_PROVISIONAL_METADATA_KEY) is True
    changed = False
    if isinstance(current, str) and current.strip():
        # A title that leaked the expanded workflow brief is not a real title -
        # clear it so the next prompt (or LLM title) can replace it.
        if _WORKFLOW_BRIEF_HEADER_RE.match(current.strip()):
            session.metadata.pop(WEBUI_TITLE_METADATA_KEY, None)
            session.metadata.pop(WEBUI_TITLE_PROVISIONAL_METADATA_KEY, None)
            current = None
            provisional = False
            changed = True
        elif not provisional:
            return False
    title = provisional_title_from_user_text(user_text)
    if not title:
        return changed
    if current == title and provisional:
        return False
    session.metadata[WEBUI_TITLE_METADATA_KEY] = title
    session.metadata[WEBUI_TITLE_PROVISIONAL_METADATA_KEY] = True
    return True


def apply_provisional_webui_title(session: Session, user_text: str | None) -> bool:
    """WebUI-owned chats only. CLI uses ``apply_provisional_title`` directly."""
    if session.metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    return apply_provisional_title(session, user_text)


def _title_inputs(session: Session) -> tuple[str, str]:
    user_text = ""
    assistant_text = ""
    for message in session.messages:
        if message.get("_command") is True:
            continue
        if is_hidden_history_message(message):
            continue
        message = public_history_message(message)
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = strip_think(content)
        if not content:
            continue
        if role == "user" and not user_text:
            user_text = title_source_from_user_text(content)
        elif role == "assistant" and not assistant_text:
            assistant_text = content.strip()
        if user_text and assistant_text:
            break
    return user_text, assistant_text


async def maybe_generate_webui_title(
    *,
    sessions: SessionManager,
    session_key: str,
    provider: LLMProvider,
    model: str,
) -> bool:
    """Generate and persist a short title for WebUI-owned sessions only."""
    session = sessions.get_or_create(session_key)
    if session.metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    if session.metadata.get(WEBUI_TITLE_USER_EDITED_METADATA_KEY) is True:
        return False
    provisional = session.metadata.get(WEBUI_TITLE_PROVISIONAL_METADATA_KEY) is True
    current_title = session.metadata.get(WEBUI_TITLE_METADATA_KEY)
    if isinstance(current_title, str) and current_title.strip() and not provisional:
        cleaned_current_title = clean_generated_title(current_title)
        if cleaned_current_title and is_usable_generated_title(cleaned_current_title):
            if cleaned_current_title != current_title:
                session.metadata[WEBUI_TITLE_METADATA_KEY] = cleaned_current_title
                sessions.save(session)
            return False
        # Drop meta titles already persisted (e.g. "The user said salut...").
        session.metadata.pop(WEBUI_TITLE_METADATA_KEY, None)

    user_text, assistant_text = _title_inputs(session)
    if not user_text:
        return False

    prompt = (
        "Generate a concise title for this chat.\n"
        "Rules:\n"
        "- Use the same language as the user when practical.\n"
        "- 2 to 6 words.\n"
        "- No quotes.\n"
        "- No punctuation at the end.\n"
        "- Return only the title.\n"
        "- Never describe the conversation (no 'user said', no translations, "
        "no 'assistant replied').\n"
        "- For a greeting, return the greeting itself (e.g. Salut, Hello).\n\n"
        f"User: {truncate_text(user_text, 1_000)}"
    )
    if assistant_text:
        prompt += f"\nAssistant: {truncate_text(assistant_text, 1_000)}"

    try:
        chat_kwargs: dict[str, Any] = {
            "tools": None,
            "model": model,
            "max_tokens": TITLE_GENERATION_MAX_TOKENS,
            "temperature": 0.2,
            "retry_mode": "standard",
        }
        # Some gateways reject reasoning_effort=none; omit when unused.
        if TITLE_GENERATION_REASONING_EFFORT and TITLE_GENERATION_REASONING_EFFORT != "none":
            chat_kwargs["reasoning_effort"] = TITLE_GENERATION_REASONING_EFFORT
        response = await provider.chat_with_retry(
            [
                {
                    "role": "system",
                    "content": (
                        "You write short chat titles like a sidebar label. "
                        "Never narrate or explain the messages. "
                        "Return only the title text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            **chat_kwargs,
        )
    except Exception:
        logger.debug("Failed to generate webui session title for {}", session_key, exc_info=True)
        return False

    title = clean_generated_title(response.content)
    if not is_usable_generated_title(title):
        fallback = _fallback_title_from_user_text(user_text)
        if not fallback:
            logger.debug(
                "WebUI title generation returned no usable title for {} (finish_reason={})",
                session_key,
                response.finish_reason,
            )
            return False
        title = fallback
    session.metadata[WEBUI_TITLE_METADATA_KEY] = title
    session.metadata.pop(WEBUI_TITLE_PROVISIONAL_METADATA_KEY, None)
    sessions.save(session)
    return True


async def maybe_generate_webui_title_after_turn(
    *,
    channel: str,
    metadata: dict[str, Any],
    sessions: SessionManager,
    session_key: str,
    provider: LLMProvider,
    model: str,
) -> bool:
    if channel != "websocket" or metadata.get(WEBUI_SESSION_METADATA_KEY) is not True:
        return False
    return await maybe_generate_webui_title(
        sessions=sessions,
        session_key=session_key,
        provider=provider,
        model=model,
    )


def websocket_turn_wall_started_at(chat_id: str) -> float | None:
    """Return ``time.time()`` when the active user turn began, if still running."""
    return _WEBSOCKET_TURN_WALL_STARTED_AT.get(chat_id)


def build_bus_progress_callback(
    bus: MessageBus,
    msg: InboundMessage,
) -> Callable[..., Awaitable[None]]:
    """Compatibility wrapper for the generic bus progress callback."""
    return bus_progress.build_bus_progress_callback(bus, msg)


async def publish_turn_run_status(
    bus: MessageBus,
    msg: InboundMessage,
    status: str,
    *,
    started_at: float | None = None,
) -> None:
    """Notify WebSocket clients while a user turn is executing (timing strip)."""
    if msg.channel != "websocket":
        return
    cid = str(msg.chat_id)
    started_at_event: float | None = None
    if status == "running":
        if isinstance(started_at, int | float) and started_at > 0:
            t0 = float(started_at)
        else:
            t0 = time.time()
        started_at_event = t0
        _WEBSOCKET_TURN_WALL_STARTED_AT[cid] = t0
    else:
        _WEBSOCKET_TURN_WALL_STARTED_AT.pop(cid, None)
    await bus.publish_outbound(
        outbound_message_for_event(
            channel=msg.channel,
            chat_id=cid,
            event=GoalStatusEvent(status=status, started_at=started_at_event),
            metadata=msg.metadata,
        ),
    )

@dataclass
class WebuiTurnCoordinator:
    """Translate generic runtime events into WebUI/WebSocket wire messages."""

    bus: MessageBus
    sessions: SessionManager
    schedule_background: Callable[[Awaitable[None]], None]
    _title_contexts: dict[str, LLMRuntime] = field(default_factory=dict)

    def subscribe(self, runtime_events: RuntimeEventBus) -> Callable[[], None]:
        """Subscribe this coordinator to runtime events."""
        unsubscribe = [
            runtime_events.subscribe(
                self._handle_session_turn_started,
                SessionTurnStarted,
            ),
            runtime_events.subscribe(
                self._handle_run_status_changed,
                TurnRunStatusChanged,
            ),
            runtime_events.subscribe(
                self._handle_turn_completed_event,
                TurnCompleted,
            ),
            runtime_events.subscribe(
                self._handle_goal_state_changed,
                GoalStateChanged,
            ),
            runtime_events.subscribe(
                self._handle_runtime_model_changed,
                RuntimeModelChanged,
            ),
            runtime_events.subscribe(
                self._handle_context_compacted,
                ContextCompacted,
            ),
            runtime_events.subscribe(
                self._handle_checkpoint_saved,
                CheckpointSaved,
            ),
            runtime_events.subscribe(
                self._handle_model_failed_over,
                ModelFailedOver,
            ),
        ]

        def _unsubscribe() -> None:
            for fn in reversed(unsubscribe):
                fn()

        return _unsubscribe

    @staticmethod
    def _ctx_msg(ctx: RuntimeEventContext) -> InboundMessage:
        return InboundMessage(
            channel=ctx.channel,
            sender_id="runtime",
            chat_id=ctx.chat_id,
            content="",
            metadata=dict(ctx.metadata or {}),
            session_key_override=ctx.session_key,
        )

    @staticmethod
    def _is_websocket_event(ctx: RuntimeEventContext) -> bool:
        return ctx.channel == "websocket"

    async def _handle_session_turn_started(self, event: SessionTurnStarted) -> None:
        session = self.sessions.get_or_create(event.context.session_key)
        marked = False
        if self._is_websocket_event(event.context):
            marked = mark_webui_session(session, event.context.metadata)
        titled = apply_provisional_title(session, event.user_text)
        if marked or titled:
            self.sessions.save(session)
        if titled and self._is_websocket_event(event.context):
            await self._publish_session_metadata_updated(
                channel=event.context.channel,
                chat_id=event.context.chat_id,
                metadata=event.context.metadata,
            )

    async def _handle_run_status_changed(self, event: TurnRunStatusChanged) -> None:
        if not self._is_websocket_event(event.context):
            return
        started_at = event.started_at
        if event.status == "running" and not started_at:
            started_at = time.time()
        if event.status != "running":
            # Clear the persisted flag before announcing the end: a hydrate
            # racing this handler must never see "metadata running, memory
            # empty" for a turn that finished normally.
            persist_turn_run_state(
                self.sessions, event.context.session_key, event.status
            )
        await publish_turn_run_status(
            self.bus,
            self._ctx_msg(event.context),
            event.status,
            started_at=started_at,
        )
        if event.status == "running":
            # Persist after publish: the in-memory wall clock is set first,
            # so a concurrent hydrate sees the live turn, not a false crash.
            persist_turn_run_state(
                self.sessions,
                event.context.session_key,
                event.status,
                started_at=started_at,
            )

    async def _handle_turn_completed_event(self, event: TurnCompleted) -> None:
        if not self._is_websocket_event(event.context):
            return
        msg = self._ctx_msg(event.context)
        await self.handle_turn_end(
            msg,
            session_key=event.context.session_key,
            latency_ms=event.latency_ms,
            phase_timings_ms=event.phase_timings_ms,
        )
        self._schedule_title_update_from_event(event)

    async def _handle_goal_state_changed(self, event: GoalStateChanged) -> None:
        if not self._is_websocket_event(event.context):
            return
        cid = str(event.context.chat_id or "").strip()
        if not cid:
            return
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=event.context.channel,
                chat_id=cid,
                event=GoalStateSyncEvent(
                    goal_state=goal_state_ws_blob(event.session_metadata),
                ),
                metadata=event.context.metadata,
            ),
        )

    async def _handle_runtime_model_changed(self, event: RuntimeModelChanged) -> None:
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel="websocket",
                chat_id="*",
                event=RuntimeModelUpdatedEvent(
                    model=event.model,
                    model_preset=event.model_preset,
                    reason=event.reason,
                    previous_model=event.previous_model,
                    used_percent=event.used_percent,
                ),
            )
        )

    async def _handle_model_failed_over(self, event: ModelFailedOver) -> None:
        """Record which model actually answered, without interrupting the turn.

        A warning toast used to pop over the workbench the moment failover
        started, while tools were still running. That read as a failure even
        though the substitution is the recovery. ``info`` stays in the bell;
        it does not auto-toast. The user's selection is left unchanged.
        The shared ``key`` folds a run of substitutions into one entry.
        """
        from navin.providers.fallback_policy import model_switch_notice

        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel="websocket",
                chat_id="*",
                event=NotificationEvent(
                    title=f"Answered by {event.served_model}",
                    level="info",
                    detail=model_switch_notice(event.chosen_model, event.served_model),
                    key=f"model-failover:{event.chosen_model}",
                    source="model",
                ),
            )
        )

    async def _handle_context_compacted(self, event: ContextCompacted) -> None:
        key = event.session_key or ""
        if key.startswith("websocket:"):
            chat_id = key.split(":", 1)[1]
        elif key == "unified:default":
            # Unified sessions have no single chat: broadcast to all clients.
            chat_id = "*"
        else:
            return
        if not chat_id:
            return
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel="websocket",
                chat_id=chat_id,
                event=ContextCompactedEvent(
                    kind=event.kind,
                    messages_archived=event.messages_archived,
                    tokens_before=event.tokens_before,
                    tokens_after=event.tokens_after,
                ),
            )
        )

    async def _handle_checkpoint_saved(self, event: CheckpointSaved) -> None:
        key = event.session_key or ""
        if not key.startswith("websocket:"):
            return
        chat_id = key.split(":", 1)[1]
        if not chat_id:
            return
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel="websocket",
                chat_id=chat_id,
                event=CheckpointSavedEvent(name=event.name, auto=event.auto),
            )
        )

    def capture_title_context(
        self,
        session_key: str,
        msg: InboundMessage,
        llm: LLMRuntime,
    ) -> None:
        if msg.channel == "websocket" and msg.metadata.get("webui") is True:
            self._title_contexts[session_key] = llm

    def discard(self, session_key: str) -> None:
        self._title_contexts.pop(session_key, None)

    async def publish_run_status(
        self,
        msg: InboundMessage,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        await publish_turn_run_status(self.bus, msg, status, started_at=started_at)

    async def handle_turn_end(
        self,
        msg: InboundMessage,
        *,
        session_key: str,
        latency_ms: int | None,
        phase_timings_ms: dict[str, int] | None = None,
    ) -> None:
        if msg.channel != "websocket":
            return

        session = self.sessions.get_or_create(session_key)
        turn_meta = dict(msg.metadata or {})
        for entry in reversed(session.messages):
            if not isinstance(entry, dict) or entry.get("role") != "assistant":
                continue
            if entry.get("model_name") or entry.get("model"):
                for key in (
                    "model",
                    "model_name",
                    "model_label",
                    "model_preset",
                    "task_role",
                    "model_route_role",
                ):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip() and key not in turn_meta:
                        turn_meta[key] = value.strip()
                break
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=msg.channel,
                chat_id=msg.chat_id,
                event=TurnEndEvent(
                    latency_ms=latency_ms,
                    goal_state=goal_state_ws_blob(session.metadata),
                    phase_timings_ms=phase_timings_ms,
                ),
                metadata=turn_meta,
            )
        )
        self._schedule_title_update(msg, session_key=session_key)

    def _schedule_title_update(self, msg: InboundMessage, *, session_key: str) -> None:
        title_context = self._title_contexts.pop(session_key, None)
        if msg.metadata.get("webui") is not True or title_context is None:
            return

        async def _generate_title_and_notify(
            title_llm: LLMRuntime = title_context,
        ) -> None:
            generated = await maybe_generate_webui_title_after_turn(
                channel=msg.channel,
                metadata=msg.metadata,
                sessions=self.sessions,
                session_key=session_key,
                provider=title_llm.provider,
                model=title_llm.model,
            )
            if generated:
                await self._publish_session_metadata_updated(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    metadata=msg.metadata,
                )

        self.schedule_background(_generate_title_and_notify())

    def _schedule_title_update_from_event(self, event: TurnCompleted) -> None:
        title_context = event.runtime
        if (
            event.context.metadata.get("webui") is not True
            or title_context is None
            or not isinstance(title_context, LLMRuntime)
        ):
            return

        async def _generate_title_and_notify(
            title_llm: LLMRuntime = title_context,
        ) -> None:
            generated = await maybe_generate_webui_title_after_turn(
                channel=event.context.channel,
                metadata=event.context.metadata,
                sessions=self.sessions,
                session_key=event.context.session_key,
                provider=title_llm.provider,
                model=title_llm.model,
            )
            if generated:
                await self._publish_session_metadata_updated(
                    channel=event.context.channel,
                    chat_id=event.context.chat_id,
                    metadata=event.context.metadata,
                )

        self.schedule_background(_generate_title_and_notify())

    async def _publish_session_metadata_updated(
        self,
        *,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.bus.publish_outbound(
            outbound_message_for_event(
                channel=channel,
                chat_id=chat_id,
                event=SessionUpdatedEvent(scope="metadata"),
                metadata=metadata,
            )
        )
