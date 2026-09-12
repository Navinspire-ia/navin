# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Soft budget modes must clamp managed-key tiers without a restart."""

from __future__ import annotations

import unittest
from unittest import mock

from navin.config.schema import Config, ModelPresetConfig
from navin.usage_mode import (
    apply_usage_from_response,
    apply_usage_mode_to_runtime,
    apply_usage_summary,
    budget_used_percent,
    clamp_preset_for_mode,
    clear_live_usage_mode,
    current_usage_mode,
    effective_mode_from_usage,
    is_expensive_flagship_slug,
    max_tokens_for_mode,
    slug_allowed_for_budget,
    usage_mode_from_ratio,
)


class UsageModeMathTest(unittest.TestCase):
    def test_thresholds_match_site(self):
        self.assertEqual(usage_mode_from_ratio(0, 100), "normal")
        self.assertEqual(usage_mode_from_ratio(79, 100), "normal")
        self.assertEqual(usage_mode_from_ratio(80, 100), "reduced")
        self.assertEqual(usage_mode_from_ratio(94, 100), "reduced")
        self.assertEqual(usage_mode_from_ratio(95, 100), "economy")
        self.assertEqual(usage_mode_from_ratio(100, 100), "exhausted")
        self.assertEqual(usage_mode_from_ratio(1, 0), "exhausted")

    def test_flagship_denylist_by_percent(self):
        grok = "x-ai/grok-4.6"
        gemini = "google/gemini-3.7-flash"
        pro = "deepseek/deepseek-v4-pro"
        flash = "deepseek/deepseek-v4.1-flash"
        nemo = "nvidia/nemotron-3-ultra-550b-a55b:free"
        opus5 = "anthropic/claude-opus-5"
        opus48 = "anthropic/claude-opus-4.8"
        fable5 = "anthropic/claude-fable-5"
        gpt56 = "openai/gpt-5.6-sol"
        astra = "openai/gpt-6-astra"
        self.assertTrue(slug_allowed_for_budget(opus5, 49))
        self.assertFalse(slug_allowed_for_budget(opus5, 50))
        self.assertFalse(slug_allowed_for_budget(fable5, 50))
        self.assertFalse(slug_allowed_for_budget(gpt56, 50))
        self.assertFalse(slug_allowed_for_budget(astra, 50))
        self.assertTrue(slug_allowed_for_budget(opus48, 90))
        self.assertTrue(slug_allowed_for_budget(grok, 90))
        self.assertTrue(slug_allowed_for_budget(gemini, 95))
        self.assertTrue(slug_allowed_for_budget(pro, 95))
        self.assertTrue(slug_allowed_for_budget(flash, 95))
        self.assertTrue(slug_allowed_for_budget(nemo, 100))
        self.assertFalse(slug_allowed_for_budget(flash, 100))

    def test_flagship_slug_detection(self):
        self.assertTrue(is_expensive_flagship_slug("anthropic/claude-opus-5"))
        self.assertTrue(is_expensive_flagship_slug("anthropic/claude-opus-5-fast"))
        self.assertTrue(is_expensive_flagship_slug("anthropic/claude-fable-5"))
        self.assertTrue(is_expensive_flagship_slug("openai-codex/gpt-5.6-terra"))
        self.assertTrue(is_expensive_flagship_slug("openai/gpt-6-astra"))
        self.assertTrue(is_expensive_flagship_slug("openai-codex/gpt-6-astra"))
        self.assertTrue(is_expensive_flagship_slug("gpt-6-astra"))
        self.assertFalse(is_expensive_flagship_slug("anthropic/claude-opus-4.8"))
        self.assertFalse(is_expensive_flagship_slug("anthropic/claude-opus-4-8"))
        self.assertFalse(is_expensive_flagship_slug("x-ai/grok-4.6"))
        self.assertFalse(is_expensive_flagship_slug("openai/gpt-5"))

    def test_daily_cap_elevates_to_economy(self):
        self.assertEqual(
            effective_mode_from_usage({"mode": "normal", "dailyCapReached": True}),
            "economy",
        )
        self.assertEqual(
            effective_mode_from_usage({"mode": "exhausted", "dailyCapReached": True}),
            "exhausted",
        )


