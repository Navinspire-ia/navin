# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Full Android toolchain bootstrap under ``~/.navin`` (multi-OS).

Idempotent cycle: adb → JDK → cmdline-tools → SDK packages → AVD → emulator
(when acceleration is available). Emits ``task_progress`` for the WebUI bars.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from navin.mobile.adb import (
    clear_adb_cache,
    host_platform,
    resolve_adb_location,
    try_install_adb,
)
from navin.toolchains import is_stale, record_build
from navin.utils.proc import no_window_kwargs
from navin.utils.task_progress import emit_task_progress

AVD_NAME = "navin_api34"
API_LEVEL = "34"
PLATFORM_PKG = f"platforms;android-{API_LEVEL}"
STEPS_TOTAL = 8

# Kept for older imports / docs; prefer system_image_package().
SYSTEM_IMAGE = f"system-images;android-{API_LEVEL};google_apis;x86_64"

_JDK_URLS = {
    "linux": (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_linux_hotspot_17.0.13_11.tar.gz"
    ),
    "wsl": (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_linux_hotspot_17.0.13_11.tar.gz"
    ),
    "macos": (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_mac_hotspot_17.0.13_11.tar.gz"
    ),
    "macos_aarch64": (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.13%2B11/OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.13_11.tar.gz"
    ),
    "windows": (
        "https://github.com/adoptium/temurin17-binaries/releases/download/"
        "jdk-17.0.13%2B11/OpenJDK17U-jdk_x64_windows_hotspot_17.0.13_11.zip"
    ),
}


def host_cpu_arch() -> str:
    """Normalized CPU arch: ``x86_64`` or ``arm64``."""
    machine = (platform.machine() or "").strip().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "x86_64"


def emulator_abi() -> str:
    """Android system-image ABI for the local emulator."""
    return "arm64-v8a" if host_cpu_arch() == "arm64" else "x86_64"


def system_image_package() -> str:
    return f"system-images;android-{API_LEVEL};google_apis;{emulator_abi()}"


def _jdk_download_url(plat: str) -> str | None:
    if plat == "macos" and host_cpu_arch() == "arm64":
        return _JDK_URLS.get("macos_aarch64")
    return _JDK_URLS.get(plat)

_CMDLINE_TOOLS_URLS = {
    "linux": "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip",
    "wsl": "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip",
    "macos": "https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip",
    "windows": "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip",
}


def navin_sdk_root() -> Path:
    plat = host_platform()
    if plat == "windows":
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local:
            return Path(local) / "Navin" / "android-platform-tools"
    return Path.home() / ".navin" / "android-platform-tools"


def navin_jdk_root() -> Path:
    return Path.home() / ".navin" / "jdk17"


def _is_navin_sdk(sdk: Path | str | None) -> bool:
    """Whether this SDK is the one we manage, not the user's Android Studio.

    Only ours gets a build stamp: writing markers into a tree installed by
    Android Studio would be meddling with something we do not own.
    """
    if not sdk:
        return False
    try:
        return Path(sdk).expanduser().resolve() == navin_sdk_root().resolve()
    except OSError:
        return False


def record_sdk_build(sdk: Path | str | None) -> None:
    if _is_navin_sdk(sdk):
        record_build(Path(sdk))


def sdk_toolchain_stale(sdk: Path | str | None) -> bool:
    """Whether our Android SDK was prepared by a different Navin build.

    Reinstalling Navin never touches ``~/.navin``, so a newer build otherwise
    keeps running against whatever the first Prepare pulled. The steps are
    idempotent, so re-running Prepare only fills what the new build added.
    """
    if not _is_navin_sdk(sdk):
        return False
    return is_stale(Path(sdk))


