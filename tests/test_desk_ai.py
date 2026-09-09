# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The shared desk model call: budget bump for reasoning models, preset fallback."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from navin import desk_ai


class _Response:
    def __init__(self, content: str, finish: str = "stop", reasoning: int = 0) -> None:
        self.content = content
        self.finish_reason = finish
        self.reasoning_content = "..." if reasoning else None
        self.usage = {"reasoning_tokens": reasoning}


class DeskAiAskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.answers: dict[str, list[tuple[str, bool]]] = {}

        async def fake_chat(preset: str, system: str, user: str, max_tokens: int, temperature: float, timeout_s: float) -> tuple[str, bool]:
            self.calls.append((preset, max_tokens))
            queue = self.answers.get(preset) or [("", False)]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        patches = [
            mock.patch.object(desk_ai, "_chat", fake_chat),
            mock.patch.object(desk_ai, "route_presets", lambda role, routes=None: ["deep-preset", "fast-preset"]),
            mock.patch.object(desk_ai, "preset_model", lambda preset, config=None: f"model/{preset}"),
            mock.patch.object(desk_ai, "_THINKING_PRESETS", set()),
            mock.patch.dict("os.environ", {"NAVIN_DESK_AI": "1"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_plain_answer_uses_the_role_preset_once(self) -> None:
        self.answers["deep-preset"] = [("```json\n{\"ok\": true}\n```", False)]
        text, model = desk_ai.ask("deep", "sys", "user", max_tokens=700)
        self.assertEqual(text, '{"ok": true}')
        self.assertEqual(model, "model/deep-preset")
        self.assertEqual(self.calls, [("deep-preset", 700)])

    def test_starved_reasoning_model_gets_a_bigger_budget(self) -> None:
        self.answers["deep-preset"] = [("", True), ("answer", False)]
        text, model = desk_ai.ask("deep", "sys", "user", max_tokens=700)
        self.assertEqual(text, "answer")
        self.assertEqual(model, "model/deep-preset")
        self.assertEqual(self.calls, [("deep-preset", 700), ("deep-preset", 2800)])

    def test_a_thinking_preset_is_remembered_for_the_next_call(self) -> None:
        self.answers["deep-preset"] = [("", True), ("first", False), ("second", False)]
        desk_ai.ask("deep", "sys", "user", max_tokens=700)
        self.calls.clear()
        text, _model = desk_ai.ask("deep", "sys", "user", max_tokens=900)
        self.assertEqual(text, "second")
        self.assertEqual(self.calls, [("deep-preset", 3600)])

    def test_silent_preset_hands_over_to_the_next_route(self) -> None:
        self.answers["deep-preset"] = [("", False)]
        self.answers["fast-preset"] = [("from fast", False)]
        text, model = desk_ai.ask("deep", "sys", "user", max_tokens=700)
        self.assertEqual((text, model), ("from fast", "model/fast-preset"))
        self.assertEqual(self.calls, [("deep-preset", 700), ("fast-preset", 700)])

    def test_still_starved_after_the_bump_falls_back_then_gives_up(self) -> None:
        self.answers["deep-preset"] = [("", True), ("", True), ("", True)]
        self.answers["fast-preset"] = [("", False)]
        self.assertEqual(desk_ai.ask("deep", "sys", "user", max_tokens=700), ("", ""))
        self.assertEqual(self.calls, [("deep-preset", 700), ("deep-preset", 2800), ("fast-preset", 700)])

    def test_provider_error_moves_on_without_raising(self) -> None:
        async def boom(*_args: Any, **_kwargs: Any) -> tuple[str, bool]:
            raise RuntimeError("provider down")

        with mock.patch.object(desk_ai, "_chat", boom):
            self.assertEqual(desk_ai.ask("deep", "sys", "user"), ("", ""))

    def test_kill_switch_never_calls_a_model(self) -> None:
        with mock.patch.dict("os.environ", {"NAVIN_DESK_AI": "off"}):
            self.assertEqual(desk_ai.ask("deep", "sys", "user"), ("", ""))
        self.assertEqual(self.calls, [])


class ChatStarvationTests(unittest.TestCase):
    def _chat(self, response: _Response) -> tuple[str, bool]:
        class _Provider:
            async def chat_with_retry(self, *_args: Any, **_kwargs: Any) -> _Response:
                return response

        class _Snapshot:
            provider = _Provider()
            model = "m"

        with mock.patch("navin.providers.factory.load_provider_snapshot", lambda **_kw: _Snapshot()):
            return desk_ai._run(desk_ai._chat("p", "s", "u", 100, 0.2, 5.0))

    def test_reasoning_budget_exhausted_is_reported_as_starved(self) -> None:
        self.assertEqual(self._chat(_Response("", finish="length", reasoning=100)), ("", True))

    def test_empty_answer_without_reasoning_is_not_starved(self) -> None:
        self.assertEqual(self._chat(_Response("", finish="stop")), ("", False))

    def test_answer_text_passes_through(self) -> None:
        self.assertEqual(self._chat(_Response("hello", finish="stop", reasoning=12)), ("hello", False))


class BudgetTests(unittest.TestCase):
    def test_bump_has_a_floor_and_a_ceiling(self) -> None:
        self.assertEqual(desk_ai._starved_budget(100), 2400)
        self.assertEqual(desk_ai._starved_budget(900), 3600)
        self.assertEqual(desk_ai._starved_budget(5000), 6000)


if __name__ == "__main__":
    unittest.main()
