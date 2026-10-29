# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Multitask and concurrent chats on one real AgentLoop, end to end.

User report: queued prompts started in parallel "lose balance", and two chats
started at the same time "lag and lose everything". These tests drive a real
AgentLoop (fake provider, real bus, real SessionManager, real
SubagentManager) and check that every prompt runs, every result lands in its
own chat, and no message is lost from memory or from disk.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import patch

import pytest

from navin.agent.loop import AgentLoop
from navin.agent.subagent import request_multitask_spawn
from navin.agent.tools import exec_session
from navin.agent.tools.registry import ToolRegistry
from navin.bus.events import InboundMessage
from navin.bus.queue import MessageBus
from navin.config.loader import get_config_path, set_config_path
from navin.providers.anthropic_provider import AnthropicProvider
from navin.providers.base import LLMProvider, LLMResponse
from navin.session.history_visibility import is_hidden_history_message
from navin.session.manager import Session, SessionManager
from navin.utils.runtime import BACKGROUND_CLAIM_CONTINUE_PROMPT

_TAG = re.compile(r"\b(JOB-[A-Z0-9]+)\b")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


class RoutingProvider(LLMProvider):
    """Answers each call from the job tag found in its last user message.

    A main turn for ``JOB-X`` answers ``reply-JOB-X``; a subagent task answers
    ``result-JOB-X``; the parent turn that receives a subagent announcement
    answers ``ack-JOB-X``. Every call yields to the loop first so concurrent
    turns really interleave.
    """

    def __init__(self, *, delay: float = 0.05) -> None:
        super().__init__()
        self.delay = delay
        self.calls: list[list[dict]] = []
        self.hold: dict[str, asyncio.Event] = {}
        self.entered: dict[str, asyncio.Event] = {}

    def get_default_model(self) -> str:
        return "test-multitask"

    async def chat(self, **kwargs: Any) -> LLMResponse:
        messages = list(kwargs.get("messages") or [])
        self.calls.append(messages)
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = _text(message.get("content"))
                break
        tags = _TAG.findall(last_user)
        tag = tags[-1] if tags else "JOB-NONE"
        self.entered.setdefault(tag, asyncio.Event()).set()
        gate = self.hold.get(tag)
        if gate is not None:
            await gate.wait()
        await asyncio.sleep(self.delay)
        if "[Subagent '" in last_user:
            # Results injected together reach the model in one call, as
            # consecutive user messages: acknowledge every one of them.
            trailing: list[str] = []
            for message in reversed(messages):
                if message.get("role") != "user":
                    break
                trailing.extend(_TAG.findall(_text(message.get("content"))))
            done = sorted(set(trailing)) or [tag]
            return LLMResponse(content=" ".join(f"ack-{t}" for t in done))
        if "queued task" in last_user:
            return LLMResponse(content=f"result-{tag}")
        return LLMResponse(content=f"reply-{tag}")

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat(**kwargs)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path):
    old = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        with (
            patch("navin.agent.skills._home_skill_dirs", return_value=[]),
            patch("navin.agent.subagent._outcomes_dir", return_value=tmp_path / "outcomes"),
        ):
            yield
    finally:
        set_config_path(old)


def _make_loop(tmp_path, provider: RoutingProvider) -> AgentLoop:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    loop = AgentLoop(provider=provider, workspace=workspace, bus=MessageBus())
    loop._mcp_servers = {}
    loop.tools = ToolRegistry()
    return loop


async def _collect_outbound(loop: AgentLoop, sink: list) -> None:
    while True:
        sink.append(await loop.bus.consume_outbound())


def _contents(session_messages: list[dict], role: str) -> list[str]:
    return [_text(m.get("content")) for m in session_messages if m.get("role") == role]


