# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Watchdog must start a dead gateway and must never SIGTERM a live one."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.gateway.watchdog import ensure_gateway_health
from navin.process_runtime import ProcessResult, ProcessStartOptions, ProcessStatus


class _FakeRuntime:
    def __init__(self, *, running: bool) -> None:
        self.running = running
        self.starts = 0
        self.restarts = 0

    def status(self) -> ProcessStatus:
        return ProcessStatus(
            running=self.running,
            pid=1 if self.running else None,
            state_path=".",
            log_path=".",
            reason="running" if self.running else "not_started",
        )

    def start_background(self, options: ProcessStartOptions) -> ProcessResult:
        self.starts += 1
        self.running = True
        return ProcessResult(True, "gateway_started_background", self.status())

    def restart(self, options: ProcessStartOptions, *, timeout_s: int = 20) -> ProcessResult:
        self.restarts += 1
        return ProcessResult(True, "gateway_restarted", self.status())


class GatewayWatchdogTest(unittest.TestCase):
    def test_port_open_is_healthy_without_restart(self) -> None:
        runtime = _FakeRuntime(running=True)
        options = ProcessStartOptions(port=8766)
        action = ensure_gateway_health(
            runtime,
            options,
            probe_port=lambda host, port, timeout_s: True,
        )
        self.assertEqual(action, "healthy")
        self.assertEqual(runtime.restarts, 0)
        self.assertEqual(runtime.starts, 0)

    def test_dead_port_starts_without_restart(self) -> None:
        runtime = _FakeRuntime(running=False)
        options = ProcessStartOptions(port=8766)
        action = ensure_gateway_health(
            runtime,
            options,
            probe_port=lambda host, port, timeout_s: False,
        )
        self.assertEqual(action, "started")
        self.assertEqual(runtime.starts, 1)
        self.assertEqual(runtime.restarts, 0)

    def test_live_but_not_listening_is_left_alone(self) -> None:
        runtime = _FakeRuntime(running=True)
        options = ProcessStartOptions(port=8766)
        action = ensure_gateway_health(
            runtime,
            options,
            probe_port=lambda host, port, timeout_s: False,
        )
        self.assertEqual(action, "starting")
        self.assertEqual(runtime.restarts, 0)
        self.assertEqual(runtime.starts, 0)


if __name__ == "__main__":
    unittest.main()
