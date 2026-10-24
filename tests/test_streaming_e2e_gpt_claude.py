# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Streaming end to end for GPT and Claude shapes, with mocked provider streams.

Covers the provider stream parsers (OpenAI Chat Completions, OpenAI Responses,
Anthropic Messages), the retry ladder once content has streamed, idle stalls,
cancellation, the runner's stream_delta / stream_end segmentation and the
channel layer (ordering, reasoning opt-in, no duplicated final message).
No network: every SDK client is replaced by an in-memory stream.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from contextlib import suppress
from types import SimpleNamespace as N
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.filesystem import ListDirTool, ReadFileTool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.anthropic_provider import AnthropicProvider
from navin.providers.base import GenerationSettings, LLMProvider, LLMResponse
from navin.providers.openai_compat_provider import OpenAICompatProvider
from navin.providers.openai_responses.parsing import (
    consume_sdk_stream,
    consume_sse_with_reasoning,
)
from navin.utils.llm_runtime import LLMRuntime

# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class FakeConnectionError(Exception):
    """Class name carries "connection": providers classify it as transient."""


class _Steps:
    """Async iterator over (pause, item) steps; an Exception item is raised."""

    def __init__(self, steps: list[tuple[float, Any]], hang_at_end: bool = False) -> None:
        self._steps = list(steps)
        self._hang = hang_at_end
        self.closed = False
        self.hanging = asyncio.Event()

    def __aiter__(self) -> "_Steps":
        return self

    async def __anext__(self) -> Any:
        if not self._steps:
            if self._hang:
                self.hanging.set()
                await asyncio.Event().wait()
            raise StopAsyncIteration
        pause, item = self._steps.pop(0)
        if pause:
            await asyncio.sleep(pause)
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def cb(self, kind: str):
        async def _cb(value: Any) -> None:
            self.events.append((kind, value))
        return _cb

    def of(self, kind: str) -> list[Any]:
        return [v for k, v in self.events if k == kind]


@pytest.fixture(autouse=True)
def _short_stream_windows(monkeypatch):
    monkeypatch.setenv("NAVIN_STREAM_IDLE_TIMEOUT_S", "0.3")
    monkeypatch.setenv("NAVIN_STREAM_BUFFERED_IDLE_S", "0.3")
    # Retry back-off is not what these tests measure.
    monkeypatch.setattr(LLMProvider, "_sleep_with_heartbeat", AsyncMock())


# --- OpenAI Chat Completions shapes ---------------------------------------


def _cc(content=None, reasoning=None, tool_calls=None, finish=None, usage=None, empty=False):
    if empty:
        return N(choices=[], usage=usage)
    delta = N(
        content=content, reasoning_content=reasoning, reasoning=None,
        tool_calls=tool_calls, function_call=None,
    )
    return N(choices=[N(delta=delta, finish_reason=finish, index=0)], usage=usage)


def _tc(index, id=None, name=None, args=None):
    return N(index=index, id=id, type="function", function=N(name=name, arguments=args))


def _gpt_provider(streams: list[_Steps]) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(
        api_key="t", api_base="https://example.invalid/v1", default_model="gpt-4.1",
    )
    pending = list(streams)

    async def create(**_kwargs):
        return pending.pop(0)

    async def ready():
        return None

    async def reset():
        return None

    provider._ensure_client = ready  # type: ignore[method-assign]
    provider._reset_client = reset  # type: ignore[method-assign]
    provider._client = N(chat=N(completions=N(create=create)))
    return provider


# --- Anthropic Messages shapes --------------------------------------------


def _a_start_tool(index, id, name):
    return N(type="content_block_start", index=index, content_block=N(type="tool_use", id=id, name=name))


def _a_delta(index, kind, **fields):
    return N(type="content_block_delta", index=index, delta=N(type=kind, **fields))


def _a_final(blocks, stop="end_turn"):
    return N(
        content=blocks,
        stop_reason=stop,
        usage=N(input_tokens=12, output_tokens=7, cache_creation_input_tokens=0, cache_read_input_tokens=3),
    )


class _AStream:
    def __init__(self, steps, final, hang_at_end=False):
        self.iter = _Steps(steps, hang_at_end=hang_at_end)
        self.final = final
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.exited = True
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.iter.__anext__()

    async def get_final_message(self):
        return self.final


def _claude_provider(streams: list[_AStream]) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="t", default_model="claude-sonnet-4-5")
    pending = list(streams)
    provider._client_instance = N(messages=N(stream=lambda **_kw: pending.pop(0)))
    return provider


# ---------------------------------------------------------------------------
# Provider level: GPT (Chat Completions)
# ---------------------------------------------------------------------------


