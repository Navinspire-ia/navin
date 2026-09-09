# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""iOS preview tooling: Xcode simctl + libimobiledevice, selected by host OS.

On macOS Navin installs/detects the Apple stack (Xcode CLT, simulators,
USB iPhone via ``xcrun devicectl`` - the same path VS Code / Flutter use).
``idevice_*`` is only a fallback. On Linux / Windows / WSL the iOS preview
path is unavailable - Prepare keeps the Android/adb stack only.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from navin.utils.host import HostPlatform, host_platform
from navin.utils.proc import no_window_kwargs

_IOS_SIM_PREFIX = "ios-sim:"
_IOS_USB_PREFIX = "ios-usb:"


def is_ios_serial(serial: str | None) -> bool:
    if not serial:
        return False
    return serial.startswith(_IOS_SIM_PREFIX) or serial.startswith(_IOS_USB_PREFIX)


def ios_stack_chain() -> list[dict[str, Any]]:
    """Ordered install/verify chain for the iOS preview stack (macOS only).

    Each step: id, title, ok, required, command?, verify?, detail?
    Prepare / the agent must follow this order - never skip ahead.
    """
    plat = host_platform()
    if plat != "macos":
        return [
            {
                "id": "ios_host",
                "title": "macOS host",
                "ok": False,
                "required": True,
                "detail": (
                    f"Current OS is '{plat}'. iOS Simulator / iPhone USB preview "
                    "needs macOS + Xcode. Use the Android stack here."
                ),
            }
        ]

    brew = _which("brew")
    xcode_app = _xcode_app_present()
    simctl = _xcode_ok()
    sims = _list_simulators() if simctl else []
    has_runtime = bool(sims)
    booted = [s for s in sims if s.get("state") == "Booted"]
    idevice = _which("idevice_id") is not None
    idevice_shot = _which("idevicescreenshot") is not None
    usb = _list_usb_devices()
    idb = _which("idb") is not None
    pipx = _which("pipx") is not None

    return [
        {
            "id": "brew",
            "title": "Homebrew",
            "ok": brew is not None,
            # Optional for Simulator-only; required later if USB/idb packages are needed.
            "required": False,
            "command": (
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            ),
            "verify": "brew --version",
            "detail": (
                "Package manager for libimobiledevice / idb. "
                "Simulator-only preview can work with Xcode alone."
            ),
        },
        {
            "id": "xcode_app",
            "title": "Xcode.app (App Store)",
            "ok": xcode_app,
            "required": True,
            "command": "open 'macappstore://apps.apple.com/app/xcode/id497799835'",
            "verify": "ls /Applications/Xcode.app",
            "detail": (
                "Full Xcode is required for Simulator.app (CLT alone is not enough). "
                "Ask the user to install from the App Store, then reopen the terminal."
            ),
        },
        {
            "id": "xcode_select",
            "title": "xcode-select + license",
            "ok": simctl,
            "required": True,
            "command": (
                "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer "
                "&& sudo xcodebuild -license accept "
                "&& xcodebuild -runFirstLaunch"
            ),
            "verify": "xcrun --find simctl",
            "detail": "Points CLI tools at Xcode and accepts the license.",
        },
        {
            "id": "sim_runtime",
            "title": "iOS Simulator runtime",
            "ok": has_runtime,
            "required": True,
            "command": "open -a Simulator",
            "verify": "xcrun simctl list devices available",
            "detail": (
                "If empty: Xcode → Settings → Platforms → download an iOS Simulator, "
                "or: xcodebuild -downloadPlatform iOS"
            ),
        },
        {
            "id": "libimobiledevice",
            "title": "libimobiledevice (USB iPhone)",
            "ok": idevice and idevice_shot,
            "required": False,
            "command": "brew install libimobiledevice ios-deploy",
            "verify": "xcrun devicectl list devices; idevice_id -l",
            "detail": (
                "Optional fallback. USB iPhone listing uses Xcode "
                "devicectl first (same as VS Code). idevicescreenshot "
                "is only used when devicectl capture is missing."
            ),
        },
        {
            "id": "idb",
            "title": "idb (tap/swipe on Simulator)",
            "ok": idb,
            "required": False,
            "command": (
                "brew tap facebook/fb && brew install idb-companion"
                + (" && pipx install fb-idb" if pipx else " && brew install pipx && pipx install fb-idb")
            ),
            "verify": "idb --help",
            "detail": (
                "Optional. Without idb, iOS Simulator preview is view-only "
                "(screenshots work, taps do not)."
            ),
        },
        {
            "id": "device_online",
            "title": "Booted Simulator or USB iPhone",
            "ok": bool(booted) or bool(usb),
            "required": True,
            "command": "open -a Simulator",
            "verify": (
                "xcrun simctl list devices | grep Booted; "
                "xcrun devicectl list devices"
            ),
            "detail": (
                "Boot: open -a Simulator  OR  xcrun simctl boot <udid>. "
                "USB: plug the iPhone - Xcode sees it like VS Code. "
                "Unlock, Trust this computer, enable Developer Mode."
            ),
        },
    ]


