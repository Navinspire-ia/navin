# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The managed catalog becomes tier aliases + selectable navin presets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from navin.config.loader import get_config_path, load_config, set_config_path
from navin.config.schema import Config, ModelPresetConfig
from navin.providers import managed_catalog
from navin.providers.managed_catalog import (
    apply_catalog,
    catalog_presets,
    catalog_url,
    parse_catalog,
    slug_preset_key,
    sync_managed_catalog,
)

# The shape /api/models serves, reduced to two models per relevant tier.
SITE_PAYLOAD = {
    "provider": "openrouter",
    "defaultModel": "deepseek/deepseek-v4.1-flash",
    "models": [
        {"slug": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron 3 Ultra", "tier": "light"},
        {"slug": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "tier": "executor"},
        {"slug": "minimax/minimax-m3", "name": "MiniMax M3", "tier": "executor"},
        {"slug": "z-ai/glm-5.2", "name": "GLM 5.2", "tier": "main"},
        {"slug": "x-ai/grok-4.5", "name": "Grok 4.5", "tier": "expert", "priceUnconfirmed": True},
        {"slug": "moonshotai/kimi-k3", "name": "Kimi K3", "tier": "expert"},
    ],
}

FLASH_BY_PLAN = {
    "flash": {
        "defaultModel": "deepseek/deepseek-v4.1-flash",
        "models": [
            {
                "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "name": "Nemotron 3 Ultra",
                "tier": "light",
            },
            {"slug": "qwen/qwen3.7-flash", "name": "Qwen3.7 Flash", "tier": "light"},
            {"slug": "z-ai/glm-4.7-flash", "name": "GLM 4.7 Flash", "tier": "light"},
            {
                "slug": "deepseek/deepseek-v4.1-flash",
                "name": "DeepSeek V4.1 Flash",
                "tier": "executor",
            },
            {"slug": "minimax/minimax-m3", "name": "MiniMax M3", "tier": "main"},
        ],
    }
}


class ParseTest(unittest.TestCase):
    def test_fallback_catalog_defaults_to_glm_flash_on_every_plan(self):
        plus = parse_catalog(managed_catalog.FALLBACK_CATALOG_PAYLOAD)
        self.assertEqual(plus.default_model, "z-ai/glm-5.3-flash")
        slugs = {m.slug for m in plus.models}
        # Grok 4.6 stays the Plus+ vision brain, not the chat default.
        self.assertIn("x-ai/grok-4.6", slugs)
        self.assertEqual(managed_catalog.DEFAULT_VISION_MODEL, "x-ai/grok-4.6")
        self.assertIn("z-ai/glm-5.3-flash", slugs)
        self.assertIn("z-ai/glm-5.3", slugs)
        self.assertIn("anthropic/claude-fable-5.1", slugs)
        self.assertIn("meta/muse-spark-1.3", slugs)
        self.assertIn("openai/gpt-6-astra", slugs)
        self.assertIn("deepseek/deepseek-v4.1-flash", slugs)
        self.assertIn("deepseek/deepseek-v4-pro", slugs)
        self.assertNotIn("deepseek/deepseek-v4-flash", slugs)
        self.assertFalse(any(s.startswith("meta/muse-spark-1.1") for s in slugs))
        self.assertFalse(any(s.startswith("meta/muse-spark-1.2") for s in slugs))

        flash = parse_catalog(managed_catalog.FALLBACK_CATALOG_PAYLOAD, plan="flash")
        self.assertEqual(flash.default_model, "z-ai/glm-5.3-flash")
        flash_slugs = {m.slug for m in flash.models}
        self.assertIn("z-ai/glm-5.3-flash", flash_slugs)
        self.assertNotIn("x-ai/grok-4.6", flash_slugs)
        self.assertNotIn("z-ai/glm-5.3", flash_slugs)
        self.assertNotIn("openai/gpt-6-astra", flash_slugs)
        self.assertIn("deepseek/deepseek-v4.1-flash", flash_slugs)
        self.assertNotIn("deepseek/deepseek-v4-flash", flash_slugs)
        self.assertNotIn("deepseek/deepseek-v4-pro", flash_slugs)

    def test_stale_flash_payload_drops_grok_and_keeps_glm_flash(self):
        payload = {
            "provider": "openrouter",
            "defaultModel": "x-ai/grok-4.6",
            "byPlan": {
                "flash": {
                    "defaultModel": "x-ai/grok-4.6",
                    "models": [
                        {
                            "slug": "x-ai/grok-4.6",
                            "name": "Grok 4.6",
                            "tier": "expert",
                        },
                        {
                            "slug": "openai/gpt-6-astra",
                            "name": "GPT-6 Astra",
                            "tier": "expert",
                        },
                        {
                            "slug": "deepseek/deepseek-v4.1-flash",
                            "name": "DeepSeek V4.1 Flash",
                            "tier": "executor",
                        },
                    ],
                }
            },
        }
        flash = parse_catalog(payload, plan="flash")
        slugs = {m.slug for m in flash.models}
        self.assertNotIn("x-ai/grok-4.6", slugs)
        self.assertNotIn("openai/gpt-6-astra", slugs)
        self.assertIn("z-ai/glm-5.3-flash", slugs)
        self.assertEqual(flash.default_model, "z-ai/glm-5.3-flash")

    def test_ox_alpha_is_dropped_from_every_plan(self):
        payload = {
            "provider": "openrouter",
            "defaultModel": "stealth/ox-alpha",
            "models": [
                {
                    "slug": "stealth/ox-alpha",
                    "name": "Ox Alpha",
                    "tier": "main",
                },
                {
                    "slug": "deepseek/deepseek-v4.1-flash",
                    "name": "DeepSeek V4.1 Flash",
                    "tier": "executor",
                },
            ],
            "byPlan": {
                "free": {
                    "defaultModel": "stealth/ox-alpha",
                    "models": [
                        {
                            "slug": "stealth/ox-alpha",
                            "name": "Ox Alpha",
                            "tier": "main",
                        },
                        {
                            "slug": "nvidia/nemotron-3-ultra-550b-a55b:free",
                            "name": "Nemotron 3 Ultra",
                            "tier": "light",
                        },
                    ],
                },
                "flash": {
                    "defaultModel": "stealth/ox-alpha",
                    "models": [
                        {
                            "slug": "stealth/ox-alpha",
                            "name": "Ox Alpha",
                            "tier": "main",
                        },
                        {
                            "slug": "z-ai/glm-5.3-flash",
                            "name": "GLM 5.3 Flash",
                            "tier": "executor",
                        },
                    ],
                },
            },
        }
        plus = parse_catalog(payload, plan="plus")
        self.assertNotIn("stealth/ox-alpha", {m.slug for m in plus.models})
        self.assertNotEqual(plus.default_model, "stealth/ox-alpha")

        flash = parse_catalog(payload, plan="flash")
        self.assertNotIn("stealth/ox-alpha", {m.slug for m in flash.models})
        self.assertEqual(flash.default_model, "z-ai/glm-5.3-flash")

        free = parse_catalog(payload, plan="free")
        self.assertNotIn("stealth/ox-alpha", {m.slug for m in free.models})
        self.assertNotEqual(free.default_model, "stealth/ox-alpha")

        fallback_slugs = {
            m.slug
            for m in parse_catalog(managed_catalog.FALLBACK_CATALOG_PAYLOAD).models
        }
        self.assertNotIn("stealth/ox-alpha", fallback_slugs)

    def test_the_site_payload_parses(self):
        catalog = parse_catalog(SITE_PAYLOAD)
        self.assertEqual(catalog.provider, "openrouter")
        self.assertEqual(catalog.default_model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(len(catalog.models), 6)

    def test_garbage_is_refused(self):
        for payload in (None, [], {}, {"models": []}, {"models": [{"tier": "light"}]}):
            with self.assertRaises(ValueError):
                parse_catalog(payload)

    def test_unknown_tiers_are_dropped_not_fatal(self):
        payload = {
            "models": [
                {"slug": "a/b", "tier": "galactic"},
                {"slug": "z-ai/glm-5.2", "tier": "main"},
            ]
        }
        catalog = parse_catalog(payload)
        self.assertEqual([m.slug for m in catalog.models], ["z-ai/glm-5.2"])

    def test_a_degraded_payload_never_makes_a_media_model_the_default(self):
        """Old site code + new DB rows: media models leak into ``models`` with
        text tiers and Lyria arrives as defaultModel, with no ``media`` block.
        The gateway must recognize them anyway (2026-08-09 incident: the chat
        default became a music model and every agent turn lost tool support).
        """
        degraded = {
            "provider": "openrouter",
            "defaultModel": "google/lyria-3-clip-preview",
            "models": [
                {"slug": "google/lyria-3-clip-preview", "name": "Lyria 3", "tier": "executor"},
                {"slug": "google/veo-3.1-fast", "name": "Veo 3.1 Fast", "tier": "executor"},
                {"slug": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "tier": "executor"},
                {"slug": "z-ai/glm-5.2", "name": "GLM 5.2", "tier": "main"},
            ],
        }
        catalog = parse_catalog(degraded)
        slugs = [m.slug for m in catalog.models]
        self.assertNotIn("google/lyria-3-clip-preview", slugs)
        self.assertNotIn("google/veo-3.1-fast", slugs)
        self.assertEqual(catalog.default_model, "deepseek/deepseek-v4.1-flash")

        config = Config()
        apply_catalog(config, catalog, force_managed=True)
        self.assertEqual(config.agents.defaults.model, "deepseek/deepseek-v4.1-flash")

    def test_an_stt_model_never_lands_on_a_text_tier(self):
        """Regression (2026-08-10 incident): a degraded payload mapped the
        'executor' tier to fish-audio/transcribe-1 (STT). The slug is absent
        from every known-media list, so only the heuristic can catch it. In
        economy budget mode the executor tier runs chat turns: an STT slug
        there breaks every clamped conversation.
        """
        degraded = {
            "provider": "openrouter",
            "defaultModel": "deepseek/deepseek-v4.1-flash",
            "models": [
                {"slug": "fish-audio/transcribe-1", "name": "Transcribe 1", "tier": "executor"},
                {"slug": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "tier": "executor"},
                {"slug": "moonshotai/kimi-k3", "name": "Kimi K3", "tier": "expert"},
            ],
        }
        catalog = parse_catalog(degraded)
        slugs = [m.slug for m in catalog.models]
        self.assertNotIn("fish-audio/transcribe-1", slugs)
        self.assertEqual(
            catalog.tier_leader("executor").slug, "deepseek/deepseek-v4.1-flash"
        )

    def test_an_existing_lyria_default_is_healed_even_without_media_block(self):
        """A config already poisoned by the degraded payload must be repaired
        on the next sync, even when the payload still has no media block."""
        config = Config()
        config.agents.defaults.model = "google/lyria-3-clip-preview"
        config.agents.defaults.provider = "navin"
        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)
        self.assertEqual(config.agents.defaults.model, "deepseek/deepseek-v4.1-flash")


class PresetTest(unittest.TestCase):
    def test_each_tier_gets_the_first_confirmed_model(self):
        presets = catalog_presets(parse_catalog(SITE_PAYLOAD))
        self.assertEqual(presets["executor"].model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(presets["executor"].provider, "navin")
        self.assertEqual(presets["main"].label, "GLM 5.2")

    def test_an_unconfirmed_price_does_not_lead_a_tier(self):
        """Grok's price is not published; Kimi K3 must lead expert despite
        being listed second."""
        presets = catalog_presets(parse_catalog(SITE_PAYLOAD))
        self.assertEqual(presets["expert"].model, "moonshotai/kimi-k3")

    def test_selectable_presets_cover_every_catalog_model(self):
        presets = catalog_presets(parse_catalog(SITE_PAYLOAD))
        selectable = {
            name: preset
            for name, preset in presets.items()
            if name not in managed_catalog.TIERS
        }
        self.assertEqual(len(selectable), 6)
        self.assertEqual(
            presets[slug_preset_key("deepseek/deepseek-v4.1-flash")].provider,
            "navin",
        )


class ApplyTest(unittest.TestCase):
    def test_a_fresh_config_is_fully_wired(self):
        config = Config()
        self.assertTrue(apply_catalog(config, parse_catalog(SITE_PAYLOAD)))
        self.assertEqual(config.agents.defaults.model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(config.agents.defaults.provider, "navin")
        self.assertEqual(
            config.agents.defaults.model_preset,
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )
        self.assertEqual(config.model_routes["review"], slug_preset_key("z-ai/glm-5.2"))
        self.assertEqual(
            config.model_routes["fast"],
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )
        self.assertEqual(
            config.model_presets["light"].model,
            "nvidia/nemotron-3-ultra-550b-a55b:free",
        )
        self.assertEqual(config.model_presets["executor"].model, "deepseek/deepseek-v4.1-flash")
        # Every task role used by /pilot and the Settings routing UI is wired.
        for role in ("fast", "search", "docs", "dev", "plan", "deep", "review", "security"):
            self.assertIn(role, config.model_routes)

    def test_the_second_apply_changes_nothing(self):
        config = Config()
        catalog = parse_catalog(SITE_PAYLOAD)
        apply_catalog(config, catalog)
        self.assertFalse(apply_catalog(config, catalog))

    def test_the_users_own_choices_are_left_alone(self):
        config = Config.model_validate(
            {
                "agents": {"defaults": {"model": "mistral-large-latest"}},
                "modelPresets": {"mine": {"model": "mistral-large-latest"}},
                "modelRoutes": {"review": "mine"},
            }
        )
        apply_catalog(config, parse_catalog(SITE_PAYLOAD))
        self.assertEqual(config.agents.defaults.model, "mistral-large-latest")
        self.assertEqual(config.model_routes["review"], "mine")
        # Roles the user never routed still get filled.
        self.assertEqual(
            config.model_routes["fast"],
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )

    def test_a_user_pinned_default_survives_a_forced_sync(self):
        """Regression: picking GLM 5.2 in the composer promoted it to the
        account default, then the very next catalog sync on a paid plan
        (opening the picker, a new chat) snapped the default back to DeepSeek.
        An explicit user pick must hold until the user changes it."""
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)
        config.model_presets["glm-5-2"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="zai", modality="text",
        )
        config.agents.defaults.model_preset = "glm-5-2"
        config.agents.defaults.model_preset_user_pinned = True

        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)

        self.assertEqual(config.agents.defaults.model_preset, "glm-5-2")
        self.assertTrue(config.agents.defaults.model_preset_user_pinned)

    def test_an_unpinned_default_still_follows_the_catalog(self):
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)
        # Simulate an old config where the preset was set without a user pin.
        config.agents.defaults.model_preset = "expert"
        config.agents.defaults.model_preset_user_pinned = False

        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)

        self.assertEqual(
            config.agents.defaults.model_preset,
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )

    def test_a_pin_on_a_media_preset_is_healed_and_released(self):
        """The pin protects a user choice, never a broken one: a media model
        pinned as chat default is healed to a text model and unpinned."""
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)
        config.model_presets["lyria"] = ModelPresetConfig(
            label="Lyria", model="google/lyria-3-clip-preview",
            provider="navin", modality="music",
        )
        config.agents.defaults.model_preset = "lyria"
        config.agents.defaults.model_preset_user_pinned = True

        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)

        self.assertNotEqual(config.agents.defaults.model_preset, "lyria")
        self.assertFalse(config.agents.defaults.model_preset_user_pinned)

    def test_a_pin_on_a_vanished_preset_is_released(self):
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)
        config.agents.defaults.model_preset = "gone-preset"
        config.agents.defaults.model_preset_user_pinned = True
        # The validator would reject this on a reload; apply_catalog must not
        # crash on it either and must fall back to the managed default.
        config.model_presets.pop("gone-preset", None)

        apply_catalog(config, parse_catalog(SITE_PAYLOAD), force_managed=True)

        self.assertEqual(
            config.agents.defaults.model_preset,
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )

    def test_a_new_catalog_rewrites_the_tier_presets(self):
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD))
        moved = dict(SITE_PAYLOAD, defaultModel="z-ai/glm-5.2")
        moved["models"] = [
            {"slug": "z-ai/glm-5.2", "name": "GLM 5.2", "tier": "executor"},
        ]
        self.assertTrue(apply_catalog(config, parse_catalog(moved)))
        self.assertEqual(config.model_presets["executor"].model, "z-ai/glm-5.2")
        # The already-set default model is a user-visible choice by now: kept.
        self.assertEqual(config.agents.defaults.model, "deepseek/deepseek-v4.1-flash")


class SyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        managed_catalog._last_sync_attempt_mono = 0.0

    def tearDown(self):
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_throttle_skips_repeat_fetch(self):
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        response = httpx.Response(
            200,
            json=SITE_PAYLOAD,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response) as fetched:
            self.assertTrue(sync_managed_catalog(config, min_interval_s=60))
            self.assertFalse(sync_managed_catalog(config, min_interval_s=60))
            # force bypasses throttle; unchanged catalog → False but still fetched
            sync_managed_catalog(config, force=True, min_interval_s=60)
        self.assertEqual(fetched.call_count, 2)

    def test_network_blip_keeps_the_catalog_synced_earlier(self):
        # A synced catalog (27 models, glm default) used to be replaced by the
        # embedded fallback (15 models, grok default) each time navin.live was
        # unreachable, then restored on the next fetch: a flip-flopping default.
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        response = httpx.Response(
            200,
            json=SITE_PAYLOAD,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response):
            self.assertTrue(sync_managed_catalog(config))
        before_presets = dict(config.model_presets)
        before_default = config.agents.defaults.model
        with mock.patch.object(
            managed_catalog.httpx, "get", side_effect=httpx.ConnectError("network unreachable")
        ):
            self.assertFalse(sync_managed_catalog(config, force=True, min_interval_s=0))
        self.assertEqual(config.model_presets, before_presets)
        self.assertEqual(config.agents.defaults.model, before_default)

    def test_first_sync_without_network_still_gets_the_fallback(self):
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        with mock.patch.object(
            managed_catalog.httpx, "get", side_effect=httpx.ConnectError("network unreachable")
        ):
            self.assertTrue(sync_managed_catalog(config))
        self.assertTrue(
            any(p.provider == "navin" for p in config.model_presets.values()),
        )

    def test_a_good_fetch_is_applied_and_saved(self):
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        response = httpx.Response(
            200,
            json=SITE_PAYLOAD,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response):
            self.assertTrue(sync_managed_catalog(config))
        saved = json.loads(Path(get_config_path()).read_text(encoding="utf-8"))
        self.assertEqual(saved["agents"]["defaults"]["model"], "deepseek/deepseek-v4.1-flash")
        self.assertIn("expert", saved["modelPresets"])
        self.assertEqual(saved["modelPresets"]["expert"]["provider"], "navin")

    def test_a_dead_network_applies_embedded_fallback(self):
        """Prod /api/models may 404 or be offline - subscribers still get wired."""
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        with mock.patch.object(
            managed_catalog.httpx, "get", side_effect=httpx.ConnectError("offline")
        ):
            self.assertTrue(sync_managed_catalog(config))
        saved = json.loads(Path(get_config_path()).read_text(encoding="utf-8"))
        self.assertEqual(saved["agents"]["defaults"]["model"], "z-ai/glm-5.3-flash")
        self.assertEqual(saved["modelPresets"]["executor"]["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(saved["modelPresets"]["executor"]["provider"], "navin")
        self.assertEqual(
            saved["modelRoutes"]["dev"],
            slug_preset_key("z-ai/glm-5.3-flash"),
        )
        self.assertEqual(
            saved["modelRoutes"]["security"],
            slug_preset_key("z-ai/glm-5.3-flash"),
        )
        self.assertEqual(
            saved["modelRoutes"]["deep"],
            slug_preset_key("qwen/qwen3.8-max"),
        )

    def test_http_error_also_uses_fallback(self):
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        response = httpx.Response(
            404,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response):
            self.assertTrue(sync_managed_catalog(config))
        self.assertEqual(config.agents.defaults.model, "z-ai/glm-5.3-flash")
        expected_roles = set(managed_catalog.ROLE_TIERS) | {"vision", "computer"}
        self.assertEqual(set(config.model_routes), expected_roles)
        self.assertEqual(
            config.model_routes["vision"],
            managed_catalog.slug_preset_key(managed_catalog.DEFAULT_VISION_MODEL),
        )

    def test_the_url_can_be_overridden(self):
        config = Config.model_validate(
            {"modelCatalog": {"enabled": True, "url": "https://staging.navin.live/api/models"}}
        )
        self.assertEqual(catalog_url(config), "https://staging.navin.live/api/models")
        with mock.patch.dict("os.environ", {"NAVIN_MODEL_CATALOG_URL": "https://env.example/m"}):
            self.assertEqual(catalog_url(Config()), "https://env.example/m")

    def test_the_synced_config_reloads_cleanly(self):
        """model_routes validation drops routes to missing presets; the tier
        presets have to survive a round-trip through the loader."""
        config = Config.model_validate({"modelCatalog": {"enabled": True}})
        response = httpx.Response(
            200,
            json=SITE_PAYLOAD,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response):
            sync_managed_catalog(config)
        reloaded = load_config()
        self.assertEqual(
            reloaded.model_routes["review"],
            slug_preset_key("z-ai/glm-5.2"),
        )
        self.assertEqual(reloaded.model_presets["expert"].model, "moonshotai/kimi-k3")

    def test_flash_plan_includes_glm_flash_and_not_grok(self):
        payload = {**SITE_PAYLOAD, "byPlan": FLASH_BY_PLAN}
        catalog = parse_catalog(payload, plan="flash")
        slugs = {m.slug for m in catalog.models}
        self.assertIn("z-ai/glm-5.3-flash", slugs)
        self.assertNotIn("x-ai/grok-4.6", slugs)
        self.assertNotIn("x-ai/grok-4.5", slugs)
        self.assertEqual(len(catalog.models), 6)
        self.assertEqual(catalog.role_tiers["review"], "main")
        config = Config()
        self.assertTrue(apply_catalog(config, catalog, force_managed=True))
        self.assertNotIn("expert", config.model_presets)
        self.assertEqual(
            config.model_routes["security"],
            slug_preset_key("minimax/minimax-m3"),
        )
        self.assertEqual(config.agents.defaults.model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(config.agents.defaults.provider, "navin")
        selectable = [
            name
            for name, preset in config.model_presets.items()
            if name not in managed_catalog.TIERS and preset.provider == "navin"
        ]
        self.assertEqual(len(selectable), 6)
        for slug in (
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "deepseek/deepseek-v4.1-flash",
            "minimax/minimax-m3",
            "z-ai/glm-4.7-flash",
            "qwen/qwen3.7-flash",
            "z-ai/glm-5.3-flash",
        ):
            self.assertIn(slug_preset_key(slug), config.model_presets)
            self.assertEqual(config.model_presets[slug_preset_key(slug)].provider, "navin")

    def test_paid_task_routes_never_use_a_free_endpoint(self):
        config = Config()
        apply_catalog(config, parse_catalog(managed_catalog.FALLBACK_CATALOG_PAYLOAD))
        self.assertEqual(config.model_routes["deep"], slug_preset_key("qwen/qwen3.8-max"))
        self.assertEqual(
            config.model_routes["dev"],
            slug_preset_key("z-ai/glm-5.3-flash"),
        )
        self.assertEqual(
            config.model_routes["fast"],
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )
        self.assertEqual(
            config.model_routes["code"],
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )
        self.assertEqual(
            config.model_routes["search"],
            slug_preset_key("deepseek/deepseek-v4.1-flash"),
        )
        for role, target in config.model_routes.items():
            if role == "vision":
                continue
            preset = config.model_presets.get(target)
            slug = preset.model if preset is not None else target
            self.assertFalse(
                managed_catalog.is_free_model_slug(slug),
                f"{role} still routes to free model {slug}",
            )

    def test_an_existing_free_light_route_is_rewritten(self):
        config = Config()
        apply_catalog(config, parse_catalog(SITE_PAYLOAD))
        config.model_routes["fast"] = "light"
        config.model_routes["search"] = "nemotron-3-ultra-550b-a55b-free"
        self.assertTrue(apply_catalog(config, parse_catalog(SITE_PAYLOAD)))
        paid = slug_preset_key("deepseek/deepseek-v4.1-flash")
        self.assertEqual(config.model_routes["fast"], paid)
        self.assertEqual(config.model_routes["search"], paid)

    def test_paid_sync_selects_by_plan_and_drops_legacy_openrouter(self):
        config = Config.model_validate(
            {
                "modelCatalog": {"enabled": True},
                "license": {"plan": "flash"},
                "modelPresets": {
                    "expert": {
                        "label": "Kimi K3",
                        "model": "moonshotai/kimi-k3",
                        "provider": "openrouter",
                    },
                    "glm-5-2": {
                        "label": "GLM 5.2",
                        "model": "z-ai/glm-5.2",
                        "provider": "openrouter",
                    },
                    "mine": {
                        "label": "Mine",
                        "model": "deepseek/deepseek-v4.1-flash",
                        "provider": "openrouter",
                    },
                },
            }
        )
        payload = {**SITE_PAYLOAD, "byPlan": FLASH_BY_PLAN}
        response = httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", managed_catalog.DEFAULT_CATALOG_URL),
        )
        with mock.patch.object(managed_catalog.httpx, "get", return_value=response):
            self.assertTrue(sync_managed_catalog(config))
        self.assertEqual(len([p for p in config.model_presets if p in managed_catalog.TIERS]), 3)
        self.assertNotIn("expert", config.model_presets)
        self.assertNotIn("glm-5-2", config.model_presets)
        # True BYOK (custom name) stays even if the slug overlaps the plan.
        self.assertIn("mine", config.model_presets)
        self.assertEqual(config.model_presets["mine"].provider, "openrouter")


if __name__ == "__main__":
    unittest.main()
