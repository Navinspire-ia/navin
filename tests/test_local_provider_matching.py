# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Local provider auto-routing must not steal cloud models or invent endpoints."""

from __future__ import annotations

import unittest

from navin.config.schema import Config, ModelPresetConfig
from navin.providers.factory import make_provider
from tests.provider_test_utils import isolated_provider_env


class LocalProviderMatchingTest(unittest.TestCase):
    def test_unconfigured_ollama_does_not_claim_nemotron(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertIsNone(config.get_provider_name("nemotron-70b"))

    def test_nvidia_key_wins_nemotron_over_empty_ollama(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.nvidia.api_key = "nvapi-test"
            self.assertEqual(config.get_provider_name("nemotron-70b"), "nvidia")

    def test_nvidia_key_wins_even_when_ollama_is_configured(self) -> None:
        """Keyword order must not prefer an opted-in Ollama for NVIDIA Nemotron ids."""
        with isolated_provider_env():
            config = Config()
            config.providers.nvidia.api_key = "nvapi-test"
            config.providers.ollama.api_base = "http://localhost:11434/v1"
            self.assertEqual(config.get_provider_name("nemotron-70b"), "nvidia")

    def test_ollama_prefix_uses_default_base_without_config(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertEqual(config.get_provider_name("ollama/llama3.2"), "ollama")
            self.assertEqual(config.get_api_base("ollama/llama3.2"), "http://localhost:11434/v1")

    def test_plain_local_model_needs_configured_api_base(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertIsNone(config.get_provider_name("llama3.2"))

            config.providers.ollama.api_base = "http://localhost:11434/v1"
            self.assertEqual(config.get_provider_name("llama3.2"), "ollama")
            self.assertEqual(config.get_api_base("llama3.2"), "http://localhost:11434/v1")

    def test_vllm_prefix_without_api_base_does_not_match(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertIsNone(config.get_provider_name("vllm/served-model"))

    def test_vllm_matches_when_api_base_is_set(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.vllm.api_base = "http://127.0.0.1:8000/v1"
            self.assertEqual(config.get_provider_name("served-model"), "vllm")
            self.assertEqual(config.get_api_base("served-model"), "http://127.0.0.1:8000/v1")

    def test_forced_vllm_without_api_base_raises(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.agents.defaults.model = "served-model"
            config.agents.defaults.provider = "vllm"
            with self.assertRaises(ValueError) as caught:
                make_provider(config)
            self.assertIn("api_base", str(caught.exception))

    def test_forced_ollama_uses_default_base(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.model_presets["local"] = ModelPresetConfig(
                provider="ollama",
                model="llama3.2",
            )
            provider = make_provider(config, preset_name="local")
            self.assertEqual(provider._effective_base, "http://localhost:11434/v1")
            self.assertTrue(provider._is_local)


if __name__ == "__main__":
    unittest.main()
