"""Let a tool stop mid-turn, ask the user, and continue with the answer.

Every guard in navin used to be decided in advance: a regex deny-list, a
workspace boundary, an SSRF check. That makes the answer to "may I do this?"
independent of what is actually being done, so the only way to permit one
dangerous command was to permit the whole shape of it in the configuration, for
every future turn. The agent could not ask, and the user could not say yes once.

This module adds the missing round trip. A tool calls :func:`request_approval`,
which suspends that tool call (nothing else: there is no per-call timeout, and
the turn keeps its session lock) while the request is published to the channel.
The user answers, the answer travels back through the runtime-control path -
the only inbound route that is handled while a tool is running - and the tool
resumes with a decision.

Three properties are deliberate:

* **The gate is off unless asked for.** navin does not decide on an operator's
  behalf that the agent should stop and check; an operator who wants that turns
  ``tools.approvals.enabled`` on. With it off, nothing here interrupts anything.
* **Once on, a missing answer refuses.** No channel able to ask, a timeout, a
  cancelled turn: all of these deny, because an operator who asked to be
  consulted has not been. Every degraded path says out loud why it refused so
  the model can tell the user what to do instead.
* **Remembering is per session, never persisted.** "Allow every time" here means
  "for the rest of this conversation". A permanent exemption is what the exec
  allow-list is for, and it belongs in the config where it can be reviewed.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from loguru import logger
from pydantic import Field

from navin.config_base import Base

# What the model is told when nobody could be asked. It has to be actionable:
# the turn is not going to succeed, so the useful outcome is the model telling
# the user precisely what it wanted to do and how they can allow it.
NO_APPROVER = (
    "This needs the user's approval and no one could be asked on this channel. "
    "Do not try to work around the refusal. Tell the user exactly what you "
    "wanted to do and why, and let them either run it themselves or allow it "
    "from the WebUI."
)
TIMED_OUT = (
    "The user did not answer the approval request in time, so it was refused. "
    "Say what you were asking for and stop; do not retry the same operation."
)
DECLINED = "The user refused this operation. Do not attempt it another way."

# Channels that can put a request on screen and send the answer back. A channel
# joins this set once it renders the card and replies with an approval_decision
# envelope; until then, publishing to it would wait out the timeout for nothing,
# which is worse than treating the run as unattended from the start.
ANSWERING_CHANNELS: frozenset[str] = frozenset({"websocket"})


class ApprovalConfig(Base):
    """When and for how long the agent may stop to ask.

    Off by default. The agent acts on its own, and an operator who wants it to
    stop and ask before destructive work turns this on - the product does not
    decide that for them.
    """

    enabled: bool = False
    # A human has to read the request, and may be in another window. Well above
    # a machine timeout, well below "forever": a turn that hangs all afternoon
    # holds its session lock and looks broken.
    timeout_s: int = Field(default=300, ge=10, le=3600)
    # Whether "allow every time" is offered at all. Off means each occurrence is
    # asked separately, which some operators will want.
    remember: bool = True
    # What the shell asks about once the gate is on. ``destructive`` is the
    # assisted default (rm, git reset --hard, terraform destroy, …).
    # ``always`` pauses before every exec command.
    exec_ask: Literal["destructive", "always"] = "destructive"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """One question, phrased for someone who is not reading the transcript."""

    tool: str
    # Imperative and specific: "delete 12 files", not "perform an operation".
    action: str
    # Why it is being asked, in the user's terms.
    reason: str
    # The literal thing at stake: the command, the paths, the ref.
    detail: str = ""
    # What cannot be taken back, when that is the point of asking.
    consequence: str = ""
    # Requests sharing a scope are covered by one "allow every time". Keep it
    # narrow enough that consent stays meaningful: a rule name, not a tool name.
    scope: str = ""
    # What to do when there is nobody to ask, which is the normal case for the
    # CLI, a scheduled run and a subagent. It must be whatever the tool did
    # before it started asking, so that adding a question to an operation never
    # silently takes that operation away from an unattended run. Set it to True
    # for something that used to go through with a warning, and leave it False
    # for something that used to be refused outright.
    allow_when_unattended: bool = False

    def payload(self, request_id: str, *, expires_at_ms: int | None = None) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "tool": self.tool,
            "action": self.action,
            "reason": self.reason,
            "detail": self.detail,
            "consequence": self.consequence,
            "scope": self.scope,
            "expires_at_ms": expires_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    allowed: bool
    remembered: bool = False
    # Filled on refusal, and handed to the model as is.
    reason: str = ""


def _unattended(request: ApprovalRequest, why: str) -> ApprovalDecision:
    """The decision for a run with nobody watching: whatever used to happen."""
    if request.allow_when_unattended:
        return ApprovalDecision(allowed=True, reason=why)
    return ApprovalDecision(allowed=False, reason=why)


class ApprovalGate(Protocol):
    """What a tool sees. Implementations decide how the user is reached."""

    async def ask(self, request: ApprovalRequest) -> ApprovalDecision: ...


@dataclass(slots=True)
class _Pending:
    request: ApprovalRequest
    future: asyncio.Future[ApprovalDecision]
    session_key: str
    channel: str
    chat_id: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


# Publishes a request to whoever can show it, and is told when it is over so a
# stale card can be cleared.
Publisher = Callable[[str, ApprovalRequest, dict[str, Any]], Awaitable[None]]
Closer = Callable[[str, ApprovalDecision, dict[str, Any]], Awaitable[None]]


class ApprovalBroker:
    """Holds the open questions and matches answers back to their waiter.

    The waiting side is an ``asyncio.Future`` per request, kept in a registry
    keyed by a request id, because the answer comes from the browser and cannot
    carry a Python object the way the in-process runtime-control acknowledgement
    does. Anything that ends a request removes it from the registry, including
    the failure paths: a leaked entry is a card the user can never dismiss.
    """

    def __init__(
        self,
        *,
        publish: Publisher | None = None,
        close: Closer | None = None,
        config: ApprovalConfig | None = None,
        answering_channels: frozenset[str] = ANSWERING_CHANNELS,
    ) -> None:
        self._publish = publish
        self._close = close
        self._config = config or ApprovalConfig()
        self._answering_channels = answering_channels
        self._pending: dict[str, _Pending] = {}
        # session key -> scopes the user allowed for the rest of the session.
        self._remembered: dict[str, set[str]] = {}

    def apply_config(self, config: ApprovalConfig) -> None:
        """Hot-apply a new approvals posture from Settings > Security."""
        self._config = config

    def _timeout_s(self) -> float:
        """How long to wait. Overridable so tests do not sleep for minutes."""
        return float(self._config.timeout_s)

    # -- asking -------------------------------------------------------------

    async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
        if not self._config.enabled:
            # Turning the gate off means the operator does not want to be asked,
            # so the operation goes ahead. Reading it as "nobody can be asked,
            # therefore refuse" made disabling approvals *more* restrictive than
            # leaving them on - the one setting meant to remove a stop was the
            # only way to make it permanent.
            return ApprovalDecision(
                allowed=True,
                reason="Approval requests are disabled in the configuration.",
            )

        from navin.agent.tools.context import current_request_context

        ctx = current_request_context()
        session_key = getattr(ctx, "session_key", "") or ""
        channel = getattr(ctx, "channel", "") or ""
        chat_id = getattr(ctx, "chat_id", "") or ""

        if request.scope and request.scope in self._remembered.get(session_key, ()):
            return ApprovalDecision(allowed=True, remembered=True)

        if (
            self._publish is None
            or not chat_id
            or channel not in self._answering_channels
        ):
            return _unattended(request, NO_APPROVER)

        request_id = uuid.uuid4().hex
        timeout_s = self._timeout_s()
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
                request_id, request, {**route, "expires_at_ms": int(entry.expires_at * 1000)}
            )
        except Exception:
            logger.exception("could not publish approval request for {}", request.tool)
            self._pending.pop(request_id, None)
            return _unattended(request, NO_APPROVER)

        try:
            decision = await asyncio.wait_for(entry.future, timeout=timeout_s)
        except asyncio.TimeoutError:
            decision = ApprovalDecision(allowed=False, reason=TIMED_OUT)
        except asyncio.CancelledError:
            # The turn was stopped. Close the card before letting the
            # cancellation through, or it stays on screen with no waiter.
            self._pending.pop(request_id, None)
            await self._notify_closed(
                request_id,
                ApprovalDecision(allowed=False, reason="the turn was stopped"),
                route,
            )
            raise
        finally:
            self._pending.pop(request_id, None)

        if decision.allowed and decision.remembered and request.scope and self._config.remember:
            self._remembered.setdefault(session_key, set()).add(request.scope)

        await self._notify_closed(request_id, decision, route)
        return decision

    async def _notify_closed(
        self, request_id: str, decision: ApprovalDecision, route: dict[str, Any]
    ) -> None:
        if self._close is None:
            return
        try:
            await self._close(request_id, decision, route)
        except Exception:
            logger.exception("could not close approval request {}", request_id)

    # -- answering ----------------------------------------------------------

    def resolve(
        self,
        request_id: str,
        *,
        allowed: bool,
        remember: bool = False,
        reason: str = "",
        answerer_chats: Any = None,
    ) -> bool:
        """Hand an answer to its waiter. False when nothing was waiting.

        ``answerer_chats`` is the set of chats the answering connection is
        subscribed to. When provided, the answer only counts if that set
        includes the chat that asked: the request card is shown to the chat's
        subscribers, so an answer arriving from a connection that never saw
        the card is either a bug or someone replaying a captured request id
        from another conversation. None keeps the historical trust (internal
        callers and tests that resolve directly).
        """
        entry = self._pending.get(request_id)
        if entry is None or entry.future.done():
            return False
        if answerer_chats is not None and entry.chat_id:
            chats = {c for c in answerer_chats if isinstance(c, str)}
            if entry.chat_id not in chats:
                logger.warning(
                    "approval {} refused: answered from a connection not "
                    "subscribed to chat {}",
                    request_id,
                    entry.chat_id,
                )
                return False
        entry.future.set_result(
            ApprovalDecision(
                allowed=allowed,
                remembered=remember and allowed,
                reason=reason or ("" if allowed else DECLINED),
            )
        )
        return True

    def open_requests(self, session_key: str | None = None) -> list[dict[str, Any]]:
        """Questions still on the table, so a reloaded browser can show them."""
        return [
            {
                **entry.request.payload(
                    request_id, expires_at_ms=int(entry.expires_at * 1000)
                ),
                "remember_offered": bool(entry.request.scope and self._config.remember),
            }
            for request_id, entry in self._pending.items()
            if session_key is None or entry.session_key == session_key
        ]

    def cancel_session(self, session_key: str, reason: str) -> int:
        """Refuse everything still open for a session, e.g. on /stop."""
        count = 0
        for request_id, entry in list(self._pending.items()):
            if entry.session_key != session_key or entry.future.done():
                continue
            entry.future.set_result(ApprovalDecision(allowed=False, reason=reason))
            count += 1
            self._pending.pop(request_id, None)
        return count

    def forget(self, session_key: str) -> None:
        self._remembered.pop(session_key, None)


_current_gate: ContextVar[ApprovalGate | None] = ContextVar(
    "navin_approval_gate", default=None
)


def bind_approval_gate(gate: ApprovalGate | None) -> Token[ApprovalGate | None]:
    return _current_gate.set(gate)


def reset_approval_gate(token: Token[ApprovalGate | None]) -> None:
    _current_gate.reset(token)


def current_approval_gate() -> ApprovalGate | None:
    return _current_gate.get()


async def request_approval(request: ApprovalRequest) -> ApprovalDecision:
    """Ask the user, or refuse when there is no one to ask.

    Tools call this and nothing else. The absence of a gate is not an error: a
    cron run, a subagent and a plain CLI turn all have no one watching, and the
    right answer there is a refusal that explains itself.
    """
    gate = current_approval_gate()
    if gate is None:
        return _unattended(request, NO_APPROVER)
    try:
        return await gate.ask(request)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("approval gate failed for {}", request.tool)
        return _unattended(request, NO_APPROVER)


async def request_open_approvals(
    bus: Any,
    session_key: str,
    *,
    timeout: float = 2.0,
) -> list[dict[str, Any]]:
    """Ask the running loop what *session_key* is still waiting on.

    A refreshed or reconnected browser has forgotten the cards it was showing
    while the tools that opened them are still suspended. Only the loop holds the
    registry, so this borrows the runtime-control acknowledgement path to reach
    it, the same way the exec policy hot reload does. A short timeout on purpose:
    this runs while a client is attaching, and no answer just means no cards.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_APPROVALS_QUERY,
        InboundMessage,
    )

    if not session_key:
        return []
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-approval",
            chat_id="runtime",
            content=RUNTIME_CONTROL_APPROVALS_QUERY,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_APPROVALS_QUERY,
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


