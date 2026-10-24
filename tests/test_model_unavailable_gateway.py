"""A model the gateway's provider account cannot reach is not a key problem:
clear message, failover allowed, no license resync, skipped while refused."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from navin.providers import fallback_provider as fp
from navin.providers.base import LLMResponse
from navin.providers.user_facing_errors import content_allows_model_switch, user_facing_llm_error

BEDROCK_403 = (
    "Error: {'message': 'litellm.PermissionDeniedError: BedrockException - "
    "{\"message\":\"anthropic.claude-sonnet-5-5 is not available for this account. "
    "You can explore other available models on Amazon Bedrock.\"}', 'type': None, 'code': '403'}"
)


def test_bedrock_account_refusal_is_not_shown_as_a_bad_key():
    message = user_facing_llm_error(BEDROCK_403)
    assert "API key" not in message
    assert "not available right now" in message
    assert content_allows_model_switch(message)


def test_a_bad_key_still_says_so():
    assert "API key" in user_facing_llm_error("Error: 401 Unauthorized: invalid api key")


def test_a_refused_fallback_is_skipped_on_the_next_turn(monkeypatch):
    fp._REFUSED_UNTIL.clear()
    calls: list[str] = []

    class Provider:
        def __init__(self, model):
            self.model = model

        def get_default_model(self):
            return self.model

    def refused(model):
        return LLMResponse(content=user_facing_llm_error(BEDROCK_403), finish_reason="error", error_status_code=403)

    ok = LLMResponse(content="done", finish_reason="stop")

    async def chat(provider, kwargs):
        calls.append(kwargs["model"])
        return refused(kwargs["model"]) if kwargs["model"] in {"primary", "broken"} else ok

    presets = [SimpleNamespace(model="broken", max_tokens=100, temperature=0.1, reasoning_effort=None),
               SimpleNamespace(model="works", max_tokens=100, temperature=0.1, reasoning_effort=None)]

    async def scenario():
        for _ in range(2):
            provider = fp.FallbackProvider(primary=Provider("primary"), fallback_presets=presets,
                                           provider_factory=lambda preset: Provider(preset.model))
            await provider._try_with_fallback(chat, {"model": "primary", "messages": []}, None)

    monkeypatch.setattr(fp.FallbackProvider, "_announce_switch", lambda self, a, b: asyncio.sleep(0))
    asyncio.run(scenario())
    assert calls.count("broken") == 1  # refused once, then skipped
    assert calls.count("works") == 2
