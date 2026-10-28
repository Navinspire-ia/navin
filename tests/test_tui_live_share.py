"""CLI live share: the CLI chat shared through the relay, like the desktop."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import (
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    TurnEndEvent,
)
from navin.share.host import ShareManager
from navin.tui.live_share import CliLiveShare, CliShareChannel, frame_from_outbound


class _Guest:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, text: str) -> None:
        self.frames.append(json.loads(text))


class _FakeManager:
    """Records what CliLiveShare asks of the share manager."""

    def __init__(self, channel: Any, **kwargs: Any) -> None:
        self.channel, self.kwargs = channel, kwargs
        self.created: list[dict] = []
        self.revoked: list[str] = []
        self.stopped = False

    def create(self, chat_id: str, **kwargs: Any) -> dict:
        self.created.append({"chat_id": chat_id, **kwargs})
        return {"id": "s1", "link": "https://navin.live/s/abc#key"}

    async def revoke(self, share_id: str) -> bool:
        self.revoked.append(share_id)
        return True

    async def stop(self) -> None:
        self.stopped = True


def _msg(event: Any, content: str = "") -> OutboundMessage:
    return OutboundMessage(channel="cli", chat_id="direct", content=content, event=event)


def test_guest_sees_chat_events_and_its_messages_reach_the_cli() -> None:
    received: list[str] = []

    async def scenario() -> None:
        channel = CliShareChannel(
            chat_id="direct",
            on_guest_message=lambda text, meta: received.append(text),
            snapshot=lambda: [{"role": "user", "content": "bonjour"}],
        )
        guest = _Guest()
        await channel._attach(guest, "direct")
        await channel._hydrate_after_subscribe("direct")
        await channel.send_snapshot(guest)
        assert channel.guest_count == 1
        assert guest.frames[0]["event"] == "thread"
        assert await channel.broadcast({"event": "delta", "text": "sal"}) == 1
        assert guest.frames[-1] == {"event": "delta", "text": "sal", "chat_id": "direct"}
        await channel._dispatch_envelope(guest, "client-1", {"type": "message", "content": "une question"})
        await channel._dispatch_envelope(guest, "client-1", {"type": "typing"})
        await channel._cleanup_connection(guest)
        assert channel.guest_count == 0

    asyncio.run(scenario())
    assert received == ["une question"]


def test_outbound_events_become_the_desktop_guest_frames() -> None:
    assert frame_from_outbound(_msg(StreamDeltaEvent(content="sa", stream_id="1"), "sa")) == {
        "event": "delta", "text": "sa", "stream_id": "1",
    }
    assert frame_from_outbound(_msg(StreamEndEvent(discarded=True))) == {"event": "stream_end", "discarded": True}
    assert frame_from_outbound(_msg(StreamedResponseEvent(), "fini")) == {"event": "message", "text": "fini"}
    assert frame_from_outbound(_msg(TurnEndEvent(latency_ms=12))) == {"event": "turn_end", "latency_ms": 12}


def test_tools_subagents_choices_and_approvals_reach_the_guest_like_the_desktop() -> None:
    # Text only used to cross: a guest on a phone saw the replies and nothing of
    # the work between them. These are the desktop channel's own frames.
    from navin.bus.outbound_events import (
        ApprovalClosedEvent,
        ApprovalRequestedEvent,
        ChoiceClosedEvent,
        ChoiceRequestedEvent,
        ProgressEvent,
        SubagentProgressEvent,
    )

    tool = frame_from_outbound(_msg(ProgressEvent(content="Ran pytest", tool_hint=True, tool_events=[{"tool": "exec", "phase": "end"}]), "Ran pytest"))
    assert tool["event"] == "message" and tool["kind"] == "tool_hint" and tool["text"] == "Ran pytest"
    assert tool["tool_events"][0]["tool"] == "exec"
    assert frame_from_outbound(_msg(ProgressEvent(content="Reading files"), "Reading files")) == {
        "event": "message", "text": "Reading files", "kind": "progress",
    }
    assert frame_from_outbound(_msg(ProgressEvent(content="thinking", reasoning_delta=True), "thinking")) is None
    sub = frame_from_outbound(_msg(SubagentProgressEvent(task_id="t1", label="Writer", phase="running", status_line="Editing", model="m")))
    assert sub["event"] == "subagent_progress" and sub["task_id"] == "t1" and sub["label"] == "Writer" and sub["done"] is False
    choice = frame_from_outbound(_msg(ChoiceRequestedEvent(request_id="r1", question="Which?", options=[{"id": "a", "label": "A"}])))
    assert choice["event"] == "choice_request" and choice["request_id"] == "r1" and choice["options"] == [{"id": "a", "label": "A"}]
    assert frame_from_outbound(_msg(ChoiceClosedEvent(request_id="r1", option_id="a")))["event"] == "choice_closed"
    approval = frame_from_outbound(_msg(ApprovalRequestedEvent(request_id="p1", tool="exec", action="rm", reason="deletes")))
    assert approval["event"] == "approval_request" and approval["tool"] == "exec" and approval["request_id"] == "p1"
    closed = frame_from_outbound(_msg(ApprovalClosedEvent(request_id="p1", allowed=False)))
    assert closed["event"] == "approval_closed" and closed["request_id"] == "p1" and closed["allowed"] is False


def test_live_share_uses_the_cli_session_key_and_forwards_events(tmp_path: Path) -> None:
    async def scenario() -> tuple[CliLiveShare, _Guest]:
        share = CliLiveShare(
            chat_id="direct",
            session_key="cli:direct",
            sessions_workspace=tmp_path,
            project_root=tmp_path,
            on_guest_message=lambda text, meta: None,
            store_path=tmp_path / "cli-shares.json",
            manager_factory=_FakeManager,
        )
        assert share.manager.kwargs["session_key_for"]("direct") == "cli:direct"
        assert share.manager.kwargs["project_root_for"]("direct") == tmp_path
        with pytest.raises(ValueError):
            share.create(access="admin")
        share.create(access="Write", ttl="1h")
        assert share.manager.created[-1]["access"] == "write"
        assert share.link.startswith("https://navin.live/")
        guest = _Guest()
        await share.channel._attach(guest, "direct")
        await share.on_user_text("salut")
        await share.on_outbound(_msg(StreamedResponseEvent(), "reponse"))
        await share.stop()
        return share, guest

    share, guest = asyncio.run(scenario())
    assert [frame["event"] for frame in guest.frames] == ["user_message", "message"]
    assert guest.frames[0]["text"] == "salut"
    assert share.manager.revoked == ["s1"]
    assert share.manager.stopped is True
    assert share.active is False


def test_real_share_manager_reads_the_cli_session(tmp_path: Path) -> None:
    async def scenario() -> dict:
        channel = CliShareChannel(chat_id="direct", on_guest_message=lambda text, meta: None)
        manager = ShareManager(
            channel,
            store_path=tmp_path / "shares.json",
            sessions_workspace=tmp_path,
            session_key_for=lambda chat_id: f"cli:{chat_id}",
        )
        assert manager.session_key_for("direct") == "cli:direct"
        share = manager.create("direct")
        await manager.stop()
        return share

    assert "link" in asyncio.run(scenario())


def test_runtime_forwards_to_the_share_observer() -> None:
    from navin.tui.runtime import TuiRuntime

    dispatch = inspect.getsource(TuiRuntime._dispatch)
    send = inspect.getsource(TuiRuntime.send)
    assert "on_outbound" in dispatch and "msg.channel == self.channel" in dispatch
    assert "on_user_text" in send


def test_cli_palette_offers_live_share_copy_and_stop() -> None:
    from navin.tui.app import NavinActions, NavinApp

    source = inspect.getsource(NavinActions._entries)
    for action in ("share_live", "copy_live_link", "stop_live_share"):
        assert f'"{action}"' in source
        assert callable(getattr(NavinApp, f"action_{action}", None))


def test_fit_link_keeps_head_and_tail_within_width() -> None:
    from navin.tui.live_share_line import fit_link

    link = "https://relay.navin.live/s/abcdef123456#verylongsecretkey0123456789"
    assert fit_link(link, 200) == link
    short = fit_link(link, 40)
    assert len(short) == 40
    assert "\u2026" in short
    assert short.startswith("https://relay.navin.live/s/")
    assert short.endswith(link[-8:])
    for width in range(0, 90):
        assert len(fit_link(link, width)) <= width
    assert fit_link(link, 0) == ""
    assert fit_link("", 10) == ""


class _FakeShare:
    def __init__(self, link: str, guests: int = 0) -> None:
        self.link, self.active = link, True
        self.channel = _Channel(guests)

    async def stop(self) -> None:
        self.active = False


class _Channel:
    def __init__(self, guests: int) -> None:
        self.guest_count = guests


def test_live_share_line_shows_the_link_with_copy_and_stop(tmp_path: Path) -> None:
    from navin.tui.live_share_line import LiveShareLine
    from tests.test_tui_queue import make_app

    link = "https://relay.navin.live/s/abc123#key456"

    async def run() -> None:
        app = make_app(tmp_path)
        copied: list[str] = []
        flashes: list[str] = []
        app.copy_to_clipboard = lambda text, **kwargs: copied.append(text) or True

        async def flash(text: str, level: str = "info") -> None:
            flashes.append(text)

        app._flash = flash
        async with app.run_test(size=(160, 32)) as pilot:
            line = app.query_one("#live-share", LiveShareLine)
            assert line.display is False and not line.has_class("-visible")
            app._live_share = _FakeShare(link)
            app._refresh_live_share_line()
            await pilot.pause()
            assert line.display is True and line.has_class("-visible")
            assert str(line.query_one("#live-share-link").content) == link
            assert line.link == link
            assert str(line.query_one("#live-share-copy").content) == "copy"
            assert str(line.query_one("#live-share-stop").content) == "stop"
            assert str(line.query_one("#live-share-guests").content) == ""
            app._live_share.channel.guest_count = 2
            app._refresh_live_share_line()
            await pilot.pause()
            assert str(line.query_one("#live-share-guests").content) == "2 guests"
            await app.run_action("copy_live_link")
            await pilot.pause()
            assert copied == [link]
            assert flashes[-1] == "live link copied"
            await app.run_action("stop_live_share")
            await pilot.pause()
            assert app._live_share is None
            assert line.display is False and not line.has_class("-visible")
            assert flashes[-1] == "live link revoked"
            await app.run_action("copy_live_link")
            await pilot.pause()
            assert copied == [link]
            assert flashes[-1] == "no live share on this chat"

    asyncio.run(run())
