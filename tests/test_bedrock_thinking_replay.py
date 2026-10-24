# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bedrock replays only thinking blocks it can still vouch for.

A thinking signature is bound to the system prompt and history it was written
under. Replaying a previous turn's block, or one Bedrock already refused, got
"Invalid signature in thinking block. The block is bound to a different
conversation" on every later message of the session, "hello" included.
"""

from __future__ import annotations

import asyncio
import unittest

from botocore.exceptions import ClientError

from navin.providers import bedrock_provider
from navin.providers.bedrock_provider import BedrockProvider

MODEL = "global.anthropic.claude-opus-5-5"
OLD = {"type": "thinking", "thinking": "old turn", "signature": "sig-old"}
NOW = {"type": "thinking", "thinking": "this turn", "signature": "sig-now"}
TOOL_CALL = {"id": "t1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
BINDING_ERROR = (
    "The model returned the following errors: messages.3.content.0: Invalid signature in "
    "thinking block. The block is bound to a different conversation. Remove the block, or set "
    'thinking.block_binding.prefix_mismatch_behavior to "drop_block".'
)


def _history() -> list[dict]:
    return [
        {"role": "system", "content": "You are navin."},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "done", "thinking_blocks": [dict(OLD)]},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "", "tool_calls": [TOOL_CALL], "thinking_blocks": [dict(NOW)]},
        {"role": "tool", "tool_call_id": "t1", "content": "ok"},
    ]


def _signatures(kwargs: dict) -> list[str]:
    return [
        block["reasoningContent"]["reasoningText"]["signature"]
        for message in kwargs["messages"]
        for block in message["content"]
        if "reasoningContent" in block
    ]


class _Client:
    """converse() refuses while any replayed block is present, then answers."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if _signatures(kwargs):
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": BINDING_ERROR},
                 "ResponseMetadata": {"HTTPStatusCode": 400}},
                "Converse",
            )
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "hello"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


class BedrockThinkingReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        bedrock_provider._REJECTED_THINKING.clear()
        self.addCleanup(bedrock_provider._REJECTED_THINKING.clear)

    def test_only_the_current_turn_thinking_is_replayed(self) -> None:
        provider = BedrockProvider(default_model=MODEL, region="us-east-2", client=object())
        kwargs = provider._build_kwargs(_history(), None, MODEL, 1024, 0.2, None, None)
        self.assertEqual(_signatures(kwargs), ["sig-now"])

    def test_signature_refusal_retries_once_without_the_block(self) -> None:
        client = _Client()
        provider = BedrockProvider(default_model=MODEL, region="us-east-2", client=client)
        result = asyncio.run(provider.chat(_history(), model=MODEL, reasoning_effort="high"))
        self.assertEqual(result.content, "hello")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(_signatures(client.calls[1]), [])
        # The refusal does not switch reasoning off for the model.
        self.assertNotIn(MODEL.lower(), bedrock_provider._NO_REASONING_LEARNED)
        self.assertIn("additionalModelRequestFields", client.calls[1])

    def test_persisting_refusal_reaches_the_caller(self) -> None:
        bedrock_provider._REJECTED_THINKING["s:sig-now"] = None
        provider = BedrockProvider(default_model=MODEL, region="us-east-2", client=object())
        self.assertFalse(provider._learned_thinking_retry(
            MODEL, "high",
            bedrock_provider.LLMResponse(content="Error: " + BINDING_ERROR, finish_reason="error"),
            _history(),
        ))


if __name__ == "__main__":
    unittest.main()
