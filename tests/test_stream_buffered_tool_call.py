"""Silence after reasoning is not a stall: some endpoints (Kimi K3 on Bedrock via
LiteLLM) send a tool call's arguments in one piece at the end. Silence while
plain text streams still is a stall."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from navin.providers.openai_compat_provider import OpenAICompatProvider


def _chunk(**delta):
    fields = {"content": None, "reasoning_content": None, "reasoning": None, "tool_calls": None, "function_call": None}
    fields.update(delta)
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields), finish_reason=None, index=0)], usage=None)


class _Stream:
    def __init__(self, steps):
        self._steps = list(steps)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._steps:
            raise StopAsyncIteration
        pause, chunk = self._steps.pop(0)
        await asyncio.sleep(pause)
        return chunk


def _provider(steps):
    provider = OpenAICompatProvider(api_key="t", api_base="https://example.invalid/v1", default_model="m")

    async def create(**_kwargs):
        return _Stream(steps)

    async def ready():
        return None

    provider._ensure_client = ready  # type: ignore[method-assign]
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return provider


@pytest.fixture(autouse=True)
def _short_windows(monkeypatch):
    monkeypatch.setenv("NAVIN_STREAM_IDLE_TIMEOUT_S", "0.2")
    monkeypatch.setenv("NAVIN_STREAM_BUFFERED_IDLE_S", "2")


def test_silence_after_reasoning_waits_for_the_buffered_tool_call():
    tool = [SimpleNamespace(index=0, id="call_1", type="function",
                            function=SimpleNamespace(name="write_file", arguments='{"path": "a.py", "content": "x"}'))]
    provider = _provider([(0, _chunk(reasoning_content="planning the file")), (0.6, _chunk(tool_calls=tool))])

    async def noop(_value):
        return None

    result = asyncio.run(provider.chat_stream(
        messages=[{"role": "user", "content": "write it"}], tools=[{"type": "function", "function": {"name": "write_file", "parameters": {}}}],
        model="m", max_tokens=100, temperature=0.1, on_thinking_delta=noop, on_tool_call_delta=noop,
    ))
    assert result.finish_reason != "error", result.content
    assert [call.name for call in result.tool_calls] == ["write_file"]


def test_silence_while_text_streams_is_still_a_stall():
    provider = _provider([(0, _chunk(content="Hello")), (0.6, _chunk(content=" world"))])

    async def noop(_value):
        return None

    result = asyncio.run(provider.chat_stream(
        messages=[{"role": "user", "content": "hi"}], tools=None, model="m", max_tokens=100, temperature=0.1,
        on_content_delta=noop,
    ))
    assert result.finish_reason == "error"
    assert "stream stalled" in (result.content or "")
