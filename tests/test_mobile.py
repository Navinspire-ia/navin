# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for mobile project detection, doctor, run plans, and the mobile tool."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.mobile.detect import detect_mobile_project
from navin.mobile.doctor import DoctorCheck, DoctorReport, run_doctor
from navin.mobile.run import build_run_plan

_FLUTTER_PUBSPEC = """
name: app
dependencies:
  flutter:
    sdk: flutter
"""


def _write(root: Path, rel: str, content: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return target


class DetectMobileProjectTest(unittest.TestCase):
    def test_expo_from_dependency_and_app_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "name": "demo-expo",
                        "dependencies": {
                            "expo": "~52.0.0",
                            "react-native": "0.76.0",
                        },
                        "scripts": {"start": "expo start"},
                    }
                ),
            )
            _write(
                root,
                "app.json",
                json.dumps({"expo": {"name": "Demo", "slug": "demo"}}),
            )
            _write(root, "App.tsx", "export default function App() { return null }\n")
            project = detect_mobile_project(root)
            self.assertEqual(project.kind, "expo")
            self.assertEqual(project.name, "demo-expo")
            self.assertEqual(project.package_manager, "npm")
            self.assertIn("dependency: expo", project.evidence)

    def test_expo_preferred_over_react_native_when_both_present(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "dependencies": {
                            "expo": "51.0.0",
                            "react-native": "0.74.0",
                        }
                    }
                ),
            )
            project = detect_mobile_project(root)
            self.assertEqual(project.kind, "expo")

    def test_react_native_cli(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "name": "BareApp",
                        "dependencies": {"react-native": "0.76.0"},
                        "scripts": {
                            "start": "react-native start",
                            "android": "react-native run-android",
                        },
                    }
                ),
            )
            (root / "android").mkdir()
            (root / "ios").mkdir()
            (root / "yarn.lock").write_text("", encoding="utf-8")
            project = detect_mobile_project(root)
            self.assertEqual(project.kind, "react-native")
            self.assertTrue(project.has_android)
            self.assertTrue(project.has_ios)
            self.assertEqual(project.package_manager, "yarn")

    def test_flutter(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "pubspec.yaml",
                """
                name: flutter_demo
                environment:
                  sdk: ">=3.0.0 <4.0.0"
                dependencies:
                  flutter:
                    sdk: flutter
                """,
            )
            _write(root, "lib/main.dart", "void main() {}\n")
            project = detect_mobile_project(root)
            self.assertEqual(project.kind, "flutter")
            self.assertEqual(project.name, "flutter_demo")
            self.assertEqual(project.entry, "lib/main.dart")

    def test_none_for_plain_web_package(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps({"name": "web", "dependencies": {"react": "18.0.0"}}),
            )
            project = detect_mobile_project(root)
            self.assertEqual(project.kind, "none")
            self.assertIn("No mobile project", project.render())

    def test_pnpm_lock_detected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps({"dependencies": {"expo": "52.0.0"}}),
            )
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            project = detect_mobile_project(root)
            self.assertEqual(project.package_manager, "pnpm")