def ios_readiness() -> dict[str, Any]:
    """Structured iOS status merged into the Mobile tab readiness payload."""
    plat = host_platform()
    chain = ios_stack_chain()
    if plat != "macos":
        return {
            "available": False,
            "supported": False,
            "platform": plat,
            "reason": "ios_needs_macos",
            "help": (
                "iOS preview (simulator / USB iPhone) needs macOS + Xcode. "
                "On this OS Navin uses the Android stack (emulator or USB phone)."
            ),
            "xcode": False,
            "simctl": False,
            "idevice": False,
            "idb": False,
            "devices": [],
            "simulators": [],
            "chain": chain,
            "fixes": [],
        }

    by_id = {str(step["id"]): step for step in chain}
    xcode = bool(by_id.get("xcode_app", {}).get("ok")) and bool(
        by_id.get("xcode_select", {}).get("ok")
    )
    simctl = bool(by_id.get("xcode_select", {}).get("ok"))
    idevice = bool(by_id.get("libimobiledevice", {}).get("ok"))
    idb = bool(by_id.get("idb", {}).get("ok"))
    sims = _list_simulators() if simctl else []
    usb = _list_usb_devices()
    devices = [
        *[f"{_IOS_SIM_PREFIX}{s['udid']}" for s in sims if s.get("state") == "Booted"],
        *[f"{_IOS_USB_PREFIX}{u}" for u in usb],
    ]
    ready = bool(devices)
    missing_required = [
        step for step in chain if step.get("required") and not step.get("ok")
    ]
    error: str | None = None
    help_text: str | None = None
    if missing_required:
        first = missing_required[0]
        error = f"ios_{first['id']}_missing"
        help_text = (
            f"iOS stack blocked at: {first['title']}. "
            f"{first.get('detail') or ''} "
            f"Fix: {first.get('command') or 'see Prepare'}".strip()
        )
    elif not ready:
        error = "no_ios_device"
        help_text = (
            "iOS tools OK, but nothing online. Boot a Simulator "
            "(open -a Simulator) or plug an iPhone with Developer Mode."
        )
    return {
        "available": True,
        "supported": True,
        "platform": plat,
        "ready": ready,
        "error": error,
        "help": help_text,
        "xcode": xcode,
        "simctl": simctl,
        "idevice": idevice,
        "idb": idb,
        "devices": devices,
        "simulators": sims,
        "usb_devices": usb,
        "chain": chain,
        "fixes": ios_prepare_fixes(plat, chain=chain),
    }