def test_gpt_chat_stream_text_reasoning_parallel_tool_args_and_usage_only_chunk():
    stream = _Steps([
        (0, _cc(empty=True)),  # keep-alive chunk without choices nor usage
        (0, _cc(reasoning="Need two ")),
        (0, _cc(reasoning="files.")),
        (0, _cc(content="Let me ")),
        (0, _cc(content="check.")),
        (0, _cc()),  # empty delta
        (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"pa')])),
        (0, _cc(tool_calls=[_tc(1, "call_b", "list_dir", "")])),
        (0, _cc(tool_calls=[_tc(0, args='th": "a.py"}')])),
        (0, _cc(tool_calls=[_tc(1, args='{"path": "src"}')])),
        (0, _cc(finish="tool_calls")),
        (0, _cc(empty=True, usage=N(prompt_tokens=50, completion_tokens=9, total_tokens=59))),
    ])
    provider = _gpt_provider([stream])
    rec = Recorder()

    async def run():
        return await provider.chat_stream(
            [{"role": "user", "content": "go"}],
            on_content_delta=rec.cb("text"),
            on_thinking_delta=rec.cb("thinking"),
            on_tool_call_delta=rec.cb("tool"),
        )

    response = asyncio.run(run())
    assert rec.of("text") == ["Let me ", "check."]
    assert rec.of("thinking") == ["Need two ", "files."]
    assert response.content == "Let me check."
    assert response.reasoning_content == "Need two files."
    assert response.finish_reason == "tool_calls"
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("call_a", "read_file", {"path": "a.py"}),
        ("call_b", "list_dir", {"path": "src"}),
    ]
    assert response.usage["prompt_tokens"] == 50
    # Live tool deltas keep each interleaved call on its own slot.
    slots: dict[int, str] = {}
    for delta in rec.of("tool"):
        slots[delta["index"]] = slots.get(delta["index"], "") + delta["arguments_delta"]
    assert sorted(slots.values()) == ['{"path": "a.py"}', '{"path": "src"}']
    assert stream.closed