async def _wait_for(predicate, what: str, timeout: float = 30.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def _disk_messages(loop: AgentLoop, key: str) -> list[dict]:
    fresh = SessionManager(loop.sessions.workspace)
    session = fresh.get_or_create(key)
    return session.messages


def _finals(outbound: list, chat_id: str) -> list[str]:
    return [
        str(m.content)
        for m in outbound
        if m.chat_id == chat_id and isinstance(m.content, str) and m.content
    ]


def _finals_text(outbound: list, chat_id: str) -> str:
    return "\n".join(_finals(outbound, chat_id))


def test_two_chats_started_together_keep_their_own_history(tmp_path):
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        try:
            for chat, tag in (("chat-a", "JOB-A1"), ("chat-b", "JOB-B1")):
                await loop.bus.publish_inbound(InboundMessage(
                    channel="websocket", sender_id="user", chat_id=chat,
                    content=f"do {tag}", metadata={"_wants_stream": False},
                ))
            await _wait_for(
                lambda: any("reply-JOB-A1" in c for c in _finals(outbound, "chat-a"))
                and any("reply-JOB-B1" in c for c in _finals(outbound, "chat-b")),
                "both first replies",
            )
            # Second round, again simultaneous, on the same two chats.
            for chat, tag in (("chat-a", "JOB-A2"), ("chat-b", "JOB-B2")):
                await loop.bus.publish_inbound(InboundMessage(
                    channel="websocket", sender_id="user", chat_id=chat,
                    content=f"do {tag}", metadata={"_wants_stream": False},
                ))
            await _wait_for(
                lambda: any("reply-JOB-A2" in c for c in _finals(outbound, "chat-a"))
                and any("reply-JOB-B2" in c for c in _finals(outbound, "chat-b")),
                "both second replies",
            )
            await _wait_for(lambda: not loop._pending_queues, "turns idle")
            await asyncio.sleep(0.3)

            for chat, own, other in (
                ("chat-a", ("A1", "A2"), ("B1", "B2")),
                ("chat-b", ("B1", "B2"), ("A1", "A2")),
            ):
                key = f"websocket:{chat}"
                for source in (loop.sessions.get_or_create(key).messages, _disk_messages(loop, key)):
                    users = _contents(source, "user")
                    assistants = _contents(source, "assistant")
                    for job in own:
                        assert any(f"JOB-{job}" in u for u in users), (key, users)
                        assert any(f"reply-JOB-{job}" in a for a in assistants), (key, assistants)
                    for job in other:
                        assert not any(f"JOB-{job}" in u for u in users), (key, users)
                # Replies never cross chats.
                for content in _finals(outbound, chat):
                    for job in other:
                        assert f"JOB-{job}" not in content, (chat, content)
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


def test_queued_prompts_multitasked_in_parallel_all_land_in_their_chat(tmp_path):
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        try:
            # Chat A runs a long turn; the user queues three prompts and hits
            # "Start Multitasking" while chat B starts its own turn.
            provider.hold["JOB-MAIN"] = asyncio.Event()
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="chat-a",
                content="PARENT do JOB-MAIN", metadata={"_wants_stream": True},
            ))
            await asyncio.wait_for(
                provider.entered.setdefault("JOB-MAIN", asyncio.Event()).wait(), 10
            )
            await loop.bus.publish_inbound(InboundMessage(
                channel="websocket", sender_id="user", chat_id="chat-b",
                content="PARENT do JOB-OTHER", metadata={"_wants_stream": True},
            ))
            results = await asyncio.gather(*(
                request_multitask_spawn(
                    loop.bus, "websocket:chat-a", f"queued task JOB-Q{i}",
                    client_key=f"qp-{i}", timeout=10,
                )
                for i in range(3)
            ))
            assert all(r.get("ok") for r in results), results
            provider.hold["JOB-MAIN"].set()

            # The user sees every answer in its own chat (streamed, like the
            # webui), not only the last one of a turn that absorbed results.
            await _wait_for(
                lambda: all(
                    f"ack-JOB-Q{i}" in _finals_text(outbound, "chat-a") for i in range(3)
                )
                and "reply-JOB-MAIN" in _finals_text(outbound, "chat-a")
                and "reply-JOB-OTHER" in _finals_text(outbound, "chat-b"),
                "all multitask results acknowledged",
            )
            await _wait_for(
                lambda: not loop._pending_queues and not loop.subagents._running_tasks,
                "everything idle",
            )
            await asyncio.sleep(0.3)

            key_a, key_b = "websocket:chat-a", "websocket:chat-b"
            for source in (loop.sessions.get_or_create(key_a).messages, _disk_messages(loop, key_a)):
                everything = " ".join(_text(m.get("content")) for m in source)
                assert "PARENT do JOB-MAIN" in everything
                assert "reply-JOB-MAIN" in everything
                for i in range(3):
                    # The subagent's result reached the parent history...
                    assert f"result-JOB-Q{i}" in everything, everything
                    # ...and the parent's answer to it too.
                    assert f"ack-JOB-Q{i}" in everything, everything
                assert "JOB-OTHER" not in everything
            for source in (loop.sessions.get_or_create(key_b).messages, _disk_messages(loop, key_b)):
                everything = " ".join(_text(m.get("content")) for m in source)
                assert "PARENT do JOB-OTHER" in everything
                assert "reply-JOB-OTHER" in everything
                assert "JOB-Q" not in everything and "JOB-MAIN" not in everything
            for content in _finals(outbound, "chat-b"):
                assert "JOB-Q" not in content and "JOB-MAIN" not in content
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


