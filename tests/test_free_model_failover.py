# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Automatic failover chain: the model list is the fallback list.

Free OpenRouter models regularly return 429 / at-capacity. Any other model
can block too: saturated pool, expired key, dropped slug, refusal. When no
explicit ``fallback_models`` are configured, the factory derives a chain from
the other configured text presets whose provider holds credentials, so the
step hops instead of the turn dying. The switch is announced, never silent.
"""

from __future__ import annotations

import unittest

from navin.config.schema import Config, InlineFallbackConfig, ModelPresetConfig
from navin.providers.factory import (
    _MAX_AUTO_FREE_FALLBACKS,
    _MAX_AUTO_PRESET_FALLBACKS,
    _resolve_fallback_presets,
)
from navin.providers.fallback_provider import FallbackProvider
from navin.providers.managed_catalog import FREE_VISION_MODEL


def _preset(model: str, provider: str = "openrouter") -> ModelPresetConfig:
    return ModelPresetConfig(model=model, provider=provider)


def _config_with_keys(*providers: str) -> Config:
    config = Config()
    for name in providers:
        getattr(config.providers, name).api_key = f"sk-{name}"
    return config


class AutoFreeFallbackTest(unittest.TestCase):
    def test_free_primary_gets_auto_chain_from_presets(self) -> None:
        config = Config()
        config.model_presets["ultra"] = _preset("nvidia/nemotron-3-ultra-550b-a55b:free")
        config.model_presets["super"] = _preset("nvidia/nemotron-3-super-120b-a12b:free")
        config.model_presets["laguna"] = _preset("poolside/laguna-s-2.1:free")
        primary = config.model_presets["ultra"]

        chain = _resolve_fallback_presets(config, primary)

        models = [preset.model for preset in chain]
        self.assertIn("nvidia/nemotron-3-super-120b-a12b:free", models)
        self.assertIn("poolside/laguna-s-2.1:free", models)
        self.assertNotIn(primary.model, models)

    def test_auto_chain_skips_vision_disabled_and_paid_models(self) -> None:
        config = Config()
        config.model_presets["ultra"] = _preset("nvidia/nemotron-3-ultra-550b-a55b:free")
        config.model_presets["omni"] = _preset(FREE_VISION_MODEL)
        disabled = _preset("openai/gpt-oss-20b:free")
        disabled.enabled = False
        config.model_presets["oss"] = disabled
        config.model_presets["paid"] = _preset("deepseek/deepseek-v4-flash")
        config.model_presets["super"] = _preset("nvidia/nemotron-3-super-120b-a12b:free")

        chain = _resolve_fallback_presets(config, config.model_presets["ultra"])

        models = [preset.model for preset in chain]
        self.assertEqual(models, ["nvidia/nemotron-3-super-120b-a12b:free"])

    def test_free_primary_without_presets_uses_bundled_catalog(self) -> None:
        config = Config()
        primary = _preset("openai/gpt-oss-20b:free", provider="navin")

        chain = _resolve_fallback_presets(config, primary)

        self.assertTrue(chain)
        self.assertLessEqual(len(chain), _MAX_AUTO_FREE_FALLBACKS)
        for preset in chain:
            self.assertTrue(preset.model.endswith(":free"))
            self.assertNotEqual(preset.model, FREE_VISION_MODEL)
            self.assertEqual(preset.provider, "navin")

    def test_byok_primary_continues_on_the_other_configured_models(self) -> None:
        # The operator's rule: a model that blocks hands the step to the next
        # model of the list, whoever holds the key. Paid presets come before
        # free ones, and the switch is announced by the provider wrapper.
        config = _config_with_keys("openrouter")
        config.model_presets["super"] = _preset("nvidia/nemotron-3-super-120b-a12b:free")
        config.model_presets["gemini"] = _preset(
            "google/gemini-3.7-flash", provider="openrouter"
        )
        primary = _preset("deepseek/deepseek-v4-flash", provider="openrouter")

        chain = _resolve_fallback_presets(config, primary)

        self.assertEqual(
            [preset.model for preset in chain],
            ["google/gemini-3.7-flash", "nvidia/nemotron-3-super-120b-a12b:free"],
        )

    def test_presets_whose_provider_has_no_credentials_are_skipped(self) -> None:
        # A fallback that can only ever answer 401 is not worth a call.
        config = _config_with_keys("openrouter")
        config.model_presets["gemini"] = _preset(
            "google/gemini-3.7-flash", provider="openrouter"
        )
        config.model_presets["mistral"] = _preset("mistral-large-latest", provider="mistral")
        config.model_presets["claude"] = _preset("claude-sonnet-5", provider="anthropic")
        primary = _preset("deepseek/deepseek-v4-flash", provider="openrouter")

        chain = _resolve_fallback_presets(config, primary)

        self.assertEqual([preset.model for preset in chain], ["google/gemini-3.7-flash"])

    def test_the_chain_starts_with_the_default_then_the_routed_picks(self) -> None:
        # The models the operator named (default preset, routes) are the most
        # trusted substitutes; the rest of the catalog follows.
        config = _config_with_keys("navin")
        for name, slug in (
            ("mimo", "xiaomi/mimo-v2.5"),
            ("glm", "z-ai/glm-5.3-flash"),
            ("flash", "deepseek/deepseek-v4-flash"),
            ("qwen", "qwen/qwen3.8-max"),
            ("grok", "x-ai/grok-4.6"),
            ("gemini", "google/gemini-3.7-flash"),
        ):
            config.model_presets[name] = _preset(slug, provider="navin")
        config.agents.defaults.model_preset = "glm"
        config.model_routes = {"fast": "flash", "plan": "glm", "deep": "qwen"}
        primary = config.model_presets["grok"]

        chain = _resolve_fallback_presets(config, primary)

        models = [preset.model for preset in chain]
        self.assertEqual(
            models[:3],
            ["z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash", "qwen/qwen3.8-max"],
        )
        self.assertNotIn(primary.model, models)
        self.assertLessEqual(len(chain), _MAX_AUTO_PRESET_FALLBACKS)

    def test_the_catalog_tail_alternates_vendors(self) -> None:
        # Three z-ai slugs in a row all fail together in a z-ai outage; the tail
        # interleaves vendors so the second call already lands elsewhere.
        config = _config_with_keys("navin")
        for name, slug in (
            ("glm-a", "z-ai/glm-5.3-flash"),
            ("glm-b", "z-ai/glm-5.2"),
            ("glm-c", "z-ai/glm-5.1"),
            ("flash", "deepseek/deepseek-v4-flash"),
            ("gemini", "google/gemini-3.7-flash"),
        ):
            config.model_presets[name] = _preset(slug, provider="navin")
        primary = _preset("x-ai/grok-4.6", provider="navin")

        models = [preset.model for preset in _resolve_fallback_presets(config, primary)]

        self.assertEqual(
            models,
            [
                "z-ai/glm-5.3-flash",
                "deepseek/deepseek-v4-flash",
                "google/gemini-3.7-flash",
                "z-ai/glm-5.2",
                "z-ai/glm-5.1",
            ],
        )

    def test_the_chain_keeps_the_primary_window_and_generation(self) -> None:
        # The snapshot's usable window is the minimum over the chain; a 32k
        # preset in the list must not shrink a 200k turn.
        config = _config_with_keys("navin")
        small = _preset("qwen/qwen3.8-max", provider="navin")
        small.context_window_tokens = 32_000
        small.max_tokens = 1024
        config.model_presets["small"] = small
        primary = _preset("deepseek/deepseek-v4-flash", provider="navin")
        primary.context_window_tokens = 200_000
        primary.max_tokens = 8192
        primary.temperature = 0.3

        (fallback,) = _resolve_fallback_presets(config, primary)

        self.assertEqual(fallback.context_window_tokens, 200_000)
        self.assertEqual(fallback.max_tokens, 8192)
        self.assertEqual(fallback.temperature, 0.3)
        self.assertIsNone(fallback.reasoning_effort)

    def test_non_text_and_disabled_presets_never_enter_the_chain(self) -> None:
        config = _config_with_keys("navin")
        config.model_presets["image"] = ModelPresetConfig(
            model="google/gemini-3.1-flash-image", provider="navin", modality="image"
        )
        config.model_presets["tts"] = ModelPresetConfig(
            model="x-ai/grok-voice-tts", provider="navin", modality="audio"
        )
        off = _preset("qwen/qwen3.8-max", provider="navin")
        off.enabled = False
        config.model_presets["off"] = off
        config.model_presets["gemini"] = _preset("google/gemini-3.7-flash", provider="navin")
        primary = _preset("deepseek/deepseek-v4-flash", provider="navin")

        models = [preset.model for preset in _resolve_fallback_presets(config, primary)]

        self.assertEqual(models, ["google/gemini-3.7-flash"])

    def test_managed_subscription_primary_gets_auto_chain(self) -> None:
        # DeepSeek V4 Flash on a Navin plan is billed, not ":free". A 429
        # still has to hop to the other catalog models on that same key.
        config = _config_with_keys("navin")
        config.model_presets["flash"] = _preset(
            "deepseek/deepseek-v4-flash", provider="navin"
        )
        config.model_presets["gemini"] = _preset(
            "google/gemini-3.7-flash", provider="navin"
        )
        config.model_presets["minimax"] = _preset(
            "minimax/minimax-m3", provider="navin"
        )
        config.model_presets["glm"] = _preset("z-ai/glm-5.2", provider="navin")
        primary = config.model_presets["flash"]

        chain = _resolve_fallback_presets(config, primary)

        models = [preset.model for preset in chain]
        self.assertIn("google/gemini-3.7-flash", models)
        self.assertIn("minimax/minimax-m3", models)
        self.assertNotIn(primary.model, models)
        self.assertLessEqual(len(chain), _MAX_AUTO_PRESET_FALLBACKS)
        for preset in chain:
            self.assertEqual(preset.provider, "navin")
            self.assertFalse(preset.model.endswith(":free"))

    def test_managed_primary_without_presets_uses_bundled_catalog(self) -> None:
        config = _config_with_keys("navin")
        primary = _preset("deepseek/deepseek-v4-flash", provider="navin")

        chain = _resolve_fallback_presets(config, primary)

        self.assertTrue(chain)
        self.assertLessEqual(len(chain), _MAX_AUTO_PRESET_FALLBACKS)
        self.assertNotIn(primary.model, [preset.model for preset in chain])
        for preset in chain:
            self.assertFalse(preset.model.endswith(":free"))
            self.assertEqual(preset.provider, "navin")

    def test_a_lone_model_has_no_chain(self) -> None:
        config = _config_with_keys("openrouter")
        primary = _preset("deepseek/deepseek-v4-flash", provider="openrouter")
        config.model_presets["only"] = primary

        self.assertEqual(_resolve_fallback_presets(config, primary), [])

    def test_explicit_fallback_models_take_priority(self) -> None:
        config = Config()
        config.model_presets["super"] = _preset("nvidia/nemotron-3-super-120b-a12b:free")
        config.agents.defaults.fallback_models = [
            InlineFallbackConfig(model="z-ai/glm-5.2", provider="openrouter"),
        ]
        primary = _preset("nvidia/nemotron-3-ultra-550b-a55b:free")

        chain = _resolve_fallback_presets(config, primary)

        self.assertEqual([preset.model for preset in chain], ["z-ai/glm-5.2"])

    def test_rate_limited_free_primary_fails_over(self) -> None:
        """End to end: a 429 on the free primary lands on the next free model."""
        import asyncio

        from navin.providers.base import LLMResponse

        class _StubProvider:
            def __init__(self, response: LLMResponse):
                self._response = response
                self.calls = 0

            def get_default_model(self) -> str:
                return "stub"

            async def chat(self, **kwargs: object) -> LLMResponse:
                self.calls += 1
                return self._response

        rate_limited = LLMResponse(
            content="Error: 429 rate limit exceeded",
            finish_reason="error",
            error_status_code=429,
        )
        ok = LLMResponse(content="hello", finish_reason="stop")
        primary = _StubProvider(rate_limited)
        fallback = _StubProvider(ok)

        provider = FallbackProvider(
            primary=primary,  # type: ignore[arg-type]
            fallback_presets=[_preset("nvidia/nemotron-3-super-120b-a12b:free")],
            provider_factory=lambda fb: fallback,  # type: ignore[arg-type,return-value]
            # The free chain hops immediately: rate limits are its normal state, so
            # waiting on the current model would slow every turn down. A model the
            # operator chose is retried instead (see test_fallback_policy).
            sticky_retries=0,
        )
        response = asyncio.run(
            provider.chat(model="nvidia/nemotron-3-ultra-550b-a55b:free", messages=[])
        )

        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.content, "hello")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)


if __name__ == "__main__":
    unittest.main()
