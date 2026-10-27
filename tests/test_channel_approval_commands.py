"""Answering approval requests from text channels (/approve, /deny)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from navin.agent.approval import ANSWERING_CHANNELS, ApprovalBroker, ApprovalConfig, ApprovalRequest
from navin.agent.tools.context import RequestContext, bind_request_context, reset_request_context
from navin.bus.events import InboundMessage
from navin.command.builtin import register_builtin_commands
from navin.command.router import CommandContext, CommandRouter


def _router() -> CommandRouter:
    router = CommandRouter()
    register_builtin_commands(router)
    return router


def _ctx(broker, text, chat_id="c1", channel="telegram"):
    msg = InboundMessage(channel=channel, sender_id="u", chat_id=chat_id, content=text)
    return CommandContext(msg=msg, session=None, key=f"{channel}:c1", raw=text,
                          loop=SimpleNamespace(approvals=broker))


def test_messaging_channels_can_answer():
    for name in ("telegram", "whatsapp", "msteams", "email"):
        assert name in ANSWERING_CHANNELS


def test_approve_with_argument_is_priority():
    router = _router()
    assert router.is_priority("/approve abc123")
    assert router.is_priority("/deny")
    assert not router.is_priority("/stop now")


async def _flow(text: str, chat_id: str = "c1"):
    published: list[str] = []

    async def publish(request_id, request, route):
        published.append(request_id)

    broker = ApprovalBroker(publish=publish, config=ApprovalConfig(enabled=True))
    token = bind_request_context(
        RequestContext(channel="telegram", chat_id="c1", session_key="telegram:c1")
    )
    try:
        task = asyncio.create_task(broker.ask(ApprovalRequest(tool="exec", action="rm -rf x", reason="r")))
        while not published:
            await asyncio.sleep(0)
        rid = published[0][:8]
        reply = await _router().dispatch_priority(_ctx(broker, text.format(id=rid), chat_id))
        if not task.done():
            broker.cancel_session("telegram:c1", "test end")
        decision = await task
    finally:
        reset_request_context(token)
    return reply, decision


def test_approve_resolves_request():
    reply, decision = asyncio.run(_flow("/approve {id}"))
    assert decision.allowed and "approved" in reply.content


def test_deny_refuses_request():
    reply, decision = asyncio.run(_flow("/deny"))
    assert not decision.allowed and "denied" in reply.content


def test_answer_from_other_chat_is_ignored():
    reply, decision = asyncio.run(_flow("/approve {id}", chat_id="intruder"))
    assert not decision.allowed
    assert "no longer open" in reply.content


def test_unknown_id():
    reply, decision = asyncio.run(_flow("/approve zzzzzzzz"))
    assert "No pending" in reply.content and not decision.allowed