def _seed_heavy_session(loop: AgentLoop, key: str, *, pairs: int = 20) -> int:
    """A session whose idle tail is heavy enough for idle compaction."""
    session = loop.sessions.get_or_create(key)
    for i in range(pairs):
        session.add_message("user", f"old question {i} " + "x" * 900)
        session.add_message("assistant", f"old answer {i} " + "y" * 900)
    loop.sessions.save(session)
    return len(session.messages)


def test_turn_during_idle_compaction_keeps_its_messages(tmp_path):
    """A result or prompt that lands while an idle chat is being compacted.

    Multitask leaves the parent chat quiet while its subagents work, so the
    15-minute idle compaction can start on it; the result then arrives in the
    middle of the summary call. The compaction used to reload the session
    from disk (a second, stale copy) and write back its truncated list on
    top of the new turn.
    """
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        key = "websocket:chat-a"
        _seed_heavy_session(loop, key)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_archive(messages, **kwargs):
            entered.set()
            await release.wait()
            return "older work summarised"

        async def brief(messages, **kwargs):
            return "where the work stood"

        try:
            with (
                patch.object(loop.consolidator, "archive", slow_archive),
                patch.object(loop.consolidator, "handoff_brief", brief),
            ):
                # A turn starts in the window between the idle check's
                # snapshot and the compaction, and is past its prompt build.
                provider.hold["JOB-LATE"] = asyncio.Event()
                await loop.bus.publish_inbound(InboundMessage(
                    channel="websocket", sender_id="user", chat_id="chat-a",
                    content="do JOB-LATE", metadata={"_wants_stream": True},
                ))
                await asyncio.wait_for(
                    provider.entered.setdefault("JOB-LATE", asyncio.Event()).wait(), 10
                )
                compact = asyncio.create_task(
                    loop.consolidator.compact_idle_session(key, runtime=loop.llm_runtime())
                )
                await asyncio.wait_for(entered.wait(), 10)
                # The turn finishes and saves while the summary call runs.
                provider.hold["JOB-LATE"].set()
                await _wait_for(
                    lambda: "reply-JOB-LATE" in _finals_text(outbound, "chat-a"),
                    "late reply",
                )
                await _wait_for(lambda: not loop._pending_queues, "turn idle")
                await asyncio.sleep(0.2)
                release.set()
                await asyncio.wait_for(compact, 10)
            await asyncio.sleep(0.3)

            live = loop.sessions.get_or_create(key)
            for source in (live.messages, _disk_messages(loop, key)):
                users = _contents(source, "user")
                assistants = _contents(source, "assistant")
                assert any("JOB-LATE" in u for u in users), users[-3:]
                assert any("reply-JOB-LATE" in a for a in assistants), assistants[-3:]
            # The compaction still did its job on the old part.
            assert not any("old question 0 " in u for u in _contents(live.messages, "user"))
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


def test_idle_check_skips_chats_with_running_multitask_or_turn(tmp_path):
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        task = asyncio.create_task(loop.run())
        archived: list[str] = []
        try:
            provider.hold["JOB-SLOW"] = asyncio.Event()
            result = await request_multitask_spawn(
                loop.bus, "websocket:chat-a", "queued task JOB-SLOW", timeout=10,
            )
            assert result.get("ok"), result
            await asyncio.wait_for(
                provider.entered.setdefault("JOB-SLOW", asyncio.Event()).wait(), 10
            )
            # Chat C holds its session lock (a turn is starting) without a
            # pending queue yet.
            lock_c = loop._session_locks.setdefault("websocket:chat-c", asyncio.Lock())
            await lock_c.acquire()
            keys = ["websocket:chat-a", "websocket:chat-b", "websocket:chat-c"]
            with (
                patch.object(loop.auto_compact, "expired_candidates", lambda active: list(keys)),
                patch.object(loop.auto_compact, "check_compactable", lambda key: True),
                patch.object(
                    loop.auto_compact,
                    "archive_if_compactable",
                    lambda key, *args, **kwargs: archived.append(key),
                ),
            ):
                loop._idle_check_at = 0.0
                loop._maybe_check_idle_sessions()
                await asyncio.wait_for(loop._idle_check_task, 10)
            lock_c.release()
            assert archived == ["websocket:chat-b"], archived
            provider.hold["JOB-SLOW"].set()
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


