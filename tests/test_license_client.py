"""The license client activates devices, installs the provisioned key
without clobbering BYOK keys, and reports usage without ever breaking chat."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from navin import license_client
from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config
from navin.config.secrets import ENC_PREFIX
from navin.license_client import (
    LicenseError,
    ManagedUsageHook,
    activate,
    apply_managed_key,
    device_fingerprint,
    report_usage,
    server_url,
    uses_managed_key,
    validate,
)

MANAGED_KEY = "sk-or-v1-provisioned"


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://navin.live/api/x"),
    )


class FingerprintTest(unittest.TestCase):
    def test_a_corrupted_stored_device_is_regenerated(self):
        """A persisted fingerprint the server would reject (too short, bad
        chars) must be replaced, not sent - it made connect fail forever
        with invalid_fields."""
        from navin.license_client import _effective_device

        for bad in ("fp", "", "   ", "a" * 200, "bad!chars$here"):
            config = Config.model_validate({"license": {"device": bad}})
            regenerated = _effective_device(config)
            self.assertEqual(regenerated, device_fingerprint())
        good = "b" * 32
        config = Config.model_validate({"license": {"device": good}})
        self.assertEqual(_effective_device(config), good)

    def test_it_is_stable_and_opaque(self):
        first, second = device_fingerprint(), device_fingerprint()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        # No raw identifier may leak into the fingerprint.
        import platform

        node = platform.node()
        if node:
            self.assertNotIn(node, first)

    def test_it_survives_mac_address_changes(self):
        """WSL2/VMs randomize the MAC at every boot: a fingerprint derived
        from uuid.getnode() registered a brand-new device per reboot and
        exhausted the plan's device quota (device_limit_reached)."""
        first = device_fingerprint()
        with mock.patch("uuid.getnode", return_value=0xDEADBEEF):
            self.assertEqual(device_fingerprint(), first)

    def test_the_machine_id_is_persisted_next_to_the_config(self):
        from navin.config.loader import get_config_path

        device_fingerprint()
        path = get_config_path().parent / "machine-id"
        self.assertRegex(path.read_text().strip(), r"^[a-f0-9]{32}$")

    def test_a_corrupted_machine_id_is_regenerated(self):
        from navin.config.loader import get_config_path

        path = get_config_path().parent / "machine-id"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-a-valid-id!")
        fingerprint = device_fingerprint()
        self.assertRegex(path.read_text().strip(), r"^[a-f0-9]{32}$")
        self.assertEqual(fingerprint, device_fingerprint())


class ServerUrlTest(unittest.TestCase):
    def test_default_and_overrides(self):
        self.assertEqual(server_url(Config()), "https://navin.live")
        config = Config.model_validate({"license": {"serverUrl": "https://staging.navin.live/"}})
        self.assertEqual(server_url(config), "https://staging.navin.live")
        with mock.patch.dict("os.environ", {"NAVIN_LICENSE_SERVER_URL": "http://localhost:3000"}):
            self.assertEqual(server_url(Config()), "http://localhost:3000")

    def test_non_https_override_falls_back_to_official_server(self):
        # Plain HTTP toward anything but localhost is a downgrade/redirect
        # vector for the activation tokens and managed keys on this channel.
        for bad in (
            "http://evil.example.com",
            "ftp://navin.live",
            "https://user:pass@evil.example.com",
            "not-a-url",
        ):
            with self.subTest(url=bad):
                config = Config.model_validate({"license": {"serverUrl": bad}})
                self.assertEqual(server_url(config), "https://navin.live")
                with mock.patch.dict("os.environ", {"NAVIN_LICENSE_SERVER_URL": bad}):
                    self.assertEqual(server_url(Config()), "https://navin.live")


