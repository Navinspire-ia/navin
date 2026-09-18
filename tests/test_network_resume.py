# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Connection loss during streaming and a longer offline interval both resume."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from navin.providers.base import LLMProvider, LLMResponse
from navin.session.turn_recovery import RECOVERY_KEY
from tests.test_turn_recovery import (
    LocalProvider,
    instant_retry,
    isolated_config,  # noqa: F401 - keep machine skills out of the recovery fixture
    make_loop,
    request,
)


class InterruptedStream(LLMProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error
        self.calls = 0

    def get_default_model(self):
        return "test-network"

    async def chat(self, **kwargs):
        raise AssertionError("Use streaming")

    async def chat_stream(self, **kwargs):
        self.calls += 1
        await kwargs["on_content_delta"]("Partial" if self.calls == 1 else "Completed")
        return self.error if self.calls == 1 else LLMResponse(content="Completed")


@pytest.mark.parametrize("error", [
    LLMResponse(content="offline", finish_reason="error", error_kind="connection"),
    LLMResponse(content="upstream unavailable", finish_reason="error", error_status_code=503),
])
def test_interrupted_stream_retries_in_a_new_segment(monkeypatch, error):
    async def run():
        provider = InterruptedStream(error)
        monkeypatch.setattr(provider, "_sleep_with_heartbeat", AsyncMock())
        reset = AsyncMock()
        deltas = AsyncMock()
        response = await provider.chat_stream_with_retry(
            [{"role": "user", "content": "Finish"}],
            on_content_delta=deltas, on_stream_recover=reset,
        )
        assert response.content == "Completed"
        assert provider.calls == 2
        reset.assert_awaited_once()
    asyncio.run(run())


def test_invalid_credentials_still_stop_after_partial_output(monkeypatch):
    async def run():
        provider = InterruptedStream(LLMResponse(content="Invalid key", finish_reason="error", error_status_code=401))
        reset = AsyncMock()
        response = await provider.chat_stream_with_retry(
            [{"role": "user", "content": "Finish"}],
            on_content_delta=AsyncMock(), on_stream_recover=reset,
        )
        assert response.finish_reason == "error"
        assert provider.calls == 1
        reset.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize("channel", ["websocket", "cli"])
def test_interactive_work_survives_repeated_offline_recovery_attempts(tmp_path, monkeypatch, channel):
    instant_retry(monkeypatch)

    async def run():
        provider = LocalProvider(
            *(LLMResponse(content="offline", finish_reason="error", error_kind="connection") for _ in range(5)),
            LLMResponse(content="Work completed after reconnection."),
        )
        loop = make_loop(tmp_path, provider)
        message = request(channel=channel)
        task = asyncio.create_task(loop.run(recovery_channel=channel))
        try:
            await loop.bus.publish_inbound(message)
            async def completed():
                while True:
                    session = loop.sessions.get_or_create(message.session_key)
                    if len(provider.requests) == 6 and RECOVERY_KEY not in session.metadata:
                        return session
                    await asyncio.sleep(0.02)
            session = await asyncio.wait_for(completed(), 20)
            assert sum(m["role"] == "user" for m in session.messages) == 1
            assert any(m.get("content") == "Work completed after reconnection." for m in session.messages)
        finally:
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.close_mcp()
    asyncio.run(run())
