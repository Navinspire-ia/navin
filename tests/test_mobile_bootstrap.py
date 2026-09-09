# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for mobile bootstrap orchestration (no network)."""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.mobile.adb import AdbLocation
from navin.mobile.bootstrap import (
    acceleration_available,
    emulator_abi,
    render_bootstrap_result,
    run_bootstrap,
    system_image_package,
)


class AccelerationTest(unittest.TestCase):
    def test_acceleration_reports_bool_and_detail(self):
        ok, detail = acceleration_available()
        self.assertIsInstance(ok, bool)
        self.assertTrue(detail)

    def test_system_image_matches_host_abi(self):
        abi = emulator_abi()
        self.assertIn(abi, {"x86_64", "arm64-v8a"})
        self.assertEqual(
            system_image_package(),
            f"system-images;android-34;google_apis;{abi}",
        )

    def test_macos_arm_uses_arm64_image(self):
        with patch("navin.mobile.bootstrap.host_cpu_arch", return_value="arm64"):
            self.assertEqual(emulator_abi(), "arm64-v8a")
            self.assertIn("arm64-v8a", system_image_package())

    def test_wsl_without_kvm_blocks_local_emulator(self):
        from navin.mobile.bootstrap import host_mobile_capability

        with patch("navin.mobile.bootstrap.host_platform", return_value="wsl"), patch(
            "navin.mobile.bootstrap.Path.exists", return_value=False
        ), patch(
            "navin.mobile.bootstrap.soft_accel_emulator_running", return_value=False
        ):
            cap = host_mobile_capability()
        self.assertFalse(cap["acceleration_ok"])
        self.assertTrue(cap["block_local_emulator"])
        self.assertTrue(cap["recommendations"])

    def test_kvm_exists_but_denied_blocks(self):
        from navin.mobile.bootstrap import acceleration_available

        with patch("navin.mobile.bootstrap.host_platform", return_value="wsl"), patch(
            "navin.mobile.bootstrap.Path.exists", return_value=True
        ), patch(
            "navin.mobile.bootstrap.os.open",
            side_effect=PermissionError("denied"),
        ):
            ok, detail = acceleration_available()
        self.assertFalse(ok)
        self.assertIn("not accessible", detail.lower())
        self.assertIn("usermod", detail)
        self.assertIn("wsl --shutdown", detail)
        self.assertIn("PowerShell", detail)

    def test_linux_kvm_denied_mentions_relogin_not_only_wsl(self):
        from navin.mobile.bootstrap import acceleration_available

        with patch("navin.mobile.bootstrap.host_platform", return_value="linux"), patch(
            "navin.mobile.bootstrap.Path.exists", return_value=True
        ), patch(
            "navin.mobile.bootstrap.os.open",
            side_effect=PermissionError("denied"),
        ):
            ok, detail = acceleration_available()
        self.assertFalse(ok)
        self.assertIn("usermod", detail)
        self.assertNotIn("re-login to WSL", detail)

    def test_macos_and_windows_accel_ok_by_default(self):
        from navin.mobile.bootstrap import acceleration_available

        with patch("navin.mobile.bootstrap.host_platform", return_value="macos"):
            ok, _ = acceleration_available()
            self.assertTrue(ok)
        with patch("navin.mobile.bootstrap.host_platform", return_value="windows"):
            ok, _ = acceleration_available()
            self.assertTrue(ok)

    def test_soft_emu_keeps_usb_phone_usable(self):
        from navin.mobile.bootstrap import _is_emulator_serial

        self.assertTrue(_is_emulator_serial("emulator-5554 device"))
        self.assertFalse(_is_emulator_serial("R58M123ABCD device"))

    def test_macos_host_prereqs_detects_applications_studio(self):
        from navin.mobile.bootstrap import _ensure_android_studio_macos

        logs: list[str] = []
        fake = Path("/Applications/Android Studio.app")
        with patch("navin.mobile.bootstrap.Path.exists", return_value=True):
            # Patch candidates loop by making first path exist
            with patch(
                "navin.mobile.bootstrap.Path",
                wraps=Path,
            ):
                pass
        with patch.object(Path, "exists", return_value=True):
            out = _ensure_android_studio_macos(logs)
        self.assertTrue(out["installed"])
        self.assertFalse(out["attempted"])
        del fake



class BootstrapDryRunTest(unittest.TestCase):
    def test_stops_when_adb_cannot_install(self):
        async def _run() -> dict:
            with patch(
                "navin.mobile.bootstrap._ensure_host_prereqs",
                return_value={"kvm_fix": None, "android_studio": {}},
            ), patch(
                "navin.mobile.bootstrap.resolve_adb_location",
                return_value=None,
            ), patch(
                "navin.mobile.bootstrap.try_install_adb",
                return_value={"ok": False, "help": "install adb"},
            ):
                return await run_bootstrap(start_emulator=False)

        payload = asyncio.run(_run())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["step"], "adb")
        self.assertIn("adb", (payload.get("next") or "").lower())

    def test_ready_when_device_already_online(self):
        async def _run() -> dict:
            with TemporaryDirectory() as tmp:
                sdk = Path(tmp) / "sdk"
                sdk.mkdir()
                loc = AdbLocation(adb="/usr/bin/adb", sdk=str(sdk), source="test")
                with patch(
                    "navin.mobile.bootstrap.resolve_adb_location",
                    return_value=loc,
                ), patch(
                    "navin.mobile.bootstrap._ensure_host_prereqs",
                    return_value={
                        "kvm_fix": None,
                        "android_studio": {"installed": True, "path": "/fake"},
                    },
                ), patch(
                    "navin.mobile.bootstrap._ensure_jdk",
                    return_value=str(Path(tmp) / "jdk"),
                ), patch(
                    "navin.mobile.bootstrap._ensure_cmdline_tools",
                    return_value=str(sdk / "sdkmanager"),
                ), patch(
                    "navin.mobile.bootstrap._ensure_sdk_packages",
                    return_value=True,
                ), patch(
                    "navin.mobile.bootstrap._ensure_avd",
                    return_value=True,
                ), patch(
                    "navin.mobile.bootstrap._list_devices",
                    return_value=["emulator-5554"],
                ), patch(
                    "navin.mobile.bootstrap.acceleration_available",
                    return_value=(False, "no kvm"),
                ):
                    return await run_bootstrap(start_emulator=True)

        payload = asyncio.run(_run())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["device"], "emulator-5554")
        self.assertEqual(payload["step"], "ready")
        text = render_bootstrap_result(payload)
        self.assertIn("emulator-5554", text)

    def test_host_prereqs_sets_kvm_fix_when_denied(self):
        from navin.mobile.bootstrap import _ensure_host_prereqs

        logs: list[str] = []
        with patch("navin.mobile.bootstrap.host_platform", return_value="wsl"), patch(
            "navin.mobile.bootstrap.Path.exists", return_value=True
        ), patch(
            "navin.mobile.bootstrap.os.open",
            side_effect=PermissionError("denied"),
        ), patch(
            "navin.mobile.bootstrap._ensure_android_studio_windows",
            return_value={"installed": False, "attempted": True},
        ):
            out = _ensure_host_prereqs(logs)
        self.assertEqual(out.get("kvm_fix"), "sudo usermod -aG kvm $USER")
        self.assertTrue(out.get("android_studio", {}).get("attempted"))


if __name__ == "__main__":
    unittest.main()
