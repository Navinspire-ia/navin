# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A Gemini conversation that moves to another backend keeps working: refused
thought signatures are replaced once by Google's neutral signature."""

from __future__ import annotations

import asyncio

from navin.providers.base import (
    NEUTRAL_THOUGHT_SIGNATURE,
    LLMProvider,
    LLMResponse,
    neutralize_thought_signatures,
)

HISTORY = [
    {"role": "user", "content": "Météo à Paris ?"},
    {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_1__thought__QUlTdHVkaW9TaWc=", "type": "function",
        "function": {"name": "get_weather", "arguments": "{\"city\": \"Paris\"}"},
        "extra_content": {"google": {"thought_signature": "QUlTdHVkaW9TaWc="}},
    }]},
    {"role": "tool", "tool_call_id": "call_1__thought__QUlTdHVkaW9TaWc=", "content": "21 C"},
]


def test_every_signature_is_neutralized_and_tool_results_stay_attached():
    neutral = neutralize_thought_signatures(HISTORY)
    call = neutral[1]["tool_calls"][0]
    assert call["id"] == f"call_1__thought__{NEUTRAL_THOUGHT_SIGNATURE}"
    assert call["extra_content"]["google"]["thought_signature"] == NEUTRAL_THOUGHT_SIGNATURE
    assert neutral[2]["tool_call_id"] == call["id"]
    assert HISTORY[1]["tool_calls"][0]["id"].endswith("QUlTdHVkaW9TaWc=")  # input untouched
    assert neutralize_thought_signatures([{"role": "user", "content": "hi"}]) is None


class Backend(LLMProvider):
    def __init__(self) -> None:
        super().__init__(None, None)
        self.seen: list[list[dict]] = []

    def get_default_model(self) -> str:
        return "google/gemini-3.8-flash"

    async def chat(self, messages, **_kw) -> LLMResponse:
        self.seen.append(messages)
        if any("QUlTdHVkaW9TaWc=" in str(m) for m in messages):
            return LLMResponse(content="Sorry, I encountered an error calling the AI model.", finish_reason="error",
                               error_status_code=400, error_detail="Corrupted thought signature.")
        return LLMResponse(content="Il fait 21 °C à Paris.")


def test_a_refused_signature_is_retried_once_with_the_neutral_one():
    backend = Backend()
    history = [dict(m) for m in HISTORY]
    response = asyncio.run(backend.chat_with_retry(history))
    assert response.content == "Il fait 21 °C à Paris."
    assert len(backend.seen) == 2
    # The next turns reuse the neutral history: no second refusal.
    assert NEUTRAL_THOUGHT_SIGNATURE in history[1]["tool_calls"][0]["id"]