class DoctorAndRunPlanTest(unittest.TestCase):
    def test_doctor_none_project_is_not_ready(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = detect_mobile_project(root)
            report = run_doctor(project)
            self.assertFalse(report.ready)
            self.assertTrue(any(c.status == "missing" for c in report.checks))

    def test_run_plan_blocked_without_mobile_project(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = detect_mobile_project(root)
            plan = build_run_plan(project)
            self.assertTrue(plan.blocked)

    def test_expo_android_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "dependencies": {"expo": "52.0.0", "react-native": "0.76.0"},
                        "scripts": {"start": "expo start"},
                    }
                ),
            )
            (root / "node_modules").mkdir()
            project = detect_mobile_project(root)
            doctor = DoctorReport(
                project=project,
                checks=[
                    DoctorCheck("node", "ok", "node"),
                    DoctorCheck("package_manager", "ok", "npm"),
                    DoctorCheck("npx", "ok", "npx"),
                ],
                devices=["emulator-5554\tdevice"],
                avds=["Pixel_7"],
            )
            plan = build_run_plan(
                project, target="android", doctor=doctor, start_emulator=True
            )
            self.assertFalse(plan.blocked)
            self.assertIn("expo start", plan.packager_command)
            self.assertIn("--android", plan.packager_command)
            self.assertEqual(plan.emulator_command, "")

    def test_expo_plan_starts_emulator_when_no_device(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps({"dependencies": {"expo": "52.0.0"}}),
            )
            project = detect_mobile_project(root)
            doctor = DoctorReport(
                project=project,
                checks=[
                    DoctorCheck("node", "ok", "node"),
                    DoctorCheck("package_manager", "ok", "npm"),
                    DoctorCheck("npx", "ok", "npx"),
                ],
                devices=[],
                avds=["Pixel_8_API_34"],
            )
            plan = build_run_plan(
                project, target="android", doctor=doctor, start_emulator=True
            )
            self.assertEqual(plan.emulator_command, "emulator -avd Pixel_8_API_34")

    def test_run_plan_blocked_when_doctor_missing_required(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps({"dependencies": {"expo": "52.0.0"}}),
            )
            project = detect_mobile_project(root)
            doctor = DoctorReport(
                project=project,
                checks=[
                    DoctorCheck(
                        "node",
                        "missing",
                        "node not on PATH",
                        fix="Install Node.js",
                    ),
                    DoctorCheck("package_manager", "ok", "npm"),
                    DoctorCheck("npx", "ok", "npx"),
                ],
            )
            plan = build_run_plan(project, doctor=doctor)
            self.assertTrue(plan.blocked)
            self.assertIn("node", plan.blocked_reason)

    def test_flutter_plan_is_informational(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "pubspec.yaml",
                """
                name: app
                dependencies:
                  flutter:
                    sdk: flutter
                """,
            )
            project = detect_mobile_project(root)
            plan = build_run_plan(project, target="android")
            self.assertFalse(plan.blocked)
            # No device known yet: never "-d android" (matches nothing) and
            # never a bare run that stops on the device prompt.
            self.assertEqual(plan.packager_command, "flutter run --device-timeout 90")

    def test_flutter_android_targets_the_connected_serial(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pubspec.yaml", _FLUTTER_PUBSPEC)
            project = detect_mobile_project(root)
            doctor = DoctorReport(
                project=project,
                checks=[DoctorCheck("flutter", "ok", "3.24")],
                devices=["emulator-5554          device product:sdk"],
                avds=[],
            )
            plan = build_run_plan(
                project, target="android", doctor=doctor, start_emulator=False
            )
            self.assertEqual(plan.packager_command, "flutter run -d emulator-5554")

    def test_flutter_ios_targets_the_plugged_iphone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pubspec.yaml", _FLUTTER_PUBSPEC)
            _write(
                root,
                "ios/Runner.xcodeproj/project.pbxproj",
                'DEVELOPMENT_TEAM = ABCDE12345;\n',
            )
            project = detect_mobile_project(root)
            with patch("navin.utils.host.host_platform", return_value="macos"):
                with patch(
                    "navin.mobile.ios.ios_run_devices",
                    return_value=(["00008110-0008404E3E4B801E"], []),
                ):
                    plan = build_run_plan(project, target="ios")
        self.assertEqual(
            plan.packager_command,
            "flutter run -d 00008110-0008404E3E4B801E",
        )

    def test_flutter_ios_blocks_before_a_long_unsigned_build(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pubspec.yaml", _FLUTTER_PUBSPEC)
            _write(
                root,
                "ios/Runner.xcodeproj/project.pbxproj",
                'DEVELOPMENT_TEAM = "";\n',
            )
            project = detect_mobile_project(root)
            with patch("navin.utils.host.host_platform", return_value="macos"):
                with patch(
                    "navin.mobile.ios.ios_run_devices",
                    return_value=(["00008110-0008404E3E4B801E"], []),
                ):
                    plan = build_run_plan(project, target="ios")
        self.assertTrue(plan.blocked)
        self.assertIn("DEVELOPMENT_TEAM", plan.blocked_reason)

    def test_flutter_ios_blocks_when_no_device_is_online(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "pubspec.yaml", _FLUTTER_PUBSPEC)
            project = detect_mobile_project(root)
            with patch("navin.utils.host.host_platform", return_value="macos"):
                with patch(
                    "navin.mobile.ios.ios_run_devices", return_value=([], [])
                ):
                    plan = build_run_plan(project, target="ios")
        self.assertTrue(plan.blocked)
        self.assertIn("No iOS device online", plan.blocked_reason)


class MobileToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_detect_and_plan_actions(self):
        from navin.agent.tools.mobile import MobileTool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "name": "tool-expo",
                        "dependencies": {"expo": "52.0.0"},
                        "scripts": {"start": "expo start"},
                    }
                ),
            )
            tool = MobileTool(workspace=root, restrict_to_workspace=True)
            detected = await tool.execute(action="detect")
            self.assertIn("expo", detected.lower())
            with patch("navin.mobile.doctor.shutil.which", return_value=None):
                planned = await tool.execute(action="plan", target="android")
            self.assertIn("Run plan", planned)
            self.assertIn("Environment:", planned)

    async def test_unknown_action(self):
        from navin.agent.tools.mobile import MobileTool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = MobileTool(workspace=root, restrict_to_workspace=True)
            result = await tool.execute(action="fly")
            self.assertTrue(getattr(result, "is_error", False))
            self.assertIn("unknown action", str(result).lower())