def ios_agent_prepare_prompt(chain: list[dict[str, Any]] | None = None) -> str:
    """Single ordered Prepare prompt for the iOS half of the macOS stack."""
    steps = chain if chain is not None else ios_stack_chain()
    lines = [
        "Prepare Mobile iOS stack on macOS (Agent mode). ORDERED checklist - "
        "do NOT skip steps. After each step, verify before continuing:",
        "",
    ]
    n = 0
    for step in steps:
        if step.get("ok"):
            continue
        n += 1
        req = "REQUIRED" if step.get("required") else "OPTIONAL"
        lines.append(f"{n}) [{req}] {step['title']}")
        if step.get("detail"):
            lines.append(f"   Why: {step['detail']}")
        if step.get("command"):
            lines.append(f"   Run / ask user: {step['command']}")
        if step.get("verify"):
            lines.append(f"   Verify: {step['verify']}")
        # Xcode App Store cannot be fully automated.
        if step.get("id") == "xcode_app":
            lines.append(
                "   DEMANDE à l'utilisateur d'installer Xcode (App Store), "
                "puis de revenir ici - tu ne peux pas le télécharger tout seul."
            )
        lines.append("")
    if n == 0:
        lines.append(
            "All iOS tooling steps look OK. A plugged iPhone is listed via "
            "Xcode (same as VS Code) as ios-usb:…. Do not hunt Flutter under "
            "~/.config. Tell the user to Refresh Mobile and Start preview."
        )
    else:
        lines.append(
            "When required steps are green: tell the user to Refresh Mobile, "
            "then Start preview (pick ios-sim:… or ios-usb:… in the device list)."
        )
    lines.append(
        "Never claim iOS works on Linux/Windows/WSL. Never -accel off for Android."
    )
    return "\n".join(lines)