def acceleration_available() -> tuple[bool, str]:
    """Return (ok, detail) for starting a local emulator.

    Existence of ``/dev/kvm`` is not enough - the process must be able to open it
    (user typically needs membership in the ``kvm`` group).
    """
    plat = host_platform()
    if plat in {"linux", "wsl"}:
        kvm = Path("/dev/kvm")
        if not kvm.exists():
            if plat == "wsl":
                return False, (
                    "No /dev/kvm on this WSL host. A software emulator (-accel off) "
                    "will ANR, freeze input, and drop to 0 fps. Use Android Studio on "
                    "Windows (Device Manager > Play) or a USB phone, then Refresh."
                )
            return False, (
                "No /dev/kvm. Install KVM (nested virt) or use a physical device. "
                "Software GPU emulators are blocked - they crash repeatedly."
            )
        try:
            fd = os.open(kvm, os.O_RDWR)
            os.close(fd)
        except OSError as exc:
            if plat == "wsl":
                return False, (
                    f"{kvm} exists but is not accessible ({exc}). "
                    "1) Run once in WSL: `sudo usermod -aG kvm $USER` "
                    "2) Then in Windows PowerShell (NOT inside Ubuntu): `wsl --shutdown` "
                    "3) Reopen WSL. "
                    "OR skip KVM: open Android Studio on Windows → Device Manager → Play an AVD, "
                    "then Refresh Mobile. Do not use -accel off."
                )
            return False, (
                f"{kvm} exists but is not accessible ({exc}). "
                "Run once: `sudo usermod -aG kvm $USER` then log out and back in "
                "(or reboot). Or plug a USB phone. Do not start -accel off / swiftshader."
            )
        return True, f"{kvm} present and accessible"
    if plat == "macos":
        # Apple Hypervisor is the supported path; we do not probe HVF here.
        return True, "macOS hypervisor available for the Android Emulator"
    if plat == "windows":
        return True, "Windows Hyper-V / WHPX may accelerate the emulator"
    return False, "Unknown platform"


