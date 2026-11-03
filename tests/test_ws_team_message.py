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


class _Transcripts:
    def __init__(self) -> None:
        self.kept: list[tuple[str, dict]] = []

    def prepare_and_append(self, chat_id: str, event: dict, **_options: object) -> None:
        self.kept.append((chat_id, event))


MARK = {
    "id": "mark", "name": "Mark", "title": "Senior Developer", "role": "developer", "avatar": "visor",
    "color": "#2f7de1", "photo": "custom:0123456789ab", "gender": "male", "email": "mark@team.navin.local",
}


def test_a_teammate_report_is_its_own_message_in_the_chat_and_its_history() -> None:
    """Mark answers in the chat under his name, job and photo: in the window
    open now, and after a reload (the chat history keeps it)."""
    from navin.webui.transcript import replay_transcript_to_ui_messages

    channel = WebSocketChannel.__new__(WebSocketChannel)
    sent: list[dict] = []

    async def fake_send(connection, raw, *, label=""):  # noqa: ARG001 - signature of _safe_send_to
        sent.append(json.loads(raw))

    channel._safe_send_to = fake_send  # type: ignore[method-assign]
    channel._subs = {"chat-1": {"conn-a"}}
    channel._transcripts = _Transcripts()
    report = "Login fixed: the token was refreshed too late. " + "detail " * 900
    event = TeamMessageEvent(
        entry_id="e9", kind="result", sender=MARK, target={"id": "lead", "name": "Navin"},
        content=report[:4000], status="ok", full_text=report.strip(),
    )

    asyncio.run(channel.send_team_message("chat-1", event))

    assert [frame["event"] for frame in sent] == ["team_reply", "team_message"]
    reply = sent[0]
    assert reply["text"] == report.strip()  # the whole report, not the journal's 4000 characters
    assert reply["author"] == {key: value for key, value in MARK.items() if key != "email"}
    assert channel._transcripts.kept == [("chat-1", reply)]
    history = replay_transcript_to_ui_messages([
        {"event": "user", "text": "Mark, fix the login"},
        reply,
        {"event": "message", "text": "Salma tests it next."},
    ])
    assert [(m["role"], m.get("author", {}).get("name")) for m in history] == [
        ("user", None), ("assistant", "Mark"), ("assistant", None),
    ]
    assert history[1]["content"] == report.strip() and history[1]["author"]["title"] == "Senior Developer"

    # A window reopening replays the journal: nothing is added to the chat again.
    sent.clear()
    channel._transcripts.kept.clear()
    asyncio.run(channel.send_team_message("chat-1", event, replay=True))
    assert [frame["event"] for frame in sent] == ["team_message"]
    assert channel._transcripts.kept == []


def test_a_teammate_question_is_kept_even_with_no_window_open() -> None:
    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._subs = {}
    channel._transcripts = _Transcripts()
    question = TeamMessageEvent(
        entry_id="q1", kind="message", sender=MARK, target={"id": "user", "name": "You"},
        content="Should the old sessions be migrated too?",
    )
    asyncio.run(channel.send_team_message("chat-1", question))
    assert [event["text"] for _chat, event in channel._transcripts.kept] == [
        "Should the old sessions be migrated too?",
    ]


def test_journal_only_exchanges_never_become_chat_messages() -> None:
    from navin.webui.transcript import team_reply_event

    salma = {"id": "salma", "name": "Salma"}
    lead = {"id": "lead", "name": "Navin"}
    for kind, sender, target in (
        ("assign", lead, MARK),  # the lead's assignment
        ("message", MARK, salma),  # teammates between them
        ("result", {"id": "helper-1", "name": "Helper"}, MARK),  # a helper reporting to Mark
        ("message", lead, {"id": "user"}),  # the lead talks in its own messages
        ("result", MARK, {"id": "lead"}),  # empty report
    ):
        content = "" if kind == "result" and sender is MARK else "text"
        event = TeamMessageEvent(entry_id="x", kind=kind, sender=sender, target=target, content=content)
        assert team_reply_event("chat-1", event) is None, (kind, sender["id"], target["id"])
