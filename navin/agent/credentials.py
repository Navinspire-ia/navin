# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ask the user for a login or a git account through a secure card.

A password typed in the chat lands in the history, the logs and the model
context. This is the other way: the agent asks for *a kind* of credential
(``site_login`` for a website, ``git_host`` for a forge), the UI shows a card
with masked fields or the GitHub device sign-in, and the values go from that
card straight to the existing vaults (``/api/browser-credentials``, Settings
> Git & GitHub). The agent only ever gets refs and a status back:
``saved site:app.example.com/password`` or ``connected github.com as octo``.

The answer from the UI is a claim. Before the tool call is released, the
broker checks the vault (or the forge account) itself, so a card that says
"saved" while nothing was stored reads as ``not_saved``.
"""

from __future__ import annotations

import asyncio
import re
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
KINDS = ("site_login", "git_host")
SITE_NAMES = ("username", "password", "totp")
DEFAULT_SITE_NAMES = ("username", "password")
REASON_MAX = 240
LOGIN_MAX = 80
SITES_POINTER = "Settings > Sites & logins"
GIT_POINTER = "Settings > Git & GitHub"
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}(:[0-9]{1,5})?$")
_LOGIN_RE = re.compile(r"[^A-Za-z0-9._@-]")

# What the UI may claim when it closes a card.
UI_STATUSES = frozenset({"saved", "connected", "declined"})


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    kind: str
    host: str
    names: tuple[str, ...] = ()
    reason: str = ""
    #: Raised by a tool on its own (browser login, git push), not by the model.
    auto: bool = False

    def refs(self) -> list[str]:
        if self.kind != "site_login":
            return []
        return [f"site:{self.host}/{name}" for name in self.names]

    def payload(self, request_id: str, *, expires_at_ms: int | None = None) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "kind": self.kind,
            "host": self.host,
            "names": list(self.names),
            "reason": self.reason,
            "expires_at_ms": expires_at_ms,
        }


@dataclass(frozen=True, slots=True)
class CredentialAnswer:
    """What came back. Never holds a secret: refs, a host, a login name."""

    status: str  # saved | connected | declined | not_saved | timed_out | stopped | unattended
    kind: str
    host: str
    refs: tuple[str, ...] = ()
    login: str = ""
    repeated: bool = False

    @property
    def ok(self) -> bool:
        return self.status in {"saved", "connected"}

    def as_text(self) -> str:
        where = self.host or "the site"
        pointer = SITES_POINTER if self.kind == "site_login" else GIT_POINTER
        prefix = "Already asked in this turn. " if self.repeated else ""
        if self.status == "saved":
            refs = ", ".join(self.refs)
            return (
                f"{prefix}The user saved {refs}. Use them by ref (browser action=login, "
                "or secret_ref); the tool fills the values and you never see them."
            )
        if self.status == "connected":
            who = f" as {self.login}" if self.login else ""
            return f"{prefix}Connected {where}{who}. Retry the git or forge action now."
        if self.status == "declined":
            return (
                f"{prefix}The user declined to give credentials for {where}. Do not ask "
                "again in this turn and never ask for a password in the chat; continue "
                "without it or say what is blocked."
            )
        if self.status == "timed_out":
            return (
                f"{prefix}The user did not fill the credential card for {where} in time. "
                f"Say what is blocked and that they can add it in {pointer}."
            )
        if self.status == "stopped":
            return "The turn was stopped before credentials were entered."
        if self.status == "not_saved":
            return (
                f"{prefix}The card for {where} closed but nothing usable was saved. Say "
                f"what is blocked and that they can add it in {pointer}."
            )
        return (
            f"No one can enter credentials in this run (unattended). Do not ask for "
            f"them in the chat; tell the user to add them for {where} in {pointer}, "
            "then continue with what does not need them."
        )


@dataclass(slots=True)
class _Pending:
    request: CredentialRequest
    future: asyncio.Future[dict[str, Any]]
    session_key: str
    channel: str
    chat_id: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


Publisher = Callable[[str, CredentialRequest, dict[str, Any]], Awaitable[None]]
Closer = Callable[[str, CredentialAnswer, dict[str, Any]], Awaitable[None]]


# -- verification seams (tests replace them) ---------------------------------


def _site_has(ref: str) -> bool:
    from navin.browser_credentials import BrowserCredentialStore

    return BrowserCredentialStore().has(ref)


def _git_host_status(host: str) -> tuple[bool, str]:
    """``(connected, login)`` for ``host`` with whatever token is configured now."""
    try:
        from navin.webui import git_accounts

        result = git_accounts.test_account(host)
        if result.get("ok"):
            return True, str(result.get("login") or "")
    except Exception as exc:  # noqa: BLE001 - fall back to "is there a token"
        logger.debug("credential check for {} failed: {}", host, type(exc).__name__)
    try:
        from navin.config.loader import load_config
        from navin.utils import git_credentials

        effective = git_credentials.as_full_config(load_config())
        kind = git_credentials.kind_for_host(host, config=effective)
        token, _source = git_credentials.resolve_host_token(host, kind=kind, config=effective)
        return bool(token), ""
    except Exception:  # noqa: BLE001
        return False, ""


def normalize_host(kind: str, value: str) -> str:
    """A bare host (``host[:port]`` for sites), or ``""`` when it is not one."""
    text = (value or "").strip()
    if not text:
        return ""
    if kind == "site_login":
        try:
            from navin.browser_credentials import BrowserCredentialError, normalize_site

            try:
                if text.lower().startswith("site:"):
                    text = text[5:].split("/", 1)[0]
                return normalize_site(text)
            except BrowserCredentialError:
                return ""
        except Exception:  # noqa: BLE001
            return ""
    lowered = text.lower()
    if "://" in lowered:
        from urllib.parse import urlsplit

        try:
            lowered = (urlsplit(lowered).hostname or "").lower()
        except ValueError:
            return ""
    elif "@" in lowered and ":" in lowered:
        # git@github.com:owner/repo.git
        lowered = lowered.split("@", 1)[1].split(":", 1)[0]
    lowered = lowered.strip("/").split("/")[0].rstrip(".")
    return lowered if _HOST_RE.match(lowered) else ""


def clean_names(names: Any) -> tuple[str, ...]:
    if not isinstance(names, (list, tuple)):
        return DEFAULT_SITE_NAMES
    out: list[str] = []
    for raw in names:
        name = str(raw or "").strip().lower()
        if name in SITE_NAMES and name not in out:
            out.append(name)
    return tuple(out) or DEFAULT_SITE_NAMES


def _clean_reason(reason: str) -> str:
    text = " ".join(str(reason or "").split())
    return text[:REASON_MAX]


def _turn_is_unattended(ctx: Any) -> bool:
    """Cron, heartbeat and subagent turns have no one at the card."""
    metadata = getattr(ctx, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    if metadata.get("subagent") or metadata.get("heartbeat"):
        return True
    try:
        from navin.cron.session_turns import is_cron_turn

        if is_cron_turn(metadata):
            return True
    except Exception:  # noqa: BLE001
        pass
    return getattr(ctx, "session_key", "") == "heartbeat"


class CredentialBroker:
    """Holds open credential cards and matches answers to the waiting tool."""

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

    @classmethod
    def for_bus(cls, bus: Any, **kwargs: Any) -> CredentialBroker:
        """A broker that talks to the UI through the outbound bus."""

        async def publish(request_id: str, request: CredentialRequest, route: dict[str, Any]) -> None:
            from navin.bus.outbound_events import (
                CredentialRequestedEvent,
                outbound_message_for_event,
            )

            await bus.publish_outbound(
                outbound_message_for_event(
                    channel=str(route.get("channel") or ""),
                    chat_id=str(route.get("chat_id") or ""),
                    event=CredentialRequestedEvent(
                        request_id=request_id,
                        kind=request.kind,
                        host=request.host,
                        names=list(request.names),
                        reason=request.reason,
                        expires_at_ms=route.get("expires_at_ms"),
                    ),
                )
            )

        async def close(request_id: str, answer: CredentialAnswer, route: dict[str, Any]) -> None:
            from navin.bus.outbound_events import CredentialClosedEvent, outbound_message_for_event

            await bus.publish_outbound(
                outbound_message_for_event(
                    channel=str(route.get("channel") or ""),
                    chat_id=str(route.get("chat_id") or ""),
                    event=CredentialClosedEvent(
                        request_id=request_id,
                        kind=answer.kind,
                        host=answer.host,
                        status=answer.status,
                        refs=list(answer.refs),
                        login=answer.login,
                    ),
                )
            )

        return cls(publish=publish, close=close, **kwargs)

    def enable_channel(self, channel: str) -> None:
        if channel:
            self._answering_channels = self._answering_channels | {channel}

    def can_ask(self) -> bool:
        ctx = current_request_context()
        return bool(
            self._publish is not None
            and getattr(ctx, "chat_id", "")
            and getattr(ctx, "channel", "") in self._answering_channels
            and not _turn_is_unattended(ctx)
        )

    async def ask(self, request: CredentialRequest) -> CredentialAnswer:
        if not self.can_ask():
            return _unattended(request)
        ctx = current_request_context()
        session_key = getattr(ctx, "session_key", "") or ""
        channel = getattr(ctx, "channel", "") or ""
        chat_id = getattr(ctx, "chat_id", "") or ""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        entry = _Pending(
            request=request,
            future=loop.create_future(),
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            expires_at=time.time() + float(self._timeout_s),
        )
        self._pending[request_id] = entry
        route = {"channel": channel, "chat_id": chat_id, "session_key": session_key}
        try:
            await self._publish(  # type: ignore[misc]
                request_id, request, {**route, "expires_at_ms": int(entry.expires_at * 1000)}
            )
        except Exception:
            logger.exception("could not publish credential request")
            self._pending.pop(request_id, None)
            return _unattended(request)

        try:
            raw = await asyncio.wait_for(entry.future, timeout=float(self._timeout_s))
        except asyncio.TimeoutError:
            raw = {"status": "timed_out"}
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            await self._notify_closed(
                request_id, CredentialAnswer("stopped", request.kind, request.host), route
            )
            raise
        finally:
            self._pending.pop(request_id, None)

        answer = await _verify(request, raw)
        await self._notify_closed(request_id, answer, route)
        return answer

    async def _notify_closed(
        self, request_id: str, answer: CredentialAnswer, route: dict[str, Any]
    ) -> None:
        if self._close is None:
            return
        try:
            await self._close(request_id, answer, route)
        except Exception:
            logger.exception("could not close credential request {}", request_id)

    def resolve(self, request_id: str, *, status: str, login: str = "") -> bool:
        """Release the waiting tool. Only a status and a login name travel here."""
        entry = self._pending.get(request_id)
        if entry is None or entry.future.done():
            return False
        clean = status if status in UI_STATUSES else "declined"
        entry.future.set_result(
            {"status": clean, "login": _LOGIN_RE.sub("", str(login or ""))[:LOGIN_MAX]}
        )
        return True

    def open_requests(self, session_key: str | None = None) -> list[dict[str, Any]]:
        return [
            entry.request.payload(request_id, expires_at_ms=int(entry.expires_at * 1000))
            for request_id, entry in self._pending.items()
            if session_key is None or entry.session_key == session_key
        ]

    def cancel_session(self, session_key: str) -> int:
        count = 0
        for request_id, entry in list(self._pending.items()):
            if entry.session_key != session_key or entry.future.done():
                continue
            entry.future.set_result({"status": "stopped"})
            count += 1
            self._pending.pop(request_id, None)
        return count


async def _verify(request: CredentialRequest, raw: dict[str, Any]) -> CredentialAnswer:
    """Turn the UI's claim into what is actually stored."""
    status = str(raw.get("status") or "declined")
    if status in {"declined", "timed_out", "stopped"}:
        return CredentialAnswer(status, request.kind, request.host)
    if request.kind == "site_login":
        present: list[str] = []
        for ref in request.refs():
            try:
                if await asyncio.to_thread(_site_has, ref):
                    present.append(ref)
            except Exception:  # noqa: BLE001
                continue
        # The password (or the only name asked) is what a login cannot go without.
        needed = [ref for ref in request.refs() if not ref.endswith("/totp")] or request.refs()
        if needed and all(ref in present for ref in needed):
            return CredentialAnswer("saved", request.kind, request.host, refs=tuple(present))
        return CredentialAnswer("not_saved", request.kind, request.host, refs=tuple(present))
    try:
        connected, login = await asyncio.to_thread(_git_host_status, request.host)
    except Exception:  # noqa: BLE001
        connected, login = False, ""
    if not connected:
        return CredentialAnswer("not_saved", request.kind, request.host)
    login = login or str(raw.get("login") or "")
    return CredentialAnswer("connected", request.kind, request.host, login=login)


