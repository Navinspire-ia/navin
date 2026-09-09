# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime health must name RAM vs disk instead of a vague Host critical."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from navin.webui.runtime_health import runtime_health_payload


class RuntimeHealthPayloadTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
