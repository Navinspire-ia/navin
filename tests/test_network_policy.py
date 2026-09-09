# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Who the agent may reach over the network, and who decides.

Private-range blocking is off unless an operator turns it on. The ranges it
covers are where a dev server, a staging host and a sibling container live, so
leaving it on by default stopped ordinary work - "check that the server you just
started answers" - far more often than it stopped an attack. These tests pin
both halves: nothing is blocked by default, and everything that used to be
blocked still is once the switch is on.
"""

from __future__ import annotations

import unittest

from navin.config.schema import Config
from navin.security.network import (
    configure_ssrf_protection,
    configure_ssrf_whitelist,
    contains_internal_url,
    ssrf_protection_enabled,
    validate_url_target,
)

# Literal addresses, so nothing here depends on DNS or on a host being up.
PRIVATE_URLS = (
    "http://127.0.0.1:3000/health",
    "http://localhost:8080/",
    "http://10.1.2.3/admin",
    "http://192.168.1.10:5432/",
    "http://172.16.0.5/",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://[::1]:9000/",
)


class _PolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(configure_ssrf_protection, False)
        self.addCleanup(configure_ssrf_whitelist, [])


class DefaultTest(_PolicyTest):
    def test_the_switch_is_off_out_of_the_box(self) -> None:
        self.assertFalse(Config().tools.ssrf_protection)

    def test_a_fresh_config_leaves_protection_off(self) -> None:
        configure_ssrf_protection(Config().tools.ssrf_protection)
        self.assertFalse(ssrf_protection_enabled())

    def test_the_dev_server_is_reachable(self) -> None:
        configure_ssrf_protection(False)
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                ok, error = validate_url_target(url)
                self.assertTrue(ok, error)

    def test_a_command_naming_localhost_is_not_flagged(self) -> None:
        configure_ssrf_protection(False)
        self.assertFalse(contains_internal_url("curl -sS http://localhost:3000/health"))


class EnabledTest(_PolicyTest):
    """Turning it on restores every refusal, unchanged."""

    def setUp(self) -> None:
        super().setUp()
        configure_ssrf_protection(True)

    def test_private_targets_are_refused_again(self) -> None:
        for url in PRIVATE_URLS:
            with self.subTest(url=url):
                ok, error = validate_url_target(url)
                self.assertFalse(ok)
                self.assertIn("private/internal", error)

    def test_a_command_naming_localhost_is_flagged_again(self) -> None:
        self.assertTrue(contains_internal_url("curl -sS http://127.0.0.1:3000/health"))

    def test_the_whitelist_still_carves_out_a_range(self) -> None:
        configure_ssrf_whitelist(["192.168.0.0/16"])
        self.assertTrue(validate_url_target("http://192.168.1.10:5432/")[0])
        self.assertFalse(validate_url_target("http://10.1.2.3/admin")[0])

    def test_the_narrow_loopback_exception_is_unaffected(self) -> None:
        self.assertTrue(validate_url_target("http://127.0.0.1:3000/", allow_loopback=True)[0])
        self.assertFalse(validate_url_target("http://10.1.2.3/", allow_loopback=True)[0])


class SchemeTest(_PolicyTest):
    """Scheme and hostname checks are not part of the private-range policy."""

    def test_a_non_http_scheme_is_refused_either_way(self) -> None:
        for enabled in (False, True):
            with self.subTest(protection=enabled):
                configure_ssrf_protection(enabled)
                ok, error = validate_url_target("file:///etc/passwd")
                self.assertFalse(ok)
                self.assertIn("http/https", error)


if __name__ == "__main__":
    unittest.main()
