# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A 402 is labelled by the key that paid for the call, and quotes the provider.

Plan quota only when the Navin managed key ran the turn; a refusal on the
user's own key names the provider and repeats its message.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from navin.agent.runner import (
    _MANAGED_QUOTA_ERROR_MESSAGE,
    _PROVIDER_CREDIT_ERROR_MESSAGE,
    _PROVIDER_CREDIT_HINT,
    _arrearage_error_message,
)
from navin.providers.base import LLMResponse
from navin.providers.fallback_provider import FallbackProvider
from navin.providers.user_facing_errors import provider_error_detail
from navin.usage_mode import provider_api_key, runtime_uses_managed_key


def _config(managed_key: str = "navin-managed-key") -> SimpleNamespace:
    return SimpleNamespace(
        license=SimpleNamespace(managed_api_key=managed_key),
    )


def _runtime(api_key: str, model: str = "z-ai/glm-5.3-flash") -> SimpleNamespace:
    return SimpleNamespace(provider=SimpleNamespace(api_key=api_key), model=model)


def _wrapped_runtime(api_key: str, model: str = "z-ai/glm-5.3-flash") -> SimpleNamespace:
    """A managed preset always runs behind the automatic failover chain."""
    primary = SimpleNamespace(api_key=api_key, generation=None)
    chain = FallbackProvider(
        primary=primary,  # type: ignore[arg-type]
        fallback_presets=[SimpleNamespace(model="google/gemini-3.7-flash")],
        provider_factory=lambda preset: primary,  # type: ignore[arg-type, return-value]
    )
    return SimpleNamespace(provider=chain, model=model)


class RuntimeUsesManagedKeyTest(unittest.TestCase):
    def test_byok_key_is_not_the_managed_key(self) -> None:
        self.assertFalse(
            runtime_uses_managed_key(
                _runtime("sk-user-openrouter"),
                _config(),
            )
        )

    def test_same_key_as_license_is_managed(self) -> None:
        self.assertTrue(
            runtime_uses_managed_key(
                _runtime("navin-managed-key"),
                _config(),
            )
        )

    def test_missing_runtime_key_is_not_assumed_managed(self) -> None:
        self.assertFalse(
            runtime_uses_managed_key(
                _runtime(""),
                _config(),
            )
        )

    def test_failover_chain_shows_the_key_of_the_chosen_model(self) -> None:
        # The wrapper used to hide the key, so every managed 402 read as
        # "your own API key is out of credit".
        self.assertTrue(
            runtime_uses_managed_key(_wrapped_runtime("navin-managed-key"), _config())
        )
        self.assertFalse(
            runtime_uses_managed_key(_wrapped_runtime("sk-user-openrouter"), _config())
        )

    def test_provider_api_key_unwraps_legacy_wrappers(self) -> None:
        inner = SimpleNamespace(api_key="inner-key")
        legacy = SimpleNamespace(_primary=inner)
        self.assertEqual(provider_api_key(legacy), "inner-key")
        self.assertIsNone(provider_api_key(SimpleNamespace()))
        self.assertIsNone(provider_api_key(None))


