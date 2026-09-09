# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Who is allowed to bootstrap the WebUI, and who has to prove it.

The installer generates a token issue secret without asking, so requiring it
from the browser on the same machine locks the user out of a URL their own
package printed. The rule below lets loopback through, but only while the
gateway is itself bound to loopback.
"""

from __future__ import annotations

import unittest

from navin.webui.ws_http import bootstrap_refusal

SECRET = "s3cret-value"


def refusal(
    *,
    secret: str = "",
    is_local_browser: bool = True,
    bind_host: str = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> tuple[int, str] | None:
    return bootstrap_refusal(
        secret=secret,
        is_local_browser=is_local_browser,
        bind_host=bind_host,
        headers=headers or {},
    )


class NoSecretTest(unittest.TestCase):
    def test_a_local_browser_is_let_through(self) -> None:
        self.assertIsNone(refusal())

    def test_anything_else_is_localhost_only(self) -> None:
        self.assertEqual(refusal(is_local_browser=False), (403, "bootstrap is localhost-only"))


class LoopbackBypassTest(unittest.TestCase):
    def test_a_local_browser_needs_no_secret_on_a_loopback_gateway(self) -> None:
        self.assertIsNone(refusal(secret=SECRET))

    def test_the_bypass_holds_for_every_loopback_spelling(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                self.assertIsNone(refusal(secret=SECRET, bind_host=host))

    def test_a_gateway_open_to_the_network_still_demands_the_secret(self) -> None:
        self.assertEqual(
            refusal(secret=SECRET, bind_host="0.0.0.0"),
            (401, "Unauthorized"),
        )

    def test_a_remote_caller_still_demands_the_secret(self) -> None:
        self.assertEqual(
            refusal(secret=SECRET, is_local_browser=False),
            (401, "Unauthorized"),
        )


class SecretPresentedTest(unittest.TestCase):
    def test_the_right_secret_opens_a_network_gateway(self) -> None:
        for header in ("X-Navin-Auth", "Authorization"):
            value = SECRET if header == "X-Navin-Auth" else f"Bearer {SECRET}"
            with self.subTest(header=header):
                self.assertIsNone(
                    refusal(
                        secret=SECRET,
                        is_local_browser=False,
                        bind_host="0.0.0.0",
                        headers={header: value},
                    )
                )

    def test_a_wrong_secret_is_refused(self) -> None:
        self.assertEqual(
            refusal(
                secret=SECRET,
                is_local_browser=False,
                bind_host="0.0.0.0",
                headers={"X-Navin-Auth": "wrong"},
            ),
            (401, "Unauthorized"),
        )


if __name__ == "__main__":
    unittest.main()
