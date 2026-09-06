"""Vision task route: image/video attachments → multimodal analysis model."""

from __future__ import annotations

import unittest

from navin.agent.model_routes import (
    media_needs_vision,
    resolve_vision_route,
)
from navin.config.schema import Config
from navin.providers.managed_catalog import (
    DEFAULT_VISION_MODEL,
    ECONOMY_VISION_MODEL,
    FALLBACK_CATALOG_PAYLOAD,
    FREE_VISION_MODEL,
    apply_catalog,
    parse_catalog,
    slug_preset_key,
)
from navin.webui.openrouter_oauth import (
    FREE_DEFAULT_MODELS,
    install_free_presets,
)
from navin.webui.openrouter_oauth import (
    FREE_VISION_MODEL as OAUTH_VISION,
)


class MediaNeedsVisionTest(unittest.TestCase):
    def test_images_and_videos(self) -> None:
        self.assertTrue(media_needs_vision(["/tmp/shot.png"]))
        self.assertTrue(media_needs_vision(["clip.MP4"]))
        self.assertTrue(media_needs_vision(["data:image/png;base64,AAAA"]))
        self.assertTrue(media_needs_vision(["data:video/mp4;base64,AAAA"]))

    def test_documents_do_not_trigger_vision(self) -> None:
        self.assertFalse(media_needs_vision(["report.pdf"]))
        self.assertFalse(media_needs_vision(["notes.txt"]))
        self.assertFalse(media_needs_vision([]))
        self.assertFalse(media_needs_vision(None))


class ResolveVisionRouteTest(unittest.TestCase):
    def test_returns_configured_vision_preset(self) -> None:
        routes = {"vision": "omni-free"}
        self.assertEqual(
            resolve_vision_route(
                ["shot.webp"],
                routes=routes,
                known_presets={"omni-free", "light"},
            ),
            "omni-free",
        )

    def test_skips_when_no_media(self) -> None:
        self.assertIsNone(
            resolve_vision_route([], routes={"vision": "omni-free"})
        )


class FreePresetsIncludeNewModelsTest(unittest.TestCase):
    def test_free_list_includes_super_and_omni(self) -> None:
        models = {m for m, _ in FREE_DEFAULT_MODELS}
        self.assertIn("nvidia/nemotron-3-super-120b-a12b:free", models)
        self.assertNotIn("openai/gpt-oss-20b:free", models)
        self.assertIn(OAUTH_VISION, models)
        self.assertEqual(OAUTH_VISION, FREE_VISION_MODEL)

    def test_install_wires_vision_route(self) -> None:
        config = Config()
        config.agents.defaults.model = ""
        config.agents.defaults.model_preset = ""
        install_free_presets(config)
        vision_key = config.model_routes.get("vision")
        self.assertIsNotNone(vision_key)
        preset = config.model_presets[vision_key]
        self.assertEqual(preset.model, FREE_VISION_MODEL)


class ManagedCatalogVisionTest(unittest.TestCase):
    def test_fallback_catalog_includes_free_models(self) -> None:
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        slugs = {m.slug for m in catalog.models}
        self.assertIn(FREE_VISION_MODEL, slugs)
        self.assertIn("nvidia/nemotron-3-super-120b-a12b:free", slugs)
        self.assertNotIn("openai/gpt-oss-20b:free", slugs)

    def test_apply_catalog_sets_vision_route(self) -> None:
        config = Config()
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD)
        apply_catalog(config, catalog, force_managed=True)
        key = slug_preset_key(DEFAULT_VISION_MODEL)
        self.assertEqual(config.model_routes.get("vision"), key)
        self.assertEqual(config.model_presets[key].model, DEFAULT_VISION_MODEL)
        self.assertTrue(
            any(
                p.model == "nvidia/nemotron-3-super-120b-a12b:free"
                for p in config.model_presets.values()
            )
        )
        self.assertIn(FREE_VISION_MODEL, {m.slug for m in catalog.models})

    def test_flash_plan_keeps_mimo_for_vision(self) -> None:
        # Grok is Plus+ only. Flash has no grok slug, so vision falls to MiMo.
        config = Config()
        catalog = parse_catalog(FALLBACK_CATALOG_PAYLOAD, plan="flash")
        apply_catalog(config, catalog, force_managed=True)
        self.assertNotIn("x-ai/grok-4.6", {m.slug for m in catalog.models})
        key = slug_preset_key(ECONOMY_VISION_MODEL)
        self.assertEqual(config.model_routes.get("vision"), key)
        self.assertEqual(config.model_presets[key].model, ECONOMY_VISION_MODEL)


if __name__ == "__main__":
    unittest.main()