def _late_result(chat: str, task_id: str, body: str) -> InboundMessage:
    """The announcement SubagentManager publishes once a subagent is done."""
    return InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id=f"websocket:{chat}",
        content=(
            f"[Subagent 'worker {task_id}' completed successfully]\n\n"
            f"Task: audit the data layer\n\nResult:\n{body}"
        ),
        session_key_override=f"websocket:{chat}",
        metadata={"injected_event": "subagent_result", "subagent_task_id": task_id},
    )


async def _user_turn(loop: AgentLoop, outbound: list, chat: str, tag: str) -> None:
    await loop.bus.publish_inbound(InboundMessage(
        channel="websocket", sender_id="user", chat_id=chat,
        content=f"do {tag}", metadata={"_wants_stream": False},
    ))
    await _wait_for(lambda: f"reply-{tag}" in _finals_text(outbound, chat), f"reply to {tag}")
    await _wait_for(lambda: not loop._pending_queues, "turn idle")


async def _late_turn(loop: AgentLoop, outbound: list) -> None:
    """One answered request, then a subagent result once that turn is over."""
    await _user_turn(loop, outbound, "chat-a", "JOB-FIRST")
    await loop.bus.publish_inbound(_late_result("chat-a", "late1", "result-JOB-LATE"))
    await _wait_for(
        lambda: "ack-JOB-LATE" in _finals_text(outbound, "chat-a"), "late result answered",
    )
    await _wait_for(lambda: not loop._pending_queues, "late turn idle")


def test_stored_results_replay_as_input_only_for_the_model():
    """A late result keeps its stored shape; the model alone reads it as input."""
    session = Session(key="websocket:chat-a")
    session.add_message("user", "do JOB-FIRST")
    session.add_message("assistant", "reply-JOB-FIRST")
    # How every version so far stored a result that landed after its turn.
    session.add_message(
        "assistant",
        "[Subagent 'worker' completed successfully]\n\nResult:\nresult-JOB-LATE",
        injected_event="subagent_result",
        subagent_task_id="late1",
    )

    model = session.get_history(subagent_results_as_input=True)
    assert [m["role"] for m in model] == ["user", "assistant", "user"]
    shown = session.get_history()
    assert [m["role"] for m in shown] == ["user", "assistant", "assistant"]
    # A tight token budget still opens the window on a request the user typed.
    tight = session.get_history(max_tokens=1, subagent_results_as_input=True)
    assert tight[0]["content"] == "do JOB-FIRST"
    assert tight[-1]["role"] == "user" and "result-JOB-LATE" in tight[-1]["content"]


def test_a_result_landing_after_its_turn_reaches_the_model(tmp_path):
    """A subagent that reports once its turn is over is read, not lost.

    The result is stored as an assistant row (the chat shows it as a card).
    Replayed as the last assistant turn, every provider merged it into the
    previous answer and dropped it (no assistant prefill): the model never saw
    it and repeated its last answer ("reply-JOB-FIRST" here) to the user.
    """
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        try:
            await _late_turn(loop, outbound)

            sent = provider.calls[-1]
            assert sent[-1]["role"] == "user", sent[-1]
            assert "result-JOB-LATE" in _text(sent[-1]["content"])
            # The same request through the real provider adapters keeps it last.
            _system, anthropic = AnthropicProvider(api_key="k")._convert_messages(sent)
            assert anthropic[-1]["role"] == "user"
            assert "result-JOB-LATE" in json.dumps(anthropic[-1]["content"])
            compat = LLMProvider._enforce_role_alternation([dict(m) for m in sent])
            assert compat[-1]["role"] == "user"
            assert "result-JOB-LATE" in _text(compat[-1]["content"])
            # The answer is about the result, never a repeat of the first one.
            assert _finals_text(outbound, "chat-a").count("reply-JOB-FIRST") == 1
            # The chat still stores the result as a card, not as a user message.
            stored = [
                m for m in loop.sessions.get_or_create("websocket:chat-a").messages
                if m.get("injected_event") == "subagent_result"
            ]
            assert [m["role"] for m in stored] == ["assistant"]
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


