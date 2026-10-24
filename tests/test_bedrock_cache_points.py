# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bedrock Converse requests carry cache points for Claude.

Without them every agent step re-read tools, system prompt and history at
full price and full prefill time. Models that reject the block are left alone.
"""

from __future__ import annotations

import unittest

from navin.providers.bedrock_provider import BedrockProvider

TOOLS = [{
    "type": "function",
    "function": {"name": "read_file", "description": "Read", "parameters": {"type": "object"}},
}]
MESSAGES = [
    {"role": "system", "content": "You are navin."},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "read it"},
]
POINT = {"cachePoint": {"type": "default"}}


def _kwargs(model: str, messages=MESSAGES, tools=TOOLS) -> dict:
    provider = BedrockProvider(default_model=model, region="us-east-1", client=object())
    return provider._build_kwargs(
        [dict(m) for m in messages], tools, model, 1024, 0.2, None, None
    )


class BedrockCachePointTest(unittest.TestCase):
    def test_claude_marks_tools_system_and_conversation_end(self) -> None:
        for model in (
            "us.anthropic.claude-sonnet-4-6-v1:0",
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "anthropic.claude-3-5-haiku-20241022-v1:0",
            "global.anthropic.claude-opus-5-5-v1:0",
        ):
            with self.subTest(model=model):
                kwargs = _kwargs(model)
                self.assertEqual(kwargs["toolConfig"]["tools"][-1], POINT)
                self.assertEqual(kwargs["system"][-1], POINT)
                self.assertEqual(kwargs["messages"][-1]["content"][-1], POINT)

    def test_models_without_cache_support_are_untouched(self) -> None:
        for model in (
            "anthropic.claude-3-sonnet-20240229-v1:0",
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "meta.llama3-70b-instruct-v1:0",
            "mistral.mistral-large-2407-v1:0",
        ):
            with self.subTest(model=model):
                kwargs = _kwargs(model)
                self.assertNotIn(POINT, kwargs["toolConfig"]["tools"])
                self.assertNotIn(POINT, kwargs["system"])
                self.assertNotIn(POINT, kwargs["messages"][-1]["content"])

    def test_caller_placed_points_are_not_duplicated(self) -> None:
        messages = [
            {"role": "system", "content": [{"type": "text", "text": "sys"}, POINT]},
            {"role": "user", "content": "hi"},
        ]
        kwargs = _kwargs("us.anthropic.claude-sonnet-4-6-v1:0", messages=messages)
        total = sum(1 for block in kwargs["system"] if "cachePoint" in block)
        total += sum(
            1 for m in kwargs["messages"] for block in m["content"] if "cachePoint" in block
        )
        total += sum(1 for t in kwargs["toolConfig"]["tools"] if "cachePoint" in t)
        self.assertEqual(total, 1)


if __name__ == "__main__":
    unittest.main()
