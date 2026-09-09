# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Build run plans for Expo / React Native / Flutter packagers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from navin.mobile.detect import MobileProject
from navin.mobile.doctor import DoctorReport

MobileTarget = Literal["android", "ios", "web", "metro"]

# Flutter matches -d against a device id or name, never against a platform.
# "flutter run -d ios" therefore finds nothing and the run sits there, while
# a bare "flutter run" with several devices stops on an interactive prompt
# that no one can answer from a tool session.
_DEVICE_DISCOVERY_TIMEOUT_S = 90


@dataclass(slots=True)
class RunPlan:
    """Concrete commands the agent (or mobile tool) should execute."""

    project: MobileProject
    packager_command: str
    wait_for: str
    emulator_command: str = ""
    open_command: str = ""
    notes: list[str] = field(default_factory=list)
    blocked_reason: str = ""

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reason)

    def render(self) -> str:
        if self.blocked:
            lines = [
                "Run plan blocked:",
                f"  {self.blocked_reason}",
                "",
                self.project.render(),
            ]
            return "\n".join(lines)
        lines = [
            f"Run plan ({self.project.kind}):",
            f"  Packager: {self.packager_command}",
        ]
        if self.emulator_command:
            lines.append(f"  Emulator: {self.emulator_command}")
        if self.open_command:
            lines.append(f"  Open app: {self.open_command}")
        lines.append(f"  Ready when output contains: {self.wait_for!r}")
        if self.notes:
            lines.append("Notes:")
            for note in self.notes:
                lines.append(f"  - {note}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "packager_command": self.packager_command,
            "emulator_command": self.emulator_command,
            "open_command": self.open_command,
            "wait_for": self.wait_for,
            "notes": list(self.notes),
            "blocked_reason": self.blocked_reason,
        }