class ClampPresetTest(unittest.TestCase):
    def test_economy_drops_expert_to_executor(self):
        self.assertEqual(clamp_preset_for_mode("expert", "economy"), "executor")
        self.assertEqual(clamp_preset_for_mode("main", "economy"), "executor")
        self.assertEqual(clamp_preset_for_mode("executor", "economy"), "executor")
        self.assertEqual(clamp_preset_for_mode("light", "economy"), "light")

    def test_exhausted_forces_light(self):
        self.assertEqual(clamp_preset_for_mode("expert", "exhausted"), "light")
        self.assertEqual(clamp_preset_for_mode("main", "exhausted"), "light")

    def test_reduced_caps_at_main(self):
        self.assertEqual(clamp_preset_for_mode("expert", "reduced"), "main")
        self.assertEqual(clamp_preset_for_mode("main", "reduced"), "main")

    def test_user_named_presets_untouched(self):
        self.assertEqual(clamp_preset_for_mode("primary", "exhausted"), "primary")
        self.assertEqual(clamp_preset_for_mode("default", "economy"), "default")


class MaxTokensTest(unittest.TestCase):
    def test_reduced_halves_plan_cap(self):
        self.assertEqual(max_tokens_for_mode("reduced", 32_000, plan_output_cap=16_000), 8_000)
        self.assertIsNone(max_tokens_for_mode("reduced", 4_000, plan_output_cap=16_000))

    def test_normal_no_cap(self):
        self.assertIsNone(max_tokens_for_mode("normal", 32_000, plan_output_cap=16_000))


class PersistAndApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_live_usage_mode()

    def tearDown(self) -> None:
        clear_live_usage_mode()

    def test_validate_body_stores_mode(self):
        config = Config()
        changed = apply_usage_from_response(
            config,
            {"usage": {"mode": "economy", "spentMicroUsd": 1}},
        )
        self.assertTrue(changed)
        self.assertEqual(config.license.usage_mode, "economy")
        self.assertEqual(current_usage_mode(config), "economy")

    def test_apply_runtime_downgrades_managed_key(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "exhausted",
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "light": ModelPresetConfig(provider="openrouter", model="free/x"),
            "executor": ModelPresetConfig(provider="openrouter", model="minimax/m3"),
            "expert": ModelPresetConfig(provider="openrouter", model="opus"),
        }
        runtime = mock.Mock()
        runtime.model_preset = "expert"
        runtime.generation = mock.Mock(max_tokens=32_000)
        light = mock.Mock()
        light.model_preset = "light"
        light.generation = mock.Mock(max_tokens=32_000)
        light.with_generation_overrides = mock.Mock(return_value=light)

        def resolve(name: str):
            self.assertEqual(name, "light")
            return light

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
            uses_managed=True,
        )
        self.assertIs(out, light)
        light.with_generation_overrides.assert_called()

    def test_byok_is_never_clamped(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "usageMode": "exhausted",
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-mine"}},
            }
        )
        runtime = mock.Mock()
        runtime.model_preset = "expert"
        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda _n: (_ for _ in ()).throw(AssertionError("no")),
            known_presets={"expert": ModelPresetConfig(model="opus")},
            uses_managed=False,
        )
        self.assertIs(out, runtime)

    def test_byok_on_a_paid_account_is_not_clamped(self):
        """Navin Plus + the user's own Anthropic key: Opus stays Opus."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-navin",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 85,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-navin"}},
            }
        )
        presets = {
            "my-opus": ModelPresetConfig(
                provider="anthropic", model="anthropic/claude-opus-5"
            ),
            "grok-4-6": ModelPresetConfig(provider="navin", model="x-ai/grok-4.6"),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "my-opus"
        runtime.provider = mock.Mock(api_key="sk-ant-user")
        runtime.generation = mock.Mock(max_tokens=32_000)

        def resolve(_name: str):
            raise AssertionError("BYOK must not be swapped onto the managed catalog")

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
        )
        self.assertIs(out, runtime)

    def test_managed_swap_does_not_land_on_a_byok_preset(self):
        """If Grok exists only as BYOK, the managed clamp must pick a plan model."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 85,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "claude-opus-5": ModelPresetConfig(
                provider="navin", model="anthropic/claude-opus-5"
            ),
            "my-grok": ModelPresetConfig(provider="openrouter", model="x-ai/grok-4.6"),
            "deepseek-v4-flash": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-flash"
            ),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "claude-opus-5"
        runtime.generation = mock.Mock(max_tokens=32_000)
        flash = mock.Mock()
        flash.model = "deepseek/deepseek-v4-flash"
        flash.model_preset = "deepseek-v4-flash"
        flash.generation = mock.Mock(max_tokens=32_000)
        flash.with_generation_overrides = mock.Mock(return_value=flash)

        def resolve(name: str):
            if name == "my-grok":
                raise AssertionError("must not bill a BYOK preset from the managed clamp")
            if name == "deepseek-v4-flash":
                return flash
            return runtime

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, flash)

    def test_summary_updates_live_mode_without_save(self):
        config = Config()
        apply_usage_summary(config, {"mode": "reduced"})
        self.assertEqual(current_usage_mode(config), "reduced")

    def test_a_pinned_allowlisted_model_stays_at_80(self):
        """Grok 4.6 is still allowed at 80 %, so a pin must not be swapped."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 80,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "grok-4-6": ModelPresetConfig(provider="navin", model="x-ai/grok-4.6"),
            "executor": ModelPresetConfig(
                provider="openrouter", model="deepseek/deepseek-v4-flash"
            ),
        }
        runtime = mock.Mock()
        runtime.model = "x-ai/grok-4.6"
        runtime.model_preset = "grok-4-6"
        runtime.generation = mock.Mock(max_tokens=32_000)
        runtime.with_generation_overrides = mock.Mock(return_value=runtime)

        def resolve(_name: str):
            raise AssertionError("allowlisted pin must not be re-resolved")

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, runtime)
        runtime.with_generation_overrides.assert_called()

    def test_pinned_opus_swaps_at_85(self):
        """Opus is off the allowlist at 80 %; a pin must not keep it runnable."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 85,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "claude-opus-5": ModelPresetConfig(
                provider="navin", model="anthropic/claude-opus-5"
            ),
            "grok-4-6": ModelPresetConfig(provider="navin", model="x-ai/grok-4.6"),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "claude-opus-5"
        runtime.generation = mock.Mock(max_tokens=32_000)
        grok = mock.Mock()
        grok.model = "x-ai/grok-4.6"
        grok.model_preset = "grok-4-6"
        grok.generation = mock.Mock(max_tokens=32_000)
        grok.with_generation_overrides = mock.Mock(return_value=grok)

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda name: grok if name == "grok-4-6" else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, grok)

    def test_pinned_opus_swaps_to_flash_when_grok_missing(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 85,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "claude-opus-5": ModelPresetConfig(
                provider="navin", model="anthropic/claude-opus-5"
            ),
            "deepseek-v4-flash": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-flash"
            ),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "claude-opus-5"
        runtime.generation = mock.Mock(max_tokens=32_000)
        flash = mock.Mock()
        flash.model = "deepseek/deepseek-v4-flash"
        flash.model_preset = "deepseek-v4-flash"
        flash.generation = mock.Mock(max_tokens=32_000)
        flash.with_generation_overrides = mock.Mock(return_value=flash)

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda name: flash if name == "deepseek-v4-flash" else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, flash)

    def test_stale_disk_percent_uses_live_snapshot(self):
        """A 70 % config.json must not keep Opus after live usage hit 85 %."""
        live = Config()
        apply_usage_summary(
            live,
            {
                "mode": "reduced",
                "usedPercent": 85,
                "budgetMicroUsd": 100,
                "spentMicroUsd": 85,
            },
        )
        stale = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "normal",
                    "usageUsedPercent": 70,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        self.assertGreaterEqual(budget_used_percent(stale), 80)
        presets = {
            "claude-opus-5": ModelPresetConfig(
                provider="navin", model="anthropic/claude-opus-5"
            ),
            "grok-4-6": ModelPresetConfig(provider="navin", model="x-ai/grok-4.6"),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "claude-opus-5"
        runtime.generation = mock.Mock(max_tokens=32_000)
        grok = mock.Mock()
        grok.model = "x-ai/grok-4.6"
        grok.model_preset = "grok-4-6"
        grok.generation = mock.Mock(max_tokens=32_000)
        grok.with_generation_overrides = mock.Mock(return_value=grok)

        out = apply_usage_mode_to_runtime(
            runtime,
            stale,
            resolve_preset=lambda name: grok if name == "grok-4-6" else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, grok)

    def test_grok_stays_at_90(self):
        """Grok is not a paused flagship; 90 % must not swap it away."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "reduced",
                    "usageUsedPercent": 90,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "grok-4-6": ModelPresetConfig(provider="navin", model="x-ai/grok-4.6"),
            "deepseek-v4-pro": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-pro"
            ),
        }
        runtime = mock.Mock()
        runtime.model = "x-ai/grok-4.6"
        runtime.model_preset = "grok-4-6"
        runtime.generation = mock.Mock(max_tokens=32_000)
        runtime.with_generation_overrides = mock.Mock(return_value=runtime)
        pro = mock.Mock()
        pro.model = "deepseek/deepseek-v4-pro"
        pro.model_preset = "deepseek-v4-pro"
        pro.generation = mock.Mock(max_tokens=32_000)
        pro.with_generation_overrides = mock.Mock(return_value=pro)

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda name: pro if name == "deepseek-v4-pro" else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertEqual(out.model, "x-ai/grok-4.6")

    def test_pro_stays_at_95(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "economy",
                    "usageUsedPercent": 95,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "deepseek-v4-pro": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-pro"
            ),
            "deepseek-v4-flash": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-flash"
            ),
        }
        runtime = mock.Mock()
        runtime.model = "deepseek/deepseek-v4-pro"
        runtime.model_preset = "deepseek-v4-pro"
        runtime.generation = mock.Mock(max_tokens=8_000)
        runtime.with_generation_overrides = mock.Mock(return_value=runtime)

        def resolve(name: str):
            raise AssertionError(f"must not swap DeepSeek Pro, got {name}")

        apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )

    def test_flash_auto_switches_to_nemotron_at_100(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "exhausted",
                    "usageUsedPercent": 100,
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "deepseek-v4-flash": ModelPresetConfig(
                provider="navin", model="deepseek/deepseek-v4-flash"
            ),
            "nemotron-3-ultra-550b-a55b-free": ModelPresetConfig(
                provider="navin",
                model="nvidia/nemotron-3-ultra-550b-a55b:free",
            ),
        }
        runtime = mock.Mock()
        runtime.model = "deepseek/deepseek-v4-flash"
        runtime.model_preset = "deepseek-v4-flash"
        runtime.generation = mock.Mock(max_tokens=8_000)
        nemo = mock.Mock()
        nemo.model = "nvidia/nemotron-3-ultra-550b-a55b:free"
        nemo.model_preset = "nemotron-3-ultra-550b-a55b-free"
        nemo.generation = mock.Mock(max_tokens=8_000)
        nemo.with_generation_overrides = mock.Mock(return_value=nemo)

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda name: nemo
            if "nemotron" in name
            else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=True,
        )
        self.assertIs(out, nemo)

    def test_an_unpinned_flagship_still_downgrades(self):
        """Opus 5 unpinned at economy must leave; cheaper models stay."""
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "economy",
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "executor": ModelPresetConfig(provider="openrouter", model="deepseek/x"),
            "main": ModelPresetConfig(provider="navin", model="anthropic/claude-opus-5"),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "main"
        runtime.generation = mock.Mock(max_tokens=32_000)
        executor = mock.Mock()
        executor.model_preset = "executor"
        executor.generation = mock.Mock(max_tokens=32_000)
        executor.with_generation_overrides = mock.Mock(return_value=executor)

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=lambda name: executor if name == "executor" else runtime,
            known_presets=presets,
            uses_managed=True,
            user_pinned=False,
        )
        self.assertIs(out, executor)

    def test_a_corrupt_clamp_tier_walks_down_to_the_next_one(self):
        """Regression: a degraded catalog mapped 'executor' to an STT model.

        resolve_preset then refuses it (media guard) and the old code
        silently kept the expensive model. The clamp must walk down to the
        next usable tier instead.
        """
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "managedApiKey": "sk-or-v1-x",
                    "managedProvider": "openrouter",
                    "usageMode": "economy",
                },
                "providers": {"openrouter": {"apiKey": "sk-or-v1-x"}},
            }
        )
        presets = {
            "light": ModelPresetConfig(provider="openrouter", model="free/x"),
            "executor": ModelPresetConfig(
                provider="openrouter", model="fish-audio/transcribe-1"
            ),
            "expert": ModelPresetConfig(provider="navin", model="anthropic/claude-opus-5"),
        }
        runtime = mock.Mock()
        runtime.model = "anthropic/claude-opus-5"
        runtime.model_preset = "expert"
        runtime.generation = mock.Mock(max_tokens=32_000)
        light = mock.Mock()
        light.model_preset = "light"
        light.generation = mock.Mock(max_tokens=32_000)
        light.with_generation_overrides = mock.Mock(return_value=light)

        def resolve(name: str):
            if name == "executor":
                raise ValueError("media preset cannot run a chat turn")
            self.assertEqual(name, "light")
            return light

        out = apply_usage_mode_to_runtime(
            runtime,
            config,
            resolve_preset=resolve,
            known_presets=presets,
            uses_managed=True,
        )
        self.assertIs(out, light)


if __name__ == "__main__":
    unittest.main()
