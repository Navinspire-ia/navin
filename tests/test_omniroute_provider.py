# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OmniRoute: local free AI gateway wired as a keyless OpenAI-compatible provider."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.config.schema import Config, ModelPresetConfig, provider_supports_auth_mode
from navin.providers.factory import make_provider
from navin.providers.registry import find_by_name
from navin.providers.settings_order import settings_provider_rank
from tests.provider_test_utils import isolated_provider_env

OMNIROUTE_BASE = "http://localhost:20128/v1"


class OmniRouteRegistryTest(unittest.TestCase):
    def test_spec_is_a_local_keyless_openai_compatible_gateway(self) -> None:
        spec = find_by_name("omniroute")
        assert spec is not None
        self.assertEqual(spec.label, "OmniRoute")
        self.assertEqual(spec.backend, "openai_compat")
        self.assertTrue(spec.is_local)
        self.assertFalse(spec.is_oauth)
        self.assertFalse(spec.is_transcription_only)
        self.assertEqual(spec.default_api_base, OMNIROUTE_BASE)
        self.assertTrue(spec.route_via_default_base)
        self.assertEqual(spec.env_key, "OMNIROUTE_API_KEY")
        self.assertEqual(spec.detect_by_base_keyword, "20128")

    def test_only_the_navin_routing_prefix_is_stripped(self) -> None:
        spec = find_by_name("omniroute")
        assert spec is not None
        # OmniRoute routes on the upstream prefix (openai/..., cc/...), so a
        # blanket strip would break every non-auto model id.
        self.assertFalse(spec.strip_model_prefix)
        self.assertEqual(spec.strip_model_prefixes, ("omniroute",))

    def test_settings_rank_sits_next_to_openrouter(self) -> None:
        self.assertEqual(
            settings_provider_rank("omniroute"),
            settings_provider_rank("openrouter") + 1,
        )
        self.assertLess(settings_provider_rank("omniroute"), settings_provider_rank("custom"))

    def test_auth_none_or_bearer_is_offered(self) -> None:
        self.assertTrue(provider_supports_auth_mode("omniroute"))


