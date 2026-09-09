# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A restart reclaims a gateway the state file forgot about.

Regression: after the state file was rewritten, ``navin gateway restart`` saw
"not running", started a second gateway that died on "address already in use",
and the old process kept answering the health probe. The caller read
"restarted" and kept running the old code.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.process_runtime import (
    ManagedProcessRuntime,
    ProcessRuntimePaths,
    ProcessStartOptions,
)


def _runtime(root: Path) -> ManagedProcessRuntime:
    run_dir = root / "run"
    logs_dir = root / "logs"
    return ManagedProcessRuntime(
        paths=ProcessRuntimePaths(
            run_dir=run_dir,
            logs_dir=logs_dir,
            state_path=run_dir / "gateway.json",
            log_path=logs_dir / "gateway.log",
        ),
        sleep=lambda _s: None,
    )


def _row(status: str, pid: int | None) -> SimpleNamespace:
    listener = SimpleNamespace(pid=pid, cmdline="python -m navin gateway") if pid else None
    return SimpleNamespace(status=status, listener=listener)


class ReclaimOrphanListenersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = _runtime(Path(self._tmp.name))
        self.options = ProcessStartOptions(port=18791)

    def _reclaim(self, rows: list[SimpleNamespace]) -> list[int]:
        killed: list[int] = []
        with mock.patch("navin.ports.check_ports", return_value=rows), mock.patch(
            "navin.config.loader.load_config", return_value=None
        ), mock.patch.object(
            self.runtime,
            "_terminate",
            side_effect=lambda pid, *, timeout_s: killed.append(pid) or True,
        ):
            self.runtime._reclaim_orphan_listeners(self.options, timeout_s=5)
        return killed

    def test_a_forgotten_navin_gateway_on_our_port_is_stopped_once(self) -> None:
        killed = self._reclaim([_row("navin", 4242), _row("navin", 4242)])
        self.assertEqual(killed, [4242])

    def test_foreign_listeners_and_free_ports_are_left_alone(self) -> None:
        killed = self._reclaim([_row("free", None), _row("busy", 999)])
        self.assertEqual(killed, [])

    def test_the_current_process_is_never_its_own_orphan(self) -> None:
        killed = self._reclaim([_row("navin", os.getpid())])
        self.assertEqual(killed, [])

    def test_a_port_probe_failure_does_not_break_the_restart(self) -> None:
        with mock.patch(
            "navin.ports.check_ports", side_effect=RuntimeError("no ss")
        ), mock.patch("navin.config.loader.load_config", return_value=None):
            self.runtime._reclaim_orphan_listeners(self.options, timeout_s=5)


if __name__ == "__main__":
    unittest.main()
