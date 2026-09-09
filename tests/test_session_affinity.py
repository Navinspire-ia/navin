# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The runner must tag every provider request with the chat session key.

Cache-affinity routing (OpenRouter session_id, OpenAI prompt_cache_key) only
works when the key is visible at the exact moment the provider builds the
request body. These tests prove the runner sets the context variable around
the awaited provider call, for both plain and streaming requests.
"""

from __future__ import annotations

import unittest
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse
from navin.providers.session_affinity import current_session_id
from navin.utils.llm_runtime import LLMRuntime


class _RecordingProvider:
    """Records the session id visible while the request coroutine runs."""

    def __init__(self) -> None:
        self.seen_session_ids: list[str | None] = []

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        self.seen_session_ids.append(current_session_id())
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        kwargs.pop("on_content_delta", None)
        kwargs.pop("on_thinking_delta", None)
        kwargs.pop("on_stream_recover", None)
        return await self.chat_with_retry(**kwargs)


def _spec(provider: _RecordingProvider, session_key: str | None) -> AgentRunSpec:
    return AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=ToolRegistry(),
        runtime=LLMRuntime(
            provider=provider,
            model="test-model",
            generation=GenerationSettings(),
            context_window_tokens=128_000,
        ),
        max_iterations=2,
        max_tool_result_chars=4000,
        hook=AgentHook(),
        session_key=session_key,
    )


class SessionAffinityRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_sees_the_session_key_during_the_request(self) -> None:
        provider = _RecordingProvider()
        await AgentRunner().run(_spec(provider, "webui:main"))
        self.assertEqual(provider.seen_session_ids, ["webui:main"])
        # The tag must not leak outside the request.
        self.assertIsNone(current_session_id())

    async def test_no_session_key_means_no_affinity_tag(self) -> None:
        provider = _RecordingProvider()
        await AgentRunner().run(_spec(provider, None))
        self.assertEqual(provider.seen_session_ids, [None])


if __name__ == "__main__":
    unittest.main()
