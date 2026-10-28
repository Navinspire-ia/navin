# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""P3-4: the empty-response and length-recovery limits are per-run settings.

They were module constants, so a caller that wanted a more patient run (long
document generation) or a stricter one (cheap background job) had no lever.
``AgentRunSpec.max_empty_retries`` / ``max_length_recoveries`` now carry them,
defaulting to the historical values.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import (
    _MAX_EMPTY_RETRIES,
    _MAX_LENGTH_RECOVERIES,
    AgentRunner,
    AgentRunSpec,
)
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.runtime import (
    DELIVERY_CONTINUE_PROMPT,
    EMPTY_FINAL_RESPONSE_MESSAGE,
    empty_final_response_message,
)


class _FakeProvider:
    """Replays a scripted response list; repeats the last one when exhausted."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        self.requests.append(list(kwargs.get("messages") or []))
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


def _spec(provider: Any, **overrides: Any) -> AgentRunSpec:
    return AgentRunSpec(
        **{
            "initial_messages": [{"role": "user", "content": "go"}],
            "tools": ToolRegistry(),
            "runtime": _runtime(provider),
            "max_iterations": 12,
            "max_tool_result_chars": 2000,
            "hook": AgentHook(),
            **overrides,
        }
    )


class SpecDefaultsTest(unittest.TestCase):
    def test_defaults_match_the_historical_constants(self) -> None:
        spec = _spec(_FakeProvider([LLMResponse(content="ok", finish_reason="stop")]))
        self.assertEqual(spec.max_empty_retries, _MAX_EMPTY_RETRIES)
        self.assertEqual(spec.max_length_recoveries, _MAX_LENGTH_RECOVERIES)


class EmptyRetryLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_bounds_the_provider_calls(self) -> None:
        # An always-blank provider: N normal attempts, then the finalization
        # retry and the strict last-chance prompt, then the run gives up.
        for limit, expected_calls in ((1, 3), (4, 6)):
            provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
            await AgentRunner().run(_spec(provider, max_empty_retries=limit))
            self.assertEqual(
                provider.calls,
                expected_calls,
                f"max_empty_retries={limit} should mean {expected_calls} calls",
            )

    async def test_strict_prompt_recovers_an_answer_after_blank_finalization(self) -> None:
        """Regression: blank turns, then a blank finalization reply, ended on
        the "empty reply" notice although one explicit prompt still worked."""
        provider = _FakeProvider(
            [
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
                LLMResponse(content="Voici le resultat.", finish_reason="stop"),
            ]
        )
        result = await AgentRunner().run(_spec(provider, max_empty_retries=2))
        self.assertEqual(provider.calls, 4)
        self.assertEqual(result.final_content, "Voici le resultat.")
        self.assertNotEqual(result.stop_reason, "empty_final_response")
        self.assertIsNone(result.error)

    async def test_blank_reply_to_the_delivery_nudge_keeps_the_answer(self) -> None:
        """Regression (session cli:1792105183): a text answer, the delivery
        nudge, then only blank replies ended on the English "couldn't produce
        a final answer" and threw the model's own answer away."""
        answer = "Il n'y a pas encore de demande precise : que veux-tu construire ?"
        provider = _FakeProvider(
            [
                LLMResponse(content=answer, finish_reason="stop"),
                LLMResponse(content="", finish_reason="stop"),
            ]
        )
        result = await AgentRunner().run(_spec(provider, requires_tool_delivery=True))
        self.assertEqual(result.final_content, answer)
        self.assertEqual(result.stop_reason, "completed")
        self.assertIsNone(result.error)
        # The nudge is dropped so history ends on the answer it was sent after.
        self.assertEqual(result.messages[-1]["role"], "assistant")
        self.assertEqual(result.messages[-1]["content"], answer)
        self.assertNotIn(DELIVERY_CONTINUE_PROMPT, str(result.messages))

    async def test_empty_final_notice_speaks_the_chat_language(self) -> None:
        from navin.agent.reply_language import reply_language

        reply_language("cli:empty-fr", "corrige le bug dans le fichier de config stp")
        provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
        result = await AgentRunner().run(_spec(provider, session_key="cli:empty-fr"))
        self.assertEqual(result.stop_reason, "empty_final_response")
        self.assertEqual(result.final_content, empty_final_response_message("French"))
        self.assertIn("réponse vide", result.final_content or "")

        provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
        result = await AgentRunner().run(_spec(provider, session_key="cli:empty-unknown"))
        self.assertEqual(result.final_content, EMPTY_FINAL_RESPONSE_MESSAGE)
        self.assertNotIn("completed the tool steps", result.final_content or "")

    async def test_navins_relaunches_remind_the_model_of_the_chat_language(self) -> None:
        # The blank reply makes the runner relaunch the model with its own
        # English "Please provide your response" message: the request carries
        # the chat's language, the stored history does not change.
        from navin.agent.reply_language import reply_language

        reply_language("cli:relaunch-fr", "corrige le bug dans le fichier de config stp")
        provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
        # The user's message as the loop sends it: the language line is already in it.
        own = {"role": "user", "content": "go\n\n[Runtime Context]\nReply language: French.\n[/Runtime Context]"}
        result = await AgentRunner().run(
            _spec(provider, session_key="cli:relaunch-fr", initial_messages=[dict(own)]),
        )
        relaunches = [
            request[-1] for request in provider.requests[1:]
            if request and request[-1]["role"] == "user"
        ]
        self.assertTrue(relaunches)
        for message in relaunches:
            self.assertIn("Reply language: French", message["content"])
            self.assertEqual(message["content"].count("Reply language"), 1)
        # The user's own message is sent as it is: it is not Navin's.
        self.assertEqual(provider.requests[0][-1], own)
        for message in result.messages[1:]:
            self.assertNotIn("Reply language", str(message.get("content")))

        # A chat with no known language sends its relaunches unchanged.
        provider = _FakeProvider([LLMResponse(content="", finish_reason="stop")])
        await AgentRunner().run(_spec(provider, session_key="cli:relaunch-unknown"))
        for request in provider.requests:
            self.assertNotIn("Reply language", str(request[-1].get("content")))


class LengthRecoveryLimitTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_bounds_the_continuations(self) -> None:
        # Every response is a truncated chunk: M continuations are attempted,
        # then the truncated output is accepted as final.
        for limit, expected_calls in ((1, 2), (3, 4)):
            provider = _FakeProvider(
                [LLMResponse(content="chunk", finish_reason="length")]
            )
            result = await AgentRunner().run(
                _spec(provider, max_length_recoveries=limit)
            )
            self.assertEqual(
                provider.calls,
                expected_calls,
                f"max_length_recoveries={limit} should mean {expected_calls} calls",
            )
            self.assertIn("chunk", result.final_content or "")


if __name__ == "__main__":
    unittest.main()