class MobileCommandRegistrationTest(unittest.TestCase):
    def test_mobile_slash_command_registered(self):
        from navin.command.builtin import _WORKFLOW_BRIEFS, BUILTIN_COMMAND_SPECS

        commands = {spec.command for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/mobile", commands)
        self.assertIn("/mobile", _WORKFLOW_BRIEFS)
        title, skills, brief = _WORKFLOW_BRIEFS["/mobile"]
        self.assertEqual(title, "Run Mobile")
        self.assertIn("mobile-dev", skills)
        self.assertIn("mobile tool", brief.lower())


class AdbDiscoveryTest(unittest.TestCase):
    def test_resolve_via_navin_adb_env(self):
        from navin.mobile.adb import clear_adb_cache, resolve_adb_location

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "Sdk"
            platform_tools = sdk / "platform-tools"
            platform_tools.mkdir(parents=True)
            adb = platform_tools / "adb"
            adb.write_text("#!/bin/sh\necho adb\n", encoding="utf-8")
            adb.chmod(0o755)
            clear_adb_cache()
            with patch.dict(
                "os.environ",
                {"NAVIN_ADB": str(adb), "ANDROID_HOME": "", "ANDROID_SDK_ROOT": ""},
                clear=False,
            ):
                # Clear PATH interference for which("adb")
                with patch("navin.mobile.adb.shutil.which", return_value=None):
                    clear_adb_cache()
                    loc = resolve_adb_location()
            self.assertIsNotNone(loc)
            assert loc is not None
            self.assertEqual(loc.adb, str(adb))
            self.assertEqual(loc.source, "navin")
            self.assertEqual(loc.sdk, str(sdk))

    def test_resolve_from_sdk_platform_tools(self):
        from navin.mobile.adb import clear_adb_cache, resolve_adb_location

        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            sdk = home / "Android" / "Sdk"
            platform_tools = sdk / "platform-tools"
            platform_tools.mkdir(parents=True)
            adb = platform_tools / "adb"
            adb.write_text("#!/bin/sh\n", encoding="utf-8")
            adb.chmod(0o755)
            clear_adb_cache()
            with patch("navin.mobile.adb.Path.home", return_value=home):
                with patch("navin.mobile.adb.shutil.which", return_value=None):
                    with patch.dict(
                        "os.environ",
                        {
                            "NAVIN_ADB": "",
                            "ANDROID_HOME": "",
                            "ANDROID_SDK_ROOT": "",
                            "LOCALAPPDATA": "",
                        },
                        clear=False,
                    ):
                        clear_adb_cache()
                        loc = resolve_adb_location()
            self.assertIsNotNone(loc)
            assert loc is not None
            self.assertEqual(loc.adb, str(adb))
            self.assertEqual(loc.source, "linux")

    def test_preview_readiness_adb_missing(self):
        from navin.mobile.adb import clear_adb_cache, preview_readiness

        clear_adb_cache()
        with patch("navin.mobile.adb.resolve_adb_location", return_value=None):
            with patch("navin.mobile.adb.host_platform", return_value="wsl"):
                status = preview_readiness(auto_install=False)
        self.assertFalse(status["ready"])
        self.assertEqual(status["error"], "adb_missing")
        self.assertEqual(status["platform"], "wsl")
        self.assertTrue(status["fixes"])
        self.assertIn("apt", str(status["help"]).lower())

    def test_macos_help_mentions_homebrew_and_library_sdk(self):
        from navin.mobile.adb import adb_missing_fixes, adb_setup_help

        with patch("navin.mobile.adb.host_platform", return_value="macos"):
            help_text = adb_setup_help().lower()
            fixes = adb_missing_fixes()
        self.assertIn("homebrew", help_text)
        self.assertIn("library/android/sdk", help_text)
        self.assertIn("xcode", help_text)
        labels = " ".join(f["label"].lower() for f in fixes)
        self.assertIn("homebrew", labels)

    def test_windows_help_and_auto_install_path(self):
        from navin.mobile.adb import (
            adb_missing_fixes,
            adb_setup_help,
            can_auto_install_adb,
        )

        with patch("navin.mobile.adb.host_platform", return_value="windows"):
            help_text = adb_setup_help().lower()
            fixes = adb_missing_fixes()
            can_auto = can_auto_install_adb()
        self.assertTrue(can_auto)
        self.assertIn("localappdata", help_text)
        self.assertIn("android studio", help_text)
        joined = " ".join(
            f"{f.get('label', '')} {f.get('command', '')}".lower() for f in fixes
        )
        self.assertIn("choco", joined)
        self.assertIn("android_home", joined)

    def test_try_install_without_apt_falls_back_to_zip(self):
        from navin.mobile.adb import try_install_adb

        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "mobile-env.json"
            with patch("navin.mobile.adb._env_state_path", return_value=state_path):
                with patch("navin.mobile.adb.host_platform", return_value="linux"):
                    with patch(
                        "navin.mobile.adb.resolve_adb_location", return_value=None
                    ):
                        with patch("navin.mobile.adb.os.geteuid", return_value=1000):
                            with patch(
                                "navin.mobile.adb._sudo_nopasswd", return_value=False
                            ):
                                with patch(
                                    "navin.mobile.adb._download_platform_tools",
                                    return_value="/tmp/fake/adb",
                                ):
                                    result = try_install_adb()
            self.assertTrue(result["ok"])
            self.assertIn("platform-tools", str(result["detail"]).lower())
            # The install result is persisted for the next run.
            import json as _json

            saved = _json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["installed_adb"], "/tmp/fake/adb")

    def test_wsl_help_mentions_navin_zip_first(self):
        from navin.mobile.adb import adb_missing_fixes, adb_setup_help

        with patch("navin.mobile.adb.host_platform", return_value="wsl"):
            help_text = adb_setup_help().lower()
            fixes = adb_missing_fixes()
        self.assertIn("~/.navin", help_text)
        self.assertEqual(fixes[0]["id"], "navin_auto_wsl")

    def test_onboarding_guide_steps(self):
        from navin.mobile.adb import preview_readiness

        with patch("navin.mobile.adb.host_platform", return_value="wsl"):
            with patch(
                "navin.mobile.adb.resolve_adb_location",
                return_value=None,
            ):
                missing = preview_readiness(auto_install=False)
            self.assertEqual(missing["guide"]["current_step"], 1)
            self.assertEqual(missing["guide"]["steps"][0]["status"], "current")

            from navin.mobile.adb import AdbLocation

            loc = AdbLocation(adb="/tmp/adb", sdk="/tmp", source="linux")
            with patch("navin.mobile.adb.resolve_adb_location", return_value=loc):
                with patch(
                    "navin.mobile.adb._list_devices_detailed",
                    return_value=([], []),
                ):
                    with patch(
                        "navin.mobile.adb._alternate_adb_binaries", return_value=[]
                    ):
                        with patch(
                            "navin.mobile.adb._list_avds_with", return_value=[]
                        ):
                            no_dev = preview_readiness(auto_install=False)
            self.assertEqual(no_dev["guide"]["current_step"], 2)
            self.assertEqual(no_dev["guide"]["steps"][1]["status"], "current")

    def test_unauthorized_phone_is_surfaced_not_ignored(self):
        """A plugged phone pending the RSA prompt must not read as 'no device'."""
        from navin.mobile.adb import AdbLocation, preview_readiness

        loc = AdbLocation(adb="/tmp/adb", sdk="/tmp", source="linux")
        pending = "FA79X1A04705           unauthorized usb:1-2 transport_id:3"
        with patch("navin.mobile.adb.host_platform", return_value="linux"):
            with patch("navin.mobile.adb.resolve_adb_location", return_value=loc):
                with patch(
                    "navin.mobile.adb._list_devices_detailed",
                    return_value=([], [pending]),
                ):
                    with patch(
                        "navin.mobile.adb._alternate_adb_binaries", return_value=[]
                    ):
                        with patch(
                            "navin.mobile.adb._list_avds_with", return_value=[]
                        ):
                            status = preview_readiness(auto_install=False)
        self.assertFalse(status["ready"])
        self.assertEqual(status["error"], "device_unauthorized")
        self.assertEqual(status["pending_devices"], [pending])
        self.assertIn("USB debugging", str(status["help"]))
        self.assertIn("FA79X1A04705", str(status["help"]))

    def test_alternate_adb_is_probed_when_primary_sees_nothing(self):
        """WSL: the Linux adb sees nothing, the Windows adb.exe owns the phone."""
        from navin.mobile import adb as mobile_adb
        from navin.mobile.adb import AdbLocation, preview_readiness

        primary = AdbLocation(adb="/usr/bin/adb", sdk=None, source="path")
        alt = "/mnt/c/Users/me/AppData/Local/Android/Sdk/platform-tools/adb.exe"
        phone = "R58M42XYZ              device product:beyond1 transport_id:2"

        def detailed(adb, *, timeout_s=12):
            if adb == alt:
                return [phone], []
            return [], []

        old_preferred = mobile_adb._preferred_adb
        try:
            with TemporaryDirectory() as tmp:
                state_path = Path(tmp) / "mobile-env.json"
                with patch(
                    "navin.mobile.adb._env_state_path", return_value=state_path
                ):
                    with patch("navin.mobile.adb.host_platform", return_value="wsl"):
                        with patch(
                            "navin.mobile.adb.resolve_adb_location",
                            return_value=primary,
                        ):
                            with patch(
                                "navin.mobile.adb._list_devices_detailed",
                                side_effect=detailed,
                            ):
                                with patch(
                                    "navin.mobile.adb._alternate_adb_binaries",
                                    return_value=[alt],
                                ):
                                    with patch(
                                        "navin.mobile.adb._list_avds_with",
                                        return_value=[],
                                    ):
                                        status = preview_readiness(auto_install=False)
                self.assertTrue(status["ready"])
                self.assertEqual(status["devices"], [phone])
                self.assertIsNone(status["error"])
                self.assertEqual(mobile_adb._preferred_adb, alt)
                # The discovery is persisted: a gateway restart / Navin update
                # will reuse it instead of redoing the probe.
                import json as _json

                saved = _json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["preferred_adb"], alt)
        finally:
            mobile_adb._preferred_adb = old_preferred
            mobile_adb.clear_adb_cache()

    def test_saved_preferred_adb_survives_restart(self):
        """A prepared environment is reused after restart, not rediscovered."""
        import json as _json

        from navin.mobile import adb as mobile_adb
        from navin.mobile.adb import clear_adb_cache, resolve_adb_location

        old_preferred = mobile_adb._preferred_adb
        try:
            with TemporaryDirectory() as tmp:
                home = Path(tmp)
                adb = home / "sdk" / "platform-tools" / "adb"
                adb.parent.mkdir(parents=True)
                adb.write_text("#!/bin/sh\n", encoding="utf-8")
                adb.chmod(0o755)
                state = home / ".navin" / "mobile-env.json"
                state.parent.mkdir(parents=True)
                state.write_text(
                    _json.dumps({"preferred_adb": str(adb)}), encoding="utf-8"
                )

                # Simulate a fresh process: no runtime preference, cold cache.
                mobile_adb._preferred_adb = None
                clear_adb_cache()
                with patch(
                    "navin.mobile.adb._env_state_path", return_value=state
                ):
                    with patch(
                        "navin.mobile.adb.shutil.which", return_value=None
                    ):
                        with patch.dict(
                            "os.environ", {"NAVIN_ADB": ""}, clear=False
                        ):
                            loc = resolve_adb_location()
            self.assertIsNotNone(loc)
            assert loc is not None
            self.assertEqual(loc.adb, str(adb))
            self.assertEqual(loc.sdk, str(adb.parent.parent))
        finally:
            mobile_adb._preferred_adb = old_preferred
            mobile_adb.clear_adb_cache()

    def test_start_avd_launches_known_avd_detached(self):
        from navin.mobile.adb import AdbLocation, start_avd

        loc = AdbLocation(adb="/tmp/adb", sdk="/tmp/sdk", source="linux")
        with patch("navin.mobile.adb.resolve_adb_location", return_value=loc):
            with patch(
                "navin.mobile.adb._emulator_binary", return_value="/tmp/sdk/emulator/emulator"
            ):
                with patch(
                    "navin.mobile.adb._list_avds_with",
                    return_value=["navin_api34", "navin_avd"],
                ):
                    with patch(
                        "navin.mobile.bootstrap.host_mobile_capability",
                        return_value={"block_local_emulator": False},
                    ):
                        with patch("navin.mobile.adb.subprocess.Popen") as popen:
                            result = start_avd("navin_api34")
        self.assertTrue(result["ok"])
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], "/tmp/sdk/emulator/emulator")
        self.assertIn("navin_api34", argv)

    def test_start_avd_rejects_unknown_name_and_blocked_hosts(self):
        from navin.mobile.adb import AdbLocation, start_avd

        loc = AdbLocation(adb="/tmp/adb", sdk="/tmp/sdk", source="linux")
        with patch("navin.mobile.adb.resolve_adb_location", return_value=loc):
            with patch(
                "navin.mobile.adb._emulator_binary", return_value="/tmp/sdk/emulator/emulator"
            ):
                with patch(
                    "navin.mobile.adb._list_avds_with", return_value=["navin_api34"]
                ):
                    unknown = start_avd("evil_avd")
                    self.assertFalse(unknown["ok"])
                    self.assertIn("Unknown AVD", unknown["detail"])

                    with patch(
                        "navin.mobile.bootstrap.host_mobile_capability",
                        return_value={
                            "block_local_emulator": True,
                            "acceleration_detail": "No /dev/kvm on this host",
                        },
                    ):
                        blocked = start_avd("navin_api34")
        self.assertFalse(blocked["ok"])
        self.assertIn("kvm", blocked["detail"].lower())

    def test_corrupt_env_state_is_ignored(self):
        from navin.mobile import adb as mobile_adb

        with TemporaryDirectory() as tmp:
            state = Path(tmp) / "mobile-env.json"
            state.write_text("{not json", encoding="utf-8")
            with patch("navin.mobile.adb._env_state_path", return_value=state):
                self.assertEqual(mobile_adb._load_env_state(), {})
                # Saving over a corrupt file works and round-trips.
                mobile_adb._save_env_state({"preferred_adb": "/x/adb"})
                self.assertEqual(
                    mobile_adb._load_env_state()["preferred_adb"], "/x/adb"
                )

    def test_list_devices_parses_space_separated_long_listing(self):
        """adb devices -l on recent platform-tools uses spaces, not tabs."""
        from navin.mobile import adb as mobile_adb

        fake = (
            "List of devices attached\n"
            "emulator-5554          device product:sdk_gphone64_x86_64 "
            "model:sdk_gphone64_x86_64 device:emu64xa transport_id:1\n"
            "emulator-5556          offline transport_id:2\n"
            "\n"
        )
        with patch("navin.mobile.adb.subprocess.run") as run:
            run.return_value = type(
                "R",
                (),
                {"stdout": fake, "stderr": "", "returncode": 0},
            )()
            devices = mobile_adb._list_devices_with("/fake/adb")
        self.assertEqual(len(devices), 1)
        self.assertIn("emulator-5554", devices[0])

    def test_list_devices_still_accepts_tab_format(self):
        from navin.mobile import adb as mobile_adb

        fake = "List of devices attached\nemulator-5554\tdevice\n\n"
        with patch("navin.mobile.adb.subprocess.run") as run:
            run.return_value = type(
                "R",
                (),
                {"stdout": fake, "stderr": "", "returncode": 0},
            )()
            devices = mobile_adb._list_devices_with("/fake/adb")
        self.assertEqual(devices, ["emulator-5554\tdevice"])