def build_run_plan(
    project: MobileProject,
    *,
    target: MobileTarget = "android",
    emulator: str | None = None,
    doctor: DoctorReport | None = None,
    start_emulator: bool = True,
    device_id: str | None = None,
) -> RunPlan:
    """Return commands to start the mobile packager (and optionally an AVD)."""
    if project.kind == "none":
        return RunPlan(
            project=project,
            packager_command="",
            wait_for="",
            blocked_reason="No Expo / React Native / Flutter project detected.",
        )

    if project.kind == "flutter":
        if doctor is not None and not doctor.ready:
            missing = [c.name for c in doctor.checks if c.status == "missing"]
            return RunPlan(
                project=project,
                packager_command="",
                wait_for="",
                blocked_reason=(
                    "Environment not ready: missing "
                    + ", ".join(missing or ["flutter"])
                    + ". Run mobile(action=doctor) and fix the [no] items."
                ),
            )
        notes: list[str] = []
        if target == "web":
            cmd = "flutter run -d chrome"
        else:
            resolved, device_notes, blocked = _flutter_device(
                project, target=target, doctor=doctor, device_id=device_id
            )
            if blocked:
                return RunPlan(
                    project=project,
                    packager_command="",
                    wait_for="",
                    blocked_reason=blocked,
                )
            cmd = (
                f"flutter run -d {resolved}"
                if resolved
                else f"flutter run --device-timeout {_DEVICE_DISCOVERY_TIMEOUT_S}"
            )
            notes.extend(device_notes)
        emulator_command = ""
        if target == "android" and start_emulator and doctor is not None:
            if not doctor.devices:
                from navin.mobile.bootstrap import acceleration_available

                accel_ok, accel_detail = acceleration_available()
                if not accel_ok:
                    return RunPlan(
                        project=project,
                        packager_command="",
                        wait_for="",
                        blocked_reason=(
                            "Local emulator blocked: host lacks hardware acceleration. "
                            f"{accel_detail}"
                        ),
                    )
                avd_name = (emulator or (doctor.avds[0] if doctor.avds else "")).strip()
                if avd_name:
                    emulator_command = f"emulator -avd {avd_name}"
                    notes.append(f"Start AVD `{avd_name}` before flutter attaches.")
        return RunPlan(
            project=project,
            packager_command=cmd,
            wait_for="Flutter run key commands",
            emulator_command=emulator_command,
            notes=notes,
        )

    if doctor is not None and not doctor.ready:
        missing = [
            c.name for c in doctor.checks if c.status == "missing"
        ]
        return RunPlan(
            project=project,
            packager_command="",
            wait_for="",
            blocked_reason=(
                "Environment not ready: missing "
                + ", ".join(missing or ["required tools"])
                + ". Run mobile(action=doctor) and fix the [no] items."
            ),
        )

    packager = _packager_command(project, target)
    wait_for = _wait_token(project.kind, target)
    notes: list[str] = []
    emulator_command = ""
    open_command = ""

    if target == "android" and start_emulator:
        devices = doctor.devices if doctor else []
        avds = doctor.avds if doctor else []
        if not devices:
            from navin.mobile.bootstrap import acceleration_available

            accel_ok, accel_detail = acceleration_available()
            if not accel_ok:
                return RunPlan(
                    project=project,
                    packager_command="",
                    wait_for="",
                    blocked_reason=(
                        "Local emulator blocked: host lacks hardware acceleration. "
                        f"{accel_detail} Do not start -accel off / swiftshader."
                    ),
                )
            avd_name = (emulator or (avds[0] if avds else "")).strip()
            if avd_name:
                emulator_command = f"emulator -avd {avd_name}"
                notes.append(
                    f"No device connected - start AVD `{avd_name}` first, "
                    "wait until `adb devices` shows it, then keep the packager."
                )
            else:
                notes.append(
                    "No device/AVD available. Connect a phone with USB debugging "
                    "or create an AVD in Android Studio."
                )
        else:
            notes.append(f"Using connected device: {devices[0]}")

    if project.kind == "expo" and target == "android":
        open_command = _pm_run(project, "npx expo start --android")
        notes.append(
            "For Expo, the packager command already targets Android when "
            "target=android. Prefer a physical device or a running emulator."
        )
    elif project.kind == "react-native" and target == "android":
        if "android" in project.scripts:
            open_command = _pm_script(project, "android")
        else:
            open_command = "npx react-native run-android"
        notes.append(
            "Start Metro first, then run the open/android command in another shell "
            "if the app does not launch automatically."
        )

    if target == "ios":
        notes.append(
            "iOS requires a Mac with Xcode. On Linux/Windows, use Android or Expo web."
        )

    if project.kind == "expo" and not (project.root / "node_modules").is_dir():
        notes.insert(0, f"Install deps first: {_install_command(project)}")

    return RunPlan(
        project=project,
        packager_command=packager,
        wait_for=wait_for,
        emulator_command=emulator_command,
        open_command=open_command if open_command != packager else "",
        notes=notes,
    )


def _flutter_device(
    project: MobileProject,
    *,
    target: MobileTarget,
    doctor: DoctorReport | None,
    device_id: str | None,
) -> tuple[str, list[str], str]:
    """Resolve a concrete ``-d`` value. Returns (device, notes, blocked_reason)."""
    explicit = (device_id or "").strip()
    if explicit:
        return explicit, [f"Target device: {explicit}"], ""

    if target == "ios":
        return _flutter_ios_device(project)

    if target == "android":
        serial = _first_adb_serial(doctor)
        if serial:
            return serial, [f"Target device: {serial}"], ""
        return (
            "",
            [
                "No device online yet. flutter waits up to "
                f"{_DEVICE_DISCOVERY_TIMEOUT_S}s for the emulator or phone, "
                "instead of stopping on a device prompt.",
            ],
            "",
        )

    return "", [], ""


def _first_adb_serial(doctor: DoctorReport | None) -> str:
    for line in (doctor.devices if doctor else []) or []:
        serial = str(line).split()[0].strip()
        if serial:
            return serial
    return ""


def _flutter_ios_device(project: MobileProject) -> tuple[str, list[str], str]:
    from navin.utils.host import host_platform

    if host_platform() != "macos":
        return (
            "",
            [],
            "iOS run needs macOS + Xcode. Use target=android or target=web here.",
        )

    from navin.mobile.ios import ios_run_devices

    physical, simulators = ios_run_devices()
    if not physical and not simulators:
        return (
            "",
            [],
            "No iOS device online. Plug the iPhone (unlocked, Trust this Mac, "
            "Developer Mode on) or boot a Simulator with `open -a Simulator`, "
            "then run mobile(action=devices) again.",
        )

    if not physical:
        udid = simulators[0]
        return udid, [f"Target: iOS Simulator {udid}"], ""

    udid = physical[0]
    missing_team = _ios_signing_missing(project.root)
    if missing_team:
        return "", [], missing_team
    return (
        udid,
        [
            f"Target: iPhone {udid}",
            "First run on a physical iPhone is slow: Xcode prepares debugger "
            "support and signs the build. Watch mobile(action=logs) instead of "
            "waiting silently.",
        ],
        "",
    )