class ManagedKeyTest(unittest.TestCase):
    def test_an_empty_slot_receives_the_key(self):
        config = Config()
        self.assertTrue(apply_managed_key(config, "openrouter", MANAGED_KEY))
        self.assertEqual(config.providers.navin.api_key, MANAGED_KEY)
        self.assertEqual(config.license.managed_api_key, MANAGED_KEY)
        self.assertEqual(config.license.managed_provider, "navin")

    def test_a_byok_key_is_never_overwritten(self):
        config = Config()
        config.providers.openrouter.api_key = "sk-or-v1-users-own"
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        self.assertEqual(config.providers.openrouter.api_key, "sk-or-v1-users-own")
        # Managed key lives on the Navin provider slot.
        self.assertEqual(config.providers.navin.api_key, MANAGED_KEY)
        self.assertEqual(config.license.managed_api_key, MANAGED_KEY)

    def test_a_rotation_replaces_the_previous_managed_key(self):
        config = Config()
        apply_managed_key(config, "openrouter", "sk-or-v1-old")
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        self.assertEqual(config.providers.navin.api_key, MANAGED_KEY)

    def test_unknown_provider_still_installs_on_navin(self):
        config = Config()
        self.assertTrue(apply_managed_key(config, "mystery", MANAGED_KEY))
        self.assertEqual(config.providers.navin.api_key, MANAGED_KEY)
        self.assertFalse(config.providers.openrouter.api_key)

    def test_uses_managed_key_tracks_the_active_slot(self):
        config = Config.model_validate(
            {"license": {"activationToken": "t", "device": "d"}}
        )
        self.assertFalse(uses_managed_key(config))
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        self.assertTrue(uses_managed_key(config))
        config.providers.navin.api_key = "sk-or-v1-users-own"
        self.assertFalse(uses_managed_key(config))


