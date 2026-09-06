"""Encrypted config.json must still talk to navin.live and the local gateway."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin import license_client
from navin.channels.websocket import WebSocketConfig
from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config, ModelPresetConfig, ProviderConfig
from navin.config.secrets import ENC_PREFIX, unlock_stored_secret, unlocked_secret
from navin.license_client import report_usage, team_post, uses_managed_key, validate
from navin.providers.factory import provider_signature
from navin.providers.managed_catalog import catalog_url

MANAGED_KEY = "sk-or-v1-live-roundtrip"
TOKEN = "tok-live-roundtrip"
WS_SECRET = "ws-issue-secret-roundtrip"


def _response(status: int, payload: dict):
    import httpx

    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://navin.live/api/x"),
    )


class LiveSecretsRoundtripTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: set_config_path(self._previous))

    def _write_paid_config(self) -> Path:
        config = Config()
        config.license.activation_token = TOKEN
        config.license.device = "d" * 32
        config.license.plan = "plus"
        config.license.managed_api_key = MANAGED_KEY
        config.license.managed_provider = "navin"
        config.providers.navin = ProviderConfig(api_key=MANAGED_KEY)
        config.model_presets["glm-live"] = ModelPresetConfig(
            model="z-ai/glm-5.3-flash",
            provider="navin",
        )
        config.agents.defaults.model_preset = "glm-live"
        config.channels.websocket = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8766,
            "tokenIssueSecret": WS_SECRET,
            "websocketRequiresToken": True,
            "allowFrom": ["*"],
        }
        path = get_config_path()
        save_config(config, path)
        return path

    def test_disk_has_no_live_secrets(self) -> None:
        path = self._write_paid_config()
        text = path.read_text(encoding="utf-8")
        for secret in (TOKEN, MANAGED_KEY, WS_SECRET):
            self.assertNotIn(secret, text)
        raw = json.loads(text)
        self.assertTrue(raw["license"]["activationToken"].startswith(ENC_PREFIX))
        self.assertTrue(raw["license"]["managedApiKey"].startswith(ENC_PREFIX))
        self.assertTrue(raw["channels"]["websocket"]["tokenIssueSecret"].startswith(ENC_PREFIX))
        self.assertEqual(raw["license"]["plan"], "plus")
        self.assertEqual(raw["channels"]["websocket"]["host"], "127.0.0.1")

    def test_reload_unlocks_everything_the_gateway_needs(self) -> None:
        path = self._write_paid_config()
        loaded = load_config(path)
        self.assertEqual(loaded.license.activation_token, TOKEN)
        self.assertEqual(loaded.license.managed_api_key, MANAGED_KEY)
        self.assertEqual(loaded.providers.navin.api_key, MANAGED_KEY)
        self.assertTrue(uses_managed_key(loaded))
        preset = loaded.model_presets["glm-live"]
        self.assertEqual(loaded.get_api_key(preset.model, preset=preset), MANAGED_KEY)
        ws = WebSocketConfig.model_validate(loaded.channels.websocket)
        self.assertEqual(ws.token_issue_secret, WS_SECRET)
        self.assertEqual(unlock_stored_secret(
            json.loads(path.read_text(encoding="utf-8"))["channels"]["websocket"]["tokenIssueSecret"],
            path,
        ), WS_SECRET)
        self.assertEqual(catalog_url(loaded), "https://navin.live/api/models")
        signature = provider_signature(loaded, preset_name="glm-live", preset=preset)
        self.assertIn(MANAGED_KEY, signature)
        self.assertFalse(any(
            isinstance(item, str) and item.startswith(ENC_PREFIX) for item in signature
        ))

    def test_validate_and_usage_post_plaintext_only(self) -> None:
        loaded = load_config(self._write_paid_config())
        captured: list[dict] = []

        def fake_post(url, json=None, **_kwargs):
            captured.append({"url": url, "json": json})
            if url.endswith("/api/license/validate"):
                return _response(200, {"valid": True, "plan": "plus", "managedKeyCurrent": True})
            if url.endswith("/api/usage/report"):
                return _response(200, {"recorded": True, "usage": {}})
            if "/api/orgs" in url:
                return _response(200, {"ok": True})
            return _response(404, {"error": "unexpected"})

        with mock.patch.object(license_client.httpx, "post", side_effect=fake_post):
            validate(loaded)
            report_usage(loaded, model="z-ai/glm-5.3-flash", input_tokens=10, output_tokens=4)
            status, body = team_post(loaded, "/api/orgs", {"action": "list"})

        self.assertEqual(status, 200)
        self.assertEqual(body.get("ok"), True)
        self.assertGreaterEqual(len(captured), 3)
        for row in captured:
            token = row["json"]["activationToken"]
            self.assertEqual(token, TOKEN)
            self.assertFalse(token.startswith(ENC_PREFIX))
            self.assertNotIn(ENC_PREFIX, json.dumps(row["json"]))
        validate_body = next(row["json"] for row in captured if row["url"].endswith("/validate"))
        self.assertEqual(
            validate_body["managedKeyFingerprint"],
            hashlib.sha256(MANAGED_KEY.encode("utf-8")).hexdigest(),
        )

    def test_electron_unlocks_the_same_blob(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        path = self._write_paid_config()
        stored = json.loads(path.read_text(encoding="utf-8"))["channels"]["websocket"]["tokenIssueSecret"]
        script = Path(__file__).resolve().parent.parent / "desktop-electron" / "config-secrets.js"
        result = subprocess.run(
            [
                node,
                "-e",
                "const {unlockStoredSecret}=require(process.argv[1]);"
                "process.stdout.write(unlockStoredSecret(process.argv[2], process.argv[3]));",
                str(script),
                stored,
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, WS_SECRET)

    def test_locked_values_never_leave_the_machine(self) -> None:
        self.assertEqual(unlocked_secret(f"{ENC_PREFIX}blob"), "")
        config = Config.model_validate(
            {
                "license": {
                    "activationToken": f"{ENC_PREFIX}blob",
                    "device": "d" * 32,
                    "managedApiKey": f"{ENC_PREFIX}key",
                }
            }
        )
        config.providers.navin = ProviderConfig(api_key=f"{ENC_PREFIX}key")
        with mock.patch.object(license_client.httpx, "post") as posted:
            with self.assertRaises(license_client.LicenseError) as ctx:
                validate(config)
            self.assertIsNone(report_usage(config, model="z-ai/glm-5.3-flash"))
            status, _body = team_post(config, "/api/orgs", {})
        posted.assert_not_called()
        self.assertEqual(ctx.exception.code, "config_locked")
        self.assertEqual(status, 401)
        self.assertFalse(uses_managed_key(config))
        key = config.get_api_key("z-ai/glm-5.3-flash")
        self.assertTrue(key is None or not str(key).startswith(ENC_PREFIX))


if __name__ == "__main__":
    unittest.main()
