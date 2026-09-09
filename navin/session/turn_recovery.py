# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable recovery of accepted interactive turns across transport/process loss."""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

from navin.bus.events import InboundMessage
from navin.session import turn_continuation as continuation
from navin.session.manager import Session, SessionManager

RECOVERY_KEY = "turn_recovery"
RECOVERY_ID_META = "_turn_recovery_id"
TRACKED_REQUEST_META = "_turn_recovery_request"
_CHANNELS = frozenset({"websocket", "cli"})


def recovery_pending(metadata: dict[str, Any]) -> bool:
    record = metadata.get(RECOVERY_KEY)
    return isinstance(record, dict) and record.get("status") in {"running", "waiting"}


class TurnRecovery:
    """Keep one resumable request per session; never replay tool calls directly."""

    def __init__(self, sessions: SessionManager) -> None:
        self.sessions = sessions
        self.due: dict[str, float] = {}
        self.queued: dict[str, str] = {}

    def begin(self, session: Session, msg: InboundMessage, *, persisted: bool = True) -> None:
        if msg.channel not in _CHANNELS or msg.metadata.get("heartbeat"):
            return
        previous = session.metadata.get(RECOVERY_KEY)
        same_request = isinstance(previous, dict) and msg.metadata.get(TRACKED_REQUEST_META) == previous.get("id")
        if same_request and previous.get("status") == "running" and (not persisted or previous.get("request_persisted")):
            return
        if continuation.internal_continuation_inbound(msg.metadata) or same_request:
            if not isinstance(previous, dict):
                return
            record = dict(previous)
        else:
            # Runtime callbacks, acknowledgements and approval objects cannot
            # survive a restart. Rebuild them from the current configuration.
            metadata = {}
            for key, value in msg.metadata.items():
                if key.startswith("_") and key != "_wants_stream":
                    continue
                if key in {"message_id", "goal_requested", "original_command"}:
                    continue
                try:
                    metadata[key] = json.loads(json.dumps(value, allow_nan=False))
                except (TypeError, ValueError):
                    continue
            record = {
                "id": uuid.uuid4().hex,
                "started_at": time.time(),
                "attempts": 0,
                "request": {
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "content": msg.content,
                    "media": list(msg.media),
                    "metadata": metadata,
                },
            }
        record["request_persisted"] = bool(record.get("request_persisted")) or persisted
        msg.metadata[TRACKED_REQUEST_META] = record["id"]
        record["status"] = "running"
        record.pop("next_attempt_at", None)
        session.metadata[RECOVERY_KEY] = record
        self.due.pop(session.key, None)
        self.queued.pop(session.key, None)
        self.sessions.save(session, fsync=True)

    @staticmethod
    def user_persisted(session: Session, msg: InboundMessage) -> None:
        record = session.metadata.get(RECOVERY_KEY)
        if isinstance(record, dict) and msg.metadata.get(TRACKED_REQUEST_META) == record.get("id"):
            record["request_persisted"] = True

    def finish(self, session: Session) -> None:
        session.metadata.pop(RECOVERY_KEY, None)
        self.due.pop(session.key, None)
        self.queued.pop(session.key, None)

    def cancel(self, key: str) -> None:
        session = self.sessions.peek(key)
        if session is not None and RECOVERY_KEY in session.metadata:
            self.finish(session)
            self.sessions.save(session, fsync=True)

    def schedule(self, session: Session, *, retry_after: float | None = None) -> float | None:
        record = session.metadata.get(RECOVERY_KEY)
        if not isinstance(record, dict):
            return None
        attempts = max(0, int(record.get("attempts") or 0)) + 1
        delay = min(60.0, 5.0 * 2 ** min(attempts - 1, 4))
        if isinstance(retry_after, int | float) and math.isfinite(retry_after):
            # Honour provider cooldowns without polling that provider early.
            delay = max(delay, min(float(retry_after), 3600.0))
        when = time.time() + delay
        record.update(status="waiting", attempts=attempts, next_attempt_at=when)
        self.due[session.key] = when
        self.queued.pop(session.key, None)
        self.sessions.save(session, fsync=True)
        return delay

    def waiting(self, key: str) -> bool:
        session = self.sessions.peek(key)
        record = session.metadata.get(RECOVERY_KEY) if session is not None else None
        return isinstance(record, dict) and record.get("status") == "waiting"

    def matches(self, msg: InboundMessage, key: str) -> bool:
        token = msg.metadata.get(RECOVERY_ID_META)
        if token is None:
            return True
        session = self.sessions.peek(key)
        record = session.metadata.get(RECOVERY_KEY) if session is not None else None
        return isinstance(record, dict) and record.get("id") == token

    def discover(self, *, channel: str, session_key: str | None = None) -> list[str]:
        """Read once at startup; only this runtime's interactive channel resumes."""
        keys = [session_key] if session_key else [row["key"] for row in self.sessions.list_sessions()]
        recovered = []
        now = time.time()
        for key in keys:
            session = self.sessions.peek(key)
            if session is None or not recovery_pending(session.metadata):
                continue
            record = session.metadata[RECOVERY_KEY]
            request = record.get("request")
            if not isinstance(request, dict) or request.get("channel") != channel:
                continue
            due = record.get("next_attempt_at")
            self.due[key] = float(due) if isinstance(due, int | float) and math.isfinite(due) else now
            recovered.append(key)
        return recovered

    def take_due(
        self, *, active_keys: set[str], now: float | None = None, only_key: str | None = None,
    ) -> list[InboundMessage]:
        now = time.time() if now is None else now
        messages = []
        for key, due in list(self.due.items()):
            if only_key is not None and key != only_key:
                continue
            if due > now or key in active_keys or key in self.queued:
                continue
            session = self.sessions.peek(key)
            if session is None or not recovery_pending(session.metadata):
                self.due.pop(key, None)
                continue
            record = session.metadata[RECOVERY_KEY]
            request = record.get("request")
            if not isinstance(request, dict) or request.get("channel") not in _CHANNELS:
                self.finish(session)
                self.sessions.save(session)
                continue
            metadata = dict(request.get("metadata") or {})
            if not record.get("request_persisted", True):
                # A close during media/context preparation can precede the
                # ordinary history write. Recover that accepted input once.
                session.add_message(
                    "user", str(metadata.get("original_content") or request.get("content") or ""),
                    media=list(request.get("media") or []),
                )
                record["request_persisted"] = True
                self.sessions.save(session, fsync=True)
            metadata.update({
                RECOVERY_ID_META: record["id"],
                continuation.INTERNAL_CONTINUATION_META: True,
                continuation.INTERNAL_CONTINUATION_KIND_META: "recovery",
                continuation.INTERNAL_CONTINUATION_RUN_STARTED_AT_META: record["started_at"],
                continuation.SKIP_USER_PERSIST_META: True,
            })
            text = (
                "Resume the accepted request after an interruption or temporary connection failure. "
                "Use the saved conversation, checkpoints and current project state. "
                "Continue unfinished work without repeating completed steps. A tool without a "
                "confirmed result may already have changed files or an external service: inspect "
                "its actual outcome before retrying any mutation. Reconnect browser/computer "
                "sessions and inspect the current screen before acting. Respect current "
                "permissions, pending user decisions and the original task scope.\n\n"
                "Original request:\n" + str(request.get("content") or "")
            )
            messages.append(InboundMessage(
                channel=request["channel"], chat_id=request["chat_id"],
                sender_id="system:recovery", content=text,
                media=list(request.get("media") or []), metadata=metadata,
                session_key_override=key,
            ))
            self.queued[key] = record["id"]
        return messages
