"""Cloud/local provider wiring: registry, matching, factory, defaults."""

from __future__ import annotations

import os
import unittest

from navin.config.schema import Config, ModelPresetConfig, ProviderConfig, ProvidersConfig
from navin.agent.loop import AgentLoop
from navin.config.loader import set_config_path
from navin.providers.factory import make_provider
from navin.optional_live import live_modules_available
from navin.providers.registry import PROVIDERS, find_by_name
from tests.provider_test_utils import isolated_provider_env

# Explicit provider + model + expected base (None = SDK default, e.g. OpenAI).
_PROVIDER_CASES: tuple[tuple[str, str, dict[str, str], str | None], ...] = (
    ("openai", "gpt-4o", {"api_key": "sk-test"}, None),
    ("anthropic", "claude-sonnet-4-5", {"api_key": "sk-ant-test"}, "https://api.anthropic.com"),
    ("mistral", "mistral-large-latest", {"api_key": "mst-test"}, "https://api.mistral.ai/v1"),
    ("nvidia", "nvidia/llama-3.1-nemotron-70b-instruct", {"api_key": "nvapi-test"}, "https://integrate.api.nvidia.com/v1"),
    ("huggingface", "meta-llama/Llama-3.1-8B-Instruct", {"api_key": "hf_test"}, "https://router.huggingface.co/v1"),
    ("groq", "llama-3.3-70b-versatile", {"api_key": "gsk-test"}, "https://api.groq.com/openai/v1"),
    ("openrouter", "anthropic/claude-3.5-sonnet", {"api_key": "sk-or-test"}, "https://openrouter.ai/api/v1"),
    ("deepseek", "deepseek-chat", {"api_key": "ds-test"}, "https://api.deepseek.com"),
    ("gemini", "gemini-2.0-flash", {"api_key": "gem-test"}, "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("xai", "grok-4.6", {"api_key": "xai-test"}, "https://api.x.ai/v1"),
    ("moonshot", "kimi-k2.5", {"api_key": "ms-test"}, "https://api.moonshot.ai/v1"),
    ("ollama", "llama3.2", {"api_base": "http://localhost:11434/v1"}, "http://localhost:11434/v1"),
    ("vllm", "served-model", {"api_base": "http://127.0.0.1:8000/v1"}, "http://127.0.0.1:8000/v1"),
    # Keyless by default: a fresh OmniRoute install answers on its own port.
    ("omniroute", "auto", {}, "http://localhost:20128/v1"),
)


def _provider_base(provider: object) -> str | None:
    return getattr(provider, "_effective_base", None) or getattr(provider, "api_base", None)


class RegistrySchemaParityTest(unittest.TestCase):
    def test_every_registry_provider_has_a_schema_field(self) -> None:
        schema_fields = set(ProvidersConfig.model_fields)
        registry_names = {spec.name for spec in PROVIDERS}
        self.assertEqual(registry_names - schema_fields, set())
        missing = set() if live_modules_available() else {"navin"}
        self.assertEqual(schema_fields - registry_names, missing)


class ExplicitProviderFactoryTest(unittest.TestCase):
    def test_each_major_provider_builds_with_expected_base(self) -> None:
        with isolated_provider_env():
            for name, model, fields, expected_base in _PROVIDER_CASES:
                with self.subTest(provider=name):
                    config = Config()
                    provider_cfg = getattr(config.providers, name)
                    for key, value in fields.items():
                        setattr(provider_cfg, key, value)
                    config.model_presets["t"] = ModelPresetConfig(provider=name, model=model)
                    provider = make_provider(config, preset_name="t")
                    self.assertEqual(
                        config.get_provider_name(model, preset=config.model_presets["t"]),
                        name,
                    )
                    self.assertEqual(
                        config.get_api_base(model, preset=config.model_presets["t"]),
                        expected_base,
                    )
                    if expected_base is not None:
                        self.assertEqual(_provider_base(provider), expected_base)


class AutoMatchingTest(unittest.TestCase):
    def test_keyword_routes_to_the_provider_holding_the_key(self) -> None:
        with isolated_provider_env():
            cases = (
                ("nemotron-70b", "nvidia", "nvapi-x"),
                ("gpt-4o", "openai", "sk-x"),
                ("claude-sonnet", "anthropic", "sk-ant-x"),
                ("mistral-large", "mistral", "m-x"),
                ("codestral-latest", "mistral", "m-x"),
                ("magistral-medium", "mistral", "m-x"),
                ("gemma-2-9b", "gemini", "g-x"),
                ("deepseek-chat", "deepseek", "d-x"),
                ("kimi-k2.5", "moonshot", "k-x"),
                ("glm-4.5", "zai", "z-x"),
                ("grok-4.6", "xai", "xai-x"),
            )
            for model, provider, key in cases:
                with self.subTest(model=model):
                    config = Config()
                    getattr(config.providers, provider).api_key = key
                    self.assertEqual(config.get_provider_name(model), provider)

    def test_openrouter_model_id_stays_on_openrouter_when_pinned(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.anthropic.api_key = "sk-ant"
            config.providers.openrouter.api_key = "sk-or-x"
            preset = ModelPresetConfig(
                provider="openrouter",
                model="anthropic/claude-sonnet-4",
            )
            self.assertEqual(config.get_provider_name(preset.model, preset=preset), "openrouter")
            provider = make_provider(config, preset=preset)
            self.assertEqual(_provider_base(provider), "https://openrouter.ai/api/v1")


class EnvAliasTest(unittest.TestCase):
    def test_common_env_aliases_resolve_api_keys(self) -> None:
        cases = (
            ("moonshot", "KIMI_API_KEY", "kimi-k2.5"),
            ("huggingface", "HUGGINGFACE_TOKEN", "huggingface/foo"),
            ("nvidia", "NVIDIA_API_KEY", "nvidia/foo"),
            ("zai", "GLM_API_KEY", "glm-4.5"),
            ("gemini", "GOOGLE_API_KEY", "gemini-2.0-flash"),
        )
        with isolated_provider_env():
            for provider, env_name, model in cases:
                with self.subTest(env=env_name):
                    os.environ[env_name] = f"env-{env_name}"
                    try:
                        config = Config()
                        self.assertEqual(config.get_provider_name(model), provider)
                        self.assertEqual(config.get_api_key(model), f"env-{env_name}")
                    finally:
                        os.environ.pop(env_name, None)


class HiddenNavinBootTest(unittest.TestCase):
    def test_leftover_navin_preset_uses_openrouter_on_public_tree(self) -> None:
        if live_modules_available():
            self.skipTest("live account enabled")
        with isolated_provider_env():
            config = Config()
            config.agents.defaults.provider = "navin"
            config.agents.defaults.model = "z-ai/glm-5.3-flash"
            config.providers.navin = ProviderConfig(
                api_key="sk-or-leftover",
                api_base="https://openrouter.ai/api/v1",
            )
            self.assertEqual(config.get_provider_name("z-ai/glm-5.3-flash"), "openrouter")
            provider = make_provider(config)
            self.assertNotEqual(type(provider).__name__, "UnconfiguredProvider")

    def test_from_config_starts_when_navin_has_no_key(self) -> None:
        if live_modules_available():
            self.skipTest("live account enabled")
        import tempfile
        from pathlib import Path

        with isolated_provider_env():
            with tempfile.TemporaryDirectory() as tmp:
                set_config_path(Path(tmp) / "config.json")
                try:
                    config = Config()
                    config.agents.defaults.provider = "navin"
                    config.agents.defaults.model = "z-ai/glm-5.3-flash"
                    loop = AgentLoop.from_config(config)
                    self.assertEqual(type(loop.provider).__name__, "UnconfiguredProvider")
                finally:
                    set_config_path(Path.home() / ".navin" / "config.json")


class MistralQuirksTest(unittest.TestCase):
    def test_reasoning_quirks_are_wired(self) -> None:
        spec = find_by_name("mistral")
        assert spec is not None
        self.assertTrue(spec.reasoning_effort_remap)
        self.assertIn("magistral", spec.implicit_reasoning_models)
        self.assertTrue(spec.extract_thinking_blocks)
        self.assertTrue(spec.strip_history_reasoning_content)


if __name__ == "__main__":
    unittest.main()
