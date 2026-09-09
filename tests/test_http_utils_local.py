# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Local-client helpers used by Preview Publish and workspace controls."""

from __future__ import annotations

import ipaddress
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from navin.webui.http_utils import (
    is_localhost,
    is_same_machine_client,
)


class LocalClientTests(unittest.TestCase):
    def test_loopback_is_local(self) -> None:
        self.assertTrue(is_localhost(SimpleNamespace(remote_address=("127.0.0.1", 9))))
        self.assertTrue(
            is_same_machine_client(SimpleNamespace(remote_address=("127.0.0.1", 9)))
        )

    def test_public_ip_is_not_this_machine(self) -> None:
        self.assertFalse(
            is_same_machine_client(SimpleNamespace(remote_address=("8.8.8.8", 9)))
        )

    def test_wsl_host_on_the_same_interface_network_is_this_machine(self) -> None:
        nets = [ipaddress.ip_network("172.29.208.0/20")]
        with patch(
            "navin.webui.http_utils._iter_local_ipv4_networks",
            return_value=nets,
        ):
            self.assertTrue(
                is_same_machine_client(
                    SimpleNamespace(remote_address=("172.29.208.1", 44332))
                )
            )
            self.assertFalse(
                is_same_machine_client(
                    SimpleNamespace(remote_address=("192.168.1.40", 44332))
                )
            )


if __name__ == "__main__":
    unittest.main()
