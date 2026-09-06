"""Phase 1: Auth None / Bearer for local OpenAI-compatible providers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.config.schema import (
    Config,
    ProviderConfig,
    provider_supports_auth_mode,
    resolve_provider_auth_mode,
)


class ResolveAuthModeTest(unittest.TestCase):
    def test_legacy_empty_key_is_none(self) -> None:
        self.assertEqual(resolve_provider_auth_mode(ProviderConfig()), "none")

    def test_legacy_key_is_bearer(self) -> None:
        self.assertEqual(
            resolve_provider_auth_mode(ProviderConfig(api_key="sk-local")),
            "bearer",
        )

    def test_explicit_none_wins_even_with_key(self) -> None:
        # Persistence layer clears the key when saving none; resolution still
        # honors an explicit mode if present.
        self.assertEqual(
            resolve_provider_auth_mode(
                ProviderConfig(api_key="stale", auth_mode="none")
            ),
            "none",
        )

    def test_explicit_bearer_without_key(self) -> None:
        self.assertEqual(
            resolve_provider_auth_mode(ProviderConfig(auth_mode="bearer")),
            "bearer",
        )

    def test_support_is_limited_to_local_presets(self) -> None:
        for name in (
            "ollama",
            "vllm",
            "lm_studio",
            "omniroute",
            "custom",
            "custom_anthropic",
            "lm-studio",
        ):
            self.assertTrue(provider_supports_auth_mode(name), name)
        for name in ("openai", "anthropic", "openrouter", "navin", "gemini"):
            self.assertFalse(provider_supports_auth_mode(name), name)


class ProviderAuthModeSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def _row(self, name: str) -> dict:
        from navin.webui.settings_api import settings_payload

        payload = settings_payload()
        row = next(p for p in payload["providers"] if p["name"] == name)
        return row

    def test_payload_exposes_auth_mode_only_for_supported_providers(self) -> None:
        from navin.config.loader import save_config

        config = Config()
        config.providers.ollama.api_key = None
        config.providers.lm_studio.api_key = "lm_token"
        save_config(config)

        ollama = self._row("ollama")
        self.assertTrue(ollama["supports_auth_mode"])
        self.assertEqual(ollama["auth_mode"], "none")
        # Empty local slot stays unconfigured until the user saves an endpoint
        # (default_api_base alone does not mark configured - unchanged).
        self.assertFalse(ollama["configured"])

        lm = self._row("lm_studio")
        self.assertTrue(lm["supports_auth_mode"])
        self.assertEqual(lm["auth_mode"], "bearer")
        self.assertTrue(lm["configured"])  # legacy key alone still configures

        openai = self._row("openai")
        self.assertFalse(openai.get("supports_auth_mode"))
        self.assertNotIn("auth_mode", openai)

    def test_ollama_none_auth_with_endpoint_is_configured(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import update_provider_settings

        save_config(Config())
        update_provider_settings(
            {
                "provider": ["ollama"],
                "auth_mode": ["none"],
                "api_base": ["http://localhost:11434/v1"],
            }
        )
        row = self._row("ollama")
        self.assertEqual(row["auth_mode"], "none")
        self.assertTrue(row["configured"])
        self.assertIsNone(row["api_key_hint"])

    def test_save_auth_mode_none_clears_api_key(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import update_provider_settings

        config = Config()
        config.providers.vllm.api_key = "secret"
        config.providers.vllm.api_base = "http://localhost:8000/v1"
        save_config(config)

        update_provider_settings(
            {
                "provider": ["vllm"],
                "auth_mode": ["none"],
                "api_base": ["http://localhost:8000/v1"],
            }
        )
        reloaded = load_config()
        self.assertEqual(reloaded.providers.vllm.auth_mode, "none")
        self.assertIsNone(reloaded.providers.vllm.api_key)

    def test_save_auth_mode_bearer_persists_token(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import update_provider_settings

        config = Config()
        save_config(config)

        update_provider_settings(
            {
                "provider": ["lm_studio"],
                "auth_mode": ["bearer"],
                "api_key": ["lm_xxxxxxxxx"],
                "api_base": ["http://192.168.10.15:1234/v1"],
            }
        )
        reloaded = load_config()
        self.assertEqual(reloaded.providers.lm_studio.auth_mode, "bearer")
        self.assertEqual(reloaded.providers.lm_studio.api_key, "lm_xxxxxxxxx")
        self.assertEqual(
            reloaded.providers.lm_studio.api_base,
            "http://192.168.10.15:1234/v1",
        )
        row = self._row("lm_studio")
        self.assertEqual(row["auth_mode"], "bearer")
        self.assertTrue(row["configured"])

    def test_auth_mode_rejected_for_cloud_provider(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import WebUISettingsError, update_provider_settings

        save_config(Config())
        with self.assertRaises(WebUISettingsError):
            update_provider_settings(
                {"provider": ["openai"], "auth_mode": ["bearer"]}
            )


if __name__ == "__main__":
    unittest.main()
