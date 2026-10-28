"""A message queued behind a never-ending turn of the same chat is not a
silent spinner: after the notice window the user gets one status line naming
the running turn, and the wait is never cut (the session stays serial)."""

from __future__ import annotations

import asyncio
import time

import pytest

from navin.agent.loop import AgentLoop
from navin.bus.events import InboundMessage


def _loop_stub() -> AgentLoop:
    loop = object.__new__(AgentLoop)
    loop._session_locks = {}
    loop._session_lock_holders = {}
    loop._SESSION_LOCK_NOTICE_S = 0.05
    loop._notices: list[str] = []

    async def _build(msg):
        async def _cb(content: str) -> None:
            loop._notices.append(content)

        return _cb

    loop._build_retry_wait_callback = _build  # type: ignore[method-assign]
    return loop


def _msg(text: str) -> InboundMessage:
    return InboundMessage(channel="websocket", sender_id="u", chat_id="c", content=text)


@pytest.mark.asyncio
async def test_slow_lock_emits_one_notice_naming_the_running_turn():
    loop = _loop_stub()
    lock = loop._session_locks.setdefault("websocket:c", asyncio.Lock())
    first, second = _msg("long build please"), _msg("hello")

    async with loop._session_turn_lock(lock, first, "websocket:c"):
        assert loop._session_lock_holders["websocket:c"][0] == "long build please"
        waiter = asyncio.create_task(_hold_briefly(loop, lock, second))
        await asyncio.sleep(0.2)  # several notice windows
        assert not waiter.done()
        assert len(loop._notices) == 1
        assert "long build please" in loop._notices[0]
        assert "/stop" in loop._notices[0]
    await asyncio.wait_for(waiter, 1)
    assert "websocket:c" not in loop._session_lock_holders
    assert not lock.locked()


@pytest.mark.asyncio
async def test_free_lock_is_silent():
    loop = _loop_stub()
    lock = asyncio.Lock()
    t0 = time.monotonic()
    async with loop._session_turn_lock(lock, _msg("hello"), "websocket:c"):
        assert lock.locked()
    assert time.monotonic() - t0 < 0.05
    assert loop._notices == []


async def _hold_briefly(loop: AgentLoop, lock: asyncio.Lock, msg: InboundMessage) -> None:
    async with loop._session_turn_lock(lock, msg, "websocket:c"):
        assert loop._session_lock_holders["websocket:c"][0] == "hello"
