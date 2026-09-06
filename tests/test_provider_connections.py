"""Native region/plan/protocol connections for Chinese and custom providers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from navin.config.schema import Config, ModelPresetConfig, ProvidersConfig
from navin.providers.connection_presets import (
    lookup_connection_base,
    resolve_connection_api_base,
)
from navin.providers.factory import make_provider
from navin.optional_live import live_modules_available
from navin.providers.registry import PROVIDERS, find_by_name
from tests.provider_test_utils import isolated_provider_env


class ConnectionPresetTest(unittest.TestCase):
    def test_priority_hosts(self) -> None:
        self.assertEqual(
            lookup_connection_base("qwen", "singapore", "payg", "openai"),
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(
            lookup_connection_base("qwen", "china", "coding", "native"),
            "https://dashscope.aliyuncs.com/api/v1",
        )
        self.assertEqual(
            lookup_connection_base("deepseek", None, None, "anthropic"),
            "https://api.deepseek.com/anthropic",
        )
        self.assertEqual(
            lookup_connection_base("moonshot", "china", "payg", "openai"),
            "https://api.moonshot.cn/v1",
        )
        self.assertEqual(
            lookup_connection_base("minimax", "international", "token", "anthropic"),
            "https://api.minimax.io/anthropic",
        )
        self.assertEqual(
            lookup_connection_base("zai", "international", "coding", "openai"),
            "https://api.z.ai/api/coding/paas/v4",
        )
        self.assertEqual(
            lookup_connection_base("zhipu", "china", "coding", "openai"),
            "https://open.bigmodel.cn/api/coding/paas/v4",
        )
        self.assertEqual(
            lookup_connection_base("siliconflow", "international", "payg", "openai"),
            "https://api.siliconflow.com/v1",
        )

    def test_explicit_api_base_wins(self) -> None:
        cfg = Config().providers.qwen
        cfg.api_base = "https://example.test/v1"
        cfg.endpoint_region = "china"
        self.assertEqual(resolve_connection_api_base("qwen", cfg), "https://example.test/v1")

    def test_region_fills_when_base_is_empty(self) -> None:
        cfg = Config().providers.qwen
        cfg.endpoint_region = "us"
        cfg.wire_protocol = "openai"
        self.assertEqual(
            resolve_connection_api_base("qwen", cfg),
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        )


class RegistryParityTest(unittest.TestCase):
    def test_new_slots_exist(self) -> None:
        for name in (
            "qwen",
            "custom_anthropic",
            "volcengine",
            "hunyuan",
            "qianfan",
            "stepfun",
            "dashscope",
        ):
            self.assertIsNotNone(find_by_name(name), name)

    def test_schema_matches_registry(self) -> None:
        schema_fields = set(ProvidersConfig.model_fields)
        registry_names = {spec.name for spec in PROVIDERS}
        self.assertEqual(registry_names - schema_fields, set())
        missing = set() if live_modules_available() else {"navin"}
        self.assertEqual(schema_fields - registry_names, missing)


class FactoryConnectionTest(unittest.TestCase):
    def test_deepseek_anthropic_wire_uses_anthropic_backend(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.deepseek.api_key = "ds-test"
            config.providers.deepseek.wire_protocol = "anthropic"
            config.model_presets["t"] = ModelPresetConfig(
                provider="deepseek", model="deepseek-chat"
            )
            provider = make_provider(config, preset_name="t")
            self.assertEqual(provider.__class__.__name__, "AnthropicProvider")
            self.assertEqual(
                config.get_api_base("deepseek-chat", preset=config.model_presets["t"]),
                "https://api.deepseek.com/anthropic",
            )
            self.assertEqual(getattr(provider, "api_base", None), "https://api.deepseek.com/anthropic")

    def test_qwen_default_is_singapore_compatible(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.qwen.api_key = "sk-qwen"
            config.model_presets["t"] = ModelPresetConfig(provider="qwen", model="qwen-plus")
            self.assertEqual(
                config.get_api_base("qwen-plus", preset=config.model_presets["t"]),
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            )


class SettingsConnectionTest(unittest.TestCase):
    def setUp(self) -> None:
        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_qwen_row_exposes_native_connection(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import settings_payload

        save_config(Config())
        row = next(item for item in settings_payload()["providers"] if item["name"] == "qwen")
        self.assertIn("connection", row)
        self.assertEqual(row["endpoint_region"], "singapore")
        self.assertEqual(row["access_plan"], "payg")
        self.assertEqual(row["wire_protocol"], "openai")
        self.assertTrue(row["connection"]["docs_url"])

    def test_priority_providers_are_listed(self) -> None:
        from navin.config.loader import save_config
        from navin.providers.settings_order import is_retired_llm_provider
        from navin.webui.settings_api import settings_payload

        save_config(Config())
        names = {item["name"] for item in settings_payload()["providers"]}
        for name in (
            "qwen",
            "deepseek",
            "moonshot",
            "minimax",
            "zai",
            "custom",
            "custom_anthropic",
            "volcengine",
            "hunyuan",
            "qianfan",
            "stepfun",
            "siliconflow",
            "ollama",
            "vllm",
            "lm_studio",
        ):
            self.assertIn(name, names, name)
            self.assertFalse(is_retired_llm_provider(name), name)
        self.assertNotIn("dashscope", names)

    def test_update_fills_china_base(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import update_provider_settings

        save_config(Config())
        update_provider_settings(
            {
                "provider": ["qwen"],
                "api_key": ["sk-qwen-test"],
                "endpoint_region": ["china"],
                "access_plan": ["coding"],
                "wire_protocol": ["openai"],
            }
        )
        reloaded = load_config()
        self.assertEqual(reloaded.providers.qwen.endpoint_region, "china")
        self.assertEqual(reloaded.providers.qwen.access_plan, "coding")
        self.assertEqual(
            reloaded.providers.qwen.api_base,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def test_connection_probe_times_out_without_hanging(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import (
            _PROVIDER_PROBE_TIMEOUT_S,
            test_provider_connection,
        )

        save_config(Config())
        self.assertLessEqual(_PROVIDER_PROBE_TIMEOUT_S, 8.0)

        def _timeout(*_args, **_kwargs):
            raise httpx.TimeoutException("slow")

        with patch("navin.webui.settings_api.httpx.get", side_effect=_timeout):
            payload = test_provider_connection(
                {
                    "provider": ["deepseek"],
                    "api_key": ["sk-ds"],
                    "wire_protocol": ["openai"],
                }
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "error")
        self.assertIn("timed out", payload["message"].lower())
        self.assertLess(payload["latency_ms"], 2_000)

    def test_connection_probe_lists_models(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import test_provider_connection

        save_config(Config())

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}

        with patch("navin.webui.settings_api.httpx.get", return_value=_Resp()):
            payload = test_provider_connection(
                {
                    "provider": ["deepseek"],
                    "api_key": ["sk-ds"],
                }
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model_count"], 2)
        self.assertIn("chat", payload["capabilities"])
        self.assertEqual(payload["api_base"], "https://api.deepseek.com")


class CustomEndpointTest(unittest.TestCase):
    def test_custom_openai_builds_when_base_is_set(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.custom.api_base = "http://127.0.0.1:9000/v1"
            config.providers.custom.auth_mode = "none"
            config.model_presets["t"] = ModelPresetConfig(
                provider="custom", model="local-model"
            )
            provider = make_provider(config, preset_name="t")
            self.assertEqual(provider.__class__.__name__, "OpenAICompatProvider")
            self.assertEqual(
                config.get_api_base("local-model", preset=config.model_presets["t"]),
                "http://127.0.0.1:9000/v1",
            )

    def test_custom_openai_without_base_raises(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.model_presets["t"] = ModelPresetConfig(
                provider="custom", model="local-model"
            )
            with self.assertRaises(ValueError) as caught:
                make_provider(config, preset_name="t")
            self.assertIn("api_base", str(caught.exception))

    def test_custom_anthropic_builds_on_anthropic_backend(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.custom_anthropic.api_base = "http://127.0.0.1:8080"
            config.providers.custom_anthropic.api_key = "sk-ant-local"
            config.model_presets["t"] = ModelPresetConfig(
                provider="custom_anthropic", model="claude-local"
            )
            provider = make_provider(config, preset_name="t")
            self.assertEqual(provider.__class__.__name__, "AnthropicProvider")
            self.assertEqual(
                config.get_api_base("claude-local", preset=config.model_presets["t"]),
                "http://127.0.0.1:8080",
            )

    def test_custom_anthropic_without_base_raises(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.custom_anthropic.api_key = "sk-ant-local"
            config.model_presets["t"] = ModelPresetConfig(
                provider="custom_anthropic", model="claude-local"
            )
            with self.assertRaises(ValueError) as caught:
                make_provider(config, preset_name="t")
            self.assertIn("api_base", str(caught.exception))

    def test_custom_rows_need_a_base_url(self) -> None:
        from navin.config.loader import get_config_path, save_config, set_config_path
        from navin.webui.settings_api import settings_payload

        tmp = tempfile.TemporaryDirectory()
        previous = get_config_path()
        set_config_path(Path(tmp.name) / "config.json")
        try:
            save_config(Config())
            rows = {item["name"]: item for item in settings_payload()["providers"]}
            for name in ("custom", "custom_anthropic"):
                self.assertTrue(rows[name]["supports_auth_mode"], name)
                self.assertFalse(rows[name]["configured"], name)
                self.assertFalse(rows[name]["api_key_required"], name)
        finally:
            set_config_path(previous)
            tmp.cleanup()