def test_gpt_stream_that_ends_without_finish_reason_keeps_its_content():
    provider = _gpt_provider([_Steps([(0, _cc(content="Hello")), (0, _cc(content=" world"))])])
    response = asyncio.run(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert response.content == "Hello world"
    assert response.finish_reason == "stop"
    assert response.tool_calls == []


def test_gpt_idle_stall_is_a_timeout_error_and_closes_the_stream():
    stream = _Steps([(0, _cc(content="Par")), (2.0, _cc(content="tial"))])
    provider = _gpt_provider([stream])
    response = asyncio.run(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"
    assert "stalled" in (response.content or "")
    assert stream.closed


def test_gpt_cancellation_mid_stream_closes_the_stream():
    stream = _Steps([(0, _cc(content="Partial"))], hang_at_end=True)
    provider = _gpt_provider([stream])
    rec = Recorder()

    async def run():
        task = asyncio.create_task(provider.chat_stream(
            [{"role": "user", "content": "hi"}], on_content_delta=rec.cb("text"),
        ))
        await asyncio.wait_for(stream.hanging.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch.dict("os.environ", {"NAVIN_STREAM_IDLE_TIMEOUT_S": "30"}):
        asyncio.run(run())
    assert rec.of("text") == ["Partial"]
    assert stream.closed


def test_gpt_transient_error_after_content_retries_in_a_new_segment():
    first = _Steps([(0, _cc(content="Here is the")), (0, FakeConnectionError("reset by peer"))])
    second = _Steps([(0, _cc(content="Here is the answer.")), (0, _cc(finish="stop"))])
    provider = _gpt_provider([first, second])
    rec = Recorder()

    async def recover():
        rec.events.append(("recover", None))

    response = asyncio.run(provider.chat_stream_with_retry(
        [{"role": "user", "content": "hi"}],
        on_content_delta=rec.cb("text"), on_stream_recover=recover,
    ))
    assert response.content == "Here is the answer."
    assert response.finish_reason == "stop"
    assert rec.events == [
        ("text", "Here is the"), ("recover", None), ("text", "Here is the answer."),
    ]


def test_gpt_retry_without_recover_callback_never_redelivers_text():
    first = _Steps([(0, _cc(content="Here is the")), (0, FakeConnectionError("reset"))])
    second = _Steps([(0, _cc(content="Here is the answer.")), (0, _cc(finish="stop"))])
    provider = _gpt_provider([first, second])
    rec = Recorder()
    response = asyncio.run(provider.chat_stream_with_retry(
        [{"role": "user", "content": "hi"}], on_content_delta=rec.cb("text"),
    ))
    assert response.content == "Here is the answer."
    # The second attempt is not streamed again on top of the first one.
    assert rec.of("text") == ["Here is the"]


def test_gpt_permanent_error_after_content_is_not_retried():
    first = _Steps([(0, _cc(content="Here")), (0, ValueError("bad request shape"))])
    provider = _gpt_provider([first, _Steps([(0, _cc(content="never"))])])
    rec = Recorder()
    recover = AsyncMock()
    response = asyncio.run(provider.chat_stream_with_retry(
        [{"role": "user", "content": "hi"}],
        on_content_delta=rec.cb("text"), on_stream_recover=recover,
    ))
    assert response.finish_reason == "error"
    assert rec.of("text") == ["Here"]
    recover.assert_not_called()


# ---------------------------------------------------------------------------
# Provider level: GPT (Responses API, SDK stream and SSE)
# ---------------------------------------------------------------------------


def _r(type_, **fields):
    return N(type=type_, **fields)


def test_gpt_responses_stream_reasoning_live_parallel_calls_and_usage():
    async def events():
        yield _r("response.reasoning_summary_text.delta", delta="Plan: ")
        yield _r("response.reasoning_summary_text.delta", delta="read both.")
        yield _r("response.output_text.delta", delta="Reading.")
        yield _r("response.output_item.added", item=N(type="function_call", call_id="c1", id="fc1", name="read_file", arguments=""))
        yield _r("response.output_item.added", item=N(type="function_call", call_id="c2", id="fc2", name="list_dir", arguments=""))
        yield _r("response.function_call_arguments.delta", call_id="c1", delta='{"path":')
        yield _r("response.function_call_arguments.delta", call_id="c2", delta='{"path": "src"}')
        yield _r("response.function_call_arguments.delta", call_id="c1", delta=' "a.py"}')
        yield _r("response.output_item.done", item=N(type="function_call", call_id="c1", id="fc1", name="read_file", arguments=None))
        yield _r("response.output_item.done", item=N(type="function_call", call_id="c2", id="fc2", name="list_dir", arguments=None))
        summary = N(type="reasoning", summary=[N(type="summary_text", text="Plan: read both.")])
        yield _r("response.completed", response=N(
            status="completed", output=[summary],
            usage={"input_tokens": 40, "output_tokens": 8, "total_tokens": 48},
        ))

    rec = Recorder()
    content, calls, finish, usage, reasoning = asyncio.run(consume_sdk_stream(
        events(), rec.cb("text"), on_tool_call_delta=rec.cb("tool"), on_reasoning_delta=rec.cb("thinking"),
    ))
    assert rec.of("thinking") == ["Plan: ", "read both."]
    assert reasoning == "Plan: read both."  # not doubled by the completed summary
    assert content == "Reading." and rec.of("text") == ["Reading."]
    assert [(c.name, c.arguments) for c in calls] == [
        ("read_file", {"path": "a.py"}), ("list_dir", {"path": "src"}),
    ]
    assert finish == "stop"
    assert usage["prompt_tokens"] == 40


def test_gpt_responses_incomplete_is_a_length_cutoff_sdk_and_sse():
    async def events():
        yield _r("response.output_text.delta", delta="A long answer that")
        yield _r("response.incomplete", response=N(
            status="incomplete", output=[], usage={"input_tokens": 5, "output_tokens": 100},
        ))

    content, _calls, finish, usage, _r2 = asyncio.run(consume_sdk_stream(events()))
    assert content == "A long answer that"
    assert finish == "length"
    assert usage["completion_tokens"] == 100

    class _SSE:
        async def aiter_lines(self):
            for event in (
                {"type": "response.output_text.delta", "delta": "Cut"},
                {"type": "response.incomplete", "response": {"status": "incomplete", "usage": {"input_tokens": 1, "output_tokens": 2}}},
            ):
                yield "data: " + json.dumps(event)
                yield ""

    content, _calls, finish, usage, _r3 = asyncio.run(consume_sse_with_reasoning(_SSE()))
    assert (content, finish) == ("Cut", "length")
    assert usage["completion_tokens"] == 2


# ---------------------------------------------------------------------------
# Provider level: Claude (Anthropic Messages)
# ---------------------------------------------------------------------------


def _claude_tool_turn() -> _AStream:
    steps = [
        (0, N(type="message_start")),
        (0, _a_delta(0, "thinking_delta", thinking="Look at ")),
        (0, _a_delta(0, "thinking_delta", thinking="both files.")),
        (0, _a_delta(1, "text_delta", text="Checking ")),
        (0, _a_delta(1, "text_delta", text="now.")),
        (0, _a_start_tool(2, "toolu_a", "read_file")),
        (0, _a_start_tool(3, "toolu_b", "list_dir")),
        (0, _a_delta(2, "input_json_delta", partial_json='{"path": ')),
        (0, _a_delta(3, "input_json_delta", partial_json='{"path": "src"}')),
        (0, _a_delta(2, "input_json_delta", partial_json='"a.py"}')),
        (0, N(type="message_delta")),
    ]
    final = _a_final([
        N(type="thinking", thinking="Look at both files.", signature="sig"),
        N(type="text", text="Checking now."),
        N(type="tool_use", id="toolu_a", name="read_file", input={"path": "a.py"}),
        N(type="tool_use", id="toolu_b", name="list_dir", input={"path": "src"}),
    ], stop="tool_use")
    return _AStream(steps, final)


def test_claude_stream_thinking_text_and_interleaved_tool_json():
    stream = _claude_tool_turn()
    provider = _claude_provider([stream])
    rec = Recorder()
    response = asyncio.run(provider.chat_stream(
        [{"role": "user", "content": "go"}],
        on_content_delta=rec.cb("text"), on_thinking_delta=rec.cb("thinking"),
        on_tool_call_delta=rec.cb("tool"),
    ))
    assert rec.of("thinking") == ["Look at ", "both files."]
    assert rec.of("text") == ["Checking ", "now."]
    per_index: dict[int, str] = {}
    for delta in rec.of("tool"):
        per_index[delta["index"]] = per_index.get(delta["index"], "") + delta["arguments_delta"]
    assert per_index == {2: '{"path": "a.py"}', 3: '{"path": "src"}'}
    assert response.finish_reason == "tool_calls"
    assert [(c.id, c.arguments) for c in response.tool_calls] == [
        ("toolu_a", {"path": "a.py"}), ("toolu_b", {"path": "src"}),
    ]
    assert response.thinking_blocks and response.thinking_blocks[0]["signature"] == "sig"
    assert response.usage["prompt_tokens"] == 15 and response.usage["cached_tokens"] == 3
    assert stream.exited


def test_claude_max_tokens_maps_to_length():
    stream = _AStream([(0, _a_delta(0, "text_delta", text="cut"))], _a_final([N(type="text", text="cut")], stop="max_tokens"))
    response = asyncio.run(_claude_provider([stream]).chat_stream([{"role": "user", "content": "x"}]))
    assert response.finish_reason == "length"


def test_claude_stall_is_a_timeout_error():
    stream = _AStream([(0, _a_delta(0, "text_delta", text="Par")), (2.0, _a_delta(0, "text_delta", text="t"))], None)
    response = asyncio.run(_claude_provider([stream]).chat_stream([{"role": "user", "content": "x"}]))
    assert response.finish_reason == "error" and response.error_kind == "timeout"
    assert stream.exited


def test_claude_cancellation_exits_the_stream_context():
    stream = _AStream([(0, _a_delta(0, "text_delta", text="Partial"))], None, hang_at_end=True)
    provider = _claude_provider([stream])

    async def run():
        task = asyncio.create_task(provider.chat_stream([{"role": "user", "content": "x"}]))
        await asyncio.wait_for(stream.iter.hanging.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch.dict("os.environ", {"NAVIN_STREAM_IDLE_TIMEOUT_S": "30"}):
        asyncio.run(run())
    assert stream.exited


def test_claude_connection_drop_after_text_retries_in_a_new_segment():
    first = _AStream([(0, _a_delta(0, "text_delta", text="Hel")), (0, FakeConnectionError("dropped"))], None)
    second = _AStream([(0, _a_delta(0, "text_delta", text="Hello."))], _a_final([N(type="text", text="Hello.")]))
    provider = _claude_provider([first, second])
    rec = Recorder()

    async def recover():
        rec.events.append(("recover", None))

    response = asyncio.run(provider.chat_stream_with_retry(
        [{"role": "user", "content": "x"}], on_content_delta=rec.cb("text"), on_stream_recover=recover,
    ))
    assert response.content == "Hello."
    assert rec.events == [("text", "Hel"), ("recover", None), ("text", "Hello.")]


# ---------------------------------------------------------------------------
# Runner level
# ---------------------------------------------------------------------------


class StreamHook(AgentHook):
    """Records exactly what a streaming channel would be told, in order."""

    def __init__(self) -> None:
        super().__init__(reraise=True)
        self.events: list[tuple[str, Any]] = []

    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context, delta: str) -> None:
        if delta:
            self.events.append(("delta", delta))

    async def on_stream_end(self, context, *, resuming: bool) -> None:
        self.events.append(("end", resuming))

    async def emit_reasoning(self, reasoning_content: str | None) -> None:
        if reasoning_content:
            self.events.append(("reasoning", reasoning_content))

    async def before_execute_tools(self, context) -> None:
        self.events.append(("tools", [c.name for c in context.tool_calls]))

    def text(self) -> str:
        return "".join(v for k, v in self.events if k == "delta")


def _registry(root) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(ReadFileTool(workspace=root))
    tools.register(ListDirTool(workspace=root))
    return tools


def _spec(provider, root, hook, **kw) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": "Read a.py and list src."}],
        tools=_registry(root),
        runtime=LLMRuntime(provider=provider, model=provider.get_default_model(),
                           generation=GenerationSettings(), context_window_tokens=128_000),
        max_iterations=6,
        max_tool_result_chars=4000,
        hook=hook,
        workspace=root,
        **kw,
    )


def _workspace(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("y = 2\n")
    return tmp_path


def _assert_history_consistent(messages: list[dict[str, Any]]) -> None:
    """Every tool_use has its tool_result and no result is orphaned."""
    open_ids: set[str] = set()
    for message in messages:
        if message.get("role") == "assistant":
            assert not open_ids, f"tool calls without results: {open_ids}"
            open_ids = {c["id"] for c in message.get("tool_calls") or []}
        elif message.get("role") == "tool":
            assert message["tool_call_id"] in open_ids, "orphan tool result"
            open_ids.discard(message["tool_call_id"])
        else:
            assert not open_ids, f"tool calls without results: {open_ids}"
    assert not open_ids, f"tool calls without results: {open_ids}"


def _assert_segments(hook: StreamHook) -> None:
    """Deltas never follow a closed (resuming=False) stream in the same run."""
    closed = False
    for kind, value in hook.events:
        if kind == "delta":
            assert not closed, f"delta after final stream_end: {hook.events}"
        if kind == "end" and value is False:
            closed = True


def test_gpt_turn_through_the_runner_streams_each_step_once(tmp_path):
    root = _workspace(tmp_path)
    step1 = _Steps([
        (0, _cc(reasoning="Two reads.")),
        (0, _cc(content="Let me look.")),
        (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"path": "a.py"}')])),
        (0, _cc(tool_calls=[_tc(1, "call_b", "list_dir", '{"path": "src"}')])),
        (0, _cc(finish="tool_calls")),
        (0, _cc(empty=True, usage=N(prompt_tokens=30, completion_tokens=5, total_tokens=35))),
    ])
    step2 = _Steps([
        (0, _cc(content="a.py sets x; ")),
        (0, _cc(content="src has b.py.")),
        (0, _cc(finish="stop")),
    ])
    provider = _gpt_provider([step1, step2])
    hook = StreamHook()
    result = asyncio.run(AgentRunner().run(_spec(provider, root, hook)))
    assert result.stop_reason == "completed"
    assert result.final_content == "a.py sets x; src has b.py."
    assert hook.events == [
        ("reasoning", "Two reads."),
        ("delta", "Let me look."),
        ("end", True),
        ("tools", ["read_file", "list_dir"]),
        ("delta", "a.py sets x; "),
        ("delta", "src has b.py."),
        ("end", False),
    ]
    _assert_history_consistent(result.messages)


def test_claude_turn_through_the_runner_streams_each_step_once(tmp_path):
    root = _workspace(tmp_path)
    final = _AStream(
        [(0, _a_delta(0, "text_delta", text="Done: x = 1."))],
        _a_final([N(type="text", text="Done: x = 1.")]),
    )
    provider = _claude_provider([_claude_tool_turn(), final])
    hook = StreamHook()
    result = asyncio.run(AgentRunner().run(_spec(provider, root, hook)))
    assert result.final_content == "Done: x = 1."
    reasoning = "".join(v for k, v in hook.events if k == "reasoning")
    assert reasoning == "Look at both files."  # streamed once, not re-emitted post-hoc
    assert [e for e in hook.events if e[0] != "reasoning"] == [
        ("delta", "Checking "),
        ("delta", "now."),
        ("end", True),
        ("tools", ["read_file", "list_dir"]),
        ("delta", "Done: x = 1."),
        ("end", False),
    ]
    _assert_history_consistent(result.messages)
    # Signed thinking is kept on the assistant tool-call message for replay.
    assistant = next(m for m in result.messages if m.get("tool_calls"))
    assert assistant.get("thinking_blocks")


def test_mid_stream_retry_through_the_runner_keeps_one_final_answer(tmp_path):
    root = _workspace(tmp_path)
    first = _Steps([(0, _cc(content="The ans")), (0, FakeConnectionError("reset"))])
    second = _Steps([(0, _cc(content="The answer is 42.")), (0, _cc(finish="stop"))])
    provider = _gpt_provider([first, second])
    hook = StreamHook()
    result = asyncio.run(AgentRunner().run(_spec(provider, root, hook)))
    assert result.final_content == "The answer is 42."
    # The interrupted attempt is closed as its own segment before the retry.
    assert hook.events == [
        ("delta", "The ans"), ("end", True), ("delta", "The answer is 42."), ("end", False),
    ]
    finals = [m for m in result.messages if m.get("role") == "assistant"]
    assert [m["content"] for m in finals] == ["The answer is 42."]


def test_non_streamed_finalization_answer_reaches_the_stream(tmp_path):
    """Blank streamed replies, then the (non-streamed) finalization call
    answers: a streaming channel drops the final message, so the answer must
    arrive as a stream segment, exactly once."""
    root = _workspace(tmp_path)
    provider = _gpt_provider([
        _Steps([(0, _cc(finish="stop"))]),
        _Steps([(0, _cc(content="   ")), (0, _cc(finish="stop"))]),
    ])
    provider.chat_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value=LLMResponse(content="Final answer.", finish_reason="stop"),
    )
    hook = StreamHook()
    result = asyncio.run(AgentRunner().run(_spec(provider, root, hook, max_empty_retries=2)))
    assert result.stop_reason == "completed"
    assert result.final_content == "Final answer."
    assert hook.text().strip() == "Final answer."
    assert hook.events[-1] == ("end", False)
    assert hook.events.count(("delta", "Final answer.")) == 1


def test_length_cutoff_through_the_runner_delivers_the_whole_answer(tmp_path):
    root = _workspace(tmp_path)
    provider = _gpt_provider([
        _Steps([(0, _cc(content="First half, ")), (0, _cc(finish="length"))]),
        _Steps([(0, _cc(content="second half.")), (0, _cc(finish="stop"))]),
    ])
    hook = StreamHook()
    result = asyncio.run(AgentRunner().run(_spec(provider, root, hook)))
    assert result.final_content == "First half, second half."
    assert hook.text() == "First half, second half."
    _assert_segments(hook)


def test_stalled_stream_through_the_runner_ends_on_a_clean_error(tmp_path):
    root = _workspace(tmp_path)
    provider = _gpt_provider([
        _Steps([(0, _cc(content="Work"))], hang_at_end=True),
        _Steps([(0, _cc(content="Work"))], hang_at_end=True),
    ])
    hook = StreamHook()
    result = asyncio.run(asyncio.wait_for(AgentRunner().run(_spec(provider, root, hook)), 15))
    assert result.stop_reason == "error"
    assert result.retryable_error  # turn recovery resumes it on its own
    _assert_history_consistent(result.messages)
    assert hook.events[-1][0] == "end"


def test_cancellation_inside_a_streamed_step_leaves_no_half_tool_call(tmp_path):
    root = _workspace(tmp_path)
    hanging = _Steps([
        (0, _cc(content="Next I will ")),
        (0, _cc(tool_calls=[_tc(0, "call_x", "read_file", '{"path": "a.')])),
    ], hang_at_end=True)
    step1 = _Steps([
        (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"path": "a.py"}')])),
        (0, _cc(finish="tool_calls")),
    ])
    provider = _gpt_provider([step1, hanging])
    hook = StreamHook()
    checkpoints: list[dict[str, Any]] = []

    async def checkpoint(payload):
        checkpoints.append(payload)

    spec = _spec(provider, root, hook, checkpoint_callback=checkpoint)

    async def run():
        task = asyncio.create_task(AgentRunner().run(spec))
        await asyncio.wait_for(hanging.hanging.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch.dict("os.environ", {"NAVIN_STREAM_IDLE_TIMEOUT_S": "30"}):
        asyncio.run(run())
    assert hanging.closed
    # The last durable checkpoint is the completed first batch, not the
    # half-written second call.
    assert checkpoints[-1]["phase"] == "tools_completed"
    assert "call_x" not in json.dumps(checkpoints)


# ---------------------------------------------------------------------------
# Loop + channel layer
# ---------------------------------------------------------------------------


class _Channel:
    name = "websocket"
    is_running = True
    send_progress = False
    send_tool_hints = False

    def __init__(self, show_reasoning: bool) -> None:
        self.show_reasoning = show_reasoning
        self.frames: list[tuple] = []

    async def send(self, msg) -> None:
        self.frames.append(("send", msg.content))

    async def send_delta(self, chat_id, delta, metadata=None, *, stream_id=None, stream_end=False, resuming=False):
        self.frames.append(("end", stream_id, resuming) if stream_end else ("delta", stream_id, delta))
        if stream_end and delta:
            self.frames.append(("delta", stream_id, delta))

    async def send_reasoning_delta(self, chat_id, delta, metadata=None, *, stream_id=None):
        self.frames.append(("reasoning", delta))

    async def send_reasoning_end(self, chat_id, metadata=None, *, stream_id=None):
        self.frames.append(("reasoning_end",))

    async def send_reasoning(self, msg) -> None:
        self.frames.append(("reasoning", msg.content))

    async def send_file_edit_events(self, *_a, **_k) -> None:
        return None


def _manager(bus, channel):
    from navin.channels.manager import ChannelManager

    manager = ChannelManager.__new__(ChannelManager)
    manager.config = N(channels=N(send_max_retries=1, send_progress=False, send_tool_hints=False, show_reasoning=channel.show_reasoning))
    manager.bus = bus
    manager.channels = {"websocket": channel}
    manager._dispatch_task = None
    manager._send_lanes = {}
    manager._send_lane_tasks = {}
    manager._origin_reply_fingerprints = OrderedDict()
    return manager


@pytest.fixture
def isolated_config(tmp_path):
    from navin.config.loader import get_config_path, set_config_path

    old = get_config_path()
    set_config_path(tmp_path / "config.json")
    with patch("navin.agent.skills._home_skill_dirs", return_value=[]):
        yield
    set_config_path(old)


def _loop(root, provider):
    from navin.agent.loop import AgentLoop
    from navin.bus.queue import MessageBus

    loop = AgentLoop(provider=provider, workspace=root, bus=MessageBus(), max_iterations=6)
    loop._mcp_servers = {}
    loop.tools = _registry(root)
    return loop


def _inbound(content: str, chat: str):
    from navin.bus.events import InboundMessage

    return InboundMessage(
        channel="websocket", sender_id="user", chat_id=chat, content=content,
        metadata={"_wants_stream": True, "composer_mode": "agent"},
    )


@pytest.mark.parametrize("show_reasoning", [False, True])
def test_channel_receives_ordered_segments_and_no_duplicate_final(tmp_path, isolated_config, show_reasoning):
    root = _workspace(tmp_path / "ws")
    step1 = _Steps([
        (0, _cc(reasoning="Thinking about a.py")),
        (0, _cc(content="Reading a.py.")),
        (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"path": "a.py"}')])),
        (0, _cc(finish="tool_calls")),
    ])
    step2 = _Steps([(0, _cc(content="x is ")), (0, _cc(content="1.")), (0, _cc(finish="stop"))])
    provider = _gpt_provider([step1, step2])
    channel = _Channel(show_reasoning)

    async def run():
        loop = _loop(root, provider)
        manager = _manager(loop.bus, channel)
        dispatcher = asyncio.create_task(manager._dispatch_outbound())
        agent = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(_inbound("What is x in a.py?", "chat-stream"))
            async with asyncio.timeout(20):
                while not any(f[0] == "end" and f[2] is False for f in channel.frames):
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
            return loop.sessions.get_or_create("websocket:chat-stream").messages
        finally:
            loop.stop()
            for task in (agent, dispatcher):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await manager._stop_send_lanes()
            await loop.close_mcp()

    history = asyncio.run(run())
    stream_frames = [f for f in channel.frames if f[0] in ("delta", "end")]
    deltas = [f for f in stream_frames if f[0] == "delta"]
    assert "".join(f[2] for f in deltas) == "Reading a.py.x is 1."
    # Two segments with distinct ids, each closed, in order.
    ids = list(dict.fromkeys(f[1] for f in stream_frames))
    assert len(ids) == 2
    assert stream_frames[-1] == ("end", ids[1], False)
    assert ("end", ids[0], True) in stream_frames
    first_end = stream_frames.index(("end", ids[0], True))
    assert all(f[1] == ids[0] for f in stream_frames[:first_end])
    assert all(f[1] == ids[1] for f in stream_frames[first_end + 1:])
    # The final answer was streamed: it is never sent again as a message.
    assert not any(f[0] == "send" and "x is 1." in str(f[1]) for f in channel.frames)
    reasoning = [f for f in channel.frames if f[0] == "reasoning"]
    if show_reasoning:
        assert "".join(f[1] for f in reasoning) == "Thinking about a.py"
    else:
        assert reasoning == []
    assert history[-1]["role"] == "assistant" and history[-1]["content"] == "x is 1."
    _assert_history_consistent([m for m in history if m.get("role") != "user"])


def test_stop_mid_stream_closes_the_stream_and_keeps_history_consistent(tmp_path, isolated_config):
    root = _workspace(tmp_path / "ws")
    step1 = _Steps([
        (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"path": "a.py"}')])),
        (0, _cc(finish="tool_calls")),
    ])
    hanging = _Steps([
        (0, _cc(content="Now editing ")),
        (0, _cc(tool_calls=[_tc(0, "call_half", "read_file", '{"path": "sr')])),
    ], hang_at_end=True)
    provider = _gpt_provider([step1, hanging])
    channel = _Channel(False)

    async def run():
        loop = _loop(root, provider)
        manager = _manager(loop.bus, channel)
        dispatcher = asyncio.create_task(manager._dispatch_outbound())
        agent = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(_inbound("Read a.py then src.", "chat-stop"))
            await asyncio.wait_for(hanging.hanging.wait(), 20)
            await loop.bus.publish_inbound(_inbound("/stop", "chat-stop"))
            async with asyncio.timeout(10):
                while not hanging.closed:
                    await asyncio.sleep(0.02)
                while loop._active_tasks.get("websocket:chat-stop"):
                    await asyncio.sleep(0.02)
            await asyncio.sleep(0.2)
            return loop.sessions.get_or_create("websocket:chat-stop")
        finally:
            loop.stop()
            for task in (agent, dispatcher):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await manager._stop_send_lanes()
            await loop.close_mcp()

    with patch.dict("os.environ", {"NAVIN_STREAM_IDLE_TIMEOUT_S": "30"}):
        session = asyncio.run(run())
    assert hanging.closed
    dumped = json.dumps(session.messages)
    assert "call_half" not in dumped
    assert "Now editing" not in dumped
    _assert_history_consistent([m for m in session.messages if m.get("role") != "user"])
    users = [m for m in session.messages if m["role"] == "user"]
    assert len(users) == 1 and str(users[0]["content"]).startswith("Read a.py then src.")
    # The completed first batch survived the stop.
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "call_a" for m in session.messages)


def test_websocket_stream_buffers_are_dropped_on_turn_end():
    from navin.bus.outbound_events import TurnEndEvent, outbound_message_for_event
    from navin.channels.websocket import WebSocketChannel

    async def run():
        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._transcripts = MagicMock()
        channel._media = MagicMock()
        channel._media.rewrite_local_markdown_images = lambda text: text
        channel._stream_text_buffers = {}
        channel.logger = MagicMock()
        channel._subs = {}
        channel.send_turn_end = AsyncMock()
        channel.send_session_updated = AsyncMock()
        await channel.send_delta("c", "half an answer", stream_id="s0")
        assert channel._stream_text_buffers
        await channel.send(outbound_message_for_event(channel="websocket", chat_id="c", event=TurnEndEvent()))
        assert channel._stream_text_buffers == {}

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Discarded segment after a mid-stream retry
# ---------------------------------------------------------------------------


class _DiscardHook(StreamHook):
    async def on_stream_end(self, context, *, resuming: bool) -> None:
        self.events.append(("end", resuming, context.stream_discarded))


def test_mid_stream_retry_marks_the_partial_segment_discarded(tmp_path):
    root = _workspace(tmp_path)
    first = _Steps([(0, _cc(content="The ans")), (0, FakeConnectionError("reset"))])
    second = _Steps([(0, _cc(content="The answer is 42.")), (0, _cc(finish="stop"))])
    hook = _DiscardHook()
    result = asyncio.run(AgentRunner().run(_spec(_gpt_provider([first, second]), root, hook)))
    assert result.final_content == "The answer is 42."
    assert hook.events == [
        ("delta", "The ans"), ("end", True, True),
        ("delta", "The answer is 42."), ("end", False, False),
    ]


class _DiscardChannel(_Channel):
    async def send_delta(
        self, chat_id, delta, metadata=None, *, stream_id=None, stream_end=False,
        resuming=False, discarded=False,
    ):
        if stream_end and discarded:
            self.frames.append(("discard", stream_id))
            return
        await super().send_delta(
            chat_id, delta, metadata, stream_id=stream_id, stream_end=stream_end, resuming=resuming,
        )


def test_channel_is_told_to_discard_the_partial_segment(tmp_path, isolated_config):
    root = _workspace(tmp_path / "ws")
    first = _Steps([(0, _cc(content="The ans")), (0, FakeConnectionError("reset"))])
    second = _Steps([(0, _cc(content="The answer is 42.")), (0, _cc(finish="stop"))])
    provider = _gpt_provider([first, second])
    channel = _DiscardChannel(False)

    async def run():
        loop = _loop(root, provider)
        manager = _manager(loop.bus, channel)
        dispatcher = asyncio.create_task(manager._dispatch_outbound())
        agent = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(_inbound("What is the answer?", "chat-discard"))
            async with asyncio.timeout(20):
                while not any(f[0] == "end" and f[2] is False for f in channel.frames):
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
        finally:
            loop.stop()
            for task in (agent, dispatcher):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await manager._stop_send_lanes()
            await loop.close_mcp()

    asyncio.run(run())
    frames = [f for f in channel.frames if f[0] in ("delta", "end", "discard")]
    discarded = [f for f in frames if f[0] == "discard"]
    assert len(discarded) == 1
    partial_id = discarded[0][1]
    # What stays on screen: only the segments that were not discarded.
    shown = "".join(f[2] for f in frames if f[0] == "delta" and f[1] != partial_id)
    assert shown == "The answer is 42."
    assert not any(f[0] == "send" and "42" in str(f[1]) for f in channel.frames)


def test_websocket_discarded_stream_end_drops_buffer_and_flags_the_frame():
    from navin.channels.websocket import WebSocketChannel

    async def run():
        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._transcripts = MagicMock()
        channel._media = MagicMock()
        channel._media.rewrite_local_markdown_images = lambda text: text
        channel._stream_text_buffers = {}
        channel.logger = MagicMock()
        channel._subs = {}
        await channel.send_delta("c", "The ans", stream_id="s0")
        await channel.send_delta("c", "", stream_id="s0", stream_end=True, resuming=True, discarded=True)
        assert channel._stream_text_buffers == {}
        body = channel._transcripts.prepare_and_append.call_args_list[-1].args[1]
        assert body == {"event": "stream_end", "chat_id": "c", "discarded": True, "stream_id": "s0"}

    asyncio.run(run())


def test_manager_forwards_discarded_flag_to_channels():
    from navin.bus.outbound_events import StreamEndEvent, outbound_message_for_event
    from navin.channels.manager import ChannelManager

    channel = _DiscardChannel(False)
    event = StreamEndEvent(stream_id="s0", resuming=True, discarded=True)
    msg = outbound_message_for_event(channel="websocket", chat_id="c", event=event)
    asyncio.run(ChannelManager._send_once(channel, msg))
    assert channel.frames == [("discard", "s0")]


def test_transcript_replay_hides_a_discarded_segment():
    from navin.webui.transcript import replay_transcript_to_ui_messages

    lines = [
        {"event": "user", "chat_id": "c", "text": "q"},
        {"event": "delta", "chat_id": "c", "text": "The ans", "stream_id": "s0"},
        {"event": "stream_end", "chat_id": "c", "stream_id": "s0", "discarded": True},
        {"event": "delta", "chat_id": "c", "text": "The answer is 42.", "stream_id": "s1"},
        {"event": "stream_end", "chat_id": "c", "stream_id": "s1"},
        {"event": "turn_end", "chat_id": "c"},
    ]
    assistant = [m["content"] for m in replay_transcript_to_ui_messages(lines) if m.get("role") == "assistant"]
    assert assistant == ["The answer is 42."]


def test_progress_hook_passes_discarded_only_to_callbacks_that_take_it():
    from navin.agent.hook import AgentHookContext
    from navin.agent.progress_hook import AgentProgressHook

    seen: list[dict[str, Any]] = []

    async def modern(**kw):
        seen.append(kw)

    async def legacy(*, resuming: bool = False):
        seen.append({"legacy_resuming": resuming})

    async def run():
        ctx = AgentHookContext(iteration=0, messages=[], stream_discarded=True)
        await AgentProgressHook(on_stream=AsyncMock(), on_stream_end=modern).on_stream_end(ctx, resuming=True)
        await AgentProgressHook(on_stream=AsyncMock(), on_stream_end=legacy).on_stream_end(ctx, resuming=True)
        ctx.stream_discarded = False
        await AgentProgressHook(on_stream=AsyncMock(), on_stream_end=modern).on_stream_end(ctx, resuming=False)

    asyncio.run(run())
    assert seen == [
        {"resuming": True, "discarded": True},
        {"legacy_resuming": True},
        {"resuming": False},
    ]


# ---------------------------------------------------------------------------
# Final text when the turn stops
# ---------------------------------------------------------------------------


class _BrokenReadFile(ReadFileTool):
    async def execute(self, *args, **kwargs):  # type: ignore[override]
        raise RuntimeError("socket closed at http://localhost:8080 (run `navin doctor`)")


def test_turn_stopped_on_failing_tool_ends_on_a_plain_sentence(tmp_path):
    from navin.utils.runtime import TOOL_FAILURE_STOP_MESSAGE

    root = _workspace(tmp_path)
    provider = _gpt_provider([
        _Steps([
            (0, _cc(tool_calls=[_tc(0, "call_a", "read_file", '{"path": "a.py"}')])),
            (0, _cc(finish="tool_calls")),
        ]),
    ])
    tools = ToolRegistry()
    tools.register(_BrokenReadFile(workspace=root))
    spec = _spec(provider, root, StreamHook(), fail_on_tool_error=True)
    spec.tools = tools
    result = asyncio.run(AgentRunner().run(spec))
    assert result.stop_reason == "tool_error"
    assert result.final_content == TOOL_FAILURE_STOP_MESSAGE
    for leaked in ("Error:", "RuntimeError", "localhost", "http", "navin doctor"):
        assert leaked not in result.final_content
    # The technical detail stays available to logs and callers.
    assert "RuntimeError" in (result.error or "")


def test_tool_failure_notice_follows_the_reply_language():
    from navin.utils.runtime import TOOL_FAILURE_STOP_MESSAGE, tool_failure_stop_message

    assert tool_failure_stop_message("French").startswith("Une étape")
    assert tool_failure_stop_message("Arabic") != TOOL_FAILURE_STOP_MESSAGE
    assert tool_failure_stop_message(None) == TOOL_FAILURE_STOP_MESSAGE


@pytest.mark.parametrize("stop_reason", ["empty_final_response", "max_iterations"])
def test_unstreamed_final_notice_is_not_marked_streamed(tmp_path, isolated_config, stop_reason):
    loop = _loop(_workspace(tmp_path / "ws"), _gpt_provider([]))
    out = loop._assemble_outbound(
        _inbound("q", "chat-notice"), "notice", [], stop_reason, False, AsyncMock(),
    )
    assert out is not None and out.event is None and out.content == "notice"
    # A streamed answer is still marked, so channels never show it twice.
    streamed = loop._assemble_outbound(
        _inbound("q", "chat-notice"), "answer", [], "completed", False, AsyncMock(),
    )
    assert type(streamed.event).__name__ == "StreamedResponseEvent"
    notice = loop._assemble_outbound(
        _inbound("q", "chat-notice"), "notice", [], "completed", False, AsyncMock(),
        final_is_notice=True,
    )
    assert notice.event is None


def test_empty_final_notice_reaches_the_websocket_once(tmp_path, isolated_config):
    root = _workspace(tmp_path / "ws")
    blank = [_Steps([(0, _cc(content="   ")), (0, _cc(finish="stop"))]) for _ in range(12)]
    provider = _gpt_provider(blank)
    provider.chat_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value=LLMResponse(content="", finish_reason="stop"),
    )
    channel = _Channel(False)

    async def run():
        loop = _loop(root, provider)
        manager = _manager(loop.bus, channel)
        dispatcher = asyncio.create_task(manager._dispatch_outbound())
        agent = asyncio.create_task(loop.run())
        try:
            await loop.bus.publish_inbound(_inbound("hello there", "chat-empty"))
            async with asyncio.timeout(20):
                while not any(f[0] == "send" for f in channel.frames):
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
        finally:
            loop.stop()
            for task in (agent, dispatcher):
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            await manager._stop_send_lanes()
            await loop.close_mcp()

    asyncio.run(run())
    sends = [f for f in channel.frames if f[0] == "send" and f[1]]
    assert len(sends) == 1
    assert "empty" in sends[0][1].lower() or "vide" in sends[0][1].lower()