def soft_accel_emulator_running() -> bool:
    """True when a local qemu/emulator was started with ``-accel off``."""
    plat = host_platform()
    if plat == "windows":
        # No portable process listing required - Windows AVDs use WHPX/Hyper-V.
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            ["ps", "-eo", "args"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for line in (completed.stdout or "").splitlines():
        low = line.lower()
        if "qemu-system" not in low and "/emulator/" not in low:
            continue
        if "-accel off" in low or "-acceloff" in low.replace(" ", ""):
            return True
    return False


def _is_emulator_serial(device_line: str) -> bool:
    serial = (device_line or "").strip().split()[0] if device_line else ""
    return serial.startswith("emulator-") or serial.startswith("emulator:")


def host_mobile_capability() -> dict[str, Any]:
    """Hardware gate + recommendations for Mobile preview / emulator."""
    plat = host_platform()
    accel_ok, accel_detail = acceleration_available()
    soft = soft_accel_emulator_running()
    recommendations: list[str] = []
    if plat == "wsl" and not accel_ok:
        recommendations.extend(
            [
                "FIRST (KVM): in WSL run `sudo usermod -aG kvm $USER`, then in "
                "Windows PowerShell run `wsl --shutdown`, reopen WSL.",
                "OR (easier): Android Studio on Windows → Device Manager → Play an AVD, "
                "then Refresh Mobile (Prepare/bootstrap installs Studio via winget if missing).",
                "Or plug a USB phone (usbipd).",
                "Do not start a WSL emulator with -accel off / swiftshader.",
            ]
        )
    elif plat == "linux" and not accel_ok:
        recommendations.extend(
            [
                "FIRST: `sudo usermod -aG kvm $USER` then log out and back in (or reboot).",
                "Ensure /dev/kvm exists (qemu-kvm / nested virt).",
                "Or plug a USB phone with debugging enabled.",
                "Do not use -accel off / swiftshader.",
            ]
        )
    elif plat == "windows" and not accel_ok:
        recommendations.extend(
            [
                "Open Android Studio → Device Manager → Play an AVD, then Refresh Mobile.",
                "Prepare/bootstrap installs Android Studio via winget when missing.",
                "Or plug a USB phone with debugging enabled.",
            ]
        )
    elif plat == "macos" and not accel_ok:
        recommendations.append(
            "macOS Hypervisor unavailable. Use a USB phone or fix HVF; "
            "do not force software GPU."
        )
    elif plat == "windows":
        recommendations.append(
            "If no device yet: Android Studio → Device Manager → Play an AVD, then Refresh."
        )
    if soft:
        recommendations.append(
            "A software-GPU emulator (-accel off) is running. Stop it and use a "
            "hardware-accelerated AVD or a physical phone."
        )
    return {
        "platform": plat,
        "acceleration_ok": accel_ok,
        "acceleration_detail": accel_detail,
        "soft_accel_emulator": soft,
        # Local AVD launch is blocked without accel; USB phones can still work.
        "stable": accel_ok and not soft,
        "block_local_emulator": not accel_ok,
        "recommendations": recommendations,
    }


async def run_bootstrap(*, start_emulator: bool = True) -> dict[str, Any]:
    """Run the full bootstrap cycle and return a structured summary."""
    plat = host_platform()
    logs: list[str] = []
    result: dict[str, Any] = {
        "ok": False,
        "platform": plat,
        "step": "start",
        "adb": None,
        "sdk": None,
        "jdk": None,
        "device": None,
        "avd": None,
        "android_studio": None,
        "kvm_fix": None,
        "next": None,
        "log": "",
    }

    async def progress(
        step_index: int,
        label: str,
        *,
        percent: float | None = None,
        step: str | None = None,
    ) -> None:
        base = ((step_index - 1) / STEPS_TOTAL) * 100.0
        span = 100.0 / STEPS_TOTAL
        pct = base + (span * ((percent or 0) / 100.0)) if percent is not None else None
        await emit_task_progress(
            label=label,
            percent=pct if pct is not None else None,
            indeterminate=pct is None,
            step=step or label,
            step_index=step_index,
            steps_total=STEPS_TOTAL,
        )

    # 0) Host prereqs first (WSL/Windows): KVM access + Android Studio Windows
    await progress(1, "Checking host prereqs (KVM / Android Studio)…", percent=10, step="host")
    host_prep = await asyncio.to_thread(_ensure_host_prereqs, logs)
    result["android_studio"] = host_prep.get("android_studio")
    result["kvm_fix"] = host_prep.get("kvm_fix")
    await progress(1, "Host prereqs checked", percent=100, step="host")

    # 1) adb / platform-tools
    await progress(2, "Checking adb…", percent=10, step="adb")
    clear_adb_cache()
    loc = resolve_adb_location()
    if loc is None:
        await progress(2, "Installing adb / platform-tools…", percent=40, step="adb")
        install = await asyncio.to_thread(try_install_adb)
        logs.append(str(install.get("detail") or install.get("log") or ""))
        clear_adb_cache()
        loc = resolve_adb_location()
        if loc is None:
            result.update(
                step="adb",
                next=install.get("help") or "Install Android platform-tools, then retry.",
                log="\n".join(x for x in logs if x)[-4000:],
            )
            await progress(2, "adb missing", percent=100, step="adb")
            return result
    result["adb"] = loc.adb
    sdk = Path(loc.sdk) if loc.sdk else navin_sdk_root()
    sdk.mkdir(parents=True, exist_ok=True)
    result["sdk"] = str(sdk)
    await progress(2, f"adb ready ({loc.source})", percent=100, step="adb")

    # 2) JDK
    await progress(3, "Checking JDK…", percent=10, step="jdk")
    jdk = await asyncio.to_thread(_ensure_jdk, logs)
    if not jdk:
        result.update(
            step="jdk",
            next="Install a JDK 17+ (Temurin) and set JAVA_HOME, then retry.",
            log="\n".join(x for x in logs if x)[-4000:],
        )
        return result
    result["jdk"] = jdk
    await progress(3, "JDK ready", percent=100, step="jdk")

    # 3) cmdline-tools
    await progress(4, "Checking cmdline-tools…", percent=10, step="cmdline-tools")
    sdkmanager = await asyncio.to_thread(_ensure_cmdline_tools, sdk, logs)
    if not sdkmanager:
        result.update(
            step="cmdline-tools",
            next="Could not install Android cmdline-tools under ~/.navin.",
            log="\n".join(x for x in logs if x)[-4000:],
        )
        return result
    await progress(4, "cmdline-tools ready", percent=100, step="cmdline-tools")

    env = _sdk_env(sdk, jdk)

    # 4) SDK packages
    await progress(5, "Installing emulator / platform / system-image…", percent=5, step="sdk")
    packages_ok = await asyncio.to_thread(
        _ensure_sdk_packages,
        sdkmanager,
        env,
        logs,
    )
    if not packages_ok:
        result.update(
            step="sdk",
            next="sdkmanager failed - check network and retry bootstrap.",
            log="\n".join(x for x in logs if x)[-4000:],
        )
        return result
    await progress(5, "SDK packages ready", percent=100, step="sdk")

    # 5) AVD
    await progress(6, "Ensuring AVD…", percent=20, step="avd")
    avd_ok = await asyncio.to_thread(_ensure_avd, sdk, env, logs)
    if not avd_ok:
        result.update(
            step="avd",
            next="Could not create AVD. Check sdkmanager / avdmanager logs.",
            log="\n".join(x for x in logs if x)[-4000:],
        )
        return result
    result["avd"] = AVD_NAME
    # The toolchain itself is complete here; whether a device happens to be
    # online afterwards says nothing about what this build installed.
    record_sdk_build(sdk)
    await progress(6, f"AVD {AVD_NAME} ready", percent=100, step="avd")

    # 6) Emulator / acceleration
    await progress(7, "Checking acceleration…", percent=20, step="emulator")
    accel_ok, accel_detail = acceleration_available()
    devices = await asyncio.to_thread(_list_devices, loc.adb)
    if devices:
        result["device"] = devices[0]
        result["ok"] = True
        result["step"] = "ready"
        result["next"] = "Device online - click Start preview in the Mobile tab."
        await progress(8, "Device online", percent=100, step="device")
        result["log"] = "\n".join(x for x in logs if x)[-4000:]
        return result

    if not accel_ok:
        next_steps = _host_blocked_next(plat, host_prep, accel_detail)
        result.update(
            ok=False,
            step="acceleration",
            next=next_steps,
            log="\n".join(x for x in logs if x)[-4000:],
        )
        await progress(7, "Acceleration missing - follow Next", percent=100, step="emulator")
        return result

    if start_emulator:
        await progress(7, f"Starting emulator {AVD_NAME}…", percent=50, step="emulator")
        started = await asyncio.to_thread(_start_emulator, sdk, env, logs)
        if not started:
            result.update(
                step="emulator",
                next="Failed to start the emulator. Try Android Studio Device Manager.",
                log="\n".join(x for x in logs if x)[-4000:],
            )
            return result
    await progress(7, "Emulator starting…", percent=100, step="emulator")

    # 7) Wait for adb device
    await progress(8, "Waiting for adb device…", percent=10, step="device")
    device = await asyncio.to_thread(_wait_for_device, loc.adb, timeout_s=120)
    if device:
        result["device"] = device
        result["ok"] = True
        result["step"] = "ready"
        result["next"] = "Device online - click Start preview in the Mobile tab."
        await progress(8, "Device online", percent=100, step="device")
    else:
        result.update(
            step="device",
            next=(
                "Emulator was started but no device appeared in time. "
                "Wait a bit, then Refresh the Mobile tab."
            ),
        )
        await progress(8, "Still waiting for device", percent=100, step="device")
    result["log"] = "\n".join(x for x in logs if x)[-4000:]
    return result


def _host_blocked_next(
    plat: str, host_prep: dict[str, Any], accel_detail: str
) -> str:
    lines = [accel_detail, ""]
    kvm_fix = host_prep.get("kvm_fix")
    if kvm_fix:
        if plat == "wsl":
            lines.append("1) In WSL run once:")
            lines.append(f"   {kvm_fix}")
            lines.append(
                "2) Then in Windows PowerShell (not inside Ubuntu): `wsl --shutdown`"
            )
            lines.append("3) Reopen WSL, then retry Prepare / Refresh Mobile.")
            lines.append("")
            lines.append(
                "OR skip KVM: Android Studio on Windows → Device Manager → Play an AVD."
            )
        else:
            lines.append("1) Run once, then log out and back in (or reboot):")
            lines.append(f"   {kvm_fix}")
        lines.append("")
    studio = host_prep.get("android_studio") or {}
    step = 4 if (kvm_fix and plat == "wsl") else (2 if kvm_fix else 1)
    if plat in {"wsl", "windows"}:
        if studio.get("installed"):
            lines.append(
                f"{step}) Ask the user to open Android Studio on Windows → "
                "Device Manager → Play an AVD, then Refresh Mobile."
            )
        elif studio.get("install_started") or studio.get("attempted"):
            lines.append(
                f"{step}) Android Studio install was started (winget). "
                "Ask the user to finish the installer, create an AVD, Play it, "
                "then Refresh Mobile."
            )
        else:
            lines.append(
                f"{step}) Ask the user to install Android Studio on Windows: "
                "https://developer.android.com/studio - then Play an AVD."
            )
        lines.append(f"{step + 1}) Do NOT start a software emulator (-accel off).")
    elif plat == "macos":
        if studio.get("installed"):
            lines.append(
                f"{step}) Ask the user to open Android Studio → Device Manager → Play, "
                "or plug a USB phone, then Refresh."
            )
        elif studio.get("attempted"):
            lines.append(
                f"{step}) Finish Android Studio install (brew cask), create an AVD, Play."
            )
        else:
            lines.append(
                f"{step}) Install Android Studio: brew install --cask android-studio "
                "or https://developer.android.com/studio"
            )
        lines.append(f"{step + 1}) Do NOT force software GPU.")
    else:
        lines.append(
            f"{step}) Fix KVM (usermod + re-login), or plug a USB phone, then Refresh."
        )
        lines.append(f"{step + 1}) Do NOT start -accel off / swiftshader.")
    return "\n".join(lines)


def _ensure_host_prereqs(logs: list[str]) -> dict[str, Any]:
    """First Prepare step: accel access + Android Studio when needed (all OS)."""
    plat = host_platform()
    out: dict[str, Any] = {
        "platform": plat,
        "kvm_fix": None,
        "android_studio": {"installed": False, "attempted": False},
    }
    if plat in {"linux", "wsl"}:
        kvm = Path("/dev/kvm")
        if kvm.exists():
            try:
                fd = os.open(kvm, os.O_RDWR)
                os.close(fd)
                logs.append("KVM accessible")
            except OSError:
                cmd = "sudo usermod -aG kvm $USER"
                out["kvm_fix"] = cmd
                logs.append(
                    f"KVM present but not accessible. Run once: {cmd}. "
                    + (
                        "Then in Windows PowerShell: wsl --shutdown - then reopen WSL. "
                        "OR use Android Studio Windows → Device Manager → Play."
                        if plat == "wsl"
                        else "Then log out and back in (or reboot)."
                    )
                )
        elif plat == "wsl":
            logs.append("No /dev/kvm - prefer Android Studio Windows AVD.")
        else:
            logs.append("No /dev/kvm - install qemu-kvm / enable nested virt.")

    if plat in {"wsl", "windows"}:
        out["android_studio"] = _ensure_android_studio_windows(logs)
    elif plat == "macos":
        out["android_studio"] = _ensure_android_studio_macos(logs)
    else:
        # Linux: ~/.navin SDK + KVM is enough; Studio optional.
        out["android_studio"] = {
            "installed": False,
            "attempted": False,
            "detail": "optional on Linux (Navin SDK bootstrap is enough with KVM)",
        }
    return out


def _ensure_android_studio_macos(logs: list[str]) -> dict[str, Any]:
    """Detect Android Studio on macOS; install via Homebrew cask when missing."""
    info: dict[str, Any] = {
        "installed": False,
        "attempted": False,
        "install_started": False,
        "path": None,
        "detail": None,
    }
    candidates = [
        Path("/Applications/Android Studio.app"),
        Path.home() / "Applications/Android Studio.app",
    ]
    for path in candidates:
        try:
            exists = path.exists()
        except OSError:
            continue
        if exists:
            info["installed"] = True
            info["path"] = str(path)
            logs.append(f"Android Studio found: {path}")
            return info

    brew = shutil.which("brew")
    if not brew:
        info["detail"] = (
            "Android Studio not found and Homebrew missing. "
            "Install from https://developer.android.com/studio "
            "or install brew then: brew install --cask android-studio"
        )
        logs.append(info["detail"])
        return info

    logs.append("Android Studio not found - trying brew install --cask android-studio…")
    info["attempted"] = True
    cmd = [brew, "install", "--cask", "android-studio"]
    try:
        logs.append(f"$ {' '.join(cmd)}")
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            **no_window_kwargs(),
        )
        logs.append(
            f"exit={proc.returncode}\n{(proc.stdout or '')[-800:]}\n"
            f"{(proc.stderr or '')[-400:]}"
        )
        info["install_started"] = True
        for path in candidates:
            if path.exists():
                info["installed"] = True
                info["path"] = str(path)
                logs.append(f"Android Studio installed: {path}")
                return info
        if proc.returncode == 0:
            info["detail"] = (
                "brew reported success - open Android Studio, create an AVD, Play."
            )
            return info
        info["detail"] = "brew cask install failed - download from developer.android.com/studio"
    except Exception as exc:
        info["detail"] = str(exc)
        logs.append(f"brew install android-studio failed: {exc}")
    return info


