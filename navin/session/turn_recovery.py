# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable recovery of accepted interactive turns across transport/process loss."""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

from loguru import logger

from navin.bus.events import InboundMessage
from navin.session import turn_continuation as continuation
from navin.session.manager import Session, SessionManager

RECOVERY_KEY = "turn_recovery"
# Automatic replays of an interrupted turn (about 4 minutes of backoff in all).
MAX_RECOVERY_ATTEMPTS = 6
# Set on a record once a restart replayed it. A turn cut again after that is
# not replayed by the next start: a turn that takes the process (or the whole
# machine, a runaway command under WSL) down would otherwise do it at every
# start. The user resumes it with a message.
REPLAYED_AFTER_RESTART = "replayed_after_restart"
# Slices that carry a run on past its step budget. One that arrives with no
# tracked request belongs to work a background result started.
_RUN_SLICE_KINDS = frozenset({continuation._TURN_CONTINUATION_KIND, continuation._BOARD_CONTINUATION_KIND})
# Graceful stops from outside the turn (SIGTERM, SIGHUP) that may each spare
# its replay (see ``TurnRecovery.note_external_stop``). Past this, a turn cut
# again after a replay waits for the user, whatever stopped the process.
MAX_EXTERNAL_STOPS = 3
# Below this share of available memory, a stop is not trusted to be external:
# the turn itself may be what filled the memory.
MEMORY_PRESSURE_AVAILABLE_RATIO = 0.10
RECOVERY_ID_META = "_turn_recovery_id"
TRACKED_REQUEST_META = "_turn_recovery_request"
_CHANNELS = frozenset({"websocket", "cli"})
# An interrupted request idle for longer than this is not replayed after a
# restart: the user has moved on and the saved state may no longer apply.
MAX_RECOVERY_AGE_S = 6 * 3600.0
# Session marker for an accepted request whose turn has not ended (owned by
# the agent loop); ``cancel`` flags it so a restart never resumes a /stop.
PENDING_USER_TURN_KEY = "pending_user_turn"


def recovery_pending(metadata: dict[str, Any]) -> bool:
    record = metadata.get(RECOVERY_KEY)
    return isinstance(record, dict) and record.get("status") in {"running", "waiting"}


def portable_request(msg: InboundMessage) -> dict[str, Any]:
    """Snapshot of *msg* that can be replayed by a fresh process."""
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
    return {
        "channel": msg.channel,
        "chat_id": msg.chat_id,
        "content": msg.content,
        "media": list(msg.media),
        "metadata": metadata,
    }


def _replayable_channel(channel: Any) -> bool:
    return isinstance(channel, str) and bool(channel) and channel != "system"


def memory_pressure() -> bool | None:
    """True when less than 10% of the memory is available; None when unreadable."""
    import psutil

    try:
        memory = psutil.virtual_memory()
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not read the memory pressure: {}", exc)
        return None
    if not memory.total:
        return None
    return memory.available < MEMORY_PRESSURE_AVAILABLE_RATIO * memory.total


