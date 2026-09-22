# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An update is available only when this OS has a matching signed artifact."""

from __future__ import annotations

import unittest
from unittest import mock

from navin.update import service


def _windows_only_manifest(version: str = "1.0.1") -> dict:
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "version": version,
        "minimumVersion": "",
        "rollout": 100,
        "notes": "Windows-only drop",
        "artifacts": {
            "windows-setup-x64": {
                "url": f"releases/{version}/Navin-Desktop-{version}-windows-x64-setup.exe",
                "filename": f"Navin-Desktop-{version}-windows-x64-setup.exe",
                "size": 1024,
                "sha256": "a" * 64,
            }
        },
    }


class PlatformScopedUpdateTest(unittest.TestCase):
    def test_manual_check_explains_missing_artifact_instead_of_up_to_date(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="cli"),
            mock.patch.object(service, "_platform_keys", return_value=("cli-linux-x64",)),
            mock.patch.object(service, "_update_config", return_value=("https://updates.navin.live", "stable", "")),
            mock.patch.object(service, "_fetch_manifest", return_value=_windows_only_manifest()),
            mock.patch.object(service, "_CACHE", (0, None)),
            mock.patch.object(service, "_set_checked_release"),
        ):
            result = service.check_for_update(force=True)
        self.assertFalse(result["available"])
        self.assertEqual(result["latestVersion"], "1.0.1")
        self.assertIn("no signed update package", result["reason"])

    def test_manual_check_reconsiders_skipped_version(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="windows-setup"),
            mock.patch.object(service, "_platform_keys", return_value=("windows-setup-x64",)),
            mock.patch.object(service, "_update_config", return_value=("https://updates.navin.live", "stable", "1.0.1")),
            mock.patch.object(service, "_fetch_manifest", return_value=_windows_only_manifest()),
            mock.patch.object(service, "_CACHE", (0, None)),
            mock.patch.object(service, "_set_checked_release"),
        ):
            result = service.check_for_update(force=True)
        self.assertTrue(result["available"])

    def test_macos_is_silent_when_only_windows_shipped(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="macos-app"),
            mock.patch.object(service.platform, "machine", return_value="arm64"),
        ):
            info = service._release_info(
                _windows_only_manifest(),
                base_url="https://updates.navin.live",
                skipped_version="",
            )
        self.assertIsNone(info)

    def test_linux_is_silent_when_only_windows_shipped(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="linux"),
            mock.patch.object(service.platform, "machine", return_value="x86_64"),
        ):
            info = service._release_info(
                _windows_only_manifest(),
                base_url="https://updates.navin.live",
                skipped_version="",
            )
        self.assertIsNone(info)

    def test_windows_sees_its_own_artifact(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="windows-setup"),
            mock.patch.object(service.platform, "machine", return_value="AMD64"),
        ):
            info = service._release_info(
                _windows_only_manifest(),
                base_url="https://updates.navin.live",
                skipped_version="",
            )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info["available"])
        self.assertTrue(info["supported"])
        self.assertEqual(info["latestVersion"], "1.0.1")

    def test_source_checkout_is_told_but_not_auto_installed(self):
        with (
            mock.patch.object(service, "__version__", "1.0.0"),
            mock.patch.object(service, "_install_kind", return_value="source"),
        ):
            info = service._release_info(
                _windows_only_manifest(),
                base_url="https://updates.navin.live",
                skipped_version="",
            )
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info["available"])
        self.assertFalse(info["supported"])
        self.assertIn("navin update", str(info.get("reason") or ""))
