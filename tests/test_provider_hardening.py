# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider defects: idle timeouts, stream cleanup, effort aliases, redacted
thinking, persistent retry ceiling and the OpenRouter host check."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.providers import registry
from navin.providers.anthropic_provider import AnthropicProvider
from navin.providers.base import LLMProvider, LLMResponse
from navin.providers.bedrock_provider import BedrockProvider
from navin.providers.claude_capabilities import claude_thinking_fields
from navin.providers.gateway import is_navin_gateway, navin_managed_base
from navin.providers.openai_compat_provider import OpenAICompatProvider
from navin.providers.reasoning_control import non_anthropic_effort

MESSAGES = [{"role": "user", "content": "hi"}]


# -- Anthropic: idle timeout without callbacks ---------------------------------


class _FakeAnthropicStream:
    def __init__(self, chunks, final):
        self._chunks = list(chunks)
        self._final = final
        self.final_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        delay = self._chunks.pop(0)
        await asyncio.sleep(delay)
        return SimpleNamespace(type="ping")

    async def get_final_message(self):
        # Like the SDK: drains whatever the caller did not consume.
        self.final_called = True
        async for _ in self:
            pass
        return self._final


def _final_message():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")],
        stop_reason="end_turn",
        usage=None,
    )


def _anthropic_with_stream(stream):
    provider = AnthropicProvider(api_key="k", default_model="claude-sonnet-4-5")
    provider._client_instance = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **_kw: stream),
    )
    return provider


def test_anthropic_stream_without_callbacks_is_not_a_whole_response_limit():
    # Total stream time (3 x 0.06s) exceeds the idle window (0.1s); each gap
    # does not, so the response must succeed.
    stream = _FakeAnthropicStream([0.06, 0.06, 0.06], _final_message())
    provider = _anthropic_with_stream(stream)
    with patch(
        "navin.providers.anthropic_provider.resolve_stream_idle_timeout_s", return_value=0.1,
    ):
        response = asyncio.run(provider.chat_stream(MESSAGES))
    assert response.content == "done"
    assert stream.final_called


def test_anthropic_stream_without_callbacks_still_detects_a_stall():
    stream = _FakeAnthropicStream([0.5], _final_message())
    provider = _anthropic_with_stream(stream)
    with patch(
        "navin.providers.anthropic_provider.resolve_stream_idle_timeout_s", return_value=0.05,
    ):
        response = asyncio.run(provider.chat_stream(MESSAGES))
    assert response.finish_reason == "error"
    assert response.error_kind == "timeout"


# -- Anthropic: redacted thinking ----------------------------------------------


def test_redacted_thinking_survives_parse_and_replay_in_order():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="a", signature="sig"),
            SimpleNamespace(type="redacted_thinking", data="opaque"),
            SimpleNamespace(type="text", text="answer"),
        ],
        stop_reason="end_turn",
        usage=None,
    )
    parsed = AnthropicProvider._parse_response(response)
    assert parsed.thinking_blocks == [
        {"type": "thinking", "thinking": "a", "signature": "sig"},
        {"type": "redacted_thinking", "data": "opaque"},
    ]
    blocks = AnthropicProvider._assistant_blocks(
        {"role": "assistant", "content": "answer", "thinking_blocks": parsed.thinking_blocks},
    )
    assert [b["type"] for b in blocks] == ["thinking", "redacted_thinking", "text"]
    assert blocks[1] == {"type": "redacted_thinking", "data": "opaque"}


# -- Effort aliases --------------------------------------------------------------


def test_non_anthropic_effort_aliases():
    assert non_anthropic_effort("adaptive") == "high"
    assert non_anthropic_effort("ULTRA") == "max"
    assert non_anthropic_effort("medium") == "medium"
    assert non_anthropic_effort(None) is None


@pytest.mark.parametrize(("effort", "expected"), [("adaptive", "high"), ("ultra", "max")])
def test_openai_compat_chat_wire_never_sends_anthropic_efforts(effort, expected):
    provider = OpenAICompatProvider(
        api_base="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        spec=registry.find_by_name("deepseek"),
    )
    kwargs = provider._build_kwargs(MESSAGES, None, "deepseek-chat", 4096, 0.2, effort, None)
    assert kwargs.get("reasoning_effort") in (expected, None)
    assert effort not in str(kwargs)


def test_openai_responses_body_never_sends_anthropic_efforts():
    provider = OpenAICompatProvider(
        api_base="https://api.openai.com/v1",
        default_model="gpt-5.1",
        spec=registry.find_by_name("openai"),
    )
    body = provider._build_responses_body(MESSAGES, None, "gpt-5.1", 4096, 0.2, "adaptive", None)
    assert body["reasoning"] == {"effort": "high"}
    # "ultra" maps to "max", which GPT-5.1 does not take: it goes out as the
    # nearest level on its ladder (none..high); GPT-5.6 takes "max" itself.
    body = provider._build_responses_body(MESSAGES, None, "gpt-5.1", 4096, 0.2, "ultra", None)
    assert body["reasoning"] == {"effort": "high"}
    body = provider._build_responses_body(MESSAGES, None, "gpt-5.6", 4096, 0.2, "ultra", None)
    assert body["reasoning"] == {"effort": "max"}


