# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Provider behavior ported from upstream nanobot.

Four fixes travelled together: multimodal tool outputs on the Responses API,
a safety margin on Retry-After, config-driven request overrides for Codex,
and model-keyed thinking styles for recent Qwen models.
"""

from __future__ import annotations

import json
import types
import unittest
from unittest import mock

from navin.providers.base import RETRY_AFTER_BUFFER, LLMProvider, LLMResponse
from navin.providers.fallback_policy import JITTER_RATIO
from navin.providers.openai_codex_provider import OpenAICodexProvider
from navin.providers.openai_compat_provider import _model_thinking_style
from navin.providers.openai_responses.converters import (
    convert_messages,
    convert_tool_output,
)


class ToolOutputTest(unittest.TestCase):
    """A tool result with an image must reach the Responses API as an image,
    not as a JSON string the model cannot see."""

    def test_a_plain_string_passes_through(self):
        self.assertEqual(convert_tool_output("done"), "done")

    def test_text_and_image_blocks_stay_multimodal(self):
        blocks = [
            {"type": "text", "text": "the screenshot:"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ]
        self.assertEqual(
            convert_tool_output(blocks),
            [
                {"type": "input_text", "text": "the screenshot:"},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": "data:image/png;base64,QUJD",
                },
            ],
        )

    def test_internal_meta_is_stripped(self):
        blocks = [{"type": "text", "text": "ok", "_meta": {"origin": "read_file"}}]
        self.assertEqual(convert_tool_output(blocks), [{"type": "input_text", "text": "ok"}])

    def test_an_unknown_block_falls_back_to_json(self):
        blocks = [{"type": "text", "text": "ok"}, {"type": "audio", "data": "..."}]
        self.assertEqual(convert_tool_output(blocks), json.dumps(blocks, ensure_ascii=False))

    def test_an_unexpected_field_falls_back_to_json(self):
        blocks = [{"type": "text", "text": "ok", "internal_secret": "x"}]
        self.assertEqual(convert_tool_output(blocks), json.dumps(blocks, ensure_ascii=False))

    def test_convert_messages_routes_tool_results_through_it(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
                ],
            }
        ]
        _, items = convert_messages(messages)
        self.assertEqual(items[0]["output"][0]["type"], "input_image")


class _RetryProbe(LLMProvider):
    """Minimal concrete provider so _run_with_retry can be exercised."""

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None, tool_choice=None):  # noqa: E501
        raise NotImplementedError

    def get_default_model(self) -> str:
        return "probe"


class RetryBufferTest(unittest.IsolatedAsyncioTestCase):
    """Retrying exactly at Retry-After can land inside the same rate-limit
    window; the delay has to clear it."""

    async def _delay_used(self, first: LLMResponse) -> float:
        provider = _RetryProbe()
        responses = [first, LLMResponse(content="ok", finish_reason="stop")]

        async def call(**_kw):
            return responses.pop(0)

        slept: list[float] = []

        async def sleep(delay, **_kw):
            slept.append(delay)

        with mock.patch.object(provider, "_sleep_with_heartbeat", sleep):
            result = await provider._run_with_retry(
                call,
                {"messages": []},
                [],
                retry_mode="standard",
                on_retry_wait=None,
            )
        self.assertEqual(result.content, "ok")
        return slept[0]

    def _assert_waits_from(self, delay: float, floor: float) -> None:
        """The delay starts at *floor*; jitter may only push it later."""
        self.assertGreaterEqual(delay, floor)
        self.assertLessEqual(delay, floor * (1 + JITTER_RATIO))

    async def test_the_provider_wait_gets_a_buffer(self):
        error = LLMResponse(
            content="rate limited",
            finish_reason="error",
            error_should_retry=True,
            error_retry_after_s=3.0,
        )
        self._assert_waits_from(
            await self._delay_used(error), 3.0 + RETRY_AFTER_BUFFER
        )

    async def test_the_backoff_delay_keeps_its_step_as_a_floor(self):
        # Jitter widens the step so clients that failed together do not retry
        # together, but never shortens it.
        error = LLMResponse(
            content="upstream hiccup",
            finish_reason="error",
            error_should_retry=True,
        )
        provider = _RetryProbe()
        self._assert_waits_from(
            await self._delay_used(error), provider._CHAT_RETRY_DELAYS[0]
        )

    async def test_free_model_retries_a_connection_error(self):
        provider = _RetryProbe()
        responses = [
            LLMResponse(
                content="Error calling LLM: Connection error.",
                finish_reason="error",
                error_kind="connection",
            ),
            LLMResponse(content="ok", finish_reason="stop"),
        ]

        async def call(**_kw):
            return responses.pop(0)

        async def sleep(_delay, **_kw):
            return None

        with mock.patch.object(provider, "_sleep_with_heartbeat", sleep):
            result = await provider._run_with_retry(
                call,
                {"messages": [], "model": "vendor/model:free"},
                [],
                retry_mode="standard",
                on_retry_wait=None,
            )
        self.assertEqual(result.content, "ok")
        self.assertEqual(responses, [])

    async def test_free_model_retries_rate_limits_on_the_short_ladder(self):
        # A saturated :free pool is the normal state, not a dead end: the turn
        # gets a bounded number of extra passes instead of an instant error.
        provider = _RetryProbe()
        error = LLMResponse(
            content="rate limited",
            finish_reason="error",
            error_status_code=429,
            error_should_retry=True,
        )
        calls = 0
        slept: list[float] = []

        async def call(**_kw):
            nonlocal calls
            calls += 1
            return error

        async def sleep(delay, **_kw):
            slept.append(delay)

        with mock.patch.object(provider, "_sleep_with_heartbeat", sleep):
            result = await provider._run_with_retry(
                call,
                {"messages": [], "model": "vendor/model:free"},
                [],
                retry_mode="standard",
                on_retry_wait=None,
            )
        self.assertEqual(result.finish_reason, "error")
        self.assertEqual(calls, 1 + len(provider._FREE_CHAT_RETRY_DELAYS))
        for delay in slept:
            self.assertLessEqual(delay, provider._FREE_MAX_DELAY)

    async def test_free_model_caps_a_long_retry_after_hint(self):
        # Waiting the full advertised window on a free pool would hang the UI;
        # retrying the failover chain sooner is the better trade.
        provider = _RetryProbe()
        error = LLMResponse(
            content="rate limited",
            finish_reason="error",
            error_status_code=429,
            error_should_retry=True,
            error_retry_after_s=90.0,
        )
        responses = [error, LLMResponse(content="ok", finish_reason="stop")]

        async def call(**_kw):
            return responses.pop(0)

        slept: list[float] = []

        async def sleep(delay, **_kw):
            slept.append(delay)

        with mock.patch.object(provider, "_sleep_with_heartbeat", sleep):
            result = await provider._run_with_retry(
                call,
                {"messages": [], "model": "vendor/model:free"},
                [],
                retry_mode="standard",
                on_retry_wait=None,
            )
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(slept), 1)
        self.assertLessEqual(slept[0], provider._FREE_MAX_DELAY)


class CodexExtraBodyTest(unittest.IsolatedAsyncioTestCase):
    """providers.openai_codex.extra_body overrides the request body last,
    which is how Codex fast mode is switched on from config."""

    async def test_extra_body_lands_in_the_request(self):
        provider = OpenAICodexProvider(extra_body={"model_mode": "fast"})
        captured: dict = {}

        async def request(url, headers, body, verify, **_kw):
            captured.update(body)
            return "ok", [], "stop", {}, None

        token = types.SimpleNamespace(account_id="acct", access="tok")
        with (
            mock.patch(
                "navin.providers.openai_codex_provider.get_codex_token",
                return_value=token,
            ),
            mock.patch("navin.providers.openai_codex_provider._request_codex", request),
        ):
            response = await provider.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(response.content, "ok")
        self.assertEqual(captured["model_mode"], "fast")

    async def test_no_override_leaves_the_body_alone(self):
        provider = OpenAICodexProvider()
        self.assertEqual(provider._extra_body, {})


class QwenThinkingTest(unittest.TestCase):
    """The thinking toggle is keyed on the model, so it also reaches Qwen
    models served through a gateway instead of only through DashScope."""

    def test_recent_qwen_models_use_enable_thinking(self):
        self.assertEqual(_model_thinking_style("qwen3.7-max"), "enable_thinking")
        self.assertEqual(_model_thinking_style("qwen3.8-max"), "enable_thinking")
        self.assertEqual(_model_thinking_style("openrouter/qwen3.5-plus"), "enable_thinking")

    def test_other_models_are_untouched(self):
        self.assertEqual(_model_thinking_style("qwen2.5-coder"), "")
        self.assertEqual(_model_thinking_style("gpt-5"), "")


if __name__ == "__main__":
    unittest.main()
