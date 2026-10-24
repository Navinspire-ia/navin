# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collaboration Mode team exchanges reach every subscribed client."""

from __future__ import annotations

import asyncio
import json

from navin.bus.outbound_events import TeamMessageEvent
from navin.channels.websocket import WebSocketChannel


def test_team_message_reaches_subscribed_clients() -> None:
    channel = WebSocketChannel.__new__(WebSocketChannel)
    sent: list[tuple[object, dict]] = []

    async def fake_send(connection, raw, *, label=""):  # noqa: ARG001 - signature of _safe_send_to
        sent.append((connection, json.loads(raw)))

    channel._safe_send_to = fake_send  # type: ignore[method-assign]
    channel._subs = {"chat-1": {"conn-a", "conn-b"}}
    event = TeamMessageEvent(
        entry_id="e1",
        kind="message",
        sender={"id": "lead", "name": "Lead"},
        content="Sofia, take the hero scene.",
    )

    asyncio.run(channel.send_team_message("chat-1", event))

    assert sorted(conn for conn, _body in sent) == ["conn-a", "conn-b"]
    assert all(body["event"] == "team_message" and body["entry_id"] == "e1" for _c, body in sent)


def test_team_message_without_subscribers_sends_nothing() -> None:
    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._subs = {}
    event = TeamMessageEvent(entry_id="e2", kind="note", sender={"id": "user"})
    asyncio.run(channel.send_team_message("chat-1", event))
