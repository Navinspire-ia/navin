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
        primary = _preset("openai/gpt-oss-20b:free", provider="openrouter")

        chain = _resolve_fallback_presets(config, primary)

        self.assertTrue(chain)
        self.assertLessEqual(len(chain), _MAX_AUTO_FREE_FALLBACKS)
        for preset in chain:
            self.assertTrue(preset.model.endswith(":free"))
            self.assertNotEqual(preset.model, FREE_VISION_MODEL)
            self.assertEqual(preset.provider, "openrouter")

    def test_subscription_never_fails_over_to_openrouter_free_models(self) -> None:
        config = Config()
        primary = _preset("openai/gpt-oss-20b:free", provider="navin")

        self.assertEqual(_resolve_fallback_presets(config, primary), [])

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
        # Three slugs of one vendor in a row all fail together in an outage of
        # that vendor; the tail interleaves vendors so the second call already
        # lands elsewhere. These models are off the plan's ladder, which comes
        # first in the order the user set (RelayLadderTest).
        config = _config_with_keys("navin")
        for name, slug in (
            ("qwen-a", "qwen/qwen3.8-max"),
            ("qwen-b", "qwen/qwen3.7-max"),
            ("qwen-c", "qwen/qwen3.6-max"),
            ("flash", "deepseek/deepseek-v4-flash"),
            ("gemini", "google/gemini-3.7-flash"),
        ):
            config.model_presets[name] = _preset(slug, provider="navin")
        primary = _preset("x-ai/grok-4.6", provider="navin")

        models = [preset.model for preset in _resolve_fallback_presets(config, primary)]

        self.assertEqual(
            models,
            [
                "qwen/qwen3.8-max",
                "deepseek/deepseek-v4-flash",
                "google/gemini-3.7-flash",
                "qwen/qwen3.7-max",
                "qwen/qwen3.6-max",
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
        # The substitute runs at the effort of the turn: the wrapper keeps the
        # routed value when the preset names none. A preset default here ran
        # every substitute at "low", whatever effort the turn ran at.
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

    def test_managed_primary_without_presets_has_no_bundled_openrouter_chain(self) -> None:
        # The bundled catalog lists OpenRouter models; the subscription fails
        # over only across its own synced catalog.
        config = _config_with_keys("navin")
        primary = _preset("deepseek/deepseek-v4-flash", provider="navin")

        self.assertEqual(_resolve_fallback_presets(config, primary), [])

    def test_managed_chain_skips_openrouter_free_presets(self) -> None:
        config = _config_with_keys("navin")
        config.model_presets["glm"] = _preset("z-ai/glm-5.2", provider="navin")
        config.model_presets["super"] = _preset(
            "nvidia/nemotron-3-super-120b-a12b:free", provider="navin"
        )
        primary = _preset("deepseek/deepseek-v4-flash", provider="navin")

        chain = _resolve_fallback_presets(config, primary)

        self.assertEqual([preset.model for preset in chain], ["z-ai/glm-5.2"])

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


def _plan_config() -> Config:
    """The text models of the Navin plan, as its catalog lists them (2026-11)."""
    config = _config_with_keys("navin")
    for name, slug in (
        ("claude-sonnet-5-5", "anthropic/claude-sonnet-5.5"),
        ("claude-opus-5-5", "anthropic/claude-opus-5.5"),
        ("claude-opus-5", "anthropic/claude-opus-5"),
        ("claude-sonnet-5", "anthropic/claude-sonnet-5"),
        ("gpt-6-astra", "openai/gpt-6-astra"),
        ("gpt-6-1-sol", "openai/gpt-6.1-sol"),
        ("gpt-6-sol", "openai/gpt-6-sol"),
        ("gpt-6-luna", "openai/gpt-6-luna"),
        ("claude-haiku-5-5", "anthropic/claude-haiku-5.5"),
        ("gpt-5-6-sol", "openai/gpt-5.6-sol"),
        ("kimi-k3", "moonshotai/kimi-k3"),
        ("gpt-5-6-terra", "openai/gpt-5.6-terra"),
        ("claude-fable-5-1", "anthropic/claude-fable-5.1"),
        ("claude-fable-5", "anthropic/claude-fable-5"),
    ):
        config.model_presets[name] = _preset(slug, provider="navin")
    config.agents.defaults.model_preset = "claude-sonnet-5-5"
    config.model_routes = {"fast": "gpt-6-luna", "code": "gpt-5-6-terra", "plan": "gpt-6-1-sol"}
    return config


def _chain(config: Config, preset_name: str) -> list[tuple[str, str | None]]:
    chain = _resolve_fallback_presets(config, config.model_presets[preset_name])
    return [(preset.model, preset.reasoning_effort) for preset in chain]


#: The plan's ladder, in the order its models take over (the user's, 2026-11-06).
_LADDER = (
    "anthropic/claude-opus-5.5",
    "anthropic/claude-fable-5.1",
    "openai/gpt-6-astra",
    "anthropic/claude-sonnet-5.5",
    "openai/gpt-6.1-sol",
    "anthropic/claude-fable-5",
    "anthropic/claude-opus-5",
    "moonshotai/kimi-k3",
    "x-ai/grok-4.7",
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
)


def _ladder_effort(slug: str) -> str | None:
    """The newest Opus and Fable keep the turn's effort; the others take over at their deepest."""
    if slug in ("anthropic/claude-opus-5.5", "anthropic/claude-fable-5.1"):
        return None
    return "xhigh" if slug.startswith("openai/") else "max"


class RelayLadderTest(unittest.TestCase):
    """A model that cannot answer hands the step down the plan's ladder, in the user's order."""

    def test_every_model_hands_the_step_to_the_rest_of_the_ladder_in_order(self) -> None:
        config = _config_with_keys("navin")
        # Listed backwards, and with a model off the ladder: the order is the ladder's.
        for slug in (*reversed(_LADDER), "openai/gpt-6-luna"):
            config.model_presets[slug.split("/")[-1]] = _preset(slug, provider="navin")

        for primary in (*_LADDER, "openai/gpt-6-luna"):
            with self.subTest(primary=primary):
                self.assertEqual(
                    _chain(config, primary.split("/")[-1]),
                    [(slug, _ladder_effort(slug)) for slug in _LADDER if slug != primary],
                )

    def test_opus_hands_over_to_fable_then_astra_sonnet_sol_and_the_previous_flagships(self) -> None:
        chain = _chain(_plan_config(), "claude-opus-5-5")

        # The whole ladder, longer than the cap of the generic chain, and nothing else.
        self.assertEqual(
            chain,
            [
                ("anthropic/claude-fable-5.1", None),
                ("openai/gpt-6-astra", "xhigh"),
                ("anthropic/claude-sonnet-5.5", "max"),
                ("openai/gpt-6.1-sol", "xhigh"),
                ("anthropic/claude-fable-5", "max"),
                ("anthropic/claude-opus-5", "max"),
                ("moonshotai/kimi-k3", "max"),
            ],
        )
        self.assertGreater(len(chain), _MAX_AUTO_PRESET_FALLBACKS)

    def test_fable_hands_over_to_opus_the_same_way(self) -> None:
        chain = _chain(_plan_config(), "claude-fable-5-1")

        self.assertEqual(
            chain[:4],
            [
                ("anthropic/claude-opus-5.5", None),
                ("openai/gpt-6-astra", "xhigh"),
                ("anthropic/claude-sonnet-5.5", "max"),
                ("openai/gpt-6.1-sol", "xhigh"),
            ],
        )

    def test_gpt_astra_hands_over_to_the_claude_flagships_first(self) -> None:
        chain = _chain(_plan_config(), "gpt-6-astra")

        self.assertEqual(
            chain[:4],
            [
                ("anthropic/claude-opus-5.5", None),
                ("anthropic/claude-fable-5.1", None),
                ("anthropic/claude-sonnet-5.5", "max"),
                ("openai/gpt-6.1-sol", "xhigh"),
            ],
        )

    def test_a_newer_release_takes_the_place_of_its_family(self) -> None:
        config = _plan_config()
        config.model_presets["claude-opus-5-6"] = _preset("anthropic/claude-opus-5.6", provider="navin")

        chain = _chain(config, "claude-fable-5-1")

        self.assertEqual(chain[0], ("anthropic/claude-opus-5.6", None))
        # 5.5 becomes the release before it; 5 leaves the ladder.
        self.assertIn(("anthropic/claude-opus-5.5", "max"), chain)
        self.assertNotIn("anthropic/claude-opus-5", [model for model, _effort in chain])

    def test_a_flagship_without_credentials_gives_way_to_the_newest_usable_version(self) -> None:
        config = _plan_config()
        config.model_presets["claude-fable-5-1"] = _preset("claude-fable-5-1", provider="anthropic")

        models = [model for model, _effort in _chain(config, "claude-opus-5-5")]

        self.assertNotIn("claude-fable-5-1", models)
        self.assertEqual(models[:2], ["anthropic/claude-fable-5", "openai/gpt-6-astra"])

    def test_a_model_off_the_ladder_is_relayed_by_the_whole_ladder(self) -> None:
        self.assertEqual(
            _chain(_plan_config(), "gpt-6-luna"),
            [
                ("anthropic/claude-opus-5.5", None),
                ("anthropic/claude-fable-5.1", None),
                ("openai/gpt-6-astra", "xhigh"),
                ("anthropic/claude-sonnet-5.5", "max"),
                ("openai/gpt-6.1-sol", "xhigh"),
                ("anthropic/claude-fable-5", "max"),
                ("anthropic/claude-opus-5", "max"),
                ("moonshotai/kimi-k3", "max"),
            ],
        )

    def test_the_ladder_never_leaves_the_managed_provider(self) -> None:
        config = _plan_config()
        config.providers.openrouter.api_key = "sk-openrouter"
        # A model of the ladder the plan lacks, on the user's own key.
        config.model_presets["grok-openrouter"] = _preset("x-ai/grok-4.7", provider="openrouter")

        chain = _resolve_fallback_presets(config, config.model_presets["claude-opus-5-5"])

        self.assertEqual({preset.provider for preset in chain}, {"navin"})
        self.assertNotIn("x-ai/grok-4.7", [preset.model for preset in chain])

    def test_outside_the_managed_providers_the_generic_chain_applies(self) -> None:
        config = _config_with_keys("openrouter")
        for name, slug in (
            ("opus", "anthropic/claude-opus-5.5"),
            ("fable", "anthropic/claude-fable-5.1"),
            ("astra", "openai/gpt-6-astra"),
        ):
            config.model_presets[name] = _preset(slug, provider="openrouter")
        config.agents.defaults.model_preset = "astra"

        # The default preset first, at the turn's effort: not the plan's ladder.
        self.assertEqual(
            _chain(config, "opus"),
            [("openai/gpt-6-astra", None), ("anthropic/claude-fable-5.1", None)],
        )

    def test_bedrock_ids_and_kimi_versions_are_read_as_their_family(self) -> None:
        from navin.providers.relay_ladder import model_family, relay_candidates

        self.assertEqual(model_family("us.anthropic.claude-opus-5-5-v1:0"), ("claude-opus", (5, 5)))
        self.assertEqual(model_family("moonshotai/kimi-k3"), ("kimi", (3,)))
        self.assertEqual(model_family("z-ai/glm-5.3-flash"), ("glm-flash", (5, 3)))
        candidates = relay_candidates(
            "us.anthropic.claude-fable-5-1-v1:0",
            [("opus", "us.anthropic.claude-opus-5-5-v1:0"), ("fable", "us.anthropic.claude-fable-5-1-v1:0")],
        )
        self.assertEqual(
            [(candidate.model, candidate.reasoning_effort) for candidate in candidates],
            [("us.anthropic.claude-opus-5-5-v1:0", None)],
        )

    def test_the_relay_runs_at_the_turns_effort_or_at_its_deepest_one(self) -> None:
        import asyncio
        from unittest import mock

        from navin.providers.base import LLMResponse

        class _Recorder:
            def __init__(self, response: LLMResponse):
                self.response = response
                self.efforts: list[str | None] = []

            def get_default_model(self) -> str:
                return "stub"

            async def chat(self, **kwargs: object) -> LLMResponse:
                self.efforts.append(kwargs.get("reasoning_effort"))
                return self.response

        spent = LLMResponse(content="quota used up", finish_reason="error", error_status_code=402)
        config = _plan_config()
        chain = _resolve_fallback_presets(config, config.model_presets["claude-opus-5-5"])
        relays = {
            "anthropic/claude-fable-5.1": _Recorder(spent),
            "openai/gpt-6-astra": _Recorder(spent),
            "anthropic/claude-sonnet-5.5": _Recorder(LLMResponse(content="done")),
        }
        provider = FallbackProvider(
            primary=_Recorder(spent),
            fallback_presets=chain,
            provider_factory=lambda preset: relays[preset.model],
        )

        # The refusals noted here must not leak into the other tests.
        with mock.patch.dict("navin.providers.fallback_provider._REFUSED_UNTIL", clear=True):
            response = asyncio.run(
                provider.chat(model="anthropic/claude-opus-5.5", messages=[], reasoning_effort="medium")
            )

        self.assertEqual(response.content, "done")
        # The peer keeps the turn's effort; below it, the deepest effort of each model.
        self.assertEqual(relays["anthropic/claude-fable-5.1"].efforts, ["medium"])
        self.assertEqual(relays["openai/gpt-6-astra"].efforts, ["xhigh"])
        self.assertEqual(relays["anthropic/claude-sonnet-5.5"].efforts, ["max"])

    def test_a_model_at_its_rate_limit_hands_the_step_over_at_once_on_the_ladder(self) -> None:
        import asyncio
        from unittest import mock

        from navin.providers.base import LLMResponse
        from navin.providers.factory import make_provider
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES

        class _Recorder:
            def __init__(self, response: LLMResponse):
                self.response = response
                self.calls = 0

            def get_default_model(self) -> str:
                return "stub"

            async def chat(self, **kwargs: object) -> LLMResponse:
                self.calls += 1
                return self.response

        def run(config: Config, preset_name: str) -> tuple[_Recorder, _Recorder, LLMResponse]:
            limited = _Recorder(
                LLMResponse(content="Error: 429 Too many requests", finish_reason="error", error_status_code=429)
            )
            relay = _Recorder(LLMResponse(content="done"))

            def core(*args: object, preset: object = None, **kwargs: object) -> _Recorder:
                return limited if preset is None else relay

            with (
                mock.patch("navin.providers.factory._make_provider_core", side_effect=core),
                mock.patch("navin.providers.fallback_policy.retry_delay", return_value=0.0),
                mock.patch.dict("navin.providers.fallback_provider._REFUSED_UNTIL", clear=True),
            ):
                provider = make_provider(config, preset_name=preset_name)
                model = config.model_presets[preset_name].model
                response = asyncio.run(provider.chat(model=model, messages=[]))
            return limited, relay, response

        # On the plan: Fable has its own quota, Opus is not asked again.
        limited, relay, response = run(_plan_config(), "claude-opus-5-5")
        self.assertEqual(response.content, "done")
        self.assertEqual((limited.calls, relay.calls), (1, 1))

        # Elsewhere the chosen model keeps its short retries before the switch.
        config = _config_with_keys("openrouter")
        config.model_presets["opus"] = _preset("anthropic/claude-opus-5.5")
        config.model_presets["fable"] = _preset("anthropic/claude-fable-5.1")
        limited, relay, response = run(config, "opus")
        self.assertEqual(response.content, "done")
        self.assertEqual((limited.calls, relay.calls), (1 + PRIMARY_STICKY_RETRIES, 1))

    def test_a_configured_list_never_names_the_chosen_model_as_its_own_relay(self) -> None:
        config = _plan_config()
        config.agents.defaults.fallback_models = ["claude-opus-5-5", "claude-fable-5-1"]

        models = [model for model, _effort in _chain(config, "claude-opus-5-5")]

        self.assertEqual(models, ["anthropic/claude-fable-5.1"])


if __name__ == "__main__":
    unittest.main()
