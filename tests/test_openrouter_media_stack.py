"""OpenRouter media stack: pricing units, STT Navin, music tool wiring."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools.context import ToolContext
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.music_generation import MusicGenerationTool
from navin.agent.tools.registry import ToolRegistry
from navin.audio.transcription import resolve_transcription_config
from navin.audio.transcription_registry import (
    get_transcription_provider,
    transcription_provider_names,
)
from navin.config.schema import Config, ModelPresetConfig, ProviderConfig, ToolsConfig
from navin.providers.managed_catalog import (
    FALLBACK_CATALOG_PAYLOAD,
    apply_catalog,
    catalog_presets,
    parse_catalog,
)
from navin.providers.media_usage import catalog_cost_micro_usd, provider_cost_micro_usd
from navin.providers.music_generation import (
    get_music_gen_provider,
    music_gen_provider_names,
)
from navin.utils.artifacts import (
    generated_music_tool_result,
    store_generated_music_artifact,
)


def _keyed() -> dict[str, ProviderConfig]:
    return {
        "navin": ProviderConfig(api_key="sk-test"),
        "openrouter": ProviderConfig(api_key="sk-test"),
    }


class FallbackCatalogPricingTest(unittest.TestCase):
    def test_media_buckets_include_music_and_stt(self) -> None:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        self.assertEqual(
            set(catalog.media),
            {"image", "video", "audio", "music", "stt"},
        )
        self.assertEqual(catalog.media["video"].default_model, "minimax/hailuo-3")
        self.assertEqual(catalog.media["music"].default_model, "google/lyria-3-clip-preview")
        self.assertEqual(
            catalog.media["stt"].default_model, "nvidia/parakeet-tdt-0.6b-v3"
        )
        stt_slugs = {m.slug for m in catalog.media["stt"].models}
        self.assertEqual(
            stt_slugs,
            {
                "qwen/qwen3-asr-flash-2026-02-10",
                "openai/gpt-transcribe",
                "nvidia/parakeet-tdt-0.6b-v3",
                "microsoft/mai-transcribe-1.5",
                "deepgram/nova-3",
            },
        )

    def test_veo_fast_uses_estimated_8s_profile_not_per_second_alone(self) -> None:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        veo = next(
            m for m in catalog.media["video"].models if m.slug == "google/veo-3.1-fast"
        )
        self.assertEqual(veo.billing_unit, "video_second")
        self.assertEqual(veo.billing_rate, 0.10)
        self.assertEqual(veo.unit_price_usd, 0.80)
        self.assertEqual(veo.estimated_generation_cost, 0.80)

    def test_seedance_fast_keeps_video_token_rate_and_720p_estimate(self) -> None:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        seed = next(
            m
            for m in catalog.media["video"].models
            if m.slug == "bytedance/seedance-2.0-fast"
        )
        self.assertEqual(seed.billing_unit, "video_token")
        self.assertEqual(seed.billing_rate, 0.0000056)
        self.assertAlmostEqual(seed.estimated_generation_cost or 0, 0.96768, places=5)
        self.assertEqual(seed.unit_price_usd, 0.97)

    def test_presets_carry_music_and_stt_modalities(self) -> None:
        presets = catalog_presets(parse_catalog(FALLBACK_CATALOG_PAYLOAD))
        modalities = {p.modality for p in presets.values()}
        self.assertIn("music", modalities)
        self.assertIn("stt", modalities)

    def test_lyria_chat_default_heals_to_catalog_default(self) -> None:
        """Stale configs pinned Lyria as chat model (modality missing → text)."""
        cfg = Config()
        cfg.agents.defaults.model = "google/lyria-3-clip-preview"
        cfg.agents.defaults.model_preset = "lyria-3-clip-preview"
        cfg.agents.defaults.provider = "navin"
        cfg.model_presets["lyria-3-clip-preview"] = ModelPresetConfig(
            label="Lyria 3 Clip Preview",
            model="google/lyria-3-clip-preview",
            provider="navin",
        )
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        self.assertTrue(apply_catalog(cfg, catalog, force_managed=False))
        self.assertEqual(cfg.agents.defaults.model, "z-ai/glm-5.3-flash")
        self.assertEqual(cfg.agents.defaults.model_preset, "glm-5-3-flash")
        self.assertEqual(
            cfg.model_presets["lyria-3-clip-preview"].modality, "music"
        )


class MediaUsagePricingTest(unittest.TestCase):
    def test_provider_usage_cost_wins(self) -> None:
        self.assertEqual(
            provider_cost_micro_usd({"usage": {"cost": 0.80}}),
            800_000,
        )

    def test_catalog_scales_veo_estimate_by_duration_ratio(self) -> None:
        cfg = Config()
        cfg.model_presets = catalog_presets(parse_catalog(FALLBACK_CATALOG_PAYLOAD))
        # unit_price = 0.80 for 8s → 5s = 0.50
        cost = catalog_cost_micro_usd(cfg, "google/veo-3.1-fast", units=5 / 8)
        self.assertAlmostEqual(cost / 1_000_000, 0.50, places=2)


class TranscriptionNavinTest(unittest.TestCase):
    def test_navin_is_registered_with_gpt_transcribe_default(self) -> None:
        self.assertIn("navin", transcription_provider_names())
        spec = get_transcription_provider("navin")
        assert spec is not None
        self.assertEqual(spec.default_model, "nvidia/parakeet-tdt-0.6b-v3")

    def test_no_mistral_stt_in_catalog(self) -> None:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        slugs = {m.slug for m in catalog.media["stt"].models}
        self.assertFalse(any(slug.startswith("mistralai/") for slug in slugs))

    def test_runtime_falls_back_from_unconfigured_groq_to_navin(self) -> None:
        cfg = Config()
        cfg.transcription.provider = "groq"
        cfg.transcription.model = "whisper-large-v3"
        cfg.providers.navin = ProviderConfig(api_key="sk-managed")
        eff = resolve_transcription_config(cfg)
        self.assertEqual(eff.provider, "navin")
        self.assertEqual(eff.model, "nvidia/parakeet-tdt-0.6b-v3")
        self.assertTrue(eff.configured)

    def test_runtime_uses_managed_license_key_when_navin_slot_is_empty(self) -> None:
        cfg = Config()
        cfg.transcription.provider = "navin"
        cfg.license.managed_api_key = "sk-managed-license"
        eff = resolve_transcription_config(cfg)
        self.assertEqual(eff.provider, "navin")
        self.assertTrue(eff.configured)
        self.assertEqual(eff.api_key, "sk-managed-license")

    def test_runtime_falls_back_to_openrouter_byok(self) -> None:
        cfg = Config()
        cfg.transcription.provider = "navin"
        cfg.providers.openrouter = ProviderConfig(api_key="sk-byok")
        eff = resolve_transcription_config(cfg)
        self.assertEqual(eff.provider, "openrouter")
        self.assertTrue(eff.configured)
        self.assertEqual(eff.api_key, "sk-byok")

    def test_steer_tools_moves_stt_and_music_to_catalog_defaults(self) -> None:
        cfg = Config()
        cfg.transcription.provider = "groq"
        cfg.transcription.model = "whisper-large-v3"
        cfg.tools.music_generation.provider = "openrouter"
        cfg.tools.music_generation.model = "old/model"
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        apply_catalog(cfg, catalog, force_managed=True, steer_tools=True)
        self.assertEqual(cfg.transcription.provider, "navin")
        self.assertEqual(cfg.transcription.model, "nvidia/parakeet-tdt-0.6b-v3")
        self.assertEqual(cfg.tools.music_generation.provider, "navin")
        self.assertEqual(
            cfg.tools.music_generation.model, "google/lyria-3-clip-preview"
        )

    def test_heal_managed_voice_rewrites_legacy_groq_whisper(self) -> None:
        from navin.providers.managed_catalog import heal_managed_voice_settings

        cfg = Config()
        cfg.license.plan = "plus"
        cfg.license.managed_api_key = "sk-managed"
        cfg.transcription.enabled = False
        cfg.transcription.provider = "groq"
        cfg.transcription.model = "whisper-large-v3"
        self.assertTrue(heal_managed_voice_settings(cfg))
        self.assertTrue(cfg.transcription.enabled)
        self.assertEqual(cfg.transcription.provider, "navin")
        self.assertEqual(cfg.transcription.model, "nvidia/parakeet-tdt-0.6b-v3")
        # Idempotent on a second pass.
        self.assertFalse(heal_managed_voice_settings(cfg))

    def test_heal_keeps_curated_non_default_stt_pick(self) -> None:
        from navin.providers.managed_catalog import heal_managed_voice_settings

        cfg = Config()
        cfg.license.plan = "plus"
        cfg.license.managed_api_key = "sk-managed"
        cfg.transcription.enabled = True
        cfg.transcription.provider = "navin"
        cfg.transcription.model = "deepgram/nova-3"
        # TTS starts unset, so pin it to keep this test about the STT pick.
        cfg.voice.tts_provider = "navin"
        cfg.voice.tts_model = "google/gemini-3.1-flash-tts-preview"
        self.assertFalse(heal_managed_voice_settings(cfg))
        self.assertEqual(cfg.transcription.model, "deepgram/nova-3")


class MusicToolWiringTest(unittest.TestCase):
    def test_music_providers_registered(self) -> None:
        self.assertEqual(set(music_gen_provider_names()), {"openrouter", "navin"})
        self.assertIsNotNone(get_music_gen_provider("navin"))

    def test_generate_music_loads_when_credential_ready(self) -> None:
        # Media providers start unset, so the tools only load once one is picked.
        tools = ToolsConfig()
        tools.image_generation.provider = "navin"
        tools.video_generation.provider = "navin"
        tools.music_generation.provider = "navin"
        ctx = ToolContext(
            config=tools,
            workspace=str(Path.cwd()),
            image_generation_provider_configs=_keyed(),
        )
        loaded = set(ToolLoader().load(ctx, ToolRegistry(), scope="core"))
        self.assertIn("generate_music", loaded)
        self.assertIn("generate_image", loaded)
        self.assertIn("generate_video", loaded)

    def test_generate_music_stays_out_without_credential(self) -> None:
        ctx = ToolContext(
            config=ToolsConfig(),
            workspace=str(Path.cwd()),
            image_generation_provider_configs={},
        )
        loaded = set(ToolLoader().load(ctx, ToolRegistry(), scope="core"))
        self.assertNotIn("generate_music", loaded)

    def test_music_artifact_persists_mp3(self) -> None:
        raw = b"ID3\x03\x00\x00\x00\x00\x00\x00fake"
        meta = store_generated_music_artifact(
            raw,
            mime="audio/mpeg",
            prompt="lofi beat",
            model="google/lyria-3-pro-preview",
            provider="navin",
        )
        self.assertTrue(meta["id"].startswith("mus_"))
        self.assertTrue(os.path.isfile(meta["path"]))
        result = generated_music_tool_result([meta])
        self.assertIn("artifacts", result)
        self.assertEqual(MusicGenerationTool.config_key, "music_generation")


class MusicGenerationDeadlineTest(unittest.TestCase):
    def test_a_stalled_stream_fails_cleanly_instead_of_hanging_forever(self) -> None:
        import asyncio

        from navin.providers.music_generation import (
            MusicGenerationError,
            OpenRouterMusicGenerationClient,
        )

        client = OpenRouterMusicGenerationClient(api_key="sk-test")
        client.default_max_wait = 0.05

        async def stalled(**kwargs):
            await asyncio.sleep(10)

        with mock.patch.object(client, "_generate", side_effect=stalled):
            with self.assertRaises(MusicGenerationError) as ctx:
                asyncio.run(
                    client.generate(prompt="lofi beat", model="google/lyria-3-clip-preview")
                )
        self.assertIn("did not complete within", str(ctx.exception))


class SettingsMusicApiTest(unittest.TestCase):
    def test_update_music_settings_roundtrip(self) -> None:
        from navin.webui.settings_api import update_music_generation_settings

        cfg = Config()
        cfg.providers.navin = ProviderConfig(api_key="sk-test")
        with mock.patch("navin.webui.settings_api.load_config", return_value=cfg), mock.patch(
            "navin.webui.settings_api.save_config"
        ) as save, mock.patch(
            "navin.webui.settings_api.settings_payload",
            return_value={"ok": True},
        ):
            payload = update_music_generation_settings(
                {
                    "enabled": ["true"],
                    "provider": ["navin"],
                    "model": ["google/lyria-3-pro-preview"],
                }
            )
        self.assertEqual(cfg.tools.music_generation.model, "google/lyria-3-pro-preview")
        self.assertTrue(save.called)
        self.assertEqual(payload, {"ok": True})


if __name__ == "__main__":
    unittest.main()