class PreviewHelpersTest(unittest.TestCase):
    def test_device_gone_errors_are_detected(self):
        from navin.mobile.preview import (
            friendly_device_gone_message,
            is_device_gone_error,
        )

        self.assertTrue(
            is_device_gone_error(
                "screencap exit exit status: 255: error: device 'emulator-5554' not found"
            )
        )
        self.assertTrue(is_device_gone_error("error: no devices/emulators found"))
        self.assertFalse(is_device_gone_error("screencap did not return a PNG"))
        msg = friendly_device_gone_message("device 'emulator-5554' not found")
        self.assertIn("Device disconnected", msg)
        self.assertIn("Start emulator", msg)

    def test_png_size_and_frame_b64(self):
        from navin.mobile.preview import _png_size, frame_to_b64

        data = bytearray(b"\x89PNG\r\n\x1a\n") + bytearray(16)
        data[16:20] = (1080).to_bytes(4, "big")
        data[20:24] = (2400).to_bytes(4, "big")
        self.assertEqual(_png_size(bytes(data)), (1080, 2400))
        payload = frame_to_b64(
            {
                "png": bytes(data),
                "width": 1080,
                "height": 2400,
                "seq": 2,
                "fps": 4.0,
                "mem_mb": 200.0,
                "cpu_pct": 12.0,
                "ts_ms": 1,
            }
        )
        self.assertTrue(payload["png_b64"])
        self.assertEqual(payload["width"], 1080)
        self.assertEqual(payload["seq"], 2)