def test_claude_ultra_is_the_deepest_tier():
    fields, _ = claude_thinking_fields("claude-opus-4-7", "ultra", max_tokens=8192)
    assert fields["output_config"] == {"effort": "max"}
    fields, _ = claude_thinking_fields("claude-opus-4-7", "adaptive", max_tokens=8192)
    assert fields == {"thinking": {"type": "adaptive", "display": "summarized"}}


# -- OpenAI-compatible: stream cleanup ------------------------------------------


class _StallingStream:
    def __init__(self):
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(5)
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class _FinishedStream(_StallingStream):
    async def __anext__(self):
        raise StopAsyncIteration


def _compat_with_stream(stream):
    provider = OpenAICompatProvider(
        api_base="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        spec=registry.find_by_name("deepseek"),
    )

    async def create(**_kw):
        return stream

    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=lambda: None,
    )
    return provider


def test_openai_compat_idle_timeout_closes_stream_and_resets_client():
    stream = _StallingStream()
    provider = _compat_with_stream(stream)
    with patch(
        "navin.providers.openai_compat_provider.resolve_stream_idle_timeout_s", return_value=0.05,
    ), patch("navin.providers.openai_compat_provider._buffered_output_idle_s", return_value=0.05):
        response = asyncio.run(provider.chat_stream(MESSAGES))
    assert response.error_kind == "timeout"
    assert stream.closed
    assert provider._client is None


def test_openai_compat_finished_stream_is_closed():
    stream = _FinishedStream()
    provider = _compat_with_stream(stream)
    asyncio.run(provider.chat_stream(MESSAGES))
    assert stream.closed


# -- Bedrock client config --------------------------------------------------------


def test_bedrock_client_uses_long_reads_and_no_botocore_retries():
    captured: dict = {}

    class _Session:
        def client(self, name, **kwargs):
            captured.update(kwargs)
            return object()

    with patch("navin.providers.bedrock_media.aws_session", return_value=_Session()):
        BedrockProvider(default_model="anthropic.claude-sonnet-4-5", region="us-east-1")
    config = captured["config"]
    # Non-streamed converse() is silent until the answer exists: >= 1 h.
    assert config.read_timeout >= 3600
    assert config.retries["max_attempts"] == 1


# -- Persistent retry -------------------------------------------------------------


class _AlwaysBusy(LLMProvider):
    def __init__(self, *, structured: bool) -> None:
        super().__init__(None, None)
        self.calls = 0
        self.structured = structured

    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, messages, **_kw) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=f"429 rate limited, request id req_{self.calls:08d}",
            finish_reason="error",
            error_status_code=429 if self.structured else None,
            error_kind="rate_limit" if self.structured else None,
        )

    async def _sleep_with_heartbeat(self, *_a, **_kw) -> None:
        return None


@pytest.mark.parametrize("structured", [True, False])
def test_persistent_retry_stops_on_errors_that_differ_only_by_request_id(structured):
    provider = _AlwaysBusy(structured=structured)
    response = asyncio.run(provider.chat_with_retry(MESSAGES, retry_mode="persistent"))
    assert response.finish_reason == "error"
    assert provider.calls == provider._PERSISTENT_IDENTICAL_ERROR_LIMIT


def test_persistent_retry_has_a_hard_attempt_ceiling():
    class _Rotating(_AlwaysBusy):
        async def chat(self, messages, **_kw) -> LLMResponse:
            self.calls += 1
            words = ("alpha", "beta", "gamma")
            return LLMResponse(
                content=f"429 upstream {words[self.calls % 3]} overloaded",
                finish_reason="error",
            )

    provider = _Rotating(structured=False)
    response = asyncio.run(provider.chat_with_retry(MESSAGES, retry_mode="persistent"))
    assert response.finish_reason == "error"
    assert provider.calls == provider._PERSISTENT_MAX_ATTEMPTS


# -- Gateway host check ----------------------------------------------------------


def test_openrouter_subdomains_are_not_the_navin_gateway():
    assert not is_navin_gateway("https://openrouter.ai/api/v1")
    assert not is_navin_gateway("https://eu.openrouter.ai/api/v1")
    assert not is_navin_gateway("https://WWW.OpenRouter.ai/api/v1")
    assert is_navin_gateway("https://llm.navin.live/v1")
    assert is_navin_gateway("https://notopenrouter.ai/v1")
    assert navin_managed_base("https://eu.openrouter.ai/api/v1") == "https://llm.navin.live/v1"
