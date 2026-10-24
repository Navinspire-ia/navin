# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pooled LLM connections: reuse within a turn, replay once on a stale socket.

A fresh TCP+TLS handshake costs 130-230 ms per model step, so cloud clients
keep sockets for a short window. A socket the network dropped meanwhile must
not surface as "Connection error": the request replays at once on a fresh
connection, but only while nothing has streamed and only once.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx
import openai
from openai.types.chat import ChatCompletion

from navin.agent.turn_timing import TurnTiming
from navin.providers.openai_compat_provider import OpenAICompatProvider


def _completion(text: str) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "vendor/model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": text},
        }],
    })


def _connection_error() -> openai.APIConnectionError:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.APIConnectionError(request=request)


class _Completions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Client:
    def __init__(self, completions: _Completions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()

    async def close(self) -> None:
        return None


class StaleSocketReplayTest(unittest.TestCase):
    def _provider(self, outcomes: list[object]) -> tuple[OpenAICompatProvider, _Completions, list[int]]:
        provider = OpenAICompatProvider(
            api_key="test",
            api_base="https://example.invalid/v1",
            default_model="vendor/model",
        )
        completions = _Completions(outcomes)
        builds: list[int] = []

        def build() -> None:
            builds.append(1)
            provider._client = _Client(completions)

        provider._build_client = build  # type: ignore[method-assign]
        return provider, completions, builds

    def test_connection_error_replays_once_on_fresh_client(self) -> None:
        provider, completions, builds = self._provider([_connection_error(), _completion("ok")])
        response = asyncio.run(
            provider.chat([{"role": "user", "content": "hi"}], model="vendor/model")
        )
        self.assertEqual(response.content, "ok")
        self.assertEqual(completions.calls, 2)
        # First client plus the fresh one built for the replay.
        self.assertEqual(len(builds), 2)

    def test_second_connection_error_is_reported_not_looped(self) -> None:
        provider, completions, _builds = self._provider(
            [_connection_error(), _connection_error(), _completion("never")]
        )
        response = asyncio.run(
            provider.chat([{"role": "user", "content": "hi"}], model="vendor/model")
        )
        self.assertEqual(response.finish_reason, "error")
        self.assertEqual(response.error_kind, "connection")
        self.assertEqual(completions.calls, 2)

    def test_no_replay_after_output_was_delivered(self) -> None:
        provider, _completions, _builds = self._provider([])
        replay = asyncio.run(provider._replay_on_fresh_socket(_connection_error(), delivered=True))
        self.assertFalse(replay)

    def test_client_is_built_off_the_event_loop(self) -> None:
        # Building loads the system certificate store (seconds on Windows);
        # every reset after a connection error used to do it on the loop.
        import threading

        provider, _completions, _builds = self._provider([])
        built_on: list[threading.Thread] = []
        build = provider._build_client

        def tracked() -> None:
            built_on.append(threading.current_thread())
            build()

        provider._build_client = tracked  # type: ignore[method-assign]

        async def run() -> threading.Thread:
            await provider._ensure_client()
            return threading.current_thread()

        loop_thread = asyncio.run(run())
        self.assertEqual(len(built_on), 1)
        self.assertIsNot(built_on[0], loop_thread)
        self.assertIsNotNone(provider._client)

    def test_cloud_pool_keeps_sockets_briefly(self) -> None:
        provider = OpenAICompatProvider(
            api_key="test",
            api_base="https://example.invalid/v1",
            default_model="vendor/model",
        )
        asyncio.run(provider._ensure_client())
        pool = provider._client._client._transport._pool
        self.assertGreater(pool._keepalive_expiry or 0, 0)
        self.assertGreater(pool._max_keepalive_connections, 0)

    def test_pool_can_serve_two_hundred_workers_and_the_parent(self) -> None:
        async def check() -> None:
            for base, proxy in (
                ("https://example.invalid/v1", None),
                ("http://127.0.0.1:8000/v1", None),
                ("https://example.invalid/v1", "http://127.0.0.1:9999"),
            ):
                with self.subTest(base=base, proxy=proxy):
                    with patch("navin.providers.openai_compat_provider.AsyncOpenAI", openai.AsyncOpenAI):
                        provider = OpenAICompatProvider(
                            api_key="test", api_base=base, default_model="vendor/model", proxy=proxy,
                        )
                        provider._build_client()
                    try:
                        transport = provider._client._client._transport
                        pool = transport._pool
                        self.assertGreaterEqual(pool._max_connections, 201)
                    finally:
                        await provider._client.close()
        asyncio.run(check())


class FirstTokenTimingTest(unittest.TestCase):
    def test_first_step_and_average_are_reported(self) -> None:
        timing = TurnTiming(session_key="s", model="m")
        timing.end_iteration()
        timing.add_first_token(0.4)
        timing.end_iteration()
        timing.add_first_token(None)  # a tool-call-only step streams nothing
        timing.end_iteration()
        timing.add_first_token(0.2)
        summary = timing.summary()
        self.assertIn("ttft_first=400ms", summary)
        self.assertIn("ttft_avg=300ms", summary)

    def test_first_step_without_stream_leaves_first_unset(self) -> None:
        timing = TurnTiming(session_key="s", model="m")
        timing.end_iteration()
        timing.add_first_token(None)
        timing.end_iteration()
        timing.add_first_token(0.5)
        summary = timing.summary()
        self.assertNotIn("ttft_first", summary)
        self.assertIn("ttft_avg=500ms", summary)


if __name__ == "__main__":
    unittest.main()