async def handle_approvals_query(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: answer a reconnecting client's question."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_APPROVALS_QUERY,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_APPROVALS_QUERY:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    broker = getattr(state, "approvals", None)
    session_key = metadata.get("session_key")
    open_requests: list[dict[str, Any]] = []
    if isinstance(broker, ApprovalBroker) and isinstance(session_key, str):
        open_requests = broker.open_requests(session_key)
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(open_requests)
    return True


async def handle_approval_decision(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: hand a websocket answer to its waiting tool.

    Returns True once it recognises the message, whether or not a waiter was
    still there: a late answer to a request that already timed out is not an
    error, and must not fall through to the other control handlers.
    """
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_APPROVAL_DECISION,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_APPROVAL_DECISION:
        return False

    broker = getattr(state, "approvals", None)
    if not isinstance(broker, ApprovalBroker):
        return True
    request_id = metadata.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return True
    answerer_chats = metadata.get("answerer_chats")
    delivered = broker.resolve(
        request_id,
        allowed=bool(metadata.get("allowed")),
        remember=bool(metadata.get("remember")),
        answerer_chats=answerer_chats if isinstance(answerer_chats, list) else None,
    )
    if not delivered:
        logger.debug("approval {} had no waiter left", request_id)
    return True


class UnattendedGate:
    """A gate for runs with no human attached, such as a subagent.

    Binding this rather than leaving the gate unset is only about the wording:
    the message names the context, so a refused subagent reports the situation to
    its parent instead of retrying. What it decides is the same as everywhere
    else with nobody to ask - whatever the operation did before it could ask.
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def ask(self, request: ApprovalRequest) -> ApprovalDecision:
        return _unattended(request, self._reason)
