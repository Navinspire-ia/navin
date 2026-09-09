# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Channel setup validation uses real vendor checks, not invented steps."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from navin.webui.channel_validation import _validate_msteams, _validate_signal


class ChannelValidationHonestyTest(unittest.TestCase):
    def test_signal_reports_missing_daemon(self) -> None:
        client = MagicMock()
        client.get.side_effect = Exception("connection refused")
        with patch("navin.webui.channel_validation.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value = client
            payload = _validate_signal(
                "signal",
                {"phoneNumber": "+15551212", "daemonHost": "localhost", "daemonPort": 8080},
            )
        self.assertEqual(payload["status"], "invalid")
        self.assertTrue(any(check["id"] == "daemon" and check["status"] == "fail" for check in payload["checks"]))
        self.assertIn("signal-cli", payload["checks"][-1]["message"])

    def test_teams_warns_that_localhost_is_not_enough(self) -> None:
        payload = _validate_msteams(
            "msteams",
            {
                "appId": "11111111-1111-1111-1111-111111111111",
                "appPassword": "secret",
            },
        )
        self.assertEqual(payload["status"], "configured")
        callback = next(check for check in payload["checks"] if check["id"] == "callback")
        self.assertEqual(callback["status"], "warn")
        self.assertIn("public HTTPS", callback["message"])
        self.assertEqual(callback.get("action_url"), "https://dev.teams.microsoft.com/apps")


if __name__ == "__main__":
    unittest.main()