def _unattended(request: CredentialRequest) -> CredentialAnswer:
    return CredentialAnswer("unattended", request.kind, request.host)


# -- turn binding --------------------------------------------------------------


@dataclass(slots=True)
class _TurnState:
    broker: CredentialBroker | None
    #: (kind, host) -> answer, so a host is asked at most once per turn.
    asked: dict[tuple[str, str], CredentialAnswer] = field(default_factory=dict)


_current: ContextVar[_TurnState | None] = ContextVar("navin_credential_broker", default=None)


def bind_credential_broker(broker: CredentialBroker | None) -> Token[_TurnState | None]:
    return _current.set(_TurnState(broker))


def reset_credential_broker(token: Token[_TurnState | None]) -> None:
    _current.reset(token)


def current_credential_broker() -> CredentialBroker | None:
    state = _current.get()
    return state.broker if state is not None else None


def credentials_interactive() -> bool:
    """True when a person can answer a credential card in this turn."""
    broker = current_credential_broker()
    return broker is not None and broker.can_ask()


async def request_credentials(request: CredentialRequest) -> CredentialAnswer:
    """Ask once per turn per host; later calls get the first answer back."""
    state = _current.get()
    broker = state.broker if state is not None else None
    if broker is None:
        return _unattended(request)
    key = (request.kind, request.host)
    previous = state.asked.get(key) if state is not None else None
    if previous is not None:
        return CredentialAnswer(
            previous.status, previous.kind, previous.host, previous.refs, previous.login, repeated=True
        )
    try:
        answer = await broker.ask(request)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("credential broker failed")
        return _unattended(request)
    if state is not None and answer.status != "unattended":
        state.asked[key] = answer
    return answer