class TurnRecovery:
    """Keep one resumable request per session; never replay tool calls directly."""

    def __init__(self, sessions: SessionManager) -> None:
        self.sessions = sessions
        self.due: dict[str, float] = {}
        self.queued: dict[str, str] = {}
        # Interrupted requests a restart gave up on (too old or replayed too
        # often); the runtime closes them with a short note.
        self.abandoned: list[str] = []
        # Of those, the ones already replayed once after an earlier restart.
        self.cut_twice: set[str] = set()
        # Found by this start's discover(): their next replay is the restart one.
        self._restarted: set[str] = set()

    def begin(self, session: Session, msg: InboundMessage, *, persisted: bool = True) -> None:
        if self._begin_record(session, msg, persisted):
            self.sessions.save(session, fsync=True)

    async def begin_async(self, session: Session, msg: InboundMessage, *, persisted: bool = True) -> None:
        """``begin`` with its durable write in a worker thread, awaited.

        The record is updated on the loop; the fsync of a long transcript
        held the loop 1.2 s before each turn (CLI freeze log of 2026-10-31).
        """
        if self._begin_record(session, msg, persisted):
            await self.sessions.save_async(session, fsync=True)

    def _begin_record(self, session: Session, msg: InboundMessage, persisted: bool) -> bool:
        """Update the recovery record for a starting turn; True when it must be written."""
        if msg.channel not in _CHANNELS or msg.metadata.get("heartbeat"):
            previous = session.metadata.get(RECOVERY_KEY)
            if (
                isinstance(previous, dict)
                and not msg.metadata.get("heartbeat")
                and not continuation.internal_continuation_inbound(msg.metadata)
                and msg.metadata.get(RECOVERY_ID_META) is None
            ):
                # A new message supersedes a restart replay adopted for this
                # channel; the queued replay must not run after it.
                self.finish(session)
                return True
            return False
        previous = session.metadata.get(RECOVERY_KEY)
        same_request = isinstance(previous, dict) and msg.metadata.get(TRACKED_REQUEST_META) == previous.get("id")
        if same_request and previous.get("status") == "running" and (not persisted or previous.get("request_persisted")):
            return False
        if continuation.internal_continuation_inbound(msg.metadata) or same_request:
            if isinstance(previous, dict):
                record = dict(previous)
                if (
                    continuation.internal_continuation_inbound(msg.metadata)
                    and msg.metadata.get(continuation.INTERNAL_CONTINUATION_KIND_META) != "recovery"
                ):
                    # The slice before this one ran to its end and was saved:
                    # the replayed run works again. Kept for the whole run, the
                    # replay mark closed an hour of healthy work at the next
                    # restart ("stopped twice in a row"). ``attempts`` stays: it
                    # still bounds how often one request can restart the process.
                    record.pop(REPLAYED_AFTER_RESTART, None)
            elif msg.metadata.get(continuation.INTERNAL_CONTINUATION_KIND_META) in _RUN_SLICE_KINDS:
                # The next slice of work a background result started (a finished
                # command, a subagent's answer): no user request is tracked for it,
                # so it starts its own record. Without one, a restart or a
                # provider error in that chain lost the rest of the work silently.
                record = self._new_record(msg)
            else:
                return False
        else:
            record = self._new_record(msg)
        record["request_persisted"] = bool(record.get("request_persisted")) or persisted
        msg.metadata[TRACKED_REQUEST_META] = record["id"]
        record["status"] = "running"
        record.pop("next_attempt_at", None)
        session.metadata[RECOVERY_KEY] = record
        self.due.pop(session.key, None)
        self.queued.pop(session.key, None)
        return True

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
        """The user stopped the request: drop its replay, now and after a restart."""
        session = self.sessions.peek(key)
        if session is None:
            return
        marker = session.metadata.get(PENDING_USER_TURN_KEY)
        changed = False
        if marker and not (isinstance(marker, dict) and marker.get("stopped")):
            session.metadata[PENDING_USER_TURN_KEY] = {
                **(marker if isinstance(marker, dict) else {}), "stopped": True,
            }
            changed = True
        if RECOVERY_KEY in session.metadata:
            self.finish(session)
            changed = True
        if changed:
            self.sessions.save(session, fsync=True)

    def note_external_stop(self, keys: Any, reason: str) -> list[str]:
        """A stop signal from outside ends the process: keep the replay of these turns.

        Why: on 2026-11-05 a kernel OOM kill under WSL made systemd stop every
        terminal. Each navin-cli got SIGTERM and quit cleanly; its turn was
        replayed at the next start, then systemd killed the restarted CLIs
        again. At the start after that, every turn was abandoned as "stopped
        twice in a row": the crash-loop guard (``REPLAYED_AFTER_RESTART``)
        took the second stop for a turn that brings the process down. A
        SIGTERM or SIGHUP (systemd, terminal closed, WSL or OS shutdown) did
        not come from the turn, so it must not use up its replay.

        For each session with a record still running or waiting, the stop is
        written in ``last_stop``. Without memory pressure and below
        ``MAX_EXTERNAL_STOPS``, it counts in ``external_stops`` and the replay
        mark is dropped: this cut is explained. Under memory pressure (or
        unreadable memory, or past the bound) the mark stays and the normal
        rule applies: the turn may be what filled the memory. Each record is
        written with fsync. Returns the keys whose replay was kept.
        """
        kept, changed = self._note_external_stop_records(keys, reason)
        for session in changed:
            self.sessions.save(session, fsync=True)
        return kept

    async def note_external_stop_async(self, keys: Any, reason: str) -> list[str]:
        """``note_external_stop`` with its fsync writes in a worker thread, awaited.

        The records are updated on the loop, as in ``begin_async``.
        """
        kept, changed = self._note_external_stop_records(keys, reason)
        for session in changed:
            await self.sessions.save_async(session, fsync=True)
        return kept

    def _note_external_stop_records(self, keys: Any, reason: str) -> tuple[list[str], list[Session]]:
        kept: list[str] = []
        changed: list[Session] = []
        pressure: bool | None = False
        pressure_read = False
        for key in dict.fromkeys(keys):
            session = self.sessions.peek(key)
            if session is None or not recovery_pending(session.metadata):
                continue
            if not pressure_read:
                pressure, pressure_read = memory_pressure(), True
            record = session.metadata[RECOVERY_KEY]
            stop: dict[str, Any] = {"reason": str(reason), "at": time.time()}
            stops = max(0, int(record.get("external_stops") or 0))
            if pressure is None:
                stop["memory_unknown"] = True
            elif pressure:
                stop["memory_pressure"] = True
            elif stops >= MAX_EXTERNAL_STOPS:
                stop["limit"] = True
            else:
                record["external_stops"] = stops + 1
                record.pop(REPLAYED_AFTER_RESTART, None)
                kept.append(key)
            record["last_stop"] = stop
            changed.append(session)
        return kept, changed

    def schedule(self, session: Session, *, retry_after: float | None = None) -> float | None:
        record = session.metadata.get(RECOVERY_KEY)
        if not isinstance(record, dict):
            return None
        attempts = max(0, int(record.get("attempts") or 0)) + 1
        if attempts > MAX_RECOVERY_ATTEMPTS:
            # A provider down for this long will not come back in the next
            # minute: stop replaying the turn and let the error reach the user.
            self.finish(session)
            self.sessions.save(session, fsync=True)
            return None
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

    def adopt(self, session: Session, request: dict[str, Any], *, started_at: float) -> bool:
        """Track a request the gateway accepted on a channel without live recovery.

        Used at startup for an interrupted turn whose user message is already
        in history, so the replay never persists it a second time.
        """
        if RECOVERY_KEY in session.metadata or not isinstance(request, dict):
            return False
        if not _replayable_channel(request.get("channel")) or not request.get("chat_id"):
            return False
        session.metadata[RECOVERY_KEY] = {
            "id": uuid.uuid4().hex,
            "started_at": float(started_at),
            "attempts": 0,
            "request": request,
            "request_persisted": True,
            "status": "running",
        }
        self.sessions.save(session, fsync=True)
        return True

    def discover(
        self, *, channel: str, session_key: str | None = None, external: bool = False,
    ) -> list[str]:
        """Read once at startup; only this runtime's interactive channel resumes.

        ``external`` also resumes requests from non-interactive channels (chat
        apps), which only the gateway serves. A request is replayed after one
        restart only: cut again after that replay, it may be what brings the
        process down, so it waits for the user (``cut_twice``), unless an
        outside stop signal explained the cut (``note_external_stop``). One idle for
        over ``MAX_RECOVERY_AGE_S`` is dropped. Both end up in ``abandoned``.
        """
        keys = [session_key] if session_key else [row["key"] for row in self.sessions.list_sessions()]
        recovered = []
        now = time.time()
        for key in keys:
            session = self.sessions.peek(key)
            if session is None or not recovery_pending(session.metadata):
                continue
            record = session.metadata[RECOVERY_KEY]
            request = record.get("request")
            if not isinstance(request, dict):
                continue
            origin = request.get("channel")
            if origin != channel and not (
                external and origin not in _CHANNELS and _replayable_channel(origin)
            ):
                continue
            cut_twice = bool(record.get(REPLAYED_AFTER_RESTART))
            if cut_twice or self._expired(session, record, now) or not self._count_restart(record):
                self.finish(session)
                self.sessions.save(session, fsync=True)
                self.abandoned.append(key)
                if cut_twice:
                    self.cut_twice.add(key)
                continue
            self.sessions.save(session, fsync=True)
            self._restarted.add(key)
            due = record.get("next_attempt_at")
            self.due[key] = float(due) if isinstance(due, int | float) and math.isfinite(due) else now
            recovered.append(key)
        return recovered

    @staticmethod
    def _expired(session: Session, record: dict[str, Any], now: float) -> bool:
        started = record.get("started_at")
        last = float(started) if isinstance(started, int | float) and math.isfinite(started) else 0.0
        updated = getattr(session, "updated_at", None)
        if updated is not None:
            try:
                last = max(last, updated.timestamp())
            except (AttributeError, OSError, OverflowError, ValueError):
                pass
        return now - last > MAX_RECOVERY_AGE_S

    @staticmethod
    def _new_record(msg: InboundMessage) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "started_at": time.time(),
            "attempts": 0,
            "request": portable_request(msg),
        }

    @staticmethod
    def _count_restart(record: dict[str, Any]) -> bool:
        attempts = max(0, int(record.get("attempts") or 0)) + 1
        if attempts > MAX_RECOVERY_ATTEMPTS:
            return False
        record["attempts"] = attempts
        return True

    def take_due(
        self, *, active_keys: set[str], now: float | None = None, only_key: str | None = None,
        pending_saves: list[tuple[Session, bool]] | None = None,
    ) -> list[InboundMessage]:
        """Replays due now. With ``pending_saves`` the writes are listed there
        (session, fsync) for the caller to make before the replays run."""
        now = time.time() if now is None else now
        messages = []

        def save(session: Session, fsync: bool) -> None:
            if pending_saves is None:
                self.sessions.save(session, fsync=fsync)
            else:
                pending_saves.append((session, fsync))

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
            if (
                not isinstance(request, dict)
                or not _replayable_channel(request.get("channel"))
                or not request.get("chat_id")
            ):
                self.finish(session)
                save(session, False)
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
                save(session, True)
            if key in self._restarted:
                # Durable before the replay runs: if it takes the process down,
                # the next start sees it and does not replay it a second time.
                self._restarted.discard(key)
                record[REPLAYED_AFTER_RESTART] = True
                save(session, True)
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

    async def take_due_async(
        self, *, active_keys: set[str], now: float | None = None, only_key: str | None = None,
    ) -> list[InboundMessage]:
        """``take_due`` with its durable writes made in a worker thread.

        They are awaited before the replays are handed back, so a replay still
        never runs before its record is on disk. The main loop asks every
        second; a write there held the loop 1.0 s (CLI freeze log of 2026-10-31).
        """
        saves: list[tuple[Session, bool]] = []
        messages = self.take_due(active_keys=active_keys, now=now, only_key=only_key, pending_saves=saves)
        for session, fsync in saves:
            await self.sessions.save_async(session, fsync=fsync)
        return messages
