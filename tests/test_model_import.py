"""Tests for bulk model configuration import in the WebUI settings API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import ProviderConfig
from navin.webui.settings_api import (
    WebUISettingsError,
    _snap_context_window_tokens,
    import_model_configurations,
)


def _query(**kwargs: str) -> dict[str, list[str]]:
    return {key: [value] for key, value in kwargs.items()}


class ImportModelConfigurationsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        config = load_config()
        config.providers.anthropic = ProviderConfig(api_key="sk-test-key")
        save_config(config)

    def tearDown(self):
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_imports_every_selected_model_in_one_call(self):
        payload = import_model_configurations(
            _query(
                provider="anthropic",
                models=json.dumps(["claude-opus-5", "claude-sonnet-5", "claude-fable-5"]),
            )
        )
        self.assertEqual(payload["model_import"]["imported"], 3)
        self.assertEqual(payload["model_import"]["skipped"], 0)

        presets = load_config().model_presets
        self.assertEqual(
            {preset.model for preset in presets.values()},
            {"claude-opus-5", "claude-sonnet-5", "claude-fable-5"},
        )
        self.assertIn("claude-opus-5", presets)
        self.assertTrue(all(p.provider == "anthropic" for p in presets.values()))

    def test_import_does_not_change_the_active_preset(self):
        import_model_configurations(
            _query(provider="anthropic", models=json.dumps(["claude-opus-5"]))
        )
        self.assertIsNone(load_config().agents.defaults.model_preset)

    def test_models_already_saved_are_skipped(self):
        import_model_configurations(
            _query(provider="anthropic", models=json.dumps(["claude-opus-5"]))
        )
        payload = import_model_configurations(
            _query(provider="anthropic", models=json.dumps(["claude-opus-5", "claude-sonnet-5"]))
        )
        self.assertEqual(payload["model_import"]["imported"], 1)
        self.assertEqual(payload["model_import"]["skipped"], 1)
        self.assertEqual(len(load_config().model_presets), 2)

    def test_object_rows_carry_label_and_context_window(self):
        import_model_configurations(
            _query(
                provider="anthropic",
                models=json.dumps(
                    [{"id": "claude-opus-5", "label": "Claude Opus 5", "context_window": 1_048_576}]
                ),
            )
        )
        preset = load_config().model_presets["claude-opus-5"]
        self.assertEqual(preset.label, "Claude Opus 5")
        self.assertEqual(preset.context_window_tokens, 1_000_000)

    def test_colliding_slugs_are_disambiguated(self):
        import_model_configurations(
            _query(provider="anthropic", models=json.dumps(["gpt/5", "gpt:5"]))
        )
        self.assertEqual(sorted(load_config().model_presets), ["gpt-5", "gpt-5-2"])

    def test_unconfigured_provider_is_rejected(self):
        with self.assertRaises(WebUISettingsError):
            import_model_configurations(
                _query(provider="openai", models=json.dumps(["gpt-5"]))
            )

    def test_malformed_payloads_are_rejected(self):
        for models in ("", "not-json", "{}", "[]", json.dumps([1, None])):
            with self.subTest(models=models):
                with self.assertRaises(WebUISettingsError):
                    import_model_configurations(_query(provider="anthropic", models=models))

    def test_oversized_import_is_rejected(self):
        models = json.dumps([f"model-{i}" for i in range(201)])
        with self.assertRaises(WebUISettingsError):
            import_model_configurations(_query(provider="anthropic", models=models))


class SnapContextWindowTest(unittest.TestCase):
    def test_rounds_down_to_an_allowed_option(self):
        self.assertEqual(_snap_context_window_tokens(128_000), 65_536)
        self.assertEqual(_snap_context_window_tokens(200_000), 200_000)
        self.assertEqual(_snap_context_window_tokens(1_048_576), 1_000_000)

    def test_smaller_than_every_option_uses_the_smallest(self):
        self.assertEqual(_snap_context_window_tokens(8_192), 65_536)

    def test_invalid_values_are_ignored(self):
        for value in (None, 0, -1, True, "200000", 3.5):
            with self.subTest(value=value):
                self.assertIsNone(_snap_context_window_tokens(value))


if __name__ == "__main__":
    unittest.main()
