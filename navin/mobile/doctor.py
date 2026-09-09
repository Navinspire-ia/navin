# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Environment checks for mobile development (Node, Android SDK, adb, etc.)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from navin.mobile.detect import MobileProject
from navin.utils.proc import no_window_kwargs

CheckStatus = Literal["ok", "warn", "missing"]

_CMD_TIMEOUT_S = 12


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str
    fix: str = ""

    def render(self) -> str:
        mark = {"ok": "ok", "warn": "!!", "missing": "no"}[self.status]
        line = f"  [{mark}] {self.name}: {self.detail}"
        if self.fix and self.status != "ok":
            line += f"\n       Fix: {self.fix}"
        return line

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass(slots=True)
class DoctorReport:
    project: MobileProject
    checks: list[DoctorCheck] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    avds: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        if self.project.kind == "none":
            return False
        return all(c.status != "missing" for c in self.checks if c.name in _REQUIRED)

    def render(self) -> str:
        lines = [self.project.render(), "", "Environment:"]
        if not self.checks:
            lines.append("  (no checks)")
        else:
            lines.extend(check.render() for check in self.checks)
        if self.devices:
            lines.append("")
            lines.append("ADB devices:")
            for device in self.devices:
                lines.append(f"  - {device}")
        elif self.project.kind in {"expo", "react-native", "flutter"}:
            lines.append("")
            lines.append("ADB devices: none connected")
        if self.avds:
            lines.append("")
            lines.append("Android virtual devices:")
            for avd in self.avds:
                lines.append(f"  - {avd}")
        lines.append("")
        if self.ready:
            lines.append("Doctor: ready to run a mobile packager.")
        else:
            lines.append(
                "Doctor: blockers found. Resolve the [no] items before mobile(run)."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project.to_dict(),
            "ready": self.ready,
            "checks": [c.to_dict() for c in self.checks],
            "devices": list(self.devices),
            "avds": list(self.avds),
        }


_REQUIRED = frozenset({"node", "package_manager", "npx"})


def run_doctor(project: MobileProject) -> DoctorReport:
    """Run environment checks appropriate for the detected project kind."""
    checks: list[DoctorCheck] = []
    devices: list[str] = []
    avds: list[str] = []

    if project.kind == "none":
        checks.append(
            DoctorCheck(
                name="project",
                status="missing",
                detail="not a detected Expo / React Native / Flutter project",
                fix=(
                    "Open a workspace with package.json (expo or react-native) "
                    "or pubspec.yaml (flutter)."
                ),
            )
        )
        return DoctorReport(project=project, checks=checks)

    if project.kind == "flutter":
        checks.extend(_flutter_checks())
        from navin.mobile.adb import clear_adb_cache, resolve_adb_location

        clear_adb_cache()
        loc = resolve_adb_location()
        if loc:
            devices = _adb_devices_with(loc.adb)
            sdk = Path(loc.sdk) if loc.sdk else _android_sdk_root()
            emu = _resolve_emulator(sdk)
            avds = _list_avds_bin(emu) if emu else []
            checks.append(
                DoctorCheck(
                    name="adb",
                    status="ok",
                    detail=f"{loc.adb} (via {loc.source})",
                )
            )
        else:
            from navin.mobile.adb import adb_setup_help

            checks.append(
                DoctorCheck(
                    name="adb",
                    status="warn",
                    detail="not found (dynamic lookup)",
                    fix=adb_setup_help().replace("\n", " | "),
                )
            )
        return DoctorReport(
            project=project, checks=checks, devices=devices, avds=avds
        )

    # Expo / React Native
    checks.append(_which_check("node", "node", fix="Install Node.js 20+ LTS."))
    pm = project.package_manager if project.package_manager != "unknown" else "npm"
    checks.append(
        _which_check(
            "package_manager",
            pm,
            fix=f"Install {pm}, or use npm if the lockfile allows it.",
        )
    )
    checks.append(
        _which_check(
            "npx",
            "npx",
            fix="npx ships with npm. Reinstall Node.js / npm.",
        )
    )
    if not (project.root / "node_modules").is_dir():
        install = _install_command(project)
        checks.append(
            DoctorCheck(
                name="node_modules",
                status="warn",
                detail="dependencies not installed",
                fix=f"Run `{install}` in the project root.",
            )
        )
    else:
        checks.append(
            DoctorCheck(name="node_modules", status="ok", detail="present")
        )

    java = _which_check(
        "java",
        "java",
        fix="Install a JDK 17 (Temurin/OpenJDK) for Android builds.",
        required=False,
    )
    checks.append(java)

    from navin.mobile.adb import adb_setup_help, clear_adb_cache, resolve_adb_location

    clear_adb_cache()
    adb_loc = resolve_adb_location()
    sdk_root = Path(adb_loc.sdk) if adb_loc and adb_loc.sdk else _android_sdk_root()
    if adb_loc:
        checks.append(
            DoctorCheck(
                name="adb",
                status="ok",
                detail=f"{adb_loc.adb} (via {adb_loc.source})",
            )
        )
        devices = _adb_devices_with(adb_loc.adb)
    else:
        checks.append(
            DoctorCheck(
                name="adb",
                status="warn",
                detail="not found (PATH, ANDROID_HOME, ~/Android/Sdk, Windows SDK under WSL)",
                fix=adb_setup_help().replace("\n", " | "),
            )
        )

    if sdk_root:
        checks.append(
            DoctorCheck(name="android_sdk", status="ok", detail=str(sdk_root))
        )
    else:
        checks.append(
            DoctorCheck(
                name="android_sdk",
                status="warn",
                detail="SDK not discovered yet",
                fix=(
                    "Install Android Studio, or set ANDROID_HOME / NAVIN_ADB. "
                    "On WSL, Navin also scans /mnt/c/Users/*/AppData/Local/Android/Sdk."
                ),
            )
        )

    emulator = _resolve_emulator(sdk_root)
    if emulator:
        checks.append(DoctorCheck(name="emulator", status="ok", detail=emulator))
        avds = _list_avds_bin(emulator)
        if not avds:
            checks.append(
                DoctorCheck(
                    name="avd",
                    status="warn",
                    detail="no Android Virtual Device found",
                    fix="Create an AVD in Android Studio Device Manager.",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name="avd",
                    status="ok",
                    detail=f"{len(avds)} available ({avds[0]}"
                    + ("..." if len(avds) > 1 else "")
                    + ")",
                )
            )
    else:
        checks.append(
            DoctorCheck(
                name="emulator",
                status="warn",
                detail="emulator binary not found",
                fix="Install Android Emulator via Android Studio SDK Manager.",
            )
        )

    if not devices:
        checks.append(
            DoctorCheck(
                name="device",
                status="warn",
                detail="no device/emulator connected",
                fix=(
                    "Start an AVD (`emulator -avd <name>`) or plug a device "
                    "with USB debugging, then re-run doctor."
                ),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                name="device",
                status="ok",
                detail=f"{len(devices)} connected",
            )
        )

    return DoctorReport(
        project=project, checks=checks, devices=devices, avds=avds
    )


