"""Live share of the CLI chat through the relay (navin.live link), like the desktop.

ShareManager drives guest connections through a small channel surface (the
websocket channel on the desktop). ``CliShareChannel`` is that surface for one
CLI chat: guests receive the chat's events, and their messages land in the CLI
session under the CLI session key.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


def _later(value: Any) -> Any:
    """Run ``value`` now when it is a coroutine; awaitable either way.

    The manager awaits some channel hooks and calls others directly: a task
    runs in both cases, and a done future is harmless when nobody awaits it.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        if inspect.iscoroutine(value):
            value.close()
        return None
    if inspect.isawaitable(value):
        return asyncio.ensure_future(value)
    future = loop.create_future()
    future.set_result(value)
    return future


class _Table(dict):
    """Per-connection table that also answers when called like a lookup."""

    def __call__(self, conn: Any, *args: Any, **kwargs: Any) -> Any:
        return self.get(conn)


async def _deliver(conn: Any, payload: dict[str, Any]) -> None:
    send = getattr(conn, "send", None)
    if send is None:
        return
    try:
        result = send(json.dumps(payload, ensure_ascii=False, default=str))
    except TypeError:
        result = send(payload)
    if inspect.isawaitable(result):
        await result


class CliShareChannel:
    """The channel surface the share manager needs, for one CLI chat."""

    def __init__(
        self,
        *,
        chat_id: str,
        on_guest_message: Callable[[str, dict[str, Any]], Awaitable[None] | None],
        snapshot: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.chat_id = chat_id
        self._on_guest_message = on_guest_message
        self._snapshot = snapshot
        self._conn_identity: _Table = _Table()
        self._conn_default: _Table = _Table()
        self._guests: set[Any] = set()

    @property
    def guest_count(self) -> int:
        return len(self._guests)

    def _attach(self, conn: Any, *args: Any, **kwargs: Any) -> Any:
        self._guests.add(conn)
        self._conn_default.setdefault(conn, self.chat_id)
        return _later(None)

    def _presence_join(self, *args: Any, **kwargs: Any) -> Any:
        return _later(None)

    def _send_event(self, conn: Any, event: str, **fields: Any) -> Any:
        return _later(_deliver(conn, {"event": event, **fields}))

    def _hydrate_after_subscribe(self, *args: Any, **kwargs: Any) -> Any:
        # The share manager sends the thread pages itself (session_key_for).
        return _later(None)

    async def send_snapshot(self, conn: Any) -> None:
        if self._snapshot is None:
            return
        messages = await asyncio.to_thread(self._snapshot)
        await _deliver(conn, {"event": "thread", "chat_id": self.chat_id, "messages": messages})

    def _dispatch_envelope(self, conn: Any, client_id: Any = None, envelope: Any = None, *args: Any, **kwargs: Any) -> Any:
        if envelope is None and isinstance(client_id, (dict, str)):
            client_id, envelope = None, client_id
        return _later(self._dispatch(conn, envelope))

    async def _dispatch(self, conn: Any, envelope: Any) -> None:
        if isinstance(envelope, str):
            try:
                envelope = json.loads(envelope)
            except ValueError:
                return
        if not isinstance(envelope, dict) or envelope.get("type", "message") != "message":
            return
        text = str(envelope.get("content") or envelope.get("text") or "").strip()
        if not text:
            return
        identity = self._conn_identity.get(conn)
        result = self._on_guest_message(text, {"guest": identity if isinstance(identity, dict) else {}})
        if inspect.isawaitable(result):
            await result

    def _cleanup_connection(self, conn: Any, *args: Any, **kwargs: Any) -> Any:
        self._guests.discard(conn)
        self._conn_identity.pop(conn, None)
        self._conn_default.pop(conn, None)
        return _later(None)

    async def broadcast(self, payload: dict[str, Any]) -> int:
        """Send one chat event (delta, message, turn_end...) to every guest."""
        frame = {**payload, "chat_id": self.chat_id}
        guests = list(self._guests)
        for conn in guests:
            try:
                await _deliver(conn, frame)
            except Exception:  # noqa: BLE001 - a guest that left is cleaned up by the manager
                self._guests.discard(conn)
        return len(guests)


def frame_from_outbound(msg: Any) -> dict[str, Any] | None:
    """The guest frame for one CLI outbound message, as the desktop sends it."""
    from navin.bus.outbound_events import (
        StreamDeltaEvent,
        StreamedResponseEvent,
        StreamEndEvent,
        TurnEndEvent,
        outbound_event_from_message,
    )

    event = outbound_event_from_message(msg)
    text = msg.content or ""
    if isinstance(event, StreamDeltaEvent):
        frame: dict[str, Any] = {"event": "delta", "text": text}
        if getattr(event, "stream_id", None) is not None:
            frame["stream_id"] = event.stream_id
        return frame
    if isinstance(event, StreamEndEvent):
        frame = {"event": "stream_end"}
        if getattr(event, "discarded", False):
            frame["discarded"] = True
        return frame
    if isinstance(event, StreamedResponseEvent):
        return {"event": "message", "text": text}
    if isinstance(event, TurnEndEvent):
        frame = {"event": "turn_end"}
        latency = getattr(event, "latency_ms", None)
        if latency is not None:
            frame["latency_ms"] = int(latency)
        return frame
    return None


class CliLiveShare:
    """One live link on the current CLI chat, through the same relay as the desktop."""

    def __init__(
        self,
        *,
        chat_id: str,
        session_key: str,
        sessions_workspace: Path,
        project_root: Path | None,
        on_guest_message: Callable[[str, dict[str, Any]], Awaitable[None] | None],
        store_path: Path | None = None,
        manager_factory: Any = None,
    ) -> None:
        from navin.share.host import ShareManager

        if store_path is None:
            from navin.config.paths import get_runtime_subdir

            # Apart from the desktop store: two processes never rewrite one file.
            store_path = get_runtime_subdir("shares") / "cli-shares.json"
        self.channel = CliShareChannel(chat_id=chat_id, on_guest_message=on_guest_message)
        factory = manager_factory or ShareManager
        root = Path(project_root) if project_root else None
        self.manager = factory(
            self.channel,
            store_path=Path(store_path),
            sessions_workspace=Path(sessions_workspace),
            project_root_for=lambda _chat_id: root,
            session_key_for=lambda _chat_id: session_key,
        )
        self.share: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.share is not None

    @property
    def link(self) -> str:
        return str((self.share or {}).get("link") or "")

    def create(self, *, access: str = "read", ttl: str = "24h", max_guests: int = 5) -> dict[str, Any]:
        from navin.share.host import ACCESS_LEVELS

        access = (access or "read").strip().lower()
        if access not in ACCESS_LEVELS:
            raise ValueError(f"access must be one of: {', '.join(ACCESS_LEVELS)}")
        self.share = self.manager.create(
            self.channel.chat_id, ttl=(ttl or "24h").strip(), max_guests=max_guests, access=access,
        )
        if not self.link:
            raise RuntimeError("the relay gave no link")
        return self.share

    async def on_outbound(self, msg: Any) -> None:
        if self.share is None or not self.channel.guest_count:
            return
        frame = frame_from_outbound(msg)
        if frame is not None:
            await self.channel.broadcast(frame)

    async def on_user_text(self, text: str) -> None:
        if self.share is None or not self.channel.guest_count:
            return
        await self.channel.broadcast({"event": "user_message", "text": text, "content": text})

    async def stop(self) -> None:
        share, self.share = self.share, None
        share_id = str((share or {}).get("id") or (share or {}).get("share_id") or "")
        if share_id:
            result = self.manager.revoke(share_id)
            if inspect.isawaitable(result):
                await result
        result = self.manager.stop()
        if inspect.isawaitable(result):
            await result
