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