def already_asked(kind: str, host: str) -> CredentialAnswer | None:
    state = _current.get()
    return state.asked.get((kind, host)) if state is not None else None


async def ensure_site_credentials(
    host: str, names: tuple[str, ...] | list[str], reason: str
) -> CredentialAnswer | None:
    """Auto-ask from a tool. None when nobody can answer or it was asked already."""
    site = normalize_host("site_login", host)
    if not site or not credentials_interactive() or already_asked("site_login", site):
        return None
    return await request_credentials(
        CredentialRequest("site_login", site, clean_names(list(names)), _clean_reason(reason), auto=True)
    )


async def ensure_git_credentials(host: str, reason: str) -> CredentialAnswer | None:
    name = normalize_host("git_host", host)
    if not name or not credentials_interactive() or already_asked("git_host", name):
        return None
    return await request_credentials(
        CredentialRequest("git_host", name, (), _clean_reason(reason), auto=True)
    )


def missing_site_hint(site: str, refs: list[str], answer: CredentialAnswer | None) -> str:
    """Error text for a login that has no saved values (and was not fixed)."""
    missing = ", ".join(refs)
    head = f"Error: no saved login for {site} ({missing} missing)."
    if answer is not None:
        return f"{head} {answer.as_text()}"
    if credentials_interactive():
        previous = already_asked("site_login", site)
        if previous is not None:
            return f"{head} {previous.as_text()}"
        return (
            f"{head} Call request_credentials(kind=site_login, host={site}) once with a "
            "clear reason; never ask the user to paste a password in the chat."
        )
    return (
        f"{head} Nobody can enter it in this run: tell the user to add it in "
        f"{SITES_POINTER}; never ask for a password in the chat."
    )