class DisconnectResetTest(unittest.TestCase):
    """Account disconnect wipes per-session model state so the next person
    on the same machine starts clean: the previous user's plan catalog and
    OAuth-obtained OpenRouter key must not leak into the new session."""

    def _session_config(self) -> Config:
        from navin.config.schema import ModelPresetConfig

        config = Config()
        # Managed catalog leftovers from a paid session.
        config.model_catalog.enabled = True
        config.model_presets["main"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        config.model_presets["deepseek-v4-flash"] = ModelPresetConfig(
            label="DeepSeek V4 Flash",
            model="deepseek/deepseek-v4-flash",
            provider="navin",
        )
        config.model_routes["plan"] = "main"
        config.agents.defaults.provider = "navin"
        config.agents.defaults.model = "deepseek/deepseek-v4-flash"
        config.agents.defaults.model_preset = "deepseek-v4-flash"
        return config

    def test_managed_catalog_state_is_wiped(self):
        config = self._session_config()
        license_client._reset_session_model_state(config)
        self.assertEqual(
            [n for n, p in config.model_presets.items() if p.provider == "navin"],
            [],
        )
        self.assertNotIn("plan", config.model_routes)
        self.assertFalse(config.model_catalog.enabled)
        self.assertEqual(config.agents.defaults.provider, "auto")
        self.assertEqual(config.agents.defaults.model, "")
        self.assertEqual(config.agents.defaults.model_preset, "")

    def test_oauth_openrouter_key_and_presets_are_wiped(self):
        from navin.config.schema import ModelPresetConfig

        config = Config()
        config.providers.openrouter.api_key = "sk-or-v1-oauth-user"
        config.providers.openrouter.oauth_key = True
        config.model_presets["laguna"] = ModelPresetConfig(
            label="Laguna (free)",
            model="poolside/laguna-s-2.1:free",
            provider="openrouter",
        )
        config.agents.defaults.model_preset = "laguna"
        license_client._reset_session_model_state(config)
        self.assertFalse(config.providers.openrouter.api_key)
        self.assertFalse(config.providers.openrouter.oauth_key)
        self.assertNotIn("laguna", config.model_presets)
        self.assertEqual(config.agents.defaults.model_preset, "")

    def test_byok_keys_and_presets_survive(self):
        from navin.config.schema import ModelPresetConfig

        config = Config()
        config.providers.openrouter.api_key = "sk-or-v1-typed-by-hand"
        config.providers.anthropic.api_key = "sk-ant-byok"
        config.model_presets["my-openrouter"] = ModelPresetConfig(
            label="Mine", model="qwen/qwen3.7-max", provider="openrouter"
        )
        config.model_presets["my-claude"] = ModelPresetConfig(
            label="Claude", model="claude-sonnet-4-5", provider="anthropic"
        )
        license_client._reset_session_model_state(config)
        self.assertEqual(
            config.providers.openrouter.api_key, "sk-or-v1-typed-by-hand"
        )
        self.assertEqual(config.providers.anthropic.api_key, "sk-ant-byok")
        self.assertIn("my-openrouter", config.model_presets)
        self.assertIn("my-claude", config.model_presets)

    def test_disconnect_runs_the_reset_and_saves(self):
        config = self._session_config()
        with (
            mock.patch("navin.license_client.save_config") as save,
            mock.patch("navin.license_client.clear_live_usage_mode"),
        ):
            license_client.disconnect(config)
        save.assert_called_once()
        self.assertFalse(config.model_catalog.enabled)
        self.assertEqual(
            [n for n, p in config.model_presets.items() if p.provider == "navin"],
            [],
        )


class PersistedConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self):
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_activation_persists_token_plan_and_key(self):
        config = Config()
        payload = {
            "activated": True,
            "activationToken": "tok-123",
            "plan": "pro",
            "managedProvider": "openrouter",
            "managedKey": MANAGED_KEY,
        }
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ), mock.patch(
            "navin.providers.managed_catalog.sync_managed_catalog",
            return_value=False,
        ) as synced:
            activate(config, "  NAVIN-AAAA-BBBB-CCCC-DDDD  ")
            synced.assert_called_once()

        disk = get_config_path().read_text(encoding="utf-8")
        raw = json.loads(disk)
        self.assertTrue(raw["license"]["activationToken"].startswith(ENC_PREFIX))
        self.assertTrue(raw["license"]["managedApiKey"].startswith(ENC_PREFIX))
        self.assertNotIn("tok-123", disk)
        self.assertNotIn(MANAGED_KEY, disk)

        reloaded = load_config()
        self.assertEqual(reloaded.license.license_key, "NAVIN-AAAA-BBBB-CCCC-DDDD")
        self.assertEqual(reloaded.license.activation_token, "tok-123")
        self.assertEqual(reloaded.license.plan, "pro")
        self.assertTrue(reloaded.license.activated)
        self.assertEqual(reloaded.providers.navin.api_key, MANAGED_KEY)
        # Paid activate turns on the managed catalog so task routes can fill.
        self.assertTrue(reloaded.model_catalog.enabled)

    def test_validate_after_encrypted_disk_sends_plaintext_to_navin_live(self):
        config = Config()
        config.license.activation_token = "tok-live"
        config.license.device = "d" * 32
        config.license.plan = "plus"
        config.license.managed_api_key = MANAGED_KEY
        save_config(config)
        loaded = load_config()
        captured: dict = {}

        def fake_post(url, json=None, **_kwargs):
            captured["url"] = url
            captured["json"] = json
            return _response(200, {"valid": True, "plan": "plus", "managedKeyCurrent": True})

        with mock.patch.object(license_client.httpx, "post", side_effect=fake_post):
            validate(loaded)

        body = captured["json"]
        self.assertEqual(body["activationToken"], "tok-live")
        self.assertFalse(str(body["activationToken"]).startswith(ENC_PREFIX))
        self.assertEqual(
            body["managedKeyFingerprint"],
            hashlib.sha256(MANAGED_KEY.encode("utf-8")).hexdigest(),
        )
        self.assertIn("/api/license/validate", captured["url"])

    def test_locked_activation_token_is_never_posted(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": f"{ENC_PREFIX}not-a-real-blob",
                    "device": "d" * 32,
                }
            }
        )
        with mock.patch.object(license_client.httpx, "post") as posted:
            with self.assertRaises(LicenseError) as ctx:
                validate(config)
        posted.assert_not_called()
        self.assertEqual(ctx.exception.code, "config_locked")

    def test_activation_wires_default_model_and_task_routes(self):
        """Managed subscribers get DeepSeek V4 Flash + paid task routes."""
        from navin.providers.managed_catalog import apply_catalog, parse_catalog

        catalog_payload = {
            "provider": "openrouter",
            "defaultModel": "deepseek/deepseek-v4-flash",
            "models": [
                {"slug": "nvidia/nemotron-3-ultra-550b-a55b:free", "name": "Nemotron", "tier": "light"},
                {"slug": "deepseek/deepseek-v4-flash", "name": "DeepSeek V4 Flash", "tier": "executor"},
                {"slug": "z-ai/glm-5.2", "name": "GLM 5.2", "tier": "main"},
                {"slug": "moonshotai/kimi-k3", "name": "Kimi K3", "tier": "expert"},
            ],
        }
        config = Config()
        payload = {
            "activated": True,
            "activationToken": "tok-routes",
            "plan": "plus",
            "managedProvider": "openrouter",
            "managedKey": MANAGED_KEY,
        }

        def _fake_sync(cfg: Config, **_kwargs: object) -> bool:
            return apply_catalog(cfg, parse_catalog(catalog_payload))

        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ), mock.patch(
            "navin.providers.managed_catalog.sync_managed_catalog",
            side_effect=_fake_sync,
        ):
            activate(config, "NAVIN-ROUTES")

        reloaded = load_config()
        self.assertEqual(reloaded.agents.defaults.model, "deepseek/deepseek-v4-flash")
        self.assertEqual(reloaded.agents.defaults.provider, "navin")
        self.assertEqual(
            reloaded.agents.defaults.model_preset,
            "deepseek-v4-flash",
        )
        self.assertEqual(reloaded.model_routes["dev"], "glm-5-2")
        self.assertEqual(reloaded.model_routes["plan"], "glm-5-2")
        self.assertEqual(reloaded.model_routes["security"], "kimi-k3")
        self.assertEqual(reloaded.model_routes["fast"], "deepseek-v4-flash")
        self.assertEqual(reloaded.model_routes["deep"], "kimi-k3")

    def test_a_refused_activation_changes_nothing(self):
        config = Config()
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(404, {"activated": False, "error": "invalid_license"}),
        ):
            with self.assertRaises(LicenseError) as caught:
                activate(config, "NAVIN-BAD")
        self.assertEqual(caught.exception.code, "invalid_license")
        self.assertFalse(Path(get_config_path()).exists())

    def test_validate_refreshes_plan_and_redelivers_the_key(self):
        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp", "plan": "plus"}}
        )
        payload = {
            "valid": True,
            "plan": "ultra",
            "managedProvider": "openrouter",
            "managedKey": MANAGED_KEY,
            "usage": {"spentMicroUsd": 1_000_000},
        }
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ), mock.patch(
            "navin.providers.managed_catalog.sync_managed_catalog",
            return_value=False,
        ):
            result = validate(config)
        self.assertEqual(result["usage"]["spentMicroUsd"], 1_000_000)
        reloaded = load_config()
        self.assertEqual(reloaded.license.plan, "ultra")
        self.assertEqual(reloaded.providers.navin.api_key, MANAGED_KEY)
        self.assertTrue(reloaded.model_catalog.enabled)

    def test_validate_requires_activation(self):
        with self.assertRaises(LicenseError):
            validate(Config())

    def test_validate_sends_the_managed_key_fingerprint(self):
        """The device advertises a SHA-256 of its key so the server can skip
        re-sending the plaintext key when nothing changed."""
        import hashlib

        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp", "plan": "flash"}}
        )
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(200, {"valid": True, "plan": "flash"}),
        ) as posted:
            validate(config)
        body = posted.call_args.kwargs["json"]
        self.assertEqual(
            body["managedKeyFingerprint"],
            hashlib.sha256(MANAGED_KEY.encode("utf-8")).hexdigest(),
        )

    def test_validate_keeps_the_key_when_server_says_unchanged(self):
        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp", "plan": "flash"}}
        )
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        payload = {
            "valid": True,
            "plan": "flash",
            "managedProvider": "openrouter",
            "managedKey": None,
            "managedKeyCurrent": True,
        }
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ), mock.patch(
            "navin.providers.managed_catalog.sync_managed_catalog",
            return_value=False,
        ):
            validate(config)
        self.assertEqual(config.license.managed_api_key, MANAGED_KEY)
        self.assertEqual(config.providers.navin.api_key, MANAGED_KEY)

    def test_a_downgrade_to_free_removes_the_dead_managed_key(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "pro",
                    "managedApiKey": MANAGED_KEY,
                    "managedProvider": "navin",
                }
            }
        )
        config.providers.navin.api_key = MANAGED_KEY
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(200, {"valid": True, "plan": "free", "managedKey": None}),
        ):
            validate(config)
        reloaded = load_config()
        self.assertEqual(reloaded.license.plan, "free")
        self.assertEqual(reloaded.license.managed_api_key, "")
        self.assertEqual(reloaded.providers.navin.api_key, "")

    def test_a_downgrade_never_touches_a_byok_key(self):
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "pro",
                    "managedApiKey": MANAGED_KEY,
                    "managedProvider": "navin",
                }
            }
        )
        config.providers.navin.api_key = MANAGED_KEY
        config.providers.openrouter.api_key = "sk-or-v1-users-own"
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(200, {"valid": True, "plan": "free", "managedKey": None}),
        ):
            validate(config)
        reloaded = load_config()
        self.assertEqual(reloaded.providers.openrouter.api_key, "sk-or-v1-users-own")
        self.assertEqual(reloaded.providers.navin.api_key, "")
        self.assertEqual(reloaded.license.managed_api_key, "")


