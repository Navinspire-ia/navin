"""The WebUI account service mints one-time connect nonces, activates the
device through the browser handoff, and never breaks the payload offline."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from navin import license_client
from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.webui.account_api import (
    AccountApiError,
    WebUIAccountService,
    callback_html,
    plan_label,
)


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "https://navin.live/api/x"),
    )


class IsolatedConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        self.addCleanup(set_config_path, self._previous_path)
        self.addCleanup(self._tmp.cleanup)
        self.service = WebUIAccountService()


class ConnectNonceTest(IsolatedConfigTest):
    def test_the_url_points_at_the_site_with_a_one_time_state(self):
        with mock.patch(
            "navin.webui.account_api._license_site_reachable", return_value=True
        ):
            url = self.service.connect_url(
                callback="http://127.0.0.1:8766/webui/account/callback"
            )
        self.assertTrue(url.startswith("https://navin.live/en/connect?state="))
        self.assertNotIn("redirect=", url)
        self.assertNotIn("127.0.0.1", url)
        state = url.split("state=", 1)[1].split("&", 1)[0]
        self.assertTrue(self.service.take_state(state))
        # A nonce works exactly once.
        self.assertFalse(self.service.take_state(state))

    def test_an_unknown_state_is_refused(self):
        self.assertFalse(self.service.take_state("forged"))
        self.assertFalse(self.service.take_state(""))


class StatusPayloadTest(IsolatedConfigTest):
    def test_disconnected_state_is_clean(self):
        payload = self.service.status_payload(refresh=False)
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["plan"], "")
        self.assertNotIn("usage", payload)

    def test_activated_without_email_or_plan_does_not_crash(self):
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.plan = ""
        config.license.account_email = ""
        config.license.account_name = ""
        save_config(config)

        payload = self.service.status_payload(refresh=False)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["plan"], "free")
        self.assertEqual(payload["plan_label"], "")
        self.assertEqual(payload["email"], "")
        self.assertEqual(payload["name"], "")

    def test_a_crash_inside_status_returns_disconnected(self):
        with mock.patch.object(
            self.service, "_status_payload", side_effect=RuntimeError("boom")
        ):
            payload = self.service.status_payload(refresh=False)
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["plan"], "")
        self.assertNotIn("error", payload)

    def test_offline_validation_keeps_the_persisted_profile(self):
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.plan = "pro"
        config.license.account_email = "dev@example.com"
        config.license.account_name = "Dev"
        save_config(config)

        with mock.patch.object(
            httpx, "post", side_effect=httpx.ConnectError("offline")
        ):
            payload = self.service.status_payload(refresh=True)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["plan"], "pro")
        self.assertEqual(payload["plan_label"], "Pro")
        self.assertEqual(payload["email"], "dev@example.com")
        self.assertEqual(payload["name"], "Dev")
        # Offline is not an account error.
        self.assertNotIn("error", payload)
        self.assertNotIn("license_key_masked", payload)

    def test_forced_refresh_cannot_hammer_the_network(self):
        """A client loop asking for refresh=1 must not mean one call each.

        The WebUI pushed ``account_updated`` to every window, each window
        answered with a forced validation, and that validation could push
        again: measured at one blocking five-second round-trip every 1.7s,
        which starved the thread pool the local routes share.
        """
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.plan = "pro"
        save_config(config)

        with mock.patch.object(
            license_client, "validate", return_value={"status": "active", "plan": "pro"}
        ) as validate:
            for _ in range(20):
                self.service.status_payload(refresh=True)
        self.assertEqual(validate.call_count, 1)

    def test_an_account_mutation_is_never_delayed_by_the_floor(self):
        """Sign-in and sign-out drop the cache, so they still validate at once."""
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        save_config(config)

        with mock.patch.object(
            license_client, "validate", return_value={"status": "active", "plan": "pro"}
        ) as validate:
            self.service.status_payload(refresh=True)
            self.service.status_payload(refresh=True)
            self.assertEqual(validate.call_count, 1)
            # What activate() / activate_ticket() / disconnect() all do first.
            self.service._invalidate_cache()
            self.service.status_payload(refresh=True)
            self.assertEqual(validate.call_count, 2)

    def test_local_spend_is_not_overwritten_by_a_stale_validate(self):
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.plan = "pro"
        config.license.usage_budget_micro_usd = 64_000_000
        config.license.usage_spent_micro_usd = 1_200_000
        config.license.usage_used_percent = 2
        save_config(config)

        with mock.patch.object(
            license_client,
            "validate",
            return_value={
                "status": "active",
                "plan": "pro",
                "usage": {
                    "budgetMicroUsd": 64_000_000,
                    "spentMicroUsd": 700_000,
                    "usedPercent": 1,
                    "remainingEquivalentTokens": 0,
                    "mode": "normal",
                },
            },
        ):
            payload = self.service.status_payload(refresh=True)
        usage = payload.get("usage") or {}
        self.assertEqual(usage.get("spent_micro_usd"), 1_200_000)
        self.assertEqual(usage.get("used_percent"), 2)


class ActivateTest(IsolatedConfigTest):
    def test_a_successful_handoff_activates_and_stores_the_profile(self):
        body = {
            "activated": True,
            "activationToken": "tok-1",
            "plan": "plus",
            "profile": {"email": "a@b.c", "fullName": "Ada"},
        }
        validate_body = {"valid": True, "status": "active", "plan": "plus"}
        with mock.patch.object(
            httpx, "post", side_effect=[_response(200, body), _response(200, validate_body)]
        ):
            payload = self.service.activate("NAVIN-1111-2222-3333-4444")
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["plan"], "plus")
        self.assertEqual(payload["email"], "a@b.c")
        self.assertEqual(payload["name"], "Ada")

    def test_a_bad_key_raises_a_clean_error(self):
        with mock.patch.object(
            httpx,
            "post",
            return_value=_response(404, {"activated": False, "error": "invalid_license"}),
        ):
            with self.assertRaises(AccountApiError) as caught:
                self.service.activate("NAVIN-XXXX-XXXX-XXXX-XXXX")
        self.assertEqual(caught.exception.status, 403)

    def test_an_empty_key_is_rejected_before_any_network_call(self):
        with self.assertRaises(AccountApiError):
            self.service.activate("   ")


class DisconnectTest(IsolatedConfigTest):
    def test_it_forgets_the_account_and_the_managed_key(self):
        config = load_config()
        config.license.license_key = "NAVIN-AAAA-BBBB-CCCC-DDDD"
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.plan = "pro"
        config.license.managed_api_key = "sk-or-v1-managed"
        config.license.managed_provider = "openrouter"
        config.providers.openrouter.api_key = "sk-or-v1-managed"
        save_config(config)

        with mock.patch.object(httpx, "post", return_value=_response(200, {"ok": True})):
            payload = self.service.disconnect()
        self.assertFalse(payload["connected"])
        fresh = load_config()
        self.assertEqual(fresh.license.license_key, "")
        self.assertEqual(fresh.license.activation_token, "")
        self.assertEqual(fresh.providers.openrouter.api_key, "")

    def test_a_byok_key_survives_disconnection(self):
        config = load_config()
        config.license.activation_token = "tok"
        config.license.device = "fp"
        config.license.managed_api_key = "sk-or-v1-managed"
        config.providers.openrouter.api_key = "sk-or-v1-users-own"
        save_config(config)

        with mock.patch.object(httpx, "post", return_value=_response(200, {"ok": True})):
            self.service.disconnect()
        self.assertEqual(
            load_config().providers.openrouter.api_key, "sk-or-v1-users-own"
        )

    def test_disconnecting_releases_the_device_slot_on_the_server(self):
        """Logout must free the device slot, otherwise the quota fills up and
        the account ends blocked in device_limit_reached."""
        config = load_config()
        config.license.activation_token = "tok-123"
        config.license.device = "fp-abcdef123456"
        save_config(config)

        with mock.patch.object(
            httpx, "post", return_value=_response(200, {"ok": True})
        ) as post:
            self.service.disconnect()

        self.assertEqual(post.call_count, 1)
        url = post.call_args.args[0]
        payload = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/api/license/deactivate"))
        self.assertEqual(payload["device"], "fp-abcdef123456")
        self.assertEqual(payload["activationToken"], "tok-123")

    def test_disconnect_still_works_offline(self):
        config = load_config()
        config.license.activation_token = "tok"
        config.license.device = "fp-abcdef123456"
        save_config(config)

        with mock.patch.object(httpx, "post", side_effect=httpx.ConnectError("down")):
            payload = self.service.disconnect()
        self.assertFalse(payload["connected"])
        self.assertEqual(load_config().license.activation_token, "")


class HelpersTest(unittest.TestCase):
    def test_plan_labels(self):
        self.assertEqual(plan_label("pro"), "Pro")
        self.assertEqual(plan_label("ULTRA"), "Ultra")
        self.assertEqual(plan_label("custom"), "Custom")
        self.assertEqual(plan_label(None), "")
        self.assertEqual(plan_label(""), "")

    def test_callback_pages_render(self):
        self.assertIn("close this tab", callback_html(ok=True))
        self.assertIn("failed", callback_html(ok=False, detail="Nope").lower())
        self.assertIn("Nope", callback_html(ok=False, detail="Nope"))


if __name__ == "__main__":
    unittest.main()