def git_auth_note(host: str, answer: CredentialAnswer | None) -> str:
    """Extra line for a git / forge auth failure that was not fixed."""
    if answer is not None:
        return answer.as_text()
    if credentials_interactive():
        previous = already_asked("git_host", host)
        if previous is not None:
            return previous.as_text()
        return (
            f"Call request_credentials(kind=git_host, host={host}) once with a clear reason; "
            "never ask the user to paste a token in the chat."
        )
    return ""


# -- runtime control (answers ride the control route, like choices) ----------


async def request_open_credentials(bus: Any, session_key: str, *, timeout: float = 2.0) -> list[dict[str, Any]]:
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_CREDENTIALS_QUERY,
        InboundMessage,
    )

    if not session_key:
        return []
    loop = asyncio.get_running_loop()
    ack: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-credentials",
            chat_id="runtime",
            content=RUNTIME_CONTROL_CREDENTIALS_QUERY,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CREDENTIALS_QUERY,
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


async def handle_credentials_query(state: Any, msg: Any, registry: Any) -> bool:
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_CREDENTIALS_QUERY,
    )

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_CREDENTIALS_QUERY:
        return False
    ack = metadata.get(RUNTIME_CONTROL_ACK)
    broker = getattr(state, "credentials", None)
    session_key = metadata.get("session_key")
    open_requests: list[dict[str, Any]] = []
    if isinstance(broker, CredentialBroker) and isinstance(session_key, str):
        open_requests = broker.open_requests(session_key)
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(open_requests)
    return True


async def handle_credential_answer(state: Any, msg: Any, registry: Any) -> bool:
    from navin.bus.events import INBOUND_META_RUNTIME_CONTROL, RUNTIME_CONTROL_CREDENTIAL_ANSWER

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_CREDENTIAL_ANSWER:
        return False
    broker = getattr(state, "credentials", None)
    if not isinstance(broker, CredentialBroker):
        return True
    request_id = metadata.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return True
    if not broker.resolve(
        request_id,
        status=str(metadata.get("status") or ""),
        login=str(metadata.get("login") or ""),
    ):
        logger.debug("credential request {} had no waiter left", request_id)
    return True
