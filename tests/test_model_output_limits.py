# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-model output ceilings: known limits, provider refusals, context overflow."""

from __future__ import annotations

import unittest

from navin.config.schema import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MAX_TOKENS,
    AgentDefaults,
    ModelPresetConfig,
)
from navin.providers.model_limits import known_model_limits
from navin.providers.openai_compat_provider import OpenAICompatProvider


class _ProviderError(Exception):
    pass


def _bare_provider() -> OpenAICompatProvider:
    provider = OpenAICompatProvider.__new__(OpenAICompatProvider)
    provider._max_output_caps = {}
    return provider


class KnownModelLimitsTest(unittest.TestCase):
    def test_large_families(self) -> None:
        for slug in (
            "anthropic/claude-sonnet-5.5",
            "claude-opus-5-5",
            "anthropic/claude-opus-5",
            "us.anthropic.claude-opus-4-6-v1",
            "claude-haiku-5-5",
            "claude-fable-5-1",
            "openai/gpt-6-luna",
            "gpt-6.1-sol",
            "moonshotai/kimi-k3",
        ):
            limits = known_model_limits(slug)
            self.assertIsNotNone(limits, slug)
            self.assertEqual(limits.max_output_tokens, 128_000, slug)
            self.assertEqual(limits.context_window_tokens, 1_000_000, slug)

    def test_unknown_or_smaller_models(self) -> None:
        for slug in (
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-sonnet-4-20250514",
            "gpt-5.6-terra",
            "moonshotai/kimi-k2.6",
            "z-ai/glm-5.3-flash",
            "",
            None,
        ):
            self.assertIsNone(known_model_limits(slug), slug)


class EffectivePresetLimitsTest(unittest.TestCase):
    def test_default_follows_the_model(self) -> None:
        preset = ModelPresetConfig(model="moonshotai/kimi-k3")
        self.assertEqual(preset.effective_max_tokens, 128_000)
        self.assertEqual(preset.effective_context_window_tokens, 1_000_000)
        self.assertEqual(preset.to_generation_settings().max_tokens, 128_000)

    def test_unknown_model_keeps_generic_defaults(self) -> None:
        preset = ModelPresetConfig(model="z-ai/glm-5.3-flash")
        self.assertEqual(preset.effective_max_tokens, DEFAULT_MAX_TOKENS)
        self.assertEqual(preset.effective_context_window_tokens, DEFAULT_CONTEXT_WINDOW_TOKENS)

    def test_user_values_win(self) -> None:
        preset = ModelPresetConfig(
            model="claude-opus-5-5", max_tokens=4_000, context_window_tokens=150_000
        )
        self.assertEqual(preset.effective_max_tokens, 4_000)
        self.assertEqual(preset.effective_context_window_tokens, 150_000)

    def test_agent_defaults_window(self) -> None:
        self.assertEqual(
            AgentDefaults(model="claude-opus-5-5").effective_context_window_tokens, 1_000_000
        )


class MaxTokensRejectionTest(unittest.TestCase):
    def _learn(self, message: str, requested: int = 128_000) -> int | None:
        provider = _bare_provider()
        provider._register_max_tokens_rejection("m", _ProviderError(message), requested)
        return provider._max_output_caps.get("m")

    def test_learns_the_stated_limit(self) -> None:
        self.assertEqual(
            self._learn(
                "max_tokens is too large: 128000. This model supports at most "
                "16384 completion tokens"
            ),
            16_384,
        )
        self.assertEqual(
            self._learn("max_tokens: 128000 > 8192, which is the maximum allowed"), 8_192
        )

    def test_bedrock_minimum_is_not_a_ceiling(self) -> None:
        self.assertIsNone(
            self._learn(
                "Invalid max_output_tokens: integer below minimum value. "
                "Expected a value >= 16."
            )
        )

    def test_context_overflow_is_not_learned_as_a_ceiling(self) -> None:
        self.assertIsNone(
            self._learn(
                "input length and `max_tokens` exceed context limit: "
                "188000 + 128000 > 200000"
            )
        )


class ContextOverflowTest(unittest.TestCase):
    def _room(self, message: str, requested: int = 128_000) -> int | None:
        return OpenAICompatProvider._context_overflow_max_tokens(
            _ProviderError(message), requested
        )

    def test_summed_shape(self) -> None:
        self.assertEqual(
            self._room(
                "input length and `max_tokens` exceed context limit: "
                "100000 + 128000 > 200000"
            ),
            200_000 - 100_000 - 1_024,
        )

    def test_openai_shape(self) -> None:
        self.assertEqual(
            self._room(
                "This model's maximum context length is 131072 tokens. However, you "
                "requested 140000 tokens (12000 in the messages, 128000 in the "
                "completion)."
            ),
            131_072 - 12_000 - 1_024,
        )

    def test_requested_only_shape(self) -> None:
        self.assertEqual(
            self._room(
                "This model's maximum context length is 131072 tokens. However, you "
                "requested 140000 tokens."
            ),
            131_072 - (140_000 - 128_000) - 1_024,
        )

    def test_no_room_or_no_numbers(self) -> None:
        self.assertIsNone(
            self._room("input length and `max_tokens` exceed context limit: 199500 + 128000 > 200000")
        )
        self.assertIsNone(self._room("Input is too long for the context window."))
        self.assertIsNone(self._room("rate limited"))


if __name__ == "__main__":
    unittest.main()