def _windows_android_studio_paths() -> list[Path]:
    candidates: list[Path] = []
    if host_platform() == "wsl":
        # Typical installs under each Windows user profile.
        users = Path("/mnt/c/Users")
        if users.is_dir():
            for profile in users.iterdir():
                if not profile.is_dir():
                    continue
                name = profile.name
                if name in {"Public", "Default", "Default User", "All Users"}:
                    continue
                candidates.append(
                    profile
                    / "AppData/Local/Programs/Android/Android Studio/bin/studio64.exe"
                )
        candidates.extend(
            [
                Path("/mnt/c/Program Files/Android/Android Studio/bin/studio64.exe"),
                Path("/mnt/c/Program Files/Android/Android Studio/bin/studio.exe"),
            ]
        )
    else:
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        pf = (os.environ.get("ProgramFiles") or r"C:\Program Files").strip()
        if local:
            candidates.append(
                Path(local) / "Programs/Android/Android Studio/bin/studio64.exe"
            )
        candidates.append(Path(pf) / "Android/Android Studio/bin/studio64.exe")
    return candidates


def _ensure_android_studio_windows(logs: list[str]) -> dict[str, Any]:
    """Install Android Studio on Windows via winget when missing (WSL/Windows)."""
    info: dict[str, Any] = {
        "installed": False,
        "attempted": False,
        "install_started": False,
        "path": None,
        "detail": None,
    }
    for path in _windows_android_studio_paths():
        try:
            exists = path.is_file()
        except PermissionError:
            # Path is there but this WSL user cannot stat another Windows profile.
            info["installed"] = True
            info["path"] = str(path)
            logs.append(
                f"Android Studio likely installed (permission denied reading {path}). "
                "Open it on Windows → Device Manager → Play."
            )
            return info
        except OSError:
            continue
        if exists:
            info["installed"] = True
            info["path"] = str(path)
            logs.append(f"Android Studio found: {path}")
            return info

    logs.append("Android Studio not found on Windows - trying winget install…")
    info["attempted"] = True
    winget_cmds: list[list[str]] = []
    if host_platform() == "wsl":
        winget = shutil.which("winget.exe") or shutil.which("winget")
        ps = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if winget:
            winget_cmds.append(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "Google.AndroidStudio",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )
        if ps.is_file():
            winget_cmds.append(
                [
                    str(ps),
                    "-NoProfile",
                    "-Command",
                    "winget install -e --id Google.AndroidStudio "
                    "--accept-package-agreements --accept-source-agreements",
                ]
            )
    else:
        winget = shutil.which("winget") or shutil.which("winget.exe")
        if winget:
            winget_cmds.append(
                [
                    winget,
                    "install",
                    "-e",
                    "--id",
                    "Google.AndroidStudio",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ]
            )

    if not winget_cmds:
        info["detail"] = (
            "winget not found. Download Android Studio: "
            "https://developer.android.com/studio"
        )
        logs.append(info["detail"])
        return info

    for cmd in winget_cmds:
        try:
            logs.append(f"$ {' '.join(cmd)}")
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
                **no_window_kwargs(),
            )
            logs.append(
                f"exit={proc.returncode}\n{(proc.stdout or '')[-800:]}\n"
                f"{(proc.stderr or '')[-400:]}"
            )
            info["install_started"] = True
            # Re-scan after install attempt
            for path in _windows_android_studio_paths():
                try:
                    exists = path.is_file()
                except OSError:
                    continue
                if exists:
                    info["installed"] = True
                    info["path"] = str(path)
                    logs.append(f"Android Studio installed: {path}")
                    return info
            if proc.returncode == 0:
                info["detail"] = (
                    "winget reported success - finish setup in Android Studio UI "
                    "(Device Manager → create AVD → Play)."
                )
                return info
        except Exception as exc:
            logs.append(f"winget install failed: {exc}")
            info["detail"] = str(exc)
    if not info.get("detail"):
        info["detail"] = (
            "Could not auto-install Android Studio. Download: "
            "https://developer.android.com/studio"
        )
    return info


