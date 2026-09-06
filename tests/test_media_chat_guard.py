"""Media generation models must never run chat/agent turns - in any mode.

2026-08-09 incident: a degraded catalog payload made google/lyria-3-clip-preview
(a music model) the chat default; every mode lost tool support. The runtime
resolver is the last line of defense, and it must block without collateral
damage: a user-added model on a custom provider, whatever its slug looks
like, keeps working in every mode.
"""

from __future__ import annotations

import unittest
from unittest import mock

from navin.agent.model_runtime import ModelRuntimeResolver
from navin.config.schema import ModelPresetConfig
from navin.providers.base import GenerationSettings
from navin.providers.managed_catalog import is_media_model_slug, known_media_slugs
from navin.utils.llm_runtime import LLMRuntime


def _runtime() -> LLMRuntime:
    return LLMRuntime(
        provider=mock.Mock(),
        model="deepseek/deepseek-v4-flash",
        generation=GenerationSettings(),
        context_window_tokens=131072,
        model_preset=None,
    )


class MediaSlugKnowledgeTest(unittest.TestCase):
    def test_the_embedded_media_catalog_is_recognized(self) -> None:
        slugs = known_media_slugs()
        for slug in (
            "google/lyria-3-clip-preview",
            "google/veo-3.1-fast",
            "google/gemini-3.1-flash-image",
            "google/gemini-3.1-flash-tts-preview",
            "qwen/qwen3-asr-flash-2026-02-10",
        ):
            self.assertIn(slug, slugs, slug)

    def test_heuristic_flags_lookalikes_but_not_plain_text_models(self) -> None:
        self.assertTrue(is_media_model_slug("someorg/lyria-x-preview"))
        self.assertFalse(is_media_model_slug("deepseek/deepseek-v4-flash"))
        self.assertFalse(is_media_model_slug("z-ai/glm-5.2"))
        self.assertFalse(is_media_model_slug(""))


class ResolverGuardTest(unittest.TestCase):
    def _resolver(self, presets: dict[str, ModelPresetConfig]) -> ModelRuntimeResolver:
        return ModelRuntimeResolver(_runtime(), model_presets=presets)

    def test_an_explicit_media_modality_is_blocked(self) -> None:
        resolver = self._resolver({
            "lyria": ModelPresetConfig(
                label="Lyria",
                model="google/lyria-3-clip-preview",
                provider="navin",
                modality="music",
            ),
        })
        with self.assertRaises(ValueError):
            resolver.select_preset("lyria")

    def test_a_managed_media_slug_is_blocked_even_with_lost_modality(self) -> None:
        # The incident shape: catalog sync stored the media model as text.
        resolver = self._resolver({
            "executor": ModelPresetConfig(
                label="Lyria 3",
                model="google/lyria-3-clip-preview",
                provider="navin",
                modality="text",
            ),
        })
        with self.assertRaises(ValueError):
            resolver.select_preset("executor")

    def test_a_custom_model_with_a_lookalike_slug_is_not_blocked(self) -> None:
        # Amazon Nova is a text family: "nova-3" in the slug must not be
        # enough to block a user's own model on a custom provider.
        resolver = self._resolver({
            "mine": ModelPresetConfig(
                label="My Nova",
                model="amazon/nova-3-lite",
                provider="openrouter",
                modality="text",
            ),
        })
        resolver._reject_media_preset("mine")  # must not raise

    def test_select_model_blocks_exact_media_slugs_only(self) -> None:
        resolver = self._resolver({})
        with self.assertRaises(ValueError):
            resolver.select_model("google/lyria-3-clip-preview")
        runtime = resolver.select_model("mycorp/transcribe-helper-llm")
        self.assertEqual(runtime.model, "mycorp/transcribe-helper-llm")

    def test_refresh_falls_back_when_the_active_preset_turns_media(self) -> None:
        presets = {
            "poisoned": ModelPresetConfig(
                label="Lyria 3",
                model="google/lyria-3-clip-preview",
                provider="navin",
                modality="music",
            ),
        }
        snapshot = mock.Mock(signature=("sig",))
        resolver = ModelRuntimeResolver(
            LLMRuntime(
                provider=mock.Mock(),
                model="google/lyria-3-clip-preview",
                generation=GenerationSettings(),
                context_window_tokens=131072,
                model_preset="poisoned",
            ),
            model_presets=presets,
            provider_snapshot_loader=lambda: snapshot,
        )
        healthy = _runtime()
        with mock.patch.object(resolver, "resolve_snapshot", return_value=healthy):
            runtime = resolver.refresh()
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.model, "deepseek/deepseek-v4-flash")
        self.assertIsNone(runtime.model_preset)


if __name__ == "__main__":
    unittest.main()
