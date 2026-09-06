"""Let the agent stop mid-turn and offer a real choice.

Approvals are a security gate: allow or refuse a dangerous action. This is
the other stop. The agent does not know which path to take, so it puts two
to four options on screen, marks one as recommended, and waits. A free-text
"what do you want?" is how work starts on the wrong fork. A card with a
reco is how a colleague asks.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.agent.tools.context import current_request_context

ANSWERING_CHANNELS: frozenset[str] = frozenset({"websocket"})
DEFAULT_TIMEOUT_S = 600
# Reserved: the WebUI always offers a last option where the user types.
# Agents cannot occupy this id.
OTHER_OPTION_ID = "__other__"
CUSTOM_TEXT_MAX = 2000
SKIPPED = "The user skipped. Continue with the recommended option and say so."
TIMED_OUT = "The user did not answer in time. Continue with the recommended option and say so."
STOPPED = "The turn was stopped before a choice was made."


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    id: str
    label: str
    detail: str = ""
    recommended: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "recommended": self.recommended,
        }


@dataclass(frozen=True, slots=True)
class ChoiceRequest:
    question: str
    options: tuple[ChoiceOption, ...]
    allow_skip: bool = True

    @property
    def recommended(self) -> ChoiceOption:
        for option in self.options:
            if option.recommended:
                return option
        return self.options[0]

    def payload(self, request_id: str, *, expires_at_ms: int | None = None) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "question": self.question,
            "options": [option.payload() for option in self.options],
            "allow_skip": self.allow_skip,
            "recommended_id": self.recommended.id,
            "expires_at_ms": expires_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    option_id: str
    label: str
    detail: str = ""
    skipped: bool = False
    timed_out: bool = False
    unattended: bool = False
    custom: bool = False
    reason: str = ""

    def as_text(self) -> str:
        if self.unattended:
            return (
                "This channel cannot show a choice card. Write the options in "
                "your reply, mark the recommended one, and stop. Do not pick "
                "for the user.\n"
                f"{self.reason}"
            )
        if self.custom:
            return (
                "The user did not pick a listed option. They wrote this "
                "instead:\n"
                f"{self.detail}\n"
                "Follow that instruction. Do not substitute one of the listed "
                "options."
            )
        if self.skipped or self.timed_out:
            why = SKIPPED if self.skipped else TIMED_OUT
            return (
                f"{why} Recommended: {self.option_id} - {self.label}"
                + (f" ({self.detail})" if self.detail else "")
                + "."
            )
        return (
            f'The user chose "{self.option_id}" ({self.label})'
            + (f": {self.detail}" if self.detail else "")
            + "."
        )


@dataclass(slots=True)
class _Pending:
    request: ChoiceRequest
    future: asyncio.Future[ChoiceAnswer]
    session_key: str
    channel: str
    chat_id: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


Publisher = Callable[[str, ChoiceRequest, dict[str, Any]], Awaitable[None]]
Closer = Callable[[str, ChoiceAnswer, dict[str, Any]], Awaitable[None]]


class ChoiceBroker:
    """Holds open choices and matches answers back to the waiting tool."""

    def __init__(
        self,
        *,
        publish: Publisher | None = None,
        close: Closer | None = None,
        answering_channels: frozenset[str] = ANSWERING_CHANNELS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._publish = publish
        self._close = close
        self._answering_channels = answering_channels
        self._timeout_s = timeout_s
        self._pending: dict[str, _Pending] = {}

    def _timeout(self) -> float:
        return float(self._timeout_s)

    async def ask(self, request: ChoiceRequest) -> ChoiceAnswer:
        ctx = current_request_context()
        session_key = getattr(ctx, "session_key", "") or ""
        channel = getattr(ctx, "channel", "") or ""
        chat_id = getattr(ctx, "chat_id", "") or ""

        if (
            self._publish is None
            or not chat_id
            or channel not in self._answering_channels
        ):
            return _unattended(request)

        request_id = uuid.uuid4().hex
        timeout_s = self._timeout()
        loop = asyncio.get_running_loop()
        entry = _Pending(
            request=request,
            future=loop.create_future(),
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            expires_at=time.time() + timeout_s,
        )
        self._pending[request_id] = entry
        route = {"channel": channel, "chat_id": chat_id, "session_key": session_key}
        try:
            await self._publish(
                request_id,
                request,
                {**route, "expires_at_ms": int(entry.expires_at * 1000)},
            )
        except Exception:
            logger.exception("could not publish choice request")
            self._pending.pop(request_id, None)
            return _unattended(request)

        try:
            answer = await asyncio.wait_for(entry.future, timeout=timeout_s)
        except asyncio.TimeoutError:
            answer = _fallback(request, timed_out=True)
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            await self._notify_closed(
                request_id,
                _fallback(request, reason=STOPPED),
                route,
            )
            raise
        finally:
            self._pending.pop(request_id, None)

        await self._notify_closed(request_id, answer, route)
        return answer

    async def _notify_closed(
        self, request_id: str, answer: ChoiceAnswer, route: dict[str, Any]
    ) -> None:
        if self._close is None:
            return
        try:
            await self._close(request_id, answer, route)
        except Exception:
            logger.exception("could not close choice request {}", request_id)

    def resolve(
        self,
        request_id: str,
        *,
        option_id: str = "",
        skipped: bool = False,
        custom_text: str = "",
    ) -> bool:
        entry = self._pending.get(request_id)
        if entry is None or entry.future.done():
            return False
        if skipped:
            entry.future.set_result(_fallback(entry.request, skipped=True))
            return True
        typed = str(custom_text or "").strip()[:CUSTOM_TEXT_MAX]
        if typed:
            entry.future.set_result(
                ChoiceAnswer(
                    option_id=OTHER_OPTION_ID,
                    label="Other",
                    detail=typed,
                    custom=True,
                )
            )
            return True
        chosen = next(
            (option for option in entry.request.options if option.id == option_id),
            None,
        )
        if chosen is None:
            return False
        entry.future.set_result(
            ChoiceAnswer(
                option_id=chosen.id,
                label=chosen.label,
                detail=chosen.detail,
            )
        )
        return True

    def open_requests(self, session_key: str | None = None) -> list[dict[str, Any]]:
        return [
            entry.request.payload(request_id, expires_at_ms=int(entry.expires_at * 1000))
            for request_id, entry in self._pending.items()
            if session_key is None or entry.session_key == session_key
        ]

    def cancel_session(self, session_key: str, reason: str) -> int:
        count = 0
        for request_id, entry in list(self._pending.items()):
            if entry.session_key != session_key or entry.future.done():
                continue
            entry.future.set_result(_fallback(entry.request, reason=reason))
            count += 1
            self._pending.pop(request_id, None)
        return count


def _fallback(
    request: ChoiceRequest,
    *,
    skipped: bool = False,
    timed_out: bool = False,
    reason: str = "",
) -> ChoiceAnswer:
    chosen = request.recommended
    return ChoiceAnswer(
        option_id=chosen.id,
        label=chosen.label,
        detail=chosen.detail,
        skipped=skipped,
        timed_out=timed_out,
        reason=reason,
    )


def _unattended(request: ChoiceRequest) -> ChoiceAnswer:
    lines = [request.question]
    for option in request.options:
        mark = " (recommended)" if option.recommended else ""
        extra = f" - {option.detail}" if option.detail else ""
        lines.append(f"{option.id}) {option.label}{mark}{extra}")
    lines.append("Or write your own answer in the reply.")
    return ChoiceAnswer(
        option_id="",
        label="",
        unattended=True,
        reason="\n".join(lines),
    )


_current_broker: ContextVar[ChoiceBroker | None] = ContextVar(
    "navin_choice_broker", default=None
)


def bind_choice_broker(broker: ChoiceBroker | None) -> Token[ChoiceBroker | None]:
    return _current_broker.set(broker)


def reset_choice_broker(token: Token[ChoiceBroker | None]) -> None:
    _current_broker.reset(token)


def current_choice_broker() -> ChoiceBroker | None:
    return _current_broker.get()


async def request_choice(request: ChoiceRequest) -> ChoiceAnswer:
    broker = current_choice_broker()
    if broker is None:
        return _unattended(request)
    try:
        return await broker.ask(request)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("choice broker failed")
        return _unattended(request)


async def request_open_choices(
    bus: Any,
    session_key: str,
    *,
    timeout: float = 2.0,
) -> list[dict[str, Any]]:
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_CHOICES_QUERY,
        InboundMessage,
    )

    if not session_key:
        return []
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-choice",
            chat_id="runtime",
            content=RUNTIME_CONTROL_CHOICES_QUERY,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CHOICES_QUERY,
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


async def handle_choices_query(state: Any, msg: Any, registry: Any) -> bool:
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_CHOICES_QUERY,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_CHOICES_QUERY:
        return False
    ack = metadata.get(RUNTIME_CONTROL_ACK)
    broker = getattr(state, "choices", None)
    session_key = metadata.get("session_key")
    open_requests: list[dict[str, Any]] = []
    if isinstance(broker, ChoiceBroker) and isinstance(session_key, str):
        open_requests = broker.open_requests(session_key)
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(open_requests)
    return True


async def handle_choice_answer(state: Any, msg: Any, registry: Any) -> bool:
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_CHOICE_ANSWER,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_CHOICE_ANSWER:
        return False
    broker = getattr(state, "choices", None)
    if not isinstance(broker, ChoiceBroker):
        return True
    request_id = metadata.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return True
    delivered = broker.resolve(
        request_id,
        option_id=str(metadata.get("option_id") or ""),
        skipped=bool(metadata.get("skipped")),
        custom_text=str(metadata.get("custom_text") or ""),
    )
    if not delivered:
        logger.debug("choice {} had no waiter left", request_id)
    return True
