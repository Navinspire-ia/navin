# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Temperature handling for models that deprecated the parameter.

Anthropic's Claude 4.7/5 generation rejects ``temperature``: direct
endpoints answer 400 ("temperature is deprecated for this model") and
OpenRouter routing with ``require_parameters: true`` answers 404
("No endpoints found that can handle the requested parameters").
The provider must (a) not send temperature to known families and
(b) retry once without it when an unknown model rejects it at runtime.
"""

from __future__ import annotations

import unittest

from navin.providers.claude_capabilities import claude_supports_temperature
from navin.providers.openai_compat_provider import OpenAICompatProvider


class SupportsTemperatureTest(unittest.TestCase):
    def test_new_claude_generation_has_no_temperature(self) -> None:
        for model in (
            "anthropic/claude-sonnet-5",
            "anthropic/claude-sonnet-5:batch",
            "anthropic/claude-opus-4.7",
            "anthropic/claude-opus-4.8",
            "anthropic/claude-opus-4.8-fast",
            "anthropic/claude-opus-5",
            "anthropic/claude-opus-5-fast",
            "anthropic/claude-fable-5",
        ):
            with self.subTest(model=model):
                self.assertFalse(OpenAICompatProvider._supports_temperature(model))

    def test_future_or_unknown_claude_models_fail_safe(self) -> None:
        """Models that do not exist yet must not receive temperature."""
        for model in (
            "anthropic/claude-sonnet-6",
            "anthropic/claude-opus-7.2",
            "anthropic/claude-haiku-5",
            "anthropic/claude-fable-6",
            "anthropic/claude-nova-1",  # unknown family, unparseable
        ):
            with self.subTest(model=model):
                self.assertFalse(claude_supports_temperature(model))

    def test_older_claude_keeps_temperature(self) -> None:
        for model in (
            "anthropic/claude-sonnet-4.6",
            "claude-sonnet-4-5",
            "claude-haiku-4-5",
            "claude-3-5-sonnet-20241022",  # legacy naming, version first
            "us.anthropic.claude-sonnet-4-6-v1:0",  # Bedrock ID
        ):
            with self.subTest(model=model):
                self.assertTrue(claude_supports_temperature(model))

    def test_non_claude_models_are_untouched(self) -> None:
        self.assertTrue(claude_supports_temperature("z-ai/glm-5.2"))
        self.assertTrue(OpenAICompatProvider._supports_temperature("z-ai/glm-5.2"))

    def test_reasoning_effort_still_disables_temperature(self) -> None:
        self.assertFalse(
            OpenAICompatProvider._supports_temperature(
                "anthropic/claude-sonnet-4.6", "high"
            )
        )


class RuntimeRejectionRetryTest(unittest.TestCase):
    def _provider(self) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            api_key="test", api_base="https://example.invalid/v1",
            default_model="vendor/some-model",
        )

    def test_deprecated_temperature_error_arms_one_retry(self) -> None:
        provider = self._provider()
        exc = RuntimeError(
            '{"error":{"message":"`temperature` is deprecated for this model."}}'
        )
        self.assertTrue(
            provider._register_temperature_rejection("vendor/some-model", exc)
        )
        # Second failure for the same model must not loop.
        self.assertFalse(
            provider._register_temperature_rejection("vendor/some-model", exc)
        )

    def test_openrouter_no_endpoint_error_arms_retry(self) -> None:
        provider = self._provider()
        exc = RuntimeError(
            "Error: {'message': 'No endpoints found that can handle the "
            "requested parameters.', 'code': 404}"
        )
        self.assertTrue(provider._register_temperature_rejection(None, exc))
        self.assertIn("vendor/some-model", provider._no_temperature_models)

    def test_unrelated_error_does_not_arm_retry(self) -> None:
        provider = self._provider()
        exc = RuntimeError("rate limit exceeded (429)")
        self.assertFalse(provider._register_temperature_rejection("m", exc))

    def test_build_kwargs_drops_temperature_after_rejection(self) -> None:
        provider = self._provider()
        messages = [{"role": "user", "content": "hi"}]
        kwargs = provider._build_kwargs(
            messages, None, "vendor/some-model", 256, 0.7, None, None,
        )
        self.assertIn("temperature", kwargs)
        provider._no_temperature_models.add("vendor/some-model")
        kwargs = provider._build_kwargs(
            messages, None, "vendor/some-model", 256, 0.7, None, None,
        )
        self.assertNotIn("temperature", kwargs)


if __name__ == "__main__":
    unittest.main()
