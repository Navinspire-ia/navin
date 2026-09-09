# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenRouter OAuth PKCE: the Free onboarding path.

The key minted at the end of the flow belongs to the USER's OpenRouter
account - free models cost them nothing, paid models bill them, never us.
These tests pin the three properties that make the flow safe: the challenge
really derives from the verifier, the state nonce is one-shot, and the free
default presets install without ever hijacking an explicit model choice.
"""

from __future__ import annotations

import base64
import hashlib
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from navin.config.schema import Config
from navin.webui.openrouter_oauth import (
    FREE_DEFAULT_MODELS,
    OpenRouterOAuthError,
    OpenRouterOAuthService,
    install_free_presets,
)

_CALLBACK = "http://127.0.0.1:8765/webui/openrouter/callback"


def _parts(url: str) -> dict[str, str]:
    query = parse_qs(urlparse(url).query)
    return {key: values[0] for key, values in query.items()}


def _state_of(url: str) -> str:
    callback = _parts(url)["callback_url"]
    return _parts(callback).get("state", "")


class ConnectUrlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = OpenRouterOAuthService()

    def test_url_points_at_openrouter_with_s256_pkce(self) -> None:
        url = self.service.connect_url(callback=_CALLBACK)
        self.assertTrue(url.startswith("https://openrouter.ai/auth?"))
        parts = _parts(url)
        self.assertEqual(parts["code_challenge_method"], "S256")
        self.assertTrue(parts["code_challenge"])
        self.assertTrue(parts["callback_url"].startswith(_CALLBACK))

    def test_challenge_derives_from_the_stored_verifier(self) -> None:
        url = self.service.connect_url(callback=_CALLBACK)
        state = _state_of(url)
        verifier = self.service.take_verifier(state)
        self.assertIsNotNone(verifier)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(_parts(url)["code_challenge"], expected)

    def test_verifier_is_one_shot(self) -> None:
        url = self.service.connect_url(callback=_CALLBACK)
        state = _state_of(url)
        self.assertIsNotNone(self.service.take_verifier(state))
        self.assertIsNone(self.service.take_verifier(state))

    def test_a_new_flow_invalidates_the_previous_one(self) -> None:
        first_state = _state_of(self.service.connect_url(callback=_CALLBACK))
        self.service.connect_url(callback=_CALLBACK)
        self.assertIsNone(self.service.take_verifier(first_state))

    def test_unknown_state_yields_nothing(self) -> None:
        self.service.connect_url(callback=_CALLBACK)
        self.assertIsNone(self.service.take_verifier("forged"))

    def test_lang_rides_inside_the_callback_url(self) -> None:
        url = self.service.connect_url(callback=_CALLBACK, lang="fr")
        callback = _parts(url)["callback_url"]
        self.assertEqual(_parts(callback).get("lang"), "fr")

    def test_an_unknown_lang_is_dropped(self) -> None:
        url = self.service.connect_url(callback=_CALLBACK, lang="xx")
        callback = _parts(url)["callback_url"]
        self.assertNotIn("lang", _parts(callback))


class FinishRedirectTest(unittest.TestCase):
    def test_success_lands_on_the_localized_site_page(self) -> None:
        from navin.webui.openrouter_oauth import finish_redirect_url

        with mock.patch(
            "navin.license_client.server_url", return_value="https://navin.live"
        ):
            url = finish_redirect_url(ok=True, lang="fr")
        self.assertEqual(url, "https://navin.live/fr/connect/openrouter?ok=1")

    def test_failure_and_missing_lang_default_to_en(self) -> None:
        from navin.webui.openrouter_oauth import finish_redirect_url

        with mock.patch(
            "navin.license_client.server_url", return_value="https://navin.live"
        ):
            url = finish_redirect_url(ok=False)
        self.assertEqual(url, "https://navin.live/en/connect/openrouter?ok=0")


class ApplyUserKeyTest(unittest.TestCase):
    def test_the_key_is_marked_as_oauth_owned(self) -> None:
        config = Config()
        config.agents.defaults.model = ""
        config.agents.defaults.model_preset = ""
        service = OpenRouterOAuthService()
        with (
            mock.patch(
                "navin.webui.openrouter_oauth.load_config", return_value=config
            ),
            mock.patch("navin.webui.openrouter_oauth.save_config"),
        ):
            service.apply_user_key("sk-or-v1-user")
        self.assertEqual(config.providers.openrouter.api_key, "sk-or-v1-user")
        self.assertTrue(config.providers.openrouter.oauth_key)


class FreePresetsTest(unittest.TestCase):
    def test_installs_both_free_models_and_activates_the_first(self) -> None:
        config = Config()
        config.agents.defaults.model = ""
        config.agents.defaults.model_preset = ""
        added = install_free_presets(config)
        self.assertEqual(added, len(FREE_DEFAULT_MODELS))
        installed = {
            preset.model
            for preset in config.model_presets.values()
            if preset.provider == "openrouter"
        }
        self.assertEqual(installed, {model for model, _ in FREE_DEFAULT_MODELS})
        active = config.model_presets[config.agents.defaults.model_preset]
        self.assertEqual(active.model, FREE_DEFAULT_MODELS[0][0])

    def test_reconnecting_is_idempotent(self) -> None:
        config = Config()
        config.agents.defaults.model = ""
        config.agents.defaults.model_preset = ""
        install_free_presets(config)
        before = dict(config.model_presets)
        self.assertEqual(install_free_presets(config), 0)
        self.assertEqual(config.model_presets, before)

    def test_an_explicit_model_choice_is_never_hijacked(self) -> None:
        config = Config()
        config.agents.defaults.model = "anthropic/claude-sonnet-4.5"
        config.agents.defaults.model_preset = ""
        install_free_presets(config)
        self.assertEqual(config.agents.defaults.model_preset, "")
        self.assertEqual(config.agents.defaults.model, "anthropic/claude-sonnet-4.5")

    def test_legacy_laguna_default_moves_to_nemotron_ultra(self) -> None:
        config = Config()
        config.agents.defaults.model = ""
        config.agents.defaults.model_preset = ""
        install_free_presets(config)
        laguna = next(
            name
            for name, preset in config.model_presets.items()
            if preset.model == "poolside/laguna-s-2.1:free"
        )
        config.agents.defaults.model_preset = laguna
        install_free_presets(config)
        active = config.model_presets[config.agents.defaults.model_preset]
        self.assertEqual(active.model, FREE_DEFAULT_MODELS[0][0])
        self.assertEqual(active.model, "nvidia/nemotron-3-ultra-550b-a55b:free")


class ExchangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = OpenRouterOAuthService()

    def _response(self, status: int, body: dict | None = None) -> mock.Mock:
        response = mock.Mock()
        response.status_code = status
        response.json.return_value = body or {}
        return response

    def test_a_valid_code_yields_the_user_key(self) -> None:
        with mock.patch(
            "httpx.post", return_value=self._response(200, {"key": "sk-or-user-1"})
        ) as post:
            key = self.service._exchange(code="abc", verifier="v" * 43)
        self.assertEqual(key, "sk-or-user-1")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["code"], "abc")
        self.assertEqual(payload["code_challenge_method"], "S256")

    def test_a_rejected_code_raises(self) -> None:
        with mock.patch("httpx.post", return_value=self._response(403)):
            with self.assertRaises(OpenRouterOAuthError):
                self.service._exchange(code="bad", verifier="v" * 43)

    def test_a_keyless_response_raises(self) -> None:
        with mock.patch("httpx.post", return_value=self._response(200, {})):
            with self.assertRaises(OpenRouterOAuthError):
                self.service._exchange(code="abc", verifier="v" * 43)

    def test_network_failure_raises_a_reachable_error(self) -> None:
        with mock.patch("httpx.post", side_effect=OSError("down")):
            with self.assertRaises(OpenRouterOAuthError) as ctx:
                self.service._exchange(code="abc", verifier="v" * 43)
        self.assertEqual(ctx.exception.status, 502)


class FreeModelFlagTest(unittest.TestCase):
    """Provider model rows carry a ``free`` flag for the Settings filter."""

    def _row(self, payload: dict) -> dict:
        from navin.webui.settings_api import _model_row_payload

        row = _model_row_payload(payload)
        assert row is not None
        return row

    def test_a_free_suffix_marks_the_model_free(self) -> None:
        row = self._row({"id": "poolside/laguna-s-2.1:free"})
        self.assertTrue(row.get("free"))

    def test_zero_pricing_marks_the_model_free(self) -> None:
        row = self._row(
            {"id": "vendor/model", "pricing": {"prompt": "0", "completion": "0"}}
        )
        self.assertTrue(row.get("free"))

    def test_paid_pricing_is_not_free(self) -> None:
        row = self._row(
            {"id": "vendor/model", "pricing": {"prompt": "0.000002", "completion": "0"}}
        )
        self.assertNotIn("free", row)

    def test_no_pricing_information_is_not_free(self) -> None:
        row = self._row({"id": "vendor/model"})
        self.assertNotIn("free", row)


class FreeSelfHealTest(unittest.TestCase):
    """The Free connection stays truthful: while the OAuth key is present,
    the two default free presets reappear on the next Settings load even if
    something deleted them."""

    def setUp(self) -> None:
        import tempfile

        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        from pathlib import Path

        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_missing_free_presets_are_reinstalled_on_settings_load(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import settings_payload

        config = Config()
        config.providers.openrouter.api_key = "sk-or-v1-user"
        config.providers.openrouter.oauth_key = True
        save_config(config)

        payload = settings_payload()
        preset_models = {
            preset["model"]
            for preset in payload["model_presets"]
            if preset.get("provider") == "openrouter"
        }
        self.assertTrue(
            {model for model, _ in FREE_DEFAULT_MODELS} <= preset_models
        )
        # And they were persisted, not just injected into the payload.
        reloaded = load_config()
        persisted = {
            preset.model
            for preset in reloaded.model_presets.values()
            if preset.provider == "openrouter"
        }
        self.assertEqual(persisted, {model for model, _ in FREE_DEFAULT_MODELS})

    def test_a_byok_openrouter_key_is_not_touched(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.webui.settings_api import settings_payload

        config = Config()
        config.providers.openrouter.api_key = "sk-or-v1-typed-by-hand"
        save_config(config)

        settings_payload()
        reloaded = load_config()
        self.assertEqual(len(reloaded.model_presets), 0)

    def test_dead_plan_catalog_is_purged_on_settings_load(self) -> None:
        """A stale 'navin ·' catalog without a managed key (session that ended
        before disconnect cleanup existed) disappears on the next Settings
        load; the user's own OpenRouter presets survive."""
        from navin.config.loader import load_config, save_config
        from navin.config.schema import ModelPresetConfig
        from navin.webui.settings_api import settings_payload

        config = Config()
        config.license.plan = "free"
        config.model_catalog.enabled = True
        config.model_presets["glm"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        config.agents.defaults.model_preset = "glm"
        config.providers.openrouter.api_key = "sk-or-v1-user"
        config.providers.openrouter.oauth_key = True
        save_config(config)

        payload = settings_payload()
        providers = {
            preset.get("provider") for preset in payload["model_presets"]
        }
        self.assertNotIn("navin", providers)
        reloaded = load_config()
        self.assertNotIn("glm", reloaded.model_presets)
        self.assertFalse(reloaded.model_catalog.enabled)
        # The free presets were re-installed and one became the default.
        self.assertEqual(reloaded.providers.openrouter.api_key, "sk-or-v1-user")
        persisted = {
            preset.model
            for preset in reloaded.model_presets.values()
            if preset.provider == "openrouter"
        }
        self.assertEqual(persisted, {model for model, _ in FREE_DEFAULT_MODELS})


class PlanModelActivateTest(unittest.TestCase):
    """Saving / starring a Plan model must promote it to the chat default
    without rewriting the locked catalog entry (the old error was
    ``plan models can only be shown or hidden``)."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        set_config_path(self._previous_path)
        self._tmp.cleanup()

    @staticmethod
    def _query(**fields: str):
        class _Q(dict):
            def get(self, key, default=None):  # noqa: ANN001
                if key in self:
                    value = self[key]
                    return value if isinstance(value, list) else [value]
                return default

        return _Q(**fields)

    def test_saving_a_plan_model_sets_it_as_default(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.config.schema import ModelPresetConfig
        from navin.webui.settings_api import update_model_configuration

        config = Config()
        config.license.plan = "plus"
        config.license.managed_api_key = "sk-managed"
        config.providers.navin.api_key = "sk-managed"
        config.model_presets["deepseek-v4"] = ModelPresetConfig(
            label="DeepSeek", model="deepseek/x", provider="navin"
        )
        config.model_presets["glm-5-2"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        config.agents.defaults.model_preset = "deepseek-v4"
        save_config(config)

        payload = update_model_configuration(
            self._query(
                name="glm-5-2",
                label="GLM 5.2",
                model="z-ai/glm-5.2",
                provider="navin",
            )
        )
        self.assertEqual(payload["agent"]["model_preset"], "glm-5-2")
        reloaded = load_config()
        self.assertEqual(reloaded.agents.defaults.model_preset, "glm-5-2")
        # Identity untouched.
        self.assertEqual(reloaded.model_presets["glm-5-2"].model, "z-ai/glm-5.2")

    def test_hiding_a_plan_model_does_not_steal_the_default(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.config.schema import ModelPresetConfig
        from navin.webui.settings_api import update_model_configuration

        config = Config()
        config.license.plan = "plus"
        config.license.managed_api_key = "sk-managed"
        config.providers.navin.api_key = "sk-managed"
        config.model_presets["deepseek-v4"] = ModelPresetConfig(
            label="DeepSeek", model="deepseek/x", provider="navin"
        )
        config.model_presets["glm-5-2"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        config.agents.defaults.model_preset = "deepseek-v4"
        save_config(config)

        update_model_configuration(self._query(name="glm-5-2", enabled="false"))
        reloaded = load_config()
        self.assertEqual(reloaded.agents.defaults.model_preset, "deepseek-v4")
        self.assertFalse(reloaded.model_presets["glm-5-2"].enabled)

    def test_plan_model_reasoning_effort_can_change(self) -> None:
        """Composer Effort must persist on locked Navin catalog presets."""
        from navin.config.loader import load_config, save_config
        from navin.config.schema import ModelPresetConfig
        from navin.webui.settings_api import update_model_configuration

        config = Config()
        config.license.plan = "plus"
        config.license.managed_api_key = "sk-managed"
        config.providers.navin.api_key = "sk-managed"
        config.model_presets["deepseek-v4-flash"] = ModelPresetConfig(
            label="DeepSeek V4 Flash",
            model="deepseek/deepseek-v4-flash",
            provider="navin",
        )
        config.agents.defaults.model_preset = "deepseek-v4-flash"
        save_config(config)

        payload = update_model_configuration(
            self._query(name="deepseek-v4-flash", reasoning_effort="high")
        )
        preset_row = next(
            row
            for row in payload["model_presets"]
            if row["name"] == "deepseek-v4-flash"
        )
        self.assertEqual(preset_row["reasoning_effort"], "high")
        self.assertEqual(payload["agent"]["reasoning_effort"], "high")
        reloaded = load_config()
        self.assertEqual(
            reloaded.model_presets["deepseek-v4-flash"].reasoning_effort, "high"
        )
        self.assertEqual(reloaded.agents.defaults.reasoning_effort, "high")

    def test_imported_navin_model_id_can_change(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.config.schema import ModelPresetConfig
        from navin.providers.managed_catalog import apply_catalog, parse_catalog
        from navin.webui.settings_api import update_model_configuration

        config = Config()
        config.license.plan = "pro"
        config.license.managed_api_key = "sk-managed"
        config.providers.navin.api_key = "sk-managed"
        config.model_presets["claude-sonnet-5"] = ModelPresetConfig(
            label="Claude Sonnet 5",
            model="anthropic/claude-sonnet-5",
            provider="navin",
        )
        save_config(config)

        payload = update_model_configuration(
            self._query(
                name="claude-sonnet-5",
                label="Claude Sonnet 4.5",
                model="anthropic/claude-sonnet-4.5",
                provider="navin",
            )
        )
        row = next(
            item for item in payload["model_presets"] if item["name"] == "claude-sonnet-5"
        )
        self.assertEqual(row["model"], "anthropic/claude-sonnet-4.5")
        self.assertEqual(row["label"], "Claude Sonnet 4.5")
        self.assertFalse(row["locked"])

        reloaded = load_config()
        preset = reloaded.model_presets["claude-sonnet-5"]
        self.assertEqual(preset.model, "anthropic/claude-sonnet-4.5")
        self.assertTrue(preset.user_edited)

        catalog = parse_catalog(
            {
                "provider": "openrouter",
                "models": [
                    {
                        "slug": "anthropic/claude-sonnet-5",
                        "name": "Claude Sonnet 5",
                        "tier": "main",
                    }
                ],
            }
        )
        apply_catalog(reloaded, catalog)
        self.assertEqual(
            reloaded.model_presets["claude-sonnet-5"].model,
            "anthropic/claude-sonnet-4.5",
        )


class OnboardingFreePathTest(unittest.TestCase):
    def test_free_is_an_accepted_onboarding_path(self) -> None:
        from navin.webui.onboarding_state import normalize_webui_onboarding

        state = normalize_webui_onboarding({"completed": True, "path": "free"})
        self.assertEqual(state["path"], "free")


if __name__ == "__main__":
    unittest.main()