def _flutter_checks() -> list[DoctorCheck]:
    flutter = shutil.which("flutter")
    if not flutter:
        return [
            DoctorCheck(
                name="flutter",
                status="missing",
                detail="flutter not on PATH",
                fix="Install Flutter SDK and add it to PATH.",
            )
        ]
    version = _run_output([flutter, "--version"]) or "installed"
    first = version.splitlines()[0] if version else "installed"
    return [DoctorCheck(name="flutter", status="ok", detail=first)]


def _which_check(
    name: str,
    binary: str,
    *,
    fix: str,
    required: bool = True,
) -> DoctorCheck:
    path = shutil.which(binary)
    if path:
        version = _run_output([path, "--version"])
        detail = path
        if version:
            detail = f"{path} ({version.splitlines()[0].strip()})"
        return DoctorCheck(name=name, status="ok", detail=detail)
    return DoctorCheck(
        name=name,
        status="missing" if required else "warn",
        detail=f"{binary} not on PATH",
        fix=fix,
    )


def _android_sdk_root() -> Path | None:
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = os.environ.get(key, "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                return path
    home = Path.home()
    candidates = [
        home / "Android" / "Sdk",
        home / "Library" / "Android" / "sdk",
        Path("/usr/lib/android-sdk"),
        Path("/opt/android-sdk"),
    ]
    # Windows default
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Android" / "Sdk")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _adb_devices() -> list[str]:
    from navin.mobile.adb import resolve_adb_binary

    adb = resolve_adb_binary() or shutil.which("adb")
    if not adb:
        return []
    return _adb_devices_with(adb)


def _adb_devices_with(adb: str) -> list[str]:
    out = _run_output([adb, "devices", "-l"])
    if not out:
        return []
    devices: list[str] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "offline" in line:
            continue
        if "\tdevice" in line or line.endswith(" device"):
            devices.append(line)
    return devices


def _resolve_emulator(sdk_root: Path | None) -> str | None:
    if sdk_root is not None:
        for name in ("emulator", "emulator.exe"):
            candidate = sdk_root / "emulator" / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("emulator")


def _list_avds() -> list[str]:
    from navin.mobile.adb import resolve_android_sdk

    emulator = _resolve_emulator(resolve_android_sdk() or _android_sdk_root())
    if not emulator:
        return []
    return _list_avds_bin(emulator)


def _list_avds_bin(emulator: str) -> list[str]:
    out = _run_output([emulator, "-list-avds"])
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _install_command(project: MobileProject) -> str:
    pm = project.package_manager
    if pm == "yarn":
        return "yarn install"
    if pm == "pnpm":
        return "pnpm install"
    if pm == "bun":
        return "bun install"
    return "npm install"


def _run_output(argv: list[str]) -> str:
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CMD_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stdout or "").strip()
    if text:
        return text
    return (completed.stderr or "").strip()
