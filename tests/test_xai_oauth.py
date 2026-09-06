"""xAI API key + SuperGrok device-code OAuth.

Official docs (grok-build 02-authentication.md):
  - ``XAI_API_KEY`` from console.x.ai is the fallback
  - a session from ``auth.x.ai`` (``grok login --device-auth``) wins
  - tokens refresh via ``refresh_token``; missing expiry is 30 days
"""

from __future__ import annotations

import base64
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from navin.config.schema import Config, ModelPresetConfig
from navin.providers.factory import make_provider
from navin.providers.registry import find_by_name
from navin.providers.xai_oauth_provider import (
    CLIENT_ID,
    DEVICE_CODE_GRANT,
    SCOPE,
    _account_from_id_token,
    _token_from_payload,
    login_xai_oauth,
    subscription_headers,
)
from tests.provider_test_utils import isolated_provider_env


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"aaa.{payload}.sig"


class XaiApiProviderTest(unittest.TestCase):
    def test_xai_is_the_developer_api(self):
        spec = find_by_name("xai")
        assert spec is not None
        self.assertEqual(spec.env_key, "XAI_API_KEY")
        self.assertEqual(spec.default_api_base, "https://api.x.ai/v1")
        self.assertFalse(spec.is_oauth)

    def test_a_grok_model_routes_to_xai_when_the_key_is_set(self):
        with isolated_provider_env():
            config = Config()
            config.providers.xai.api_key = "xai-test"
            self.assertEqual(config.get_provider_name("grok-4.6"), "xai")
            self.assertEqual(config.get_api_base("grok-4.6"), "https://api.x.ai/v1")
            config.model_presets["t"] = ModelPresetConfig(provider="xai", model="xai/grok-4.6")
            provider = make_provider(config, preset_name="t")
            self.assertEqual(getattr(provider, "_effective_base", None), "https://api.x.ai/v1")


class XaiOAuthRegistryTest(unittest.TestCase):
    def test_subscription_is_a_separate_oauth_provider(self):
        spec = find_by_name("xai_oauth")
        assert spec is not None
        self.assertTrue(spec.is_oauth)
        self.assertEqual(spec.backend, "xai_oauth")
        self.assertEqual(spec.default_api_base, "https://cli-chat-proxy.grok.com/v1")
        self.assertTrue(spec.strip_model_prefix)

    def test_subscription_headers_identify_the_grok_cli(self):
        headers = subscription_headers()
        self.assertEqual(headers["X-XAI-Token-Auth"], "xai-grok-cli")
        self.assertEqual(headers["x-grok-client-identifier"], "grok-shell")


class TokenPayloadTest(unittest.TestCase):
    def test_missing_expiry_falls_back_to_thirty_days(self):
        before = time.time()
        token = _token_from_payload({"access_token": "tok", "refresh_token": "ref"})
        after = time.time()
        self.assertEqual(token.access, "tok")
        self.assertEqual(token.refresh, "ref")
        expected_min = int((before + 30 * 24 * 60 * 60) * 1000)
        expected_max = int((after + 30 * 24 * 60 * 60) * 1000)
        self.assertGreaterEqual(token.expires, expected_min)
        self.assertLessEqual(token.expires, expected_max)

    def test_id_token_email_becomes_the_account_badge(self):
        token = _token_from_payload(
            {
                "access_token": "tok",
                "id_token": _jwt({"email": "user@x.ai"}),
            }
        )
        self.assertEqual(token.account_id, "user@x.ai")

    def test_account_from_id_token_ignores_garbage(self):
        self.assertIsNone(_account_from_id_token(""))
        self.assertIsNone(_account_from_id_token("not-a-jwt"))


class DeviceFlowTest(unittest.TestCase):
    def test_prints_the_verification_url_then_exchanges_the_code(self):
        messages: list[str] = []

        class _Response:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        class _Client:
            def __init__(self, *args, **kwargs):
                self.calls: list[tuple[str, dict]] = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, headers=None, data=None):
                self.calls.append((url, dict(data or {})))
                if url.endswith("/device/code"):
                    return _Response(
                        200,
                        {
                            "device_code": "dev-1",
                            "user_code": "ABCD-EFGH",
                            "verification_uri_complete": "https://auth.x.ai/activate?user_code=ABCD-EFGH",
                            "interval": 1,
                            "expires_in": 60,
                        },
                    )
                return _Response(
                    200,
                    {
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "expires_in": 3600,
                    },
                )

        fake_client = _Client()
        with (
            patch("navin.providers.xai_oauth_provider.httpx.Client", return_value=fake_client),
            patch("navin.providers.xai_oauth_provider.get_storage") as storage,
        ):
            saved = MagicMock()
            storage.return_value.save = saved
            token = login_xai_oauth(print_fn=messages.append)

        self.assertEqual(token.access, "access-1")
        self.assertEqual(token.refresh, "refresh-1")
        self.assertTrue(any("https://auth.x.ai/activate" in line for line in messages))
        self.assertTrue(any("ABCD-EFGH" in line for line in messages))
        device_call = fake_client.calls[0][1]
        self.assertEqual(device_call["client_id"], CLIENT_ID)
        self.assertEqual(device_call["scope"], SCOPE)
        token_call = fake_client.calls[1][1]
        self.assertEqual(token_call["grant_type"], DEVICE_CODE_GRANT)
        self.assertEqual(token_call["device_code"], "dev-1")
        saved.assert_called_once()


if __name__ == "__main__":
    unittest.main()