def test_a_result_already_answered_does_not_start_a_turn(tmp_path):
    """A second announcement of an answered result changes nothing.

    It used to start a turn on nothing new, and the model repeated its
    previous answer to the user.
    """
    async def run() -> None:
        provider = RoutingProvider()
        loop = _make_loop(tmp_path, provider)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        try:
            await _late_turn(loop, outbound)
            calls = len(provider.calls)

            await loop.bus.publish_inbound(_late_result("chat-a", "late1", "result-JOB-LATE"))
            # The next request is dispatched after the duplicate, on the same lock.
            await _user_turn(loop, outbound, "chat-a", "JOB-AFTER")

            assert len(provider.calls) == calls + 1
            text = _finals_text(outbound, "chat-a")
            assert text.count("ack-JOB-LATE") == 1
            assert text.count("reply-JOB-FIRST") == 1
            # Later requests still replay the old result to the model as input.
            replay = provider.calls[-1]
            result_rows = [
                m for m in replay
                if m.get("role") != "system" and "result-JOB-LATE" in _text(m.get("content"))
            ]
            assert [m["role"] for m in result_rows] == ["user"]
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())


_STALE_CLAIM = (
    "Le chantier des données (DLP, RAG, mémoire, multimodal, qualité, audit, risque, "
    "incidents) tourne encore. L'intégration finale vient ensuite."
)


class _StaleClaimProvider(RoutingProvider):
    """Answers a late result with the out-of-date "still running" sentence."""

    async def chat(self, **kwargs: Any) -> LLMResponse:
        messages = list(kwargs.get("messages") or [])
        if messages and "result-JOB-STALE" in _text(messages[-1].get("content")):
            self.calls.append(messages)
            return LLMResponse(content=_STALE_CLAIM)
        return await super().chat(**kwargs)


def test_a_stale_still_running_answer_is_corrected_once(tmp_path):
    """Nothing runs any more, yet the answer says the work still does.

    The report of 2026-10-29: the last subagent had finished, and the agent
    told the user that its part was still going.
    """
    async def run() -> None:
        provider = _StaleClaimProvider()
        loop = _make_loop(tmp_path, provider)
        outbound: list = []
        task = asyncio.create_task(loop.run())
        sink = asyncio.create_task(_collect_outbound(loop, outbound))
        manager = exec_session.DEFAULT_EXEC_SESSION_MANAGER
        try:
            with patch.object(manager, "running_snapshot", return_value=[]):
                await _user_turn(loop, outbound, "chat-a", "JOB-FIRST")
                await loop.bus.publish_inbound(
                    _late_result("chat-a", "stale1", "result-JOB-STALE")
                )
                await _wait_for(
                    lambda: "reply-JOB-NONE" in _finals_text(outbound, "chat-a"),
                    "corrected answer",
                )
                await _wait_for(lambda: not loop._pending_queues, "late turn idle")

            nudge = provider.calls[-1][-1]
            assert nudge["role"] == "user"
            # Followed by the reply-language reminder every nudge carries.
            assert nudge["content"].startswith(BACKGROUND_CLAIM_CONTINUE_PROMPT)
            # The out-of-date sentence never reached the user.
            assert "tourne encore" not in _finals_text(outbound, "chat-a")
            # The correction is Navin's own prompt: hidden from the chat.
            saved = [
                m for m in loop.sessions.get_or_create("websocket:chat-a").messages
                if str(m.get("content") or "").startswith(BACKGROUND_CLAIM_CONTINUE_PROMPT)
            ]
            assert saved and all(is_hidden_history_message(m) for m in saved)
        finally:
            loop.stop()
            sink.cancel()
            task.cancel()
            await asyncio.gather(task, sink, return_exceptions=True)
            await loop.close_mcp()

    asyncio.run(run())