def ios_prepare_fixes(
    plat: HostPlatform,
    *,
    chain: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """OS-specific install actions - one ordered chain, not random tips."""
    if plat != "macos":
        return []
    steps = chain if chain is not None else ios_stack_chain()
    fixes: list[dict[str, str]] = [
        {
            "id": "ios_prepare_chain",
            "label": "Prepare iOS stack (ordered)",
            "prompt": ios_agent_prepare_prompt(steps),
        }
    ]
    # Expose the next missing required step as a one-click command when possible.
    for step in steps:
        if step.get("ok") or not step.get("command"):
            continue
        if step.get("id") == "xcode_app":
            # App Store - no shell install.
            continue
        fixes.append(
            {
                "id": f"ios_step_{step['id']}",
                "label": f"Next: {step['title']}",
                "command": str(step["command"]),
                "prompt": ios_agent_prepare_prompt(steps),
            }
        )
        break
    return fixes


def os_stack_prepare_prompt(*, android_ready: bool = False) -> str:
    """Full Prepare prompt for THIS host: Android and/or iOS, ordered by OS."""
    plat = host_platform()
    if plat == "macos":
        lines = [
            "Prepare Mobile (Agent mode) - host is macOS → stacks Android + iOS.",
            "Follow BOTH sections in order. Short replies. Never -accel off.",
            "",
            "=== ANDROID ===",
        ]
        if android_ready:
            lines.append("Android adb already OK. Skip bootstrap unless devices fail.")
        else:
            lines.extend(
                [
                    "1) mobile(action=bootstrap) - Android Studio via brew cask if missing.",
                    "2) If needed: ASK user to open Android Studio → Device Manager → Play an AVD.",
                    "3) mobile(action=devices)",
                ]
            )
        lines.extend(["", "=== iOS ===", ios_agent_prepare_prompt()])
        lines.append(
            "Finally: tell the user to Refresh Mobile and Start preview "
            "(Android emulator/USB or iOS Simulator/iPhone)."
        )
        return "\n".join(lines)

    if plat == "wsl":
        return (
            "Prepare Mobile (Agent mode) - host is WSL → Android stack only "
            "(iOS needs macOS).\n"
            "1) Si /dev/kvm refusé après usermod: demande PowerShell WINDOWS "
            "`wsl --shutdown` (jamais depuis Ubuntu), puis rouvrir WSL.\n"
            "2) mobile(action=bootstrap).\n"
            "3) Si KVM encore bloqué: demande Android Studio WINDOWS → "
            "Device Manager → Play un AVD, puis Actualiser Mobile.\n"
            "4) mobile(action=devices) puis Démarrer le preview.\n"
            "Jamais -accel off. Ne propose pas de stack iOS sur WSL."
        )
    if plat == "windows":
        return (
            "Prepare Mobile (Agent mode) - host is Windows → Android stack only "
            "(iOS needs macOS).\n"
            "1) mobile(action=bootstrap) - Android Studio via winget if missing.\n"
            "2) ASK user to open Android Studio → Device Manager → Play an AVD.\n"
            "3) Or plug a phone with USB debugging + accept RSA prompt.\n"
            "4) mobile(action=devices) then Start preview.\n"
            "Never -accel off. Do not propose an iOS stack on Windows."
        )
    # linux
    return (
        "Prepare Mobile (Agent mode) - host is Linux → Android stack only "
        "(iOS needs macOS).\n"
        "1) Si /dev/kvm refusé: `sudo usermod -aG kvm $USER` puis demande "
        "relogin/reboot.\n"
        "2) mobile(action=bootstrap).\n"
        "3) mobile(action=devices) puis Démarrer le preview.\n"
        "Jamais -accel off. Ne propose pas de stack iOS sur Linux."
    )


def merge_ios_into_readiness(android: dict[str, Any]) -> dict[str, Any]:
    """Attach iOS status, stacks, and the OS-aware Prepare prompt."""
    ios = ios_readiness()
    out = dict(android)
    out["ios"] = ios
    stacks = ["android"]
    if ios.get("supported"):
        stacks.append("ios")
    out["stacks"] = stacks

    # adb resolved => Android tooling installed (device may still be offline).
    android_tooling_ok = bool(out.get("adb"))
    stack_fix = {
        "id": "ask_agent_stack",
        "label": "Prepare stack for this OS",
        "prompt": os_stack_prepare_prompt(android_ready=android_tooling_ok),
    }

    fixes = [stack_fix]
    seen = {"ask_agent_stack"}
    for fix in list(out.get("fixes") or []) + list(ios.get("fixes") or []):
        if not isinstance(fix, dict):
            continue
        fid = fix.get("id")
        # Drop legacy duplicate agent prompts - ask_agent_stack replaces them.
        if fid in {"ask_agent_device", "ask_agent", "ask_agent_ios", "ios_prepare_chain"}:
            continue
        if fid in seen:
            continue
        fixes.append(fix)
        seen.add(fid)
    out["fixes"] = fixes

    if not ios.get("supported"):
        return out

    ios_devices = list(ios.get("devices") or [])
    if ios_devices:
        devices = list(out.get("devices") or [])
        for d in ios_devices:
            if d not in devices:
                devices.append(d)
        out["devices"] = devices
        if not out.get("ready"):
            out["ready"] = True
            out["error"] = None
            out["stable"] = True

    if out.get("ready"):
        return out

    ios_help = str(ios.get("help") or "").strip()
    android_help = str(out.get("help") or "").strip()
    if ios_help and ios_help not in android_help:
        out["help"] = f"{android_help}\n\n--- iOS ---\n{ios_help}".strip()
    return out


def open_ios_preview(*, serial: str, fps: float = 4.0) -> Any:
    """Open an iOS preview session (simulator screenshot loop, optional idb input)."""
    from navin.mobile.preview import PreviewError

    if not serial.startswith(_IOS_SIM_PREFIX) and not serial.startswith(_IOS_USB_PREFIX):
        raise PreviewError(f"Not an iOS serial: {serial}")
    if host_platform() != "macos":
        raise PreviewError(
            "iOS preview requires macOS + Xcode. On this OS use an Android "
            "emulator or USB phone."
        )
    if serial.startswith(_IOS_SIM_PREFIX):
        udid = serial[len(_IOS_SIM_PREFIX) :]
        return IosSimPreviewSession(udid=udid, fps=fps, serial=serial)
    udid = serial[len(_IOS_USB_PREFIX) :]
    return IosUsbPreviewSession(udid=udid, fps=fps, serial=serial)


class IosSimPreviewSession:
    """Mirror a booted Simulator via simctl screenshots; taps via idb when present."""

    def __init__(self, *, udid: str, fps: float, serial: str) -> None:
        self._udid = udid
        self._serial = serial
        self._fps = max(1.0, min(float(fps), 12.0))
        self._alive = True
        self._seq = 0
        self._lock = threading.Lock()
        self._last_png: bytes | None = None
        self._idb = _which("idb")
        self._width = 0
        self._height = 0
        self._frames = 0
        self._t0 = time.monotonic()
        # Warm first frame so open fails fast if Simulator is not booted.
        png = self._capture()
        if not png:
            raise RuntimeError(
                f"Cannot screenshot iOS Simulator {udid}. Boot it in Simulator.app first."
            )
        self._last_png = png

    def poll_frame(self, timeout_ms: int = 250, after_seq: int = 0) -> dict[str, Any]:
        del timeout_ms
        if not self._alive:
            return {"seq": self._seq, "png": None}
        if self._seq > after_seq and self._last_png is not None:
            return self._frame_payload(self._last_png)
        png = self._capture()
        if png:
            with self._lock:
                self._seq += 1
                self._last_png = png
                self._frames += 1
            time.sleep(1.0 / self._fps)
            return self._frame_payload(png)
        time.sleep(0.2)
        return {"seq": self._seq, "png": None}

    def _frame_payload(self, png: bytes) -> dict[str, Any]:
        return {
            "seq": self._seq,
            "png": base64.b64encode(png).decode("ascii"),
            "width": self._width,
            "height": self._height,
        }

    def _capture(self) -> bytes | None:
        path = Path(tempfile.mkdtemp(prefix="navin-ios-")) / "frame.png"
        try:
            completed = subprocess.run(  # noqa: S603
                ["xcrun", "simctl", "io", self._udid, "screenshot", str(path)],
                capture_output=True,
                timeout=20,
                check=False,
                **no_window_kwargs(),
            )
            if completed.returncode != 0 or not path.is_file():
                return None
            data = path.read_bytes()
            if len(data) < 32 or data[:8] != b"\x89PNG\r\n\x1a\n":
                return None
            # IHDR width/height
            if len(data) >= 24:
                self._width = int.from_bytes(data[16:20], "big")
                self._height = int.from_bytes(data[20:24], "big")
            return data
        except (OSError, subprocess.SubprocessError):
            return None
        finally:
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            except OSError:
                pass

    def poll_logs(self, max_lines: int = 80) -> list[str]:
        del max_lines
        return []

    def metrics(self) -> dict[str, Any]:
        elapsed = max(0.001, time.monotonic() - self._t0)
        return {
            "width": self._width,
            "height": self._height,
            "fps": self._frames / elapsed,
            "mem_mb": 0.0,
            "cpu_pct": 0.0,
            "backend": "ios-simctl",
        }

    def tap(self, x: int, y: int) -> None:
        if not self._idb:
            raise RuntimeError(
                "Tap on iOS Simulator needs idb. "
                "brew tap facebook/fb && brew install idb-companion && pipx install fb-idb"
            )
        subprocess.run(  # noqa: S603
            [self._idb, "ui", "tap", str(int(x)), str(int(y)), "--udid", self._udid],
            capture_output=True,
            timeout=10,
            check=False,
            **no_window_kwargs(),
        )

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        if not self._idb:
            raise RuntimeError(
                "Swipe on iOS Simulator needs idb. "
                "brew tap facebook/fb && brew install idb-companion && pipx install fb-idb"
            )
        subprocess.run(  # noqa: S603
            [
                self._idb,
                "ui",
                "swipe",
                str(int(x1)),
                str(int(y1)),
                str(int(x2)),
                str(int(y2)),
                "--duration",
                str(max(0.05, duration_ms / 1000.0)),
                "--udid",
                self._udid,
            ],
            capture_output=True,
            timeout=15,
            check=False,
            **no_window_kwargs(),
        )

    def key(self, keycode: str) -> None:
        del keycode
        raise RuntimeError("iOS key events need idb / XCUITest - not wired yet")

    def text(self, value: str) -> None:
        if not self._idb:
            raise RuntimeError("Text input on iOS Simulator needs idb")
        subprocess.run(  # noqa: S603
            [self._idb, "ui", "text", str(value), "--udid", self._udid],
            capture_output=True,
            timeout=15,
            check=False,
            **no_window_kwargs(),
        )

    def ui_dump(self) -> str:
        return ""

    def device_serial(self) -> str | None:
        return self._serial

    def is_alive(self) -> bool:
        return self._alive

    def kill(self) -> None:
        self._alive = False


class IosUsbPreviewSession:
    """Mirror a USB iPhone via Xcode ``devicectl`` (VS Code path), then idevice."""

    _MAX_MISSES = 3

    def __init__(self, *, udid: str, fps: float, serial: str) -> None:
        if not _usb_screenshot_tools():
            raise RuntimeError(
                "No iPhone screenshot tool. Install Xcode (devicectl) "
                "or: brew install libimobiledevice"
            )
        self._udid = udid
        self._serial = serial
        self._fps = max(1.0, min(float(fps), 8.0))
        self._alive = True
        self._seq = 0
        self._last_png: bytes | None = None
        self._width = 0
        self._height = 0
        self._frames = 0
        self._misses = 0
        self._t0 = time.monotonic()
        png, err = self._capture()
        if not png:
            raise RuntimeError(err or _usb_screenshot_hint(udid))
        self._last_png = png

    def poll_frame(self, timeout_ms: int = 250, after_seq: int = 0) -> dict[str, Any]:
        del timeout_ms
        if not self._alive:
            return {"seq": self._seq, "png": None}
        if self._seq > after_seq and self._last_png is not None:
            return {
                "seq": self._seq,
                "png": base64.b64encode(self._last_png).decode("ascii"),
                "width": self._width,
                "height": self._height,
            }
        png, err = self._capture()
        if png:
            self._seq += 1
            self._last_png = png
            self._frames += 1
            self._misses = 0
            time.sleep(1.0 / self._fps)
            return {
                "seq": self._seq,
                "png": base64.b64encode(png).decode("ascii"),
                "width": self._width,
                "height": self._height,
            }
        self._misses += 1
        if self._misses >= self._MAX_MISSES:
            self._alive = False
            return {
                "seq": self._seq,
                "png": None,
                "error": err or _usb_screenshot_hint(self._udid),
            }
        time.sleep(0.25)
        return {"seq": self._seq, "png": None}

    def _capture(self) -> tuple[bytes | None, str | None]:
        path = Path(tempfile.mkdtemp(prefix="navin-ios-usb-")) / "frame.png"
        last_err: str | None = None
        try:
            for argv in _usb_screenshot_argv(self._udid, path):
                try:
                    completed = subprocess.run(  # noqa: S603
                        argv,
                        capture_output=True,
                        timeout=8,
                        check=False,
                        **no_window_kwargs(),
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    last_err = str(exc)
                    continue
                if completed.returncode == 0:
                    data = _read_png(path)
                    if data:
                        self._width = int.from_bytes(data[16:20], "big")
                        self._height = int.from_bytes(data[20:24], "big")
                        return data, None
                stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
                stdout = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
                last_err = stderr or stdout or f"{argv[0]} exited {completed.returncode}"
            return None, last_err or _usb_screenshot_hint(self._udid)
        finally:
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            except OSError:
                pass

    def poll_logs(self, max_lines: int = 80) -> list[str]:
        del max_lines
        return []

    def metrics(self) -> dict[str, Any]:
        elapsed = max(0.001, time.monotonic() - self._t0)
        return {
            "width": self._width,
            "height": self._height,
            "fps": self._frames / elapsed,
            "mem_mb": 0.0,
            "cpu_pct": 0.0,
            "backend": "ios-usb",
        }

    def tap(self, x: int, y: int) -> None:
        del x, y
        raise RuntimeError(
            "Tap on USB iPhone is not available without a driver stack "
            "(WebDriverAgent / idb). Preview is view-only for USB today."
        )

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> None:
        del x1, y1, x2, y2, duration_ms
        raise RuntimeError("Swipe on USB iPhone is view-only without WebDriverAgent")

    def key(self, keycode: str) -> None:
        del keycode
        raise RuntimeError("Keys on USB iPhone are not available in this preview")

    def text(self, value: str) -> None:
        del value
        raise RuntimeError("Text on USB iPhone is not available in this preview")

    def ui_dump(self) -> str:
        return ""

    def device_serial(self) -> str | None:
        return self._serial

    def is_alive(self) -> bool:
        return self._alive

    def kill(self) -> None:
        self._alive = False


def _which(name: str) -> str | None:
    return shutil.which(name)


def _xcode_app_present() -> bool:
    """True when full Xcode.app is installed (Simulator needs it, not only CLT)."""
    candidates = [
        Path("/Applications/Xcode.app"),
        Path.home() / "Applications" / "Xcode.app",
    ]
    if any(p.is_dir() for p in candidates):
        return True
    try:
        completed = subprocess.run(  # noqa: S603
            ["xcode-select", "-p"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    path = (completed.stdout or "").strip()
    return bool(path) and path.endswith(".app/Contents/Developer") and "CommandLineTools" not in path


def _xcode_ok() -> bool:
    xcrun = _which("xcrun")
    if not xcrun:
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            [xcrun, "--find", "simctl"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **no_window_kwargs(),
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _list_simulators() -> list[dict[str, str]]:
    try:
        completed = subprocess.run(  # noqa: S603
            ["xcrun", "simctl", "list", "devices", "available", "-j"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, TypeError):
        return []
    out: list[dict[str, str]] = []
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        return []
    for _runtime, entries in devices.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            udid = str(entry.get("udid") or "")
            name = str(entry.get("name") or udid)
            state = str(entry.get("state") or "")
            if udid:
                out.append({"udid": udid, "name": name, "state": state})
    return out


_IOS_UDID_RE = re.compile(
    r"\b([0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})\b"
)


def _looks_like_ios_udid(value: str) -> bool:
    return bool(_IOS_UDID_RE.fullmatch(value.strip()))


def _usb_screenshot_hint(udid: str) -> str:
    return (
        f"Cannot screenshot iPhone {udid}. Unlock it, tap Trust on this Mac, "
        "and enable Developer Mode (Settings > Privacy & Security). "
        "VS Code only lists the device - Navin also mirrors the screen."
    )


def _usb_screenshot_tools() -> bool:
    return _which("xcrun") is not None or _which("idevicescreenshot") is not None


def _usb_screenshot_argv(udid: str, path: Path) -> list[list[str]]:
    """Prefer Xcode capture (iOS 17+), then libimobiledevice."""
    argv: list[list[str]] = []
    xcrun = _which("xcrun")
    if xcrun:
        argv.append(
            [
                xcrun,
                "devicectl",
                "device",
                "capture",
                "screenshot",
                "--device",
                udid,
                "--destination",
                str(path),
                "--timeout",
                "8",
            ]
        )
    shot = _which("idevicescreenshot")
    if shot:
        argv.append([shot, "-u", udid, str(path)])
    return argv


def _read_png(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    data = path.read_bytes()
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    return None


_USB_CACHE_S = 4.0
_usb_cache: tuple[float, list[str]] | None = None


def _clear_usb_device_cache() -> None:
    global _usb_cache
    _usb_cache = None


def _list_usb_devices() -> list[str]:
    """Physical iPhones, same sources Flutter / VS Code use on macOS.

    ``devicectl`` first (fast, Xcode 15+). ``xcdevice`` only when that
    command is missing - it is slow and made the Mobile tab spin.
    """
    global _usb_cache
    now = time.monotonic()
    if _usb_cache and now - _usb_cache[0] < _USB_CACHE_S:
        return list(_usb_cache[1])
    seen: list[str] = []
    from_devicectl, devicectl_ok = _list_usb_via_devicectl()
    for udid in from_devicectl:
        if udid not in seen:
            seen.append(udid)
    if not devicectl_ok:
        for udid in _list_usb_via_xcdevice():
            if udid not in seen:
                seen.append(udid)
    for udid in _list_usb_via_idevice():
        if udid not in seen:
            seen.append(udid)
    _usb_cache = (now, list(seen))
    return list(seen)


def ios_run_devices() -> tuple[list[str], list[str]]:
    """(physical iPhone UDIDs, booted Simulator UDIDs) for ``flutter run -d``."""
    if host_platform() != "macos":
        return [], []
    physical = _list_usb_devices()
    simulators = [
        str(sim.get("udid") or "")
        for sim in _list_simulators()
        if sim.get("state") == "Booted" and sim.get("udid")
    ]
    return physical, simulators


def _list_usb_via_idevice() -> list[str]:
    idevice = _which("idevice_id")
    if not idevice:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [idevice, "-l"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _list_usb_via_devicectl() -> tuple[list[str], bool]:
    xcrun = _which("xcrun")
    if not xcrun:
        return [], False
    out = Path(tempfile.mkdtemp(prefix="navin-devicectl-")) / "devices.json"
    try:
        completed = subprocess.run(  # noqa: S603
            [
                xcrun,
                "devicectl",
                "list",
                "devices",
                "--timeout",
                "8",
                "--json-output",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            **no_window_kwargs(),
        )
        if completed.returncode != 0 or not out.is_file():
            return [], False
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return [], False
        return parse_devicectl_usb_udids(payload), True
    except (OSError, subprocess.SubprocessError):
        return [], False
    finally:
        try:
            out.unlink(missing_ok=True)
            out.parent.rmdir()
        except OSError:
            pass


def _list_usb_via_xcdevice() -> list[str]:
    xcrun = _which("xcrun")
    if not xcrun:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [xcrun, "xcdevice", "list"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return []
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        return []
    return parse_xcdevice_usb_udids(payload)


def parse_devicectl_usb_udids(payload: object) -> list[str]:
    """Extract physical iPhone / iPad UDIDs from ``devicectl list devices`` JSON."""
    entries: list[object] = []
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("devices"), list):
            entries = list(result["devices"])
        elif isinstance(payload.get("devices"), list):
            entries = list(payload["devices"])
    elif isinstance(payload, list):
        entries = payload
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not _devicectl_is_physical_ios(entry):
            continue
        udid = _devicectl_udid(entry)
        if udid and udid not in out:
            out.append(udid)
    return out


def parse_xcdevice_usb_udids(payload: object) -> list[str]:
    """Extract physical iOS UDIDs from ``xcrun xcdevice list`` JSON."""
    if not isinstance(payload, list):
        return []
    out: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("simulator") is True:
            continue
        platform = str(entry.get("platform") or "").lower()
        if platform and "iphone" not in platform and "ipad" not in platform:
            continue
        ident = str(entry.get("identifier") or "").strip()
        if _looks_like_ios_udid(ident) and ident not in out:
            out.append(ident)
    return out


def _devicectl_udid(entry: dict[str, Any]) -> str:
    hw = entry.get("hardwareProperties")
    if isinstance(hw, dict):
        for key in ("udid", "UDID"):
            value = str(hw.get(key) or "").strip()
            if _looks_like_ios_udid(value):
                return value
    for key in ("udid", "identifier"):
        value = str(entry.get(key) or "").strip()
        if _looks_like_ios_udid(value):
            return value
    return ""


def _devicectl_is_physical_ios(entry: dict[str, Any]) -> bool:
    if entry.get("simulator") is True:
        return False
    hw = entry.get("hardwareProperties")
    conn = entry.get("connectionProperties")
    platform = ""
    if isinstance(hw, dict):
        platform = str(hw.get("platform") or "").lower()
    if not platform:
        platform = str(entry.get("platform") or "").lower()
    if platform and not any(token in platform for token in ("ios", "ipad", "iphone")):
        return False
    if isinstance(conn, dict):
        transport = str(conn.get("transportType") or "").lower()
        if transport in {"local"}:
            return False
    return True
