# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime health must name RAM vs disk instead of a vague Host critical."""

from __future__ import annotations

import sys
import unittest
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.webui.runtime_health import _CpuSampler, _memory_snapshot, runtime_health_payload


class RuntimeHealthPayloadTest(unittest.TestCase):
    def setUp(self) -> None:
        cpu = mock.patch("navin.webui.runtime_health._cpu_sampler.snapshot", return_value=(0.2, False))
        cpu.start()
        self.addCleanup(cpu.stop)

    def test_ok_when_resources_are_fine(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch(
                "navin.webui.runtime_health._memory_snapshot",
                return_value=(0.4, 8.0),
            ), mock.patch(
                "navin.webui.runtime_health._disk_usage",
                return_value=(0.5, 20.0),
            ):
                payload = runtime_health_payload(workspace=tmp)
        self.assertFalse(payload["pressure"])
        self.assertEqual(payload["level"], "ok")
        self.assertEqual(payload["reasons"], [])
        self.assertIsNone(payload["label"])

    def test_memory_critical_label_says_ram(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch(
                "navin.webui.runtime_health._memory_snapshot",
                return_value=(0.95, 0.4),
            ), mock.patch(
                "navin.webui.runtime_health._disk_usage",
                return_value=(0.5, 20.0),
            ):
                payload = runtime_health_payload(workspace=tmp)
        self.assertTrue(payload["pressure"])
        self.assertEqual(payload["level"], "critical")
        self.assertEqual(payload["reasons"], ["memory"])
        self.assertEqual(payload["label"], "RAM 95%")

    def test_disk_critical_label_says_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch(
                "navin.webui.runtime_health._memory_snapshot",
                return_value=(0.4, 8.0),
            ), mock.patch(
                "navin.webui.runtime_health._disk_usage",
                return_value=(0.97, 0.4),
            ):
                payload = runtime_health_payload(workspace=tmp)
        self.assertEqual(payload["reasons"], ["disk"])
        self.assertEqual(payload["label"], "Disk 97%")
        self.assertIn("disk", (payload["message"] or "").lower())

    def test_shape_still_includes_pid_and_paths(self) -> None:
        payload = runtime_health_payload(workspace=str(Path.home()))
        self.assertIn("pressure", payload)
        self.assertIn("level", payload)
        self.assertIn("reasons", payload)
        self.assertIn("pid", payload)
        self.assertIn(payload["level"], {"ok", "warning", "critical"})

    def test_large_machine_with_available_capacity_does_not_recommend_restart(self) -> None:
        with mock.patch("navin.webui.runtime_health._memory_snapshot", return_value=(0.9, 8)), \
             mock.patch("navin.webui.runtime_health._disk_usage", return_value=(0.96, 40)):
            payload = runtime_health_payload()
        self.assertFalse(payload["pressure"])
        self.assertEqual(payload["cpu"]["usedRatio"], 0.2)
        self.assertEqual(payload["scope"], "machine")

    def test_cpu_alert_does_not_hide_disk_and_memory(self) -> None:
        with mock.patch("navin.webui.runtime_health._memory_snapshot", return_value=(0.95, 0.4)), \
             mock.patch("navin.webui.runtime_health._disk_usage", return_value=(0.97, 0.4)), \
             mock.patch("navin.webui.runtime_health._cpu_sampler.snapshot", return_value=(0.98, True)):
            payload = runtime_health_payload()
        self.assertEqual(payload["reasons"], ["memory", "disk", "cpu"])
        self.assertEqual(payload["level"], "critical")
        self.assertNotIn("reload", payload["message"])


class ResourceSamplingTest(unittest.TestCase):
    def test_cpu_requires_a_measurement_window_and_sustained_load(self) -> None:
        counters = namedtuple("Counters", "user system idle")
        sampler = _CpuSampler()
        with mock.patch("navin.webui.runtime_health.time.monotonic", side_effect=[0, 10, 10.2, 30, 40]), \
             mock.patch("navin.webui.runtime_health.psutil.cpu_times", side_effect=[
                 counters(0, 0, 100), counters(95, 0, 105), counters(285, 0, 115), counters(290, 0, 210),
             ]) as probe:
            self.assertEqual(sampler.snapshot(), (None, False))
            ratio, pressure = sampler.snapshot()
            self.assertAlmostEqual(ratio, 0.95)
            self.assertFalse(pressure)
            self.assertEqual(sampler.snapshot(), (ratio, False))
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(sampler.snapshot(), (ratio, True))
            ratio, pressure = sampler.snapshot()
            self.assertAlmostEqual(ratio, 0.05)
            self.assertFalse(pressure)

    def test_cpu_recovers_after_probe_failure_without_a_stale_alert(self) -> None:
        sampler = _CpuSampler()
        with mock.patch("navin.webui.runtime_health.psutil.cpu_times", side_effect=OSError("unavailable")):
            self.assertEqual(sampler.snapshot(), (None, False))

    def test_available_memory_includes_reclaimable_cache(self) -> None:
        with mock.patch("navin.webui.runtime_health.psutil.virtual_memory", return_value=mock.Mock(
            total=8 * 1024**3, available=3 * 1024**3, free=0.1 * 1024**3,
        )):
            self.assertEqual(_memory_snapshot(), (0.625, 3))


class EngineIdentityTest(unittest.TestCase):
    """Desktop shells attach to any listening gateway: the health payload must
    expose which navin build actually serves the endpoint, so a stale sidecar
    attached by a fresh install is diagnosable instead of looking like random
    chat/session bugs."""

    def test_payload_carries_engine_version_and_executable(self) -> None:
        from navin import __version__ as navin_version

        payload = runtime_health_payload()
        self.assertEqual(payload["engine"]["version"], navin_version)
        self.assertEqual(payload["engine"]["executable"], sys.executable)


if __name__ == "__main__":
    unittest.main()