class PlanChangeSyncTest(unittest.TestCase):
    """Upgrades, downgrades and expiries keep the model list truthful: the
    managed catalog lives and dies with the managed key, while the user's
    own keys and presets (BYOK, OpenRouter OAuth) survive every transition."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self):
        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def _paid_config_with_catalog(self) -> Config:
        from navin.config.schema import ModelPresetConfig

        config = Config.model_validate(
            {
                "license": {
                    "activationToken": "tok",
                    "device": "fp",
                    "plan": "pro",
                    "managedApiKey": MANAGED_KEY,
                    "managedProvider": "navin",
                }
            }
        )
        config.providers.navin.api_key = MANAGED_KEY
        config.model_catalog.enabled = True
        config.model_presets["main"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        config.model_routes["plan"] = "main"
        config.agents.defaults.provider = "navin"
        config.agents.defaults.model = "deepseek/deepseek-v4-flash"
        # The user's own Free connection rides along.
        config.providers.openrouter.api_key = "sk-or-v1-oauth-user"
        config.providers.openrouter.oauth_key = True
        config.model_presets["laguna"] = ModelPresetConfig(
            label="Laguna (free)",
            model="poolside/laguna-s-2.1:free",
            provider="openrouter",
        )
        return config

    def test_a_downgrade_to_free_wipes_the_dead_catalog(self):
        config = self._paid_config_with_catalog()
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(200, {"valid": True, "plan": "free", "managedKey": None}),
        ):
            validate(config)
        reloaded = load_config()
        self.assertFalse(reloaded.model_catalog.enabled)
        self.assertNotIn("main", reloaded.model_presets)
        self.assertNotIn("plan", reloaded.model_routes)
        self.assertEqual(reloaded.agents.defaults.provider, "auto")
        self.assertEqual(reloaded.agents.defaults.model, "")
        # The user's own OpenRouter OAuth connection is not the plan's to take.
        self.assertEqual(reloaded.providers.openrouter.api_key, "sk-or-v1-oauth-user")
        self.assertIn("laguna", reloaded.model_presets)

    def test_a_free_validate_purges_a_stale_catalog_without_a_key(self):
        """Idempotent cleanup: a machine left with dead 'navin ·' presets by an
        older build (no managed key anymore) is healed by any free validate."""
        from navin.config.schema import ModelPresetConfig

        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp", "plan": "free"}}
        )
        config.model_catalog.enabled = True
        config.model_presets["main"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(200, {"valid": True, "plan": "free"}),
        ):
            validate(config)
        reloaded = load_config()
        self.assertFalse(reloaded.model_catalog.enabled)
        self.assertNotIn("main", reloaded.model_presets)

    def test_an_expired_subscription_wipes_the_dead_catalog(self):
        config = self._paid_config_with_catalog()
        with mock.patch.object(
            license_client.httpx,
            "post",
            return_value=_response(
                200, {"valid": False, "status": "expired", "plan": "pro"}
            ),
        ):
            with self.assertRaises(LicenseError):
                validate(config)
        reloaded = load_config()
        self.assertFalse(reloaded.model_catalog.enabled)
        self.assertNotIn("main", reloaded.model_presets)
        self.assertEqual(reloaded.license.managed_api_key, "")

    def test_a_free_activation_cleans_a_stale_catalog(self):
        from navin.config.schema import ModelPresetConfig

        config = Config()
        config.model_catalog.enabled = True
        config.model_presets["main"] = ModelPresetConfig(
            label="GLM 5.2", model="z-ai/glm-5.2", provider="navin"
        )
        payload = {
            "activated": True,
            "activationToken": "tok-free",
            "plan": "free",
        }
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ):
            activate(config, "NAVIN-FREE-KEY-0000")
        reloaded = load_config()
        self.assertFalse(reloaded.model_catalog.enabled)
        self.assertNotIn("main", reloaded.model_presets)

    def test_a_paid_activation_still_bootstraps_the_catalog(self):
        config = Config()
        payload = {
            "activated": True,
            "activationToken": "tok-pro",
            "plan": "pro",
            "managedProvider": "navin",
            "managedKey": MANAGED_KEY,
        }
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ), mock.patch(
            "navin.providers.managed_catalog.sync_managed_catalog",
            return_value=False,
        ) as synced:
            activate(config, "NAVIN-PRO-KEY-0000")
            synced.assert_called_once()
        reloaded = load_config()
        self.assertTrue(reloaded.model_catalog.enabled)
        self.assertEqual(reloaded.providers.navin.api_key, MANAGED_KEY)


class ReportUsageTest(unittest.TestCase):
    def _activated_config(self) -> Config:
        config = Config.model_validate(
            {"license": {"activationToken": "tok", "device": "fp"}}
        )
        return config

    def test_a_recorded_report_returns_the_usage_summary(self):
        config = self._activated_config()
        payload = {"recorded": True, "usage": {"mode": "normal", "spentMicroUsd": 42}}
        with mock.patch.object(
            license_client.httpx, "post", return_value=_response(200, payload)
        ) as posted:
            usage = report_usage(config, model="z-ai/glm-5.2", input_tokens=100, output_tokens=50)
        self.assertEqual(usage["mode"], "normal")
        body = posted.call_args.kwargs["json"]
        self.assertEqual(body["model"], "z-ai/glm-5.2")
        self.assertEqual(body["inputTokens"], 100)
        self.assertEqual(body["outputTokens"], 50)

    def test_a_dead_network_never_raises(self):
        config = self._activated_config()
        with mock.patch.object(
            license_client.httpx, "post", side_effect=httpx.ConnectError("offline")
        ):
            self.assertIsNone(report_usage(config, model="z-ai/glm-5.2", input_tokens=1))

    def test_nothing_is_sent_without_activation(self):
        with mock.patch.object(license_client.httpx, "post") as posted:
            self.assertIsNone(report_usage(Config(), model="z-ai/glm-5.2", input_tokens=1))
        posted.assert_not_called()


class ManagedUsageHookTest(unittest.IsolatedAsyncioTestCase):
    def _managed_config(self) -> Config:
        config = Config.model_validate(
            {
                "license": {"activationToken": "tok", "device": "fp"},
                "agents": {"defaults": {"model": "z-ai/glm-5.2"}},
            }
        )
        apply_managed_key(config, "openrouter", MANAGED_KEY)
        return config

    async def test_managed_usage_is_reported(self):
        config = self._managed_config()
        context = mock.Mock(usage={"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": 2})
        hook = ManagedUsageHook(config)
        with mock.patch(
            "navin.config.loader.load_config", return_value=config
        ), mock.patch.object(license_client, "report_usage") as reported:
            await hook.after_iteration(context)
            # The POST is fire-and-forget so the turn never waits on it;
            # drain the background task before asserting.
            await asyncio.gather(*hook._reports)
        reported.assert_called_once_with(
            config,
            model="z-ai/glm-5.2",
            input_tokens=10,
            output_tokens=5,
            cached_tokens=2,
            cost_micro_usd=None,
        )

    async def test_real_billed_cost_is_forwarded(self):
        """OpenRouter's usage.cost (captured as cost_micro_usd) must reach the
        server so the soft budget debits actual spend, not an estimate."""
        config = self._managed_config()
        context = mock.Mock(
            usage={"prompt_tokens": 10, "completion_tokens": 5, "cost_micro_usd": 1234}
        )
        hook = ManagedUsageHook(config)
        with mock.patch(
            "navin.config.loader.load_config", return_value=config
        ), mock.patch.object(license_client, "report_usage") as reported:
            await hook.after_iteration(context)
            await asyncio.gather(*hook._reports)
        self.assertEqual(reported.call_args.kwargs["cost_micro_usd"], 1234)

    async def test_byok_users_are_never_reported(self):
        config = self._managed_config()
        config.providers.navin.api_key = "sk-or-v1-users-own"
        context = mock.Mock(usage={"prompt_tokens": 10, "completion_tokens": 5})
        with mock.patch(
            "navin.config.loader.load_config", return_value=config
        ), mock.patch.object(license_client, "report_usage") as reported:
            await ManagedUsageHook(config).after_iteration(context)
        reported.assert_not_called()

    async def test_reports_after_activation_on_disk(self):
        """Usage must bill even when the hook was built before Account activate."""
        stale = Config.model_validate(
            {"agents": {"defaults": {"model": "deepseek/deepseek-v4-flash"}}}
        )
        live = self._managed_config()
        live.agents.defaults.model = "deepseek/deepseek-v4-flash"
        context = mock.Mock(usage={"prompt_tokens": 10, "completion_tokens": 5})
        hook = ManagedUsageHook(stale)
        with mock.patch(
            "navin.config.loader.load_config", return_value=live
        ), mock.patch.object(license_client, "report_usage") as reported:
            await hook.after_iteration(context)
            await asyncio.gather(*hook._reports)
        reported.assert_called_once()
        self.assertEqual(
            reported.call_args.kwargs["model"], "deepseek/deepseek-v4-flash"
        )

    async def test_empty_usage_is_skipped(self):
        config = self._managed_config()
        context = mock.Mock(usage={})
        with mock.patch(
            "navin.config.loader.load_config", return_value=config
        ), mock.patch.object(license_client, "report_usage") as reported:
            await ManagedUsageHook(config).after_iteration(context)
        reported.assert_not_called()


if __name__ == "__main__":
    unittest.main()