class ArrearageMessageTest(unittest.TestCase):
    def test_byok_turn_names_the_provider_and_quotes_it(self) -> None:
        response = LLMResponse(
            content="Your own API key is out of credit.",
            finish_reason="error",
            error_status_code=402,
            error_detail="Insufficient balance or no resource package. Please recharge.",
            error_provider="Z.AI",
        )
        with mock.patch(
            "navin.config.loader.load_config",
            return_value=_config(),
        ):
            text = _arrearage_error_message(_runtime("sk-user-zai", "glm-4.5"), response)
        lines = text.split("\n")
        self.assertEqual(lines[0], "__NAVIN_PROVIDER_CREDIT__")
        self.assertEqual(lines[1], "Z.AI refused the call for glm-4.5: the key is out of credit.")
        self.assertEqual(
            lines[2],
            "Provider message: Insufficient balance or no resource package. Please recharge.",
        )
        self.assertEqual(lines[3], _PROVIDER_CREDIT_HINT)
        self.assertNotIn("__NAVIN_QUOTA_LIMIT__", text)

    def test_byok_turn_without_detail_stays_generic(self) -> None:
        response = LLMResponse(content="Error: 402", finish_reason="error", error_status_code=402)
        with mock.patch(
            "navin.config.loader.load_config",
            return_value=_config(),
        ):
            text = _arrearage_error_message(_runtime("sk-user-openrouter", ""), response)
        self.assertEqual(
            text,
            "__NAVIN_PROVIDER_CREDIT__\n"
            "The provider refused the call: the key is out of credit.\n"
            f"{_PROVIDER_CREDIT_HINT}",
        )
        self.assertTrue(_PROVIDER_CREDIT_ERROR_MESSAGE.startswith("__NAVIN_PROVIDER_CREDIT__"))

    def test_byok_detail_falls_back_to_the_raw_content(self) -> None:
        # Anthropic keeps the raw body in content; the detail comes from there.
        response = LLMResponse(
            content=(
                "Error: {'type': 'error', 'error': {'type': 'invalid_request_error', "
                "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
            ),
            finish_reason="error",
            error_status_code=400,
            error_provider="Anthropic",
        )
        with mock.patch(
            "navin.config.loader.load_config",
            return_value=_config(),
        ):
            text = _arrearage_error_message(_runtime("sk-ant-user", "claude"), response)
        self.assertIn(
            "Provider message: Your credit balance is too low to access the Anthropic API.",
            text,
        )

    def test_managed_turn_gets_plan_copy(self) -> None:
        with (
            mock.patch(
                "navin.config.loader.load_config",
                return_value=_config(),
            ),
            mock.patch("navin.license_sync.request_immediate_sync"),
        ):
            text = _arrearage_error_message(_runtime("navin-managed-key"))
        self.assertEqual(text, _MANAGED_QUOTA_ERROR_MESSAGE)
        self.assertTrue(text.startswith("__NAVIN_QUOTA_LIMIT__"))

    def test_managed_turn_behind_failover_chain_gets_plan_copy(self) -> None:
        response = LLMResponse(
            content="Your own API key is out of credit.",
            finish_reason="error",
            error_status_code=402,
            error_detail="Prompt tokens limit exceeded: 78466 > 58882.",
        )
        with (
            mock.patch(
                "navin.config.loader.load_config",
                return_value=_config(),
            ),
            mock.patch("navin.license_sync.request_immediate_sync") as resync,
        ):
            text = _arrearage_error_message(_wrapped_runtime("navin-managed-key"), response)
        self.assertEqual(text, _MANAGED_QUOTA_ERROR_MESSAGE)
        self.assertNotIn("Prompt tokens limit", text)
        resync.assert_called_once()


class ProviderErrorDetailTest(unittest.TestCase):
    def test_openrouter_body_loses_the_key_link(self) -> None:
        raw = (
            "{'message': \"Prompt tokens limit exceeded: 78466 > 58882. To increase, visit "
            "https://openrouter.ai/workspaces/default/keys/489e07060afac822 and adjust the "
            "key's monthly limit\", 'code': 402, 'metadata': {'provider_name': None}}"
        )
        self.assertEqual(
            provider_error_detail(raw), "Prompt tokens limit exceeded: 78466 > 58882."
        )

    def test_openai_sdk_body_keeps_the_billing_sentence(self) -> None:
        raw = (
            "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
            "please check your plan and billing details. For more information on this "
            "error, read the docs: https://platform.openai.com/docs/guides/error-codes.', "
            "'type': 'insufficient_quota', 'param': None, 'code': 'insufficient_quota'}}"
        )
        self.assertEqual(
            provider_error_detail(raw),
            "You exceeded your current quota, please check your plan and billing details.",
        )

    def test_plain_text_drops_status_prefixes(self) -> None:
        self.assertEqual(
            provider_error_detail("Error code: 402 - Insufficient balance. Please recharge."),
            "Insufficient balance. Please recharge.",
        )
        self.assertEqual(
            provider_error_detail("Error calling LLM: payment required"),
            "payment required",
        )

    def test_our_own_sentinels_are_not_provider_words(self) -> None:
        self.assertIsNone(
            provider_error_detail(
                "Your own API key is out of credit. Top up at the provider, "
                "or switch to a Navin plan model."
            )
        )
        self.assertIsNone(provider_error_detail(""))
        self.assertIsNone(provider_error_detail("{'code': 402}"))

    def test_single_sentence_with_link_keeps_the_words(self) -> None:
        self.assertEqual(
            provider_error_detail("Top up at https://example.com/billing to continue"),
            "Top up at to continue",
        )

    def test_long_messages_are_capped(self) -> None:
        detail = provider_error_detail("x" * 1000)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertLessEqual(len(detail), 280)
        self.assertTrue(detail.endswith("..."))


if __name__ == "__main__":
    unittest.main()