def render_bootstrap_result(payload: dict[str, Any]) -> str:
    lines = [
        f"Mobile bootstrap ({payload.get('platform')})",
        f"ok: {payload.get('ok')}",
        f"step: {payload.get('step')}",
    ]
    for key in ("adb", "sdk", "jdk", "avd", "device"):
        if payload.get(key):
            lines.append(f"{key}: {payload[key]}")
    if payload.get("kvm_fix"):
        lines.append(f"kvm_fix: {payload['kvm_fix']}")
    studio = payload.get("android_studio")
    if isinstance(studio, dict) and studio:
        lines.append(
            "android_studio: "
            + (
                f"installed ({studio.get('path')})"
                if studio.get("installed")
                else studio.get("detail")
                or ("install attempted" if studio.get("attempted") else "n/a")
            )
        )
    if payload.get("next"):
        lines.append("")
        lines.append(f"Next: {payload['next']}")
    if payload.get("log"):
        lines.append("")
        lines.append("Log (tail):")
        lines.append(str(payload["log"])[-2000:])
    return "\n".join(lines)


def _sdk_env(sdk: Path, jdk: str) -> dict[str, str]:
    env = dict(os.environ)
    env["ANDROID_HOME"] = str(sdk)
    env["ANDROID_SDK_ROOT"] = str(sdk)
    env["JAVA_HOME"] = jdk
    bin_parts = [
        str(Path(jdk) / "bin"),
        str(sdk / "emulator"),
        str(sdk / "platform-tools"),
        str(sdk / "cmdline-tools" / "latest" / "bin"),
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(bin_parts)
    return env


def _ensure_jdk(logs: list[str]) -> str | None:
    java = shutil.which("java")
    if java:
        home = os.environ.get("JAVA_HOME")
        if home and Path(home, "bin", "java").exists():
            logs.append(f"Using JAVA_HOME={home}")
            return home
        # Best-effort: derive from java binary
        resolved = Path(java).resolve()
        if resolved.parent.name == "bin":
            logs.append(f"Using JDK near {resolved}")
            return str(resolved.parent.parent)

    root = navin_jdk_root()
    java_bin = root / "bin" / ("java.exe" if host_platform() == "windows" else "java")
    if java_bin.is_file():
        logs.append(f"Using cached JDK {root}")
        return str(root)

    plat = host_platform()
    url = _jdk_download_url(plat)
    if not url:
        logs.append("No JDK download URL for this platform")
        return None
    arch = host_cpu_arch()
    logs.append(f"Downloading Temurin JDK 17 for {plat}/{arch}…")
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="navin-jdk-") as tmp:
            archive = Path(tmp) / ("jdk.zip" if plat == "windows" else "jdk.tgz")
            urllib.request.urlretrieve(url, archive)  # noqa: S310
            extract_to = Path(tmp) / "extract"
            extract_to.mkdir()
            if plat == "windows":
                with zipfile.ZipFile(archive, "r") as zf:
                    zf.extractall(extract_to)
            else:
                import tarfile

                with tarfile.open(archive, "r:gz") as tf:
                    tf.extractall(extract_to)
            # Archive contains a single top-level jdk-17… folder
            children = [p for p in extract_to.iterdir() if p.is_dir()]
            if not children:
                logs.append("JDK archive had no directory")
                return None
            if root.exists():
                shutil.rmtree(root)
            shutil.move(str(children[0]), str(root))
    except Exception as exc:
        logs.append(f"JDK install failed: {exc}")
        return None
    if not java_bin.is_file():
        # macOS layout sometimes nests Contents/Home
        mac_home = root / "Contents" / "Home" / "bin" / "java"
        if mac_home.is_file():
            return str(mac_home.parent.parent)
        logs.append("java missing after JDK extract")
        return None
    return str(root)


