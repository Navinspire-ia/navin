"""Media generation follows the provider credential unless the operator decides.

``tools.imageGeneration.enabled`` and ``tools.videoGeneration.enabled`` used to
default to ``false``, so an install with a working API key still answered every
studio brief with text only. They are now tri-state: unset resolves against the
selected provider's credential, while an explicit value always wins.
"""

import unittest
from pathlib import Path

from navin.agent.tools.context import ToolContext
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.registry import ToolRegistry
from navin.config.schema import ProviderConfig, ToolsConfig
from navin.providers.media_credentials import (
    media_credentials_ready,
    resolve_media_tool_enabled,
)


def _loaded(config: ToolsConfig, provider_configs: dict[str, ProviderConfig]) -> set[str]:
    ctx = ToolContext(
        config=config,
        workspace=str(Path.cwd()),
        image_generation_provider_configs=provider_configs,
    )
    return set(ToolLoader().load(ctx, ToolRegistry(), scope="core"))


def _keyed(api_key: str = "sk-test") -> dict[str, ProviderConfig]:
    return {
        "navin": ProviderConfig(api_key=api_key),
        "openrouter": ProviderConfig(api_key=api_key),
        "gemini": ProviderConfig(api_key=api_key),
    }


class CredentialReadinessTest(unittest.TestCase):
    def test_api_key_provider_needs_a_key(self) -> None:
        self.assertTrue(media_credentials_ready("openrouter", ProviderConfig(api_key="sk-x")))
        self.assertFalse(media_credentials_ready("openrouter", ProviderConfig()))
        self.assertFalse(media_credentials_ready("openrouter", None))

    def test_blank_key_is_not_a_credential(self) -> None:
        self.assertFalse(media_credentials_ready("openrouter", ProviderConfig(api_key="   ")))

    def test_direct_provider_needs_a_base_url(self) -> None:
        self.assertFalse(media_credentials_ready("custom", ProviderConfig(api_key="sk-x")))
        self.assertTrue(
            media_credentials_ready("custom", ProviderConfig(api_base="http://localhost:9000/v1"))
        )

    def test_local_provider_falls_back_to_its_default_base_url(self) -> None:
        self.assertTrue(media_credentials_ready("ollama", ProviderConfig()))
        self.assertTrue(media_credentials_ready("vllm", ProviderConfig()))
        self.assertTrue(media_credentials_ready("lm_studio", ProviderConfig()))

    def test_oauth_provider_requires_an_explicit_opt_in(self) -> None:
        """The token lives outside the config file, so auto mode cannot vouch for it."""
        self.assertFalse(media_credentials_ready("openai_codex", ProviderConfig()))

    def test_unknown_provider_accepts_either_credential(self) -> None:
        self.assertTrue(media_credentials_ready("made_up", ProviderConfig(api_key="sk-x")))
        self.assertFalse(media_credentials_ready("made_up", ProviderConfig()))


class ResolveEnabledTest(unittest.TestCase):
    def test_explicit_value_wins_over_the_credential(self) -> None:
        self.assertFalse(resolve_media_tool_enabled(False, True))
        self.assertTrue(resolve_media_tool_enabled(True, False))

    def test_unset_defers_to_the_credential(self) -> None:
        self.assertTrue(resolve_media_tool_enabled(None, True))
        self.assertFalse(resolve_media_tool_enabled(None, False))


def _with_provider(name: str) -> ToolsConfig:
    config = ToolsConfig()
    config.image_generation.provider = name
    config.video_generation.provider = name
    config.music_generation.provider = name
    return config


class MediaToolGateTest(unittest.TestCase):
    def test_configured_provider_registers_media_tools(self) -> None:
        loaded = _loaded(_with_provider("navin"), _keyed())
        self.assertIn("generate_image", loaded)
        self.assertIn("generate_video", loaded)
        self.assertIn("generate_music", loaded)

    def test_unset_provider_keeps_media_tools_out(self) -> None:
        """No provider picked means no implicit Navin, even with keys around."""
        loaded = _loaded(ToolsConfig(), _keyed())
        self.assertNotIn("generate_image", loaded)
        self.assertNotIn("generate_video", loaded)
        self.assertNotIn("generate_music", loaded)

    def test_missing_credential_keeps_media_tools_out(self) -> None:
        loaded = _loaded(ToolsConfig(), {})
        self.assertNotIn("generate_image", loaded)
        self.assertNotIn("generate_video", loaded)
        self.assertNotIn("generate_music", loaded)

    def test_operator_opt_out_survives_a_configured_provider(self) -> None:
        config = ToolsConfig()
        config.image_generation.enabled = False
        config.video_generation.enabled = False
        config.music_generation.enabled = False
        loaded = _loaded(config, _keyed())
        self.assertNotIn("generate_image", loaded)
        self.assertNotIn("generate_video", loaded)
        self.assertNotIn("generate_music", loaded)

    def test_credential_of_another_provider_does_not_count(self) -> None:
        config = ToolsConfig()
        config.image_generation.provider = "gemini"
        loaded = _loaded(config, {"openrouter": ProviderConfig(api_key="sk-x")})
        self.assertNotIn("generate_image", loaded)


class ConfigPersistenceTest(unittest.TestCase):
    def test_auto_mode_is_not_written_to_the_config_file(self) -> None:
        """Auto is the schema default, so ``navin.json`` keeps no opinion about it."""
        dumped = ToolsConfig().model_dump(mode="json", by_alias=True, exclude_defaults=True)
        self.assertNotIn("imageGeneration", dumped)
        self.assertNotIn("videoGeneration", dumped)

    def test_an_explicit_choice_is_written(self) -> None:
        config = ToolsConfig()
        config.image_generation.enabled = False
        dumped = config.model_dump(mode="json", by_alias=True, exclude_defaults=True)
        self.assertEqual(dumped["imageGeneration"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
