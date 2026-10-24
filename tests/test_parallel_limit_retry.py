# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A key's parallel-request cap is retried in seconds, not after the gateway's 60 s."""

from __future__ import annotations

import asyncio

from navin.providers.base import LLMProvider, LLMResponse, _is_parallel_limit


def test_the_litellm_parallel_cap_is_recognised() -> None:
    body = (
        "Rate limit exceeded for api_key: 2677. Limit type: max_parallel_requests. "
        "Current limit: 10, Remaining: 0."
    )
    assert _is_parallel_limit(body)
    assert not _is_parallel_limit("Rate limit exceeded: 50 requests per minute")


class _Busy(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get_default_model(self) -> str:
        return "m"

    async def chat(self, **kwargs):  # noqa: ANN003 - test double
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="Error: rate limited",
                finish_reason="error",
                error_status_code=429,
                error_detail="Limit type: max_parallel_requests. Current limit: 10, Remaining: 0.",
                error_retry_after_s=60.0,
            )
        return LLMResponse(content="ok", finish_reason="stop")


def test_the_retry_waits_seconds_not_a_minute(monkeypatch) -> None:
    waits: list[float] = []

    async def fake_sleep(self, delay, **kwargs):  # noqa: ANN001 - test double
        waits.append(delay)

    monkeypatch.setattr(LLMProvider, "_sleep_with_heartbeat", fake_sleep)
    provider = _Busy()
    result = asyncio.run(provider.chat_with_retry(messages=[{"role": "user", "content": "hi"}]))
    assert result.content == "ok"
    assert waits and max(waits) <= 10.0