def _ensure_cmdline_tools(sdk: Path, logs: list[str]) -> str | None:
    plat = host_platform()
    bin_name = "sdkmanager.bat" if plat == "windows" else "sdkmanager"
    sdkmanager = sdk / "cmdline-tools" / "latest" / "bin" / bin_name
    if sdkmanager.is_file():
        return str(sdkmanager)

    url = _CMDLINE_TOOLS_URLS.get(plat)
    if not url:
        logs.append("No cmdline-tools URL for platform")
        return None
    logs.append("Downloading Android cmdline-tools…")
    try:
        with tempfile.TemporaryDirectory(prefix="navin-cmdline-") as tmp:
            zip_path = Path(tmp) / "cmdline-tools.zip"
            urllib.request.urlretrieve(url, zip_path)  # noqa: S310
            extract = Path(tmp) / "extract"
            extract.mkdir()
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract)
            # Zip contains cmdline-tools/… → move to cmdline-tools/latest
            src = extract / "cmdline-tools"
            if not src.is_dir():
                # Some zips nest differently
                nested = [p for p in extract.iterdir() if p.is_dir()]
                src = nested[0] if nested else src
            dest = sdk / "cmdline-tools" / "latest"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
    except Exception as exc:
        logs.append(f"cmdline-tools install failed: {exc}")
        return None
    if not sdkmanager.is_file():
        logs.append(f"{bin_name} missing after extract")
        return None
    return str(sdkmanager)