class OmniRouteRoutingTest(unittest.TestCase):
    def test_pinned_preset_builds_without_a_key_on_the_default_port(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.model_presets["free"] = ModelPresetConfig(provider="omniroute", model="auto")
            provider = make_provider(config, preset_name="free")
            self.assertEqual(provider._effective_base, OMNIROUTE_BASE)
            self.assertTrue(provider._is_local)
            self.assertEqual(provider._api_key_for_client, "no-key")
            self.assertEqual(
                config.get_provider_name("auto", preset=config.model_presets["free"]),
                "omniroute",
            )

    def test_prefix_uses_the_default_base_without_config(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertEqual(config.get_provider_name("omniroute/auto"), "omniroute")
            self.assertEqual(config.get_api_base("omniroute/auto"), OMNIROUTE_BASE)

    def test_configured_base_and_key_are_honored(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.omniroute.api_base = "http://192.168.1.20:20128/v1"
            config.providers.omniroute.api_key = "sk-omni-test"
            config.model_presets["lan"] = ModelPresetConfig(
                provider="omniroute", model="auto/coding"
            )
            provider = make_provider(config, preset_name="lan")
            self.assertEqual(provider._effective_base, "http://192.168.1.20:20128/v1")
            self.assertEqual(provider._api_key_for_client, "sk-omni-test")
            self.assertTrue(provider._is_local)

    def test_env_key_is_picked_up(self) -> None:
        with isolated_provider_env():
            with patch.dict("os.environ", {"OMNIROUTE_API_KEY": "sk-from-env"}):
                config = Config()
                preset = ModelPresetConfig(provider="omniroute", model="auto")
                self.assertEqual(config.get_api_key("auto", preset=preset), "sk-from-env")

    def test_wire_model_keeps_upstream_prefixes(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.model_presets["free"] = ModelPresetConfig(provider="omniroute", model="auto")
            provider = make_provider(config, preset_name="free")
            cases = {
                "auto": "auto",
                "auto/coding": "auto/coding",
                "omniroute/auto": "auto",
                "omniroute/auto/fast": "auto/fast",
                "openai/gpt-5.4": "openai/gpt-5.4",
                "cc/claude-sonnet-4-6": "cc/claude-sonnet-4-6",
                "oc/kimi-k2.5": "oc/kimi-k2.5",
            }
            for given, expected in cases.items():
                with self.subTest(model=given):
                    self.assertEqual(provider._request_model_name(given), expected)

    def test_unconfigured_omniroute_does_not_steal_cloud_models(self) -> None:
        with isolated_provider_env():
            config = Config()
            self.assertIsNone(config.get_provider_name("gpt-4o"))
            self.assertIsNone(config.get_provider_name("auto"))

            config.providers.openai.api_key = "sk-openai"
            config.providers.omniroute.api_base = OMNIROUTE_BASE
            self.assertEqual(config.get_provider_name("gpt-4o"), "openai")

    def test_configured_omniroute_is_the_local_fallback_for_bare_ids(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.omniroute.api_base = OMNIROUTE_BASE
            self.assertEqual(config.get_provider_name("auto"), "omniroute")
            self.assertEqual(config.get_api_base("auto"), OMNIROUTE_BASE)

    def test_pinned_preset_wins_when_several_local_servers_are_configured(self) -> None:
        with isolated_provider_env():
            config = Config()
            config.providers.ollama.api_base = "http://localhost:11434/v1"
            config.providers.omniroute.api_base = OMNIROUTE_BASE
            # Bare ids follow registry order (Ollama first); the Settings >
            # Models flow always pins the provider, which must win outright.
            self.assertEqual(config.get_provider_name("auto"), "ollama")
            pinned = ModelPresetConfig(provider="omniroute", model="auto")
            self.assertEqual(config.get_provider_name("auto", preset=pinned), "omniroute")
            self.assertEqual(config.get_api_base("auto", preset=pinned), OMNIROUTE_BASE)


class _SettingsCase(unittest.TestCase):
    """Isolated config file + clean provider env for Settings API calls."""

    def setUp(self) -> None:
        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        self._env = isolated_provider_env()
        self._env.__enter__()

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        self._env.__exit__(None, None, None)
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def _row(self, name: str) -> dict:
        from navin.webui.settings_api import settings_payload

        payload = settings_payload()
        return next(p for p in payload["providers"] if p["name"] == name)


class OmniRouteSettingsTest(_SettingsCase):
    def test_settings_row_is_a_keyless_local_slot(self) -> None:
        from navin.config.loader import save_config

        save_config(Config())
        row = self._row("omniroute")
        self.assertEqual(row["label"], "OmniRoute")
        self.assertFalse(row["api_key_required"])
        self.assertEqual(row["default_api_base"], OMNIROUTE_BASE)
        self.assertEqual(row["model_catalog"], "local")
        self.assertTrue(row["model_selectable"])
        self.assertTrue(row["supports_auth_mode"])
        self.assertEqual(row["auth_mode"], "none")
        self.assertFalse(row["configured"])

    def test_saving_the_endpoint_without_a_key_configures_it(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import update_provider_settings

        save_config(Config())
        update_provider_settings(
            {
                "provider": ["omniroute"],
                "auth_mode": ["none"],
                "api_base": [OMNIROUTE_BASE],
            }
        )
        reloaded = load_config()
        self.assertEqual(reloaded.providers.omniroute.api_base, OMNIROUTE_BASE)
        self.assertIsNone(reloaded.providers.omniroute.api_key)
        row = self._row("omniroute")
        self.assertTrue(row["configured"])
        self.assertEqual(row["auth_mode"], "none")

    def test_bearer_mode_persists_the_gateway_key(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import update_provider_settings

        save_config(Config())
        update_provider_settings(
            {
                "provider": ["omniroute"],
                "auth_mode": ["bearer"],
                "api_key": ["sk-omni-dashboard"],
                "api_base": [OMNIROUTE_BASE],
            }
        )
        reloaded = load_config()
        self.assertEqual(reloaded.providers.omniroute.auth_mode, "bearer")
        self.assertEqual(reloaded.providers.omniroute.api_key, "sk-omni-dashboard")

    def test_model_list_probes_the_default_port_without_a_bearer_header(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import provider_models_payload

        save_config(Config())
        seen: dict[str, object] = {}

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "object": "list",
                    "data": [
                        {"id": "auto", "object": "model", "owned_by": "omniroute"},
                        {"id": "auto/coding", "object": "model", "owned_by": "omniroute"},
                        {"id": "oc/kimi-k2.5", "object": "model", "owned_by": "opencode"},
                    ],
                }

        def _get(url, headers=None, params=None, timeout=None, follow_redirects=None):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            return _Resp()

        with patch("navin.webui.settings_api.httpx.get", side_effect=_get):
            payload = provider_models_payload({"provider": ["omniroute"]})

        self.assertEqual(seen["url"], f"{OMNIROUTE_BASE}/models")
        self.assertNotIn("Authorization", seen["headers"])
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["catalog_kind"], "local")
        self.assertEqual(payload["model_count"], 3)
        self.assertEqual([row["id"] for row in payload["models"]][:2], ["auto", "auto/coding"])

    def test_connection_probe_sends_the_key_when_require_api_key_is_on(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import test_provider_connection

        save_config(Config())
        seen: dict[str, object] = {}

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"data": [{"id": "auto"}]}

        def _get(url, headers=None, params=None, timeout=None, follow_redirects=None):
            seen["url"] = url
            seen["headers"] = dict(headers or {})
            return _Resp()

        with patch("navin.webui.settings_api.httpx.get", side_effect=_get):
            payload = test_provider_connection(
                {"provider": ["omniroute"], "api_key": ["sk-omni-dashboard"]}
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_base"], OMNIROUTE_BASE)
        self.assertEqual(seen["headers"].get("Authorization"), "Bearer sk-omni-dashboard")
        self.assertEqual(payload["model_count"], 1)

    def test_connection_probe_reports_a_stopped_server(self) -> None:
        import httpx

        from navin.config.loader import save_config
        from navin.webui.settings_api import test_provider_connection

        save_config(Config())

        def _refused(*_args, **_kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("navin.webui.settings_api.httpx.get", side_effect=_refused):
            payload = test_provider_connection({"provider": ["omniroute"]})

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "error")
        self.assertIn("Connection refused", payload["message"])


def _unauthorized(url: str):
    """OmniRoute 3.8.x: ``/v1/models`` is 401 until a dashboard key exists."""
    import httpx

    request = httpx.Request("GET", url)
    response = httpx.Response(
        401,
        request=request,
        json={"error": {"message": "Authentication required", "type": "invalid_api_key"}},
    )
    return httpx.HTTPStatusError("401", request=request, response=response)


class _JsonResp:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


CANDIDATES_BODY = {
    "channel": "auto",
    "candidates": [
        {
            "provider": "opencode",
            "connectionId": "noauth",
            "model": "oc/big-pickle",
            "modelStr": "oc/big-pickle",
            "excluded": False,
            "reachable": True,
        },
        {
            "provider": "opencode",
            "connectionId": "noauth",
            "model": "oc/deepseek-v4-flash-free",
            "modelStr": "oc/deepseek-v4-flash-free",
            "excluded": False,
            "reachable": True,
        },
        {
            "provider": "kiro",
            "connectionId": "acct-1",
            "model": "kiro/claude-sonnet-4-6",
            "modelStr": "kiro/claude-sonnet-4-6",
            "excluded": True,
            "reachable": False,
        },
        {
            "provider": "opencode",
            "connectionId": "noauth",
            "model": "oc/big-pickle",
            "modelStr": "oc/big-pickle",
            "excluded": False,
            "reachable": True,
        },
    ],
}


def _omniroute_server(seen: list[tuple[str, dict]]):
    """A fake OmniRoute: authenticated /v1/models, public auto-combo candidates."""

    def _get(url, headers=None, params=None, timeout=None, follow_redirects=None):
        seen.append((url, dict(headers or {})))
        if url.endswith("/v1/models"):
            raise _unauthorized(url)
        if url.endswith("/v1/auto-combo/auto/candidates"):
            return _JsonResp(CANDIDATES_BODY)
        raise AssertionError(f"unexpected probe {url}")

    return _get


class OmniRouteKeylessCatalogTest(unittest.TestCase):
    def test_root_strips_the_v1_suffix(self) -> None:
        from navin.providers.omniroute import omniroute_root

        self.assertEqual(omniroute_root("http://localhost:20128/v1"), "http://localhost:20128")
        self.assertEqual(omniroute_root("http://localhost:20128/v1/"), "http://localhost:20128")
        self.assertEqual(omniroute_root("http://10.0.0.5:20128"), "http://10.0.0.5:20128")

    def test_catalog_lists_combos_then_the_reachable_pool(self) -> None:
        from navin.providers.omniroute import OMNIROUTE_AUTO_COMBOS, omniroute_keyless_catalog

        seen: list[tuple[str, dict]] = []
        with patch("navin.providers.omniroute.httpx.get", side_effect=_omniroute_server(seen)):
            rows = omniroute_keyless_catalog(OMNIROUTE_BASE)

        assert rows is not None
        ids = [row["id"] for row in rows]
        combo_ids = [combo[0] for combo in OMNIROUTE_AUTO_COMBOS]
        self.assertEqual(ids[: len(combo_ids)], combo_ids)
        self.assertIn("auto/coding", ids)
        # Excluded candidates are dropped and duplicates collapse.
        self.assertEqual(ids[len(combo_ids) :], ["oc/big-pickle", "oc/deepseek-v4-flash-free"])
        pickle = next(row for row in rows if row["id"] == "oc/big-pickle")
        self.assertTrue(pickle["free"])
        self.assertEqual(pickle["owned_by"], "opencode")
        self.assertEqual(seen[0][0], "http://localhost:20128/v1/auto-combo/auto/candidates")

    def test_catalog_is_none_when_the_server_is_down(self) -> None:
        import httpx

        from navin.providers.omniroute import omniroute_keyless_catalog

        def _refused(*_args, **_kwargs):
            raise httpx.ConnectError("Connection refused")

        with patch("navin.providers.omniroute.httpx.get", side_effect=_refused):
            self.assertIsNone(omniroute_keyless_catalog(OMNIROUTE_BASE))


class OmniRouteKeylessSettingsTest(_SettingsCase):
    """Settings must not call a working keyless gateway 'credential rejected'."""

    def test_model_list_falls_back_to_the_public_catalog_on_401(self) -> None:
        from navin.config.loader import save_config
        from navin.providers.omniroute import OMNIROUTE_KEYLESS_MESSAGE
        from navin.webui.settings_api import provider_models_payload

        save_config(Config())
        seen: list[tuple[str, dict]] = []
        with patch("navin.webui.settings_api.httpx.get", side_effect=_omniroute_server(seen)):
            payload = provider_models_payload({"provider": ["omniroute"]})

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["message"], OMNIROUTE_KEYLESS_MESSAGE)
        ids = [row["id"] for row in payload["models"]]
        self.assertEqual(ids[0], "auto")
        self.assertIn("oc/big-pickle", ids)
        self.assertEqual(payload["model_count"], len(ids))
        self.assertEqual([url for url, _ in seen][:2], [
            f"{OMNIROUTE_BASE}/models",
            "http://localhost:20128/v1/auto-combo/auto/candidates",
        ])

    def test_connection_probe_is_ok_on_a_keyless_gateway(self) -> None:
        from navin.config.loader import save_config
        from navin.providers.omniroute import OMNIROUTE_KEYLESS_MESSAGE
        from navin.webui.settings_api import test_provider_connection

        save_config(Config())
        seen: list[tuple[str, dict]] = []
        with patch("navin.webui.settings_api.httpx.get", side_effect=_omniroute_server(seen)):
            payload = test_provider_connection({"provider": ["omniroute"]})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["message"], OMNIROUTE_KEYLESS_MESSAGE)
        self.assertIn("chat", payload["capabilities"])
        self.assertGreaterEqual(payload["model_count"], 6)

    def test_a_rejected_real_key_is_still_reported(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import test_provider_connection

        save_config(Config())
        seen: list[tuple[str, dict]] = []
        with patch("navin.webui.settings_api.httpx.get", side_effect=_omniroute_server(seen)):
            payload = test_provider_connection(
                {"provider": ["omniroute"], "api_key": ["sk-wrong"]}
            )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "not_configured")
        self.assertIn("rejected", payload["message"])
        # No public-catalog probe when a key was supplied: the 401 is the answer.
        self.assertEqual(len(seen), 1)

    def test_401_without_a_public_catalog_stays_not_configured(self) -> None:
        from navin.config.loader import save_config
        from navin.webui.settings_api import provider_models_payload

        save_config(Config())

        def _locked_down(url, headers=None, params=None, timeout=None, follow_redirects=None):
            raise _unauthorized(url)

        with patch("navin.webui.settings_api.httpx.get", side_effect=_locked_down):
            payload = provider_models_payload({"provider": ["omniroute"]})

        self.assertEqual(payload["status"], "not_configured")
        self.assertEqual(payload["models"], [])


if __name__ == "__main__":
    unittest.main()
