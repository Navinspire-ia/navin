"""Outbound fan-out: one slow or failing chat must not stall the others."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from navin.bus.events import OutboundMessage
from navin.bus.outbound_events import (
    StreamDeltaEvent,
    StreamEndEvent,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.channels import manager as manager_module
from navin.channels.manager import ChannelManager


class _FakeChannel:
    name = "websocket"
    show_reasoning = False
    is_running = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.deltas: list[tuple[str, str, bool]] = []
        self.fail_chats: dict[str, int] = {}
        self.block_chats: dict[str, asyncio.Event] = {}

    async def _gate(self, chat_id: str) -> None:
        gate = self.block_chats.get(chat_id)
        if gate is not None:
            await gate.wait()
        if self.fail_chats.get(chat_id, 0) > 0:
            self.fail_chats[chat_id] -= 1
            raise RuntimeError("transient")

    async def send(self, msg: OutboundMessage) -> None:
        await self._gate(msg.chat_id)
        self.sent.append((msg.chat_id, msg.content))

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        stream_end: bool = False,
        resuming: bool = False,
    ) -> None:
        await self._gate(chat_id)
        self.deltas.append((chat_id, delta, stream_end))


def _manager(channel: _FakeChannel, *, retries: int = 3) -> ChannelManager:
    manager = ChannelManager.__new__(ChannelManager)
    manager.config = SimpleNamespace(
        channels=SimpleNamespace(send_max_retries=retries, send_progress=True, send_tool_hints=True)
    )
    manager.bus = MessageBus()
    manager.channels = {"websocket": channel}
    manager._dispatch_task = None
    manager._send_lanes = {}
    manager._send_lane_tasks = {}
    manager._origin_reply_fingerprints = OrderedDict()
    return manager


def _text(chat_id: str, content: str) -> OutboundMessage:
    return OutboundMessage(channel="websocket", chat_id=chat_id, content=content)


def _delta(chat_id: str, content: str, stream_id: str = "s1") -> OutboundMessage:
    return outbound_message_for_event(
        channel="websocket",
        chat_id=chat_id,
        event=StreamDeltaEvent(stream_id=stream_id),
        content=content,
    )


def _end(chat_id: str, stream_id: str = "s1") -> OutboundMessage:
    return outbound_message_for_event(
        channel="websocket",
        chat_id=chat_id,
        event=StreamEndEvent(stream_id=stream_id),
        content="",
    )


async def _settle(manager: ChannelManager) -> None:
    for _ in range(50):
        await asyncio.sleep(0)
        if not manager._send_lane_tasks:
            return
    await asyncio.gather(*manager._send_lane_tasks.values())


def test_a_retrying_chat_does_not_delay_another_chat(monkeypatch):
    monkeypatch.setattr(manager_module, "_SEND_RETRY_DELAYS", (0.3, 0.3, 0.3))
    channel = _FakeChannel()
    channel.fail_chats["slow"] = 1
    manager = _manager(channel)

    async def scenario() -> None:
        manager._enqueue_send(_text("slow", "a1"))
        manager._enqueue_send(_text("slow", "a2"))
        manager._enqueue_send(_text("fast", "b1"))
        await asyncio.sleep(0.05)
        # The slow chat is sleeping in its backoff; the other chat is done.
        assert channel.sent == [("fast", "b1")]
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())
    assert channel.sent == [("fast", "b1"), ("slow", "a1"), ("slow", "a2")]
    assert manager._send_lanes == {} and manager._send_lane_tasks == {}


def test_a_stalled_chat_keeps_its_order_and_others_stream():
    channel = _FakeChannel()
    manager = _manager(channel)

    async def scenario() -> None:
        channel.block_chats["stuck"] = asyncio.Event()
        for index in range(3):
            manager._enqueue_send(_text("stuck", f"s{index}"))
        manager._enqueue_send(_text("free", "f0"))
        await asyncio.sleep(0.01)
        assert channel.sent == [("free", "f0")]
        channel.block_chats["stuck"].set()
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())
    assert [c for chat, c in channel.sent if chat == "stuck"] == ["s0", "s1", "s2"]


def test_deltas_queued_behind_a_slow_send_are_merged_in_the_lane():
    channel = _FakeChannel()
    manager = _manager(channel)

    async def scenario() -> None:
        channel.block_chats["c"] = asyncio.Event()
        manager._enqueue_send(_text("c", "first"))
        await asyncio.sleep(0)
        for piece in ("he", "ll", "o"):
            manager._enqueue_send(_delta("c", piece))
        manager._enqueue_send(_end("c"))
        manager._enqueue_send(_text("c", "after"))
        channel.block_chats["c"].set()
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())
    assert channel.deltas == [("c", "hello", False), ("c", "", True)]
    assert channel.sent == [("c", "first"), ("c", "after")]


def test_dispatcher_routes_through_lanes_in_order():
    channel = _FakeChannel()
    manager = _manager(channel)

    async def scenario() -> None:
        task = asyncio.create_task(manager._dispatch_outbound())
        for message in (
            _text("x", "one"),
            _delta("x", "a"),
            _delta("x", "b"),
            _end("x"),
            _text("x", "two"),
        ):
            await manager.bus.publish_outbound(message)
        for _ in range(100):
            await asyncio.sleep(0)
        await asyncio.wait_for(_settle(manager), timeout=5)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await manager._stop_send_lanes()

    asyncio.run(scenario())
    assert channel.sent == [("x", "one"), ("x", "two")]
    assert "".join(d for _c, d, _end_flag in channel.deltas) == "ab"
    assert channel.deltas[-1][2] is True


class _ThrottledChannel(_FakeChannel):
    stream_flush_interval_s = 0.2


def test_stream_deltas_are_coalesced_over_the_flush_window():
    channel = _ThrottledChannel()
    manager = _manager(channel)

    async def scenario() -> None:
        manager._enqueue_send(_delta("c", "a"))
        await asyncio.sleep(0.01)
        # The first token of a burst is never held.
        assert channel.deltas == [("c", "a", False)]
        for piece in ("b", "c", "d"):
            manager._enqueue_send(_delta("c", piece))
            await asyncio.sleep(0.01)
        # Inside the window: held, so the next tokens join one frame.
        assert channel.deltas == [("c", "a", False)]
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())
    assert channel.deltas == [("c", "a", False), ("c", "bcd", False)]


def test_held_delta_flushes_at_once_on_a_closing_event_and_keeps_order():
    channel = _ThrottledChannel()
    manager = _manager(channel)

    async def scenario() -> None:
        manager._enqueue_send(_delta("c", "a"))
        await asyncio.sleep(0.01)
        manager._enqueue_send(_delta("c", "b"))
        await asyncio.sleep(0.01)
        manager._enqueue_send(_end("c"))
        manager._enqueue_send(_text("c", "after"))
        await asyncio.sleep(0.02)
        # Far less than the 0.2 s window: the stream end released the hold.
        assert channel.deltas == [("c", "a", False), ("c", "b", False), ("c", "", True)]
        assert channel.sent == [("c", "after")]
        # A new burst starts unthrottled.
        manager._enqueue_send(_delta("c", "next"))
        await asyncio.sleep(0.01)
        assert channel.deltas[-1] == ("c", "next", False)
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())


def test_merge_stream_deltas_returns_the_boundary():
    queue = deque([_delta("c", "b"), _text("c", "stop"), _delta("c", "z")])
    merged, boundary = ChannelManager._merge_stream_deltas(
        _delta("c", "a"), lambda: queue.popleft() if queue else None
    )
    assert merged.content == "ab"
    assert boundary is not None and boundary.content == "stop"
    assert [m.content for m in queue] == ["z"]


def test_websocket_fan_out_does_not_wait_on_the_slowest_tab():
    from navin.channels.websocket import _send_to_all

    delivered: list[str] = []

    async def scenario() -> None:
        gate = asyncio.Event()

        async def fake_send(connection, raw, *, label=""):
            if connection == "slow":
                await gate.wait()
            delivered.append(connection)

        fan_out = asyncio.create_task(_send_to_all(fake_send, ["slow", "fast"], "{}"))
        await asyncio.sleep(0.01)
        assert delivered == ["fast"]
        gate.set()
        await fan_out

    asyncio.run(scenario())
    assert sorted(delivered) == ["fast", "slow"]


def test_websocket_fan_out_tries_every_tab_then_raises():
    import pytest

    from navin.channels.websocket import _send_to_all

    delivered: list[str] = []

    async def fake_send(connection, raw, *, label=""):
        if connection == "bad":
            raise RuntimeError("boom")
        delivered.append(connection)

    with pytest.raises(RuntimeError):
        asyncio.run(_send_to_all(fake_send, ["bad", "good"], "{}"))
    assert delivered == ["good"]


def test_dispatcher_survives_a_failing_message(monkeypatch):
    channel = _FakeChannel()
    manager = _manager(channel)
    manager._started = True
    real_enqueue = manager._enqueue_send
    calls = {"n": 0}

    def flaky_enqueue(msg):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        real_enqueue(msg)

    manager._enqueue_send = flaky_enqueue
    monkeypatch.setattr(
        "navin.bus.alerts.bind_alert_runtime", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "navin.bus.alerts.schedule_queued_alert", lambda *a, **k: False
    )

    async def scenario() -> None:
        manager._start_dispatcher()
        await manager.bus.publish_outbound(_text("c", "first"))
        await manager.bus.publish_outbound(_text("c", "second"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if channel.sent:
                break
        assert not manager._dispatch_task.done()
        manager._started = False
        manager._dispatch_task.cancel()
        with suppress(asyncio.CancelledError):
            await manager._dispatch_task

    asyncio.run(scenario())
    assert channel.sent == [("c", "second")]


def test_dispatcher_is_restarted_after_a_crash(monkeypatch):
    channel = _FakeChannel()
    manager = _manager(channel)
    manager._started = True
    monkeypatch.setattr(manager_module, "_DISPATCHER_RESTART_DELAY_S", 0.01)
    runs = {"n": 0}

    async def fake_dispatch():
        runs["n"] += 1
        if runs["n"] == 1:
            raise RuntimeError("crash")
        await asyncio.Event().wait()

    manager._dispatch_outbound = fake_dispatch

    async def scenario() -> None:
        manager._start_dispatcher()
        first = manager._dispatch_task
        for _ in range(100):
            await asyncio.sleep(0.01)
            if runs["n"] >= 2:
                break
        assert runs["n"] == 2
        assert manager._dispatch_task is not first
        manager._started = False
        manager._dispatch_task.cancel()
        with suppress(asyncio.CancelledError):
            await manager._dispatch_task

    asyncio.run(scenario())


def test_stalled_send_times_out_and_is_retried(monkeypatch):
    monkeypatch.setattr(manager_module, "_SEND_ATTEMPT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(manager_module, "_SEND_RETRY_DELAYS", (0.01,))
    channel = _FakeChannel()
    gate = asyncio.Event()
    channel.block_chats["c"] = gate
    manager = _manager(channel, retries=2)

    async def scenario() -> None:
        manager._enqueue_send(_text("c", "hello"))
        manager._enqueue_send(_text("c", "after"))
        await asyncio.wait_for(_settle(manager), timeout=5)

    asyncio.run(scenario())
    # Both attempts of each message stalled; the lane still drained.
    assert channel.sent == []
    assert manager._send_lanes == {} and manager._send_lane_tasks == {}


def test_lane_cap_drops_oldest_progress_not_replies(monkeypatch):
    from navin.bus.outbound_events import ProgressEvent

    monkeypatch.setattr(manager_module, "_SEND_LANE_MAX_ITEMS", 4)
    manager = _manager(_FakeChannel())
    lane: deque[OutboundMessage] = deque()

    def progress(i: int) -> OutboundMessage:
        return outbound_message_for_event(
            channel="websocket", chat_id="c", event=ProgressEvent(), content=f"p{i}",
        )

    lane.extend([progress(0), _text("c", "r0"), progress(1), progress(2), _text("c", "r1"), progress(3)])
    manager._trim_send_lane(("websocket", "c"), lane)
    assert [m.content for m in lane] == ["r0", "p2", "r1", "p3"]

    replies = deque(_text("c", f"r{i}") for i in range(6))
    manager._trim_send_lane(("websocket", "c"), replies)
    assert len(replies) == 6


def test_origin_fingerprints_are_bounded(monkeypatch):
    monkeypatch.setattr(manager_module, "_ORIGIN_FINGERPRINT_MAX", 3)
    manager = _manager(_FakeChannel())
    for i in range(10):
        msg = OutboundMessage(
            channel="websocket", chat_id="c", content=f"x{i}",
            metadata={"origin_message_id": f"m{i}"},
        )
        assert manager._should_suppress_outbound(msg) is False
    assert len(manager._origin_reply_fingerprints) == 3
    assert list(manager._origin_reply_fingerprints) == [
        ("websocket", "c", "m7"), ("websocket", "c", "m8"), ("websocket", "c", "m9"),
    ]
