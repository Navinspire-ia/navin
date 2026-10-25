"""A run start / end drops the cached sessions list, so a list fetched while the
turn ran never turns the sidebar's running glyph back on after it ended."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from navin.bus.outbound_events import GoalStatusEvent, outbound_message_for_event
from navin.webui.route_cache import CoalescingCache as RouteCache


def test_run_status_drops_the_cached_sessions_list() -> None:
    from navin.channels.websocket import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    cache = RouteCache()
    channel._http_router = SimpleNamespace(route_cache=cache)  # type: ignore[attr-defined]
    channel._subs = {}  # type: ignore[attr-defined]
    channel._conn_chats = {}  # type: ignore[attr-defined]
    channel._transcripts = MagicMock()
    channel.logger = MagicMock()

    async def scenario() -> None:
        stale = {"sessions": [{"key": "websocket:c1", "run_started_at": 1.0}]}
        await cache.get("webui:sessions", lambda: asyncio.sleep(0, result=stale), ttl=60)
        assert await cache.get("webui:sessions", lambda: asyncio.sleep(0, result={}), ttl=60) is stale
        msg = outbound_message_for_event(channel="websocket", chat_id="c1", event=GoalStatusEvent(status="idle"))
        await channel.send(msg)
        fresh = {"sessions": [{"key": "websocket:c1"}]}
        assert await cache.get("webui:sessions", lambda: asyncio.sleep(0, result=fresh), ttl=60) is fresh

    asyncio.run(scenario())


def _bare_channel():
    from navin.channels.websocket import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._http_router = SimpleNamespace(route_cache=RouteCache())  # type: ignore[attr-defined]
    channel._transcripts = MagicMock()
    channel.logger = MagicMock()
    sent: list[tuple[object, dict]] = []

    async def fake_send(conn: object, raw: str, *args: object, **kwargs: object) -> bool:
        import json

        sent.append((conn, json.loads(raw)))
        return True

    channel._safe_send_to = fake_send  # type: ignore[method-assign]
    return channel, sent


def test_parallel_agents_without_subscriber_still_reach_the_sidebar() -> None:
    channel, sent = _bare_channel()
    window = object()
    channel._subs = {"c1": {window}}  # type: ignore[attr-defined]
    channel._conn_chats = {window: {"c1"}}  # type: ignore[attr-defined]

    async def scenario() -> None:
        for chat in ("c1", "c2", "c3"):
            msg = outbound_message_for_event(
                channel="websocket", chat_id=chat, event=GoalStatusEvent(status="running", started_at=5.0)
            )
            await channel.send(msg)

    asyncio.run(scenario())
    running = [b["chat_id"] for _, b in sent if b.get("event") == "goal_status" and b["status"] == "running"]
    assert running == ["c1", "c2", "c3"]


def test_reconnect_replays_every_running_turn() -> None:
    from navin.session import webui_turns

    channel, sent = _bare_channel()
    window = object()
    saved = dict(webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT)
    webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
    webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.update({"a": 1.0, "b": 2.0, "c": 3.0})
    try:
        asyncio.run(channel._replay_running_turns(window, skip="a"))
    finally:
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.clear()
        webui_turns._WEBSOCKET_TURN_WALL_STARTED_AT.update(saved)
    assert [(b["chat_id"], b["started_at"]) for _, b in sent] == [("b", 2.0), ("c", 3.0)]
    assert all(conn is window for conn, _ in sent)