def _ensure_sdk_packages(sdkmanager: str, env: dict[str, str], logs: list[str]) -> bool:
    # Accept licenses non-interactively
    try:
        proc = subprocess.run(  # noqa: S603
            [sdkmanager, "--licenses"],
            input="y\n" * 80,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
            **no_window_kwargs(),
        )
        logs.append(f"$ sdkmanager --licenses\n{(proc.stdout or '')[-500:]}")
    except Exception as exc:
        logs.append(f"licenses: {exc}")

    image_pkg = system_image_package()
    packages = ["platform-tools", "emulator", PLATFORM_PKG, image_pkg]
    try:
        proc = subprocess.run(  # noqa: S603
            [sdkmanager, *packages],
            input="y\n" * 20,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            env=env,
            **no_window_kwargs(),
        )
        logs.append(
            f"$ sdkmanager {' '.join(packages)}\n"
            f"exit={proc.returncode}\n{(proc.stdout or '')[-800:]}\n{(proc.stderr or '')[-800:]}"
        )
        # sdkmanager often returns 0 even with warnings; verify system image folder
        sdk = Path(env["ANDROID_HOME"])
        abi = emulator_abi()
        img = sdk / "system-images" / f"android-{API_LEVEL}" / "google_apis" / abi
        if (img / "system.img").is_file() or any(img.glob("**/system.img")):
            return True
        # Some installs use different ABI folders
        if any(sdk.glob(f"system-images/android-{API_LEVEL}/**/system.img")):
            return True
        return proc.returncode == 0
    except Exception as exc:
        logs.append(f"sdkmanager packages failed: {exc}")
        return False


