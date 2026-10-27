"""Agent inspection is pinned to the attached/shared session."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from navin.channels.websocket import WebSocketChannel


def test_query_rejects_another_session_and_readonly_recovery(monkeypatch):
    async def scenario():
        conn = object()
        replies = []
        async def send(_conn, event, **payload):
            replies.append({"event": event, **payload})
        fake = SimpleNamespace(_conn_chats={conn: {"chat-1"}}, _send_event=send,
                               _role_for=lambda _conn: "viewer", bus=object())
        query = AsyncMock(return_value={"ok": True, "agents": [{"task_id": "one"}]})
        monkeypatch.setattr("navin.agent.subagent.request_subagent_operation", query)
        await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": "subagents_list", "chat_id": "chat-2", "request_id": "r1"})
        assert replies[-1]["error"]
        query.assert_not_awaited()
        await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": "subagent_recover", "chat_id": "chat-1", "request_id": "r2", "task_id": "one"})
        assert replies[-1]["error"] == "This conversation is read only"
        query.assert_not_awaited()
        await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": "subagents_list", "chat_id": "chat-1", "request_id": "r3", "limit": 10000})
        assert replies[-1]["result"] == [{"task_id": "one"}]
        assert query.await_args.args[1:3] == ("websocket:chat-1", "list")
        assert query.await_args.kwargs["limit"] == 200
        # Cancel and relaunch change work: a viewer may not, a member may.
        for kind in ("subagent_cancel", "subagent_retry"):
            query.reset_mock()
            await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": kind, "chat_id": "chat-1", "request_id": "r4", "task_id": "one"})
            assert replies[-1]["error"] == "This conversation is read only"
            query.assert_not_awaited()
        fake._role_for = lambda _conn: "owner"
        query.return_value = {"ok": True, "detail": "Stopped one.", "mode": "relaunch", "task_id": "two"}
        await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": "subagent_retry", "chat_id": "chat-1", "request_id": "r5", "task_id": "one"})
        assert query.await_args.args[1:4] == ("websocket:chat-1", "retry", "one")
        assert replies[-1]["result"]["task_id"] == "two"
        await WebSocketChannel._dispatch_subagent_query(fake, conn, {"type": "subagent_cancel", "chat_id": "chat-1", "request_id": "r6", "task_id": "one"})
        assert query.await_args.args[1:4] == ("websocket:chat-1", "cancel", "one")
    asyncio.run(scenario())


def test_inspection_runs_as_bounded_background_work_without_blocking_chat():
    async def scenario():
        conn = object()
        gate = asyncio.Event()
        replies = []
        async def inspect(*_args):
            await gate.wait()
        async def reply(*args, **kwargs):
            replies.append(kwargs)
        fake = SimpleNamespace(_subagent_queries={}, _dispatch_subagent_query=inspect,
                               _send_event=reply)
        envelope = {"type": "subagent_history", "chat_id": "chat-1", "request_id": "r"}
        for _ in range(4):
            await asyncio.wait_for(WebSocketChannel._dispatch_envelope(fake, conn, "client", envelope), timeout=0.2)
        assert len(fake._subagent_queries[conn]) == 4
        await WebSocketChannel._dispatch_envelope(fake, conn, "client", envelope)
        assert "busy" in replies[-1]["error"]
        pending = list(fake._subagent_queries[conn])
        gate.set()
        await asyncio.gather(*pending)
        await asyncio.sleep(0)
        assert not fake._subagent_queries[conn]
    asyncio.run(scenario())