class IosStackOsSelectionTest(unittest.TestCase):
    def test_non_macos_uses_android_stack_only(self):
        from navin.mobile.ios import (
            ios_readiness,
            merge_ios_into_readiness,
            os_stack_prepare_prompt,
        )

        with patch("navin.mobile.ios.host_platform", return_value="wsl"):
            status = ios_readiness()
            merged = merge_ios_into_readiness(
                {"ready": False, "devices": [], "fixes": [], "help": "android", "adb": "/usr/bin/adb"}
            )
            prompt = os_stack_prepare_prompt(android_ready=True)
        self.assertFalse(status["supported"])
        self.assertEqual(status["reason"], "ios_needs_macos")
        self.assertEqual(merged["stacks"], ["android"])
        self.assertEqual(merged["fixes"][0]["id"], "ask_agent_stack")
        self.assertIn("Android stack only", prompt)
        self.assertIn("iOS needs macOS", prompt)

    def test_macos_chain_ordered_and_merges_simulator(self):
        from navin.mobile.ios import (
            ios_readiness,
            ios_stack_chain,
            merge_ios_into_readiness,
            os_stack_prepare_prompt,
        )

        with patch("navin.mobile.ios.host_platform", return_value="macos"):
            with patch("navin.mobile.ios._xcode_app_present", return_value=True):
                with patch("navin.mobile.ios._xcode_ok", return_value=True):
                    with patch(
                        "navin.mobile.ios._which",
                        side_effect=lambda n: f"/usr/bin/{n}",
                    ):
                        with patch(
                            "navin.mobile.ios._list_simulators",
                            return_value=[
                                {
                                    "udid": "UDID-1",
                                    "name": "iPhone 15",
                                    "state": "Booted",
                                }
                            ],
                        ):
                            with patch(
                                "navin.mobile.ios._list_usb_devices",
                                return_value=[],
                            ):
                                chain = ios_stack_chain()
                                status = ios_readiness()
                                merged = merge_ios_into_readiness(
                                    {
                                        "ready": False,
                                        "devices": [],
                                        "fixes": [
                                            {
                                                "id": "ask_agent_device",
                                                "prompt": "old",
                                            }
                                        ],
                                        "help": "",
                                        "adb": "/usr/bin/adb",
                                    }
                                )
                                prompt = os_stack_prepare_prompt(android_ready=True)
        ids = [s["id"] for s in chain]
        self.assertEqual(
            ids[:4],
            ["brew", "xcode_app", "xcode_select", "sim_runtime"],
        )
        self.assertTrue(status["ready"])
        self.assertIn("ios-sim:UDID-1", status["devices"])
        self.assertTrue(merged["ready"])
        self.assertIn("ios", merged["stacks"])
        self.assertEqual(merged["fixes"][0]["id"], "ask_agent_stack")
        self.assertNotIn("ask_agent_device", [f["id"] for f in merged["fixes"]])
        self.assertIn("=== iOS ===", prompt)
        self.assertIn("=== ANDROID ===", prompt)

    def test_open_preview_routes_ios_serial(self):
        from navin.mobile.preview import open_preview

        fake = object()
        with patch(
            "navin.mobile.ios.open_ios_preview", return_value=fake
        ) as opener:
            session = open_preview(serial="ios-sim:UDID-1", fps=4)
        self.assertIs(session, fake)
        opener.assert_called_once()