def _ensure_avd(sdk: Path, env: dict[str, str], logs: list[str]) -> bool:
    plat = host_platform()
    avdmanager = sdk / "cmdline-tools" / "latest" / "bin" / (
        "avdmanager.bat" if plat == "windows" else "avdmanager"
    )
    emulator = sdk / "emulator" / ("emulator.exe" if plat == "windows" else "emulator")
    if not avdmanager.is_file():
        logs.append("avdmanager missing")
        return False
    # List existing
    try:
        listed = subprocess.run(  # noqa: S603
            [str(avdmanager), "list", "avd", "-c"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
            **no_window_kwargs(),
        )
        names = {ln.strip() for ln in (listed.stdout or "").splitlines() if ln.strip()}
        if AVD_NAME in names:
            logs.append(f"AVD {AVD_NAME} already exists")
            return True
    except Exception as exc:
        logs.append(f"list avd: {exc}")

    try:
        proc = subprocess.run(  # noqa: S603
            [
                str(avdmanager),
                "create",
                "avd",
                "-n",
                AVD_NAME,
                "-k",
                system_image_package(),
                "-d",
                "pixel_6",
                "--force",
            ],
            input="no\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=env,
            **no_window_kwargs(),
        )
        logs.append(
            f"$ avdmanager create avd\nexit={proc.returncode}\n"
            f"{(proc.stdout or '')[-500:]}\n{(proc.stderr or '')[-500:]}"
        )
        return proc.returncode == 0 or AVD_NAME in (proc.stdout or "")
    except Exception as exc:
        logs.append(f"create avd failed: {exc}")
        # Emulator binary presence is still useful even if create failed
        return emulator.is_file() and False


def _start_emulator(sdk: Path, env: dict[str, str], logs: list[str]) -> bool:
    plat = host_platform()
    emulator = sdk / "emulator" / ("emulator.exe" if plat == "windows" else "emulator")
    if not emulator.is_file():
        logs.append("emulator binary missing")
        return False
    try:
        subprocess.Popen(  # noqa: S603
            [
                str(emulator),
                "-avd",
                AVD_NAME,
                "-no-snapshot-save",
                "-no-audio",
                "-netdelay",
                "none",
                "-netspeed",
                "full",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **no_window_kwargs(),
        )
        logs.append(f"Started {emulator} -avd {AVD_NAME}")
        return True
    except Exception as exc:
        logs.append(f"start emulator failed: {exc}")
        return False


def _list_devices(adb: str) -> list[str]:
    try:
        completed = subprocess.run(  # noqa: S603
            [adb, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[str] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


def _wait_for_device(adb: str, *, timeout_s: int) -> str | None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        devices = _list_devices(adb)
        if devices:
            return devices[0]
        time.sleep(2)
    return None
