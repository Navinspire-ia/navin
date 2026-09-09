# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for navin.ports registry and conflict checks."""

from __future__ import annotations

import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from navin.ports import (
    PORT_REGISTRY,
    ListenerInfo,
    check_navin_ports,
    check_ports,
    format_port_table,
    is_navin_listener,
    port_in_use,
    port_registry_by_name,
    resolve_from_config,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PortRegistryTest(unittest.TestCase):
    def test_registry_has_navin_and_external_roles(self) -> None:
        by_name = port_registry_by_name()
        self.assertEqual(by_name["webui"].default_port, 8765)
        self.assertEqual(by_name["gateway"].default_port, 18790)
        self.assertEqual(by_name["api"].default_port, 8900)
        self.assertEqual(by_name["debugmcp"].owner, "external")
        self.assertEqual(by_name["figma"].owner, "external")
        self.assertEqual(len(PORT_REGISTRY), 5)

    def test_resolve_from_config_overrides(self) -> None:
        config = SimpleNamespace(
            channels=SimpleNamespace(
                websocket=SimpleNamespace(host="127.0.0.1", port=9123)
            ),
            gateway=SimpleNamespace(host="127.0.0.1", port=9124),
            api=SimpleNamespace(host="127.0.0.1", port=9125),
            tools=SimpleNamespace(
                mcp_servers={
                    "debugmcp": SimpleNamespace(url="http://127.0.0.1:3999/mcp"),
                }
            ),
        )
        resolved = {row.spec.name: row for row in resolve_from_config(config)}
        self.assertEqual(resolved["webui"].port, 9123)
        self.assertEqual(resolved["gateway"].port, 9124)
        self.assertEqual(resolved["api"].port, 9125)
        self.assertEqual(resolved["debugmcp"].port, 3999)
        self.assertEqual(resolved["figma"].port, 3845)


class PortProbeTest(unittest.TestCase):
    def test_ipv4_wildcard_uses_ipv4_loopback(self) -> None:
        with patch("navin.ports.socket.create_connection") as connect:
            connect.side_effect = OSError
            self.assertFalse(port_in_use("0.0.0.0", 8765))
        connect.assert_called_once_with(("127.0.0.1", 8765), timeout=0.25)

    def test_ipv6_wildcard_checks_both_loopback_families(self) -> None:
        with patch("navin.ports.socket.create_connection") as connect:
            connect.side_effect = OSError
            self.assertFalse(port_in_use("::", 8765))
        self.assertEqual(
            [call.args[0] for call in connect.call_args_list],
            [("::1", 8765), ("127.0.0.1", 8765)],
        )

    def test_port_in_use_false_for_free_port(self) -> None:
        port = _free_port()
        self.assertFalse(port_in_use("127.0.0.1", port))

    def test_port_in_use_true_when_listening(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = int(server.getsockname()[1])
        try:
            self.assertTrue(port_in_use("127.0.0.1", port))
        finally:
            server.close()

    def test_check_ports_marks_bound_navin_role_busy(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = int(server.getsockname()[1])
        config = SimpleNamespace(
            channels=SimpleNamespace(
                websocket=SimpleNamespace(host="127.0.0.1", port=port)
            ),
            gateway=SimpleNamespace(host="127.0.0.1", port=_free_port()),
            api=SimpleNamespace(host="127.0.0.1", port=_free_port()),
            tools=SimpleNamespace(mcp_servers={}),
        )
        try:
            results = {row.role.spec.name: row for row in check_ports(config)}
            self.assertEqual(results["webui"].status, "busy")
            conflicts = check_navin_ports(config)
            self.assertTrue(any(c.role.spec.name == "webui" for c in conflicts))
            table = format_port_table(list(results.values()))
            self.assertIn("webui", table)
            self.assertIn("busy", table)
        finally:
            server.close()

    def test_is_navin_listener_heuristic(self) -> None:
        self.assertTrue(
            is_navin_listener(
                ListenerInfo(
                    pid=1,
                    cmdline="/home/x/.venv/bin/python -m navin gateway --foreground",
                    source="test",
                )
            )
        )
        self.assertFalse(
            is_navin_listener(
                ListenerInfo(pid=1, cmdline="nginx: master process", source="test")
            )
        )
        self.assertFalse(is_navin_listener(None))


if __name__ == "__main__":
    unittest.main()