class IosUsbDiscoveryTest(unittest.TestCase):
    def tearDown(self) -> None:
        from navin.mobile.ios import _clear_usb_device_cache

        _clear_usb_device_cache()

    def test_devicectl_json_lists_physical_iphone_like_vscode(self):
        from navin.mobile.ios import parse_devicectl_usb_udids

        payload = {
            "result": {
                "devices": [
                    {
                        "identifier": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                        "hardwareProperties": {
                            "udid": "00008110-0008404E3E4B801E",
                            "platform": "iOS",
                        },
                        "connectionProperties": {"transportType": "wired"},
                    },
                    {
                        "hardwareProperties": {
                            "udid": "00000000-0000000000000000",
                            "platform": "macOS",
                        }
                    },
                ]
            }
        }
        self.assertEqual(
            parse_devicectl_usb_udids(payload),
            ["00008110-0008404E3E4B801E"],
        )

    def test_xcdevice_json_skips_simulator(self):
        from navin.mobile.ios import parse_xcdevice_usb_udids

        payload = [
            {
                "identifier": "00008110-0008404E3E4B801E",
                "platform": "com.apple.platform.iphoneos",
                "simulator": False,
            },
            {
                "identifier": "SIM-UDID-NOT-USED",
                "platform": "com.apple.platform.iphonesimulator",
                "simulator": True,
            },
        ]
        self.assertEqual(
            parse_xcdevice_usb_udids(payload),
            ["00008110-0008404E3E4B801E"],
        )

    def test_usb_screenshot_prefers_devicectl(self):
        from pathlib import Path

        from navin.mobile.ios import _usb_screenshot_argv

        with patch("navin.mobile.ios._which", side_effect=lambda n: f"/usr/bin/{n}"):
            argv = _usb_screenshot_argv("00008110-0008404E3E4B801E", Path("/tmp/frame.png"))
        self.assertEqual(argv[0][1:5], ["devicectl", "device", "capture", "screenshot"])
        self.assertEqual(argv[-1][0], "/usr/bin/idevicescreenshot")


if __name__ == "__main__":
    unittest.main()