def _ios_signing_missing(root: Path) -> str:
    """Blocked reason when the iOS project has no signing team.

    Without a team, ``flutter run`` on a physical iPhone builds for several
    minutes and only then fails on ``Signing for "Runner" requires a
    development team``. Say it before the build, not after.
    """
    ios_dir = root / "ios"
    if not ios_dir.is_dir():
        return ""
    sources = [ios_dir / "Runner.xcodeproj" / "project.pbxproj"]
    flutter_dir = ios_dir / "Flutter"
    if flutter_dir.is_dir():
        sources.extend(sorted(flutter_dir.glob("*.xcconfig")))
    checked = False
    for path in sources:
        if not path.is_file():
            continue
        checked = True
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "DEVELOPMENT_TEAM" not in line:
                continue
            value = line.split("=", 1)[-1].strip().strip(";").strip().strip('"')
            if value:
                return ""
    if not checked:
        return ""
    return (
        "iOS signing is not configured: no DEVELOPMENT_TEAM in ios/. A run on a "
        "physical iPhone would build for minutes and then fail. Open "
        "ios/Runner.xcworkspace in Xcode, select Runner > Signing & "
        "Capabilities, pick your Team, then run again. A Simulator needs no team."
    )


def _packager_command(project: MobileProject, target: MobileTarget) -> str:
    if project.kind == "expo":
        if target == "android":
            return _pm_run(project, "npx expo start --android")
        if target == "ios":
            return _pm_run(project, "npx expo start --ios")
        if target == "web":
            return _pm_run(project, "npx expo start --web")
        if "start" in project.scripts:
            return _pm_script(project, "start")
        return _pm_run(project, "npx expo start")

    # react-native
    if target == "android" and "android" in project.scripts:
        return _pm_script(project, "android")
    if target == "ios" and "ios" in project.scripts:
        return _pm_script(project, "ios")
    if "start" in project.scripts:
        return _pm_script(project, "start")
    return "npx react-native start"


def _wait_token(kind: str, target: MobileTarget) -> str:
    if kind == "expo":
        if target == "web":
            return "Web is waiting on"
        return "Metro waiting on"
    return "Welcome to Metro"


def _pm_script(project: MobileProject, script: str) -> str:
    pm = project.package_manager
    if pm == "yarn":
        return f"yarn {script}"
    if pm == "pnpm":
        return f"pnpm {script}"
    if pm == "bun":
        return f"bun run {script}"
    return f"npm run {script}"


def _pm_run(project: MobileProject, command: str) -> str:
    """Prefer package scripts when they already wrap the same tool."""
    if command.startswith("npx expo start") and "start" in project.scripts:
        script = project.scripts["start"]
        if "expo" in script:
            # Keep explicit platform flags when requested.
            if command.endswith("--android"):
                return _pm_run_raw(project, "npx expo start --android")
            if command.endswith("--ios"):
                return _pm_run_raw(project, "npx expo start --ios")
            if command.endswith("--web"):
                return _pm_run_raw(project, "npx expo start --web")
            return _pm_script(project, "start")
    return _pm_run_raw(project, command)


def _pm_run_raw(project: MobileProject, command: str) -> str:
    pm = project.package_manager
    if pm == "pnpm" and command.startswith("npx "):
        return "pnpm dlx " + command[len("npx ") :]
    if pm == "yarn" and command.startswith("npx "):
        return "yarn dlx " + command[len("npx ") :]
    if pm == "bun" and command.startswith("npx "):
        return "bunx " + command[len("npx ") :]
    return command


def _install_command(project: MobileProject) -> str:
    pm = project.package_manager
    if pm == "yarn":
        return "yarn install"
    if pm == "pnpm":
        return "pnpm install"
    if pm == "bun":
        return "bun install"
    return "npm install"
