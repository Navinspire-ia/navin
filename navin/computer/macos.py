# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""macOS backend: CoreGraphics events through ctypes, ``screencapture`` for pixels.

No ``pyobjc`` needed: ``CoreGraphics.framework`` is loaded with ``ctypes`` for
display geometry, synthetic mouse / keyboard events and the permission
preflights, and the stock ``screencapture`` and ``osascript`` binaries cover
screenshots, window listing and the accessibility snapshot (System Events'
``entireContents``). So the packaged CLI drives a Mac out of the box; what the
user must grant is *Screen Recording* and *Accessibility* to the process that
runs Navin (Terminal, iTerm, the Navin app), which :meth:`MacOSBackend.doctor`
checks and explains.

Units: Quartz works in points; ``screencapture`` writes physical pixels (2x on
Retina). The backend reports the screen in screenshot pixels, as the contract
requires, and divides by the pixel-per-point ratio before posting events.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from navin.computer.base import (
    Check,
    ComputerBackend,
    ComputerError,
    DisplayInfo,
    NotSupportedError,
    PermissionMissingError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.keys import KeyCombo

# -- CoreGraphics constants ----------------------------------------------------

CG_EVENT_LEFT_MOUSE_DOWN = 1
CG_EVENT_LEFT_MOUSE_UP = 2
CG_EVENT_RIGHT_MOUSE_DOWN = 3
CG_EVENT_RIGHT_MOUSE_UP = 4
CG_EVENT_MOUSE_MOVED = 5
CG_EVENT_LEFT_MOUSE_DRAGGED = 6
CG_EVENT_RIGHT_MOUSE_DRAGGED = 7
CG_EVENT_OTHER_MOUSE_DOWN = 25
CG_EVENT_OTHER_MOUSE_UP = 26
CG_EVENT_OTHER_MOUSE_DRAGGED = 27

CG_MOUSE_BUTTON_LEFT = 0
CG_MOUSE_BUTTON_RIGHT = 1
CG_MOUSE_BUTTON_CENTER = 2

CG_HID_EVENT_TAP = 0
CG_SCROLL_EVENT_UNIT_LINE = 1
CG_MOUSE_EVENT_CLICK_STATE = 1

CG_EVENT_FLAG_MASK_SHIFT = 1 << 17
CG_EVENT_FLAG_MASK_CONTROL = 1 << 18
CG_EVENT_FLAG_MASK_ALTERNATE = 1 << 19
CG_EVENT_FLAG_MASK_COMMAND = 1 << 20

_MOD_FLAGS: dict[str, int] = {
    "shift": CG_EVENT_FLAG_MASK_SHIFT,
    "ctrl": CG_EVENT_FLAG_MASK_CONTROL,
    "alt": CG_EVENT_FLAG_MASK_ALTERNATE,
    "meta": CG_EVENT_FLAG_MASK_COMMAND,
}

# kVK_* virtual key codes (Events.h). Letters / digits follow the ANSI layout;
# the unicode string is attached too so apps reading characters get the
# right one on non-US keyboards.
_MOD_KEYCODES: dict[str, int] = {"shift": 0x38, "ctrl": 0x3B, "alt": 0x3A, "meta": 0x37}
_KEYCODES: dict[str, int] = {
    "enter": 0x24,
    "tab": 0x30,
    "space": 0x31,
    "backspace": 0x33,
    "escape": 0x35,
    "delete": 0x75,
    "insert": 0x72,
    "home": 0x73,
    "end": 0x77,
    "pageup": 0x74,
    "pagedown": 0x79,
    "left": 0x7B,
    "right": 0x7C,
    "down": 0x7D,
    "up": 0x7E,
    "capslock": 0x39,
    "numlock": 0x47,
    "volumeup": 0x48,
    "volumedown": 0x49,
    "volumemute": 0x4A,
    "contextmenu": 0x6E,
    "f1": 0x7A,
    "f2": 0x78,
    "f3": 0x63,
    "f4": 0x76,
    "f5": 0x60,
    "f6": 0x61,
    "f7": 0x62,
    "f8": 0x64,
    "f9": 0x65,
    "f10": 0x6D,
    "f11": 0x67,
    "f12": 0x6F,
    "f13": 0x69,
    "f14": 0x6B,
    "f15": 0x71,
    "f16": 0x6A,
    "f17": 0x40,
    "f18": 0x4F,
    "f19": 0x50,
    "f20": 0x5A,
    "a": 0x00,
    "s": 0x01,
    "d": 0x02,
    "f": 0x03,
    "h": 0x04,
    "g": 0x05,
    "z": 0x06,
    "x": 0x07,
    "c": 0x08,
    "v": 0x09,
    "b": 0x0B,
    "q": 0x0C,
    "w": 0x0D,
    "e": 0x0E,
    "r": 0x0F,
    "y": 0x10,
    "t": 0x11,
    "1": 0x12,
    "2": 0x13,
    "3": 0x14,
    "4": 0x15,
    "6": 0x16,
    "5": 0x17,
    "=": 0x18,
    "9": 0x19,
    "7": 0x1A,
    "-": 0x1B,
    "8": 0x1C,
    "0": 0x1D,
    "]": 0x1E,
    "o": 0x1F,
    "u": 0x20,
    "[": 0x21,
    "i": 0x22,
    "p": 0x23,
    "l": 0x25,
    "j": 0x26,
    "'": 0x27,
    "k": 0x28,
    ";": 0x29,
    "\\": 0x2A,
    ",": 0x2B,
    "/": 0x2C,
    "n": 0x2D,
    "m": 0x2E,
    ".": 0x2F,
    "`": 0x32,
}
# Shifted punctuation shares the key of its base character.
_SHIFTED: dict[str, str] = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
    '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}  # fmt: skip

_BUTTON_EVENTS: dict[str, tuple[int, int, int, int]] = {
    # button -> (down, up, dragged, CGMouseButton)
    "left": (
        CG_EVENT_LEFT_MOUSE_DOWN,
        CG_EVENT_LEFT_MOUSE_UP,
        CG_EVENT_LEFT_MOUSE_DRAGGED,
        CG_MOUSE_BUTTON_LEFT,
    ),
    "right": (
        CG_EVENT_RIGHT_MOUSE_DOWN,
        CG_EVENT_RIGHT_MOUSE_UP,
        CG_EVENT_RIGHT_MOUSE_DRAGGED,
        CG_MOUSE_BUTTON_RIGHT,
    ),
    "middle": (
        CG_EVENT_OTHER_MOUSE_DOWN,
        CG_EVENT_OTHER_MOUSE_UP,
        CG_EVENT_OTHER_MOUSE_DRAGGED,
        CG_MOUSE_BUTTON_CENTER,
    ),
}

_SETTINGS_SCREEN = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
_SETTINGS_AX = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_SETTINGS_AUTOMATION = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _load_coregraphics() -> Any:
    path = ctypes.util.find_library("CoreGraphics") or (
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    cg = ctypes.CDLL(path)
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    cg.CGGetActiveDisplayList.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    cg.CGGetActiveDisplayList.restype = ctypes.c_int32
    cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    cg.CGDisplayBounds.restype = CGRect
    cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
    cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
    cg.CGEventCreate.argtypes = [ctypes.c_void_p]
    cg.CGEventCreate.restype = ctypes.c_void_p
    cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    cg.CGEventGetLocation.restype = CGPoint
    cg.CGEventCreateMouseEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        CGPoint,
        ctypes.c_uint32,
    ]
    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventKeyboardSetUnicodeString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_uint16),
    ]
    cg.CGEventKeyboardSetUnicodeString.restype = None
    cg.CGEventCreateScrollWheelEvent2.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_int32,
    ]
    cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    cg.CGEventSetFlags.restype = None
    cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
    cg.CGEventSetIntegerValueField.restype = None
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cg.CGEventPost.restype = None
    cg.CFRelease.argtypes = [ctypes.c_void_p]
    cg.CFRelease.restype = None
    for name in ("CGPreflightScreenCaptureAccess", "CGRequestScreenCaptureAccess"):
        fn = getattr(cg, name, None)
        if fn is not None:
            fn.argtypes = []
            fn.restype = ctypes.c_bool
    return cg


@lru_cache(maxsize=1)
def _accessibility_api() -> Any:
    path = ctypes.util.find_library("ApplicationServices") or (
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    lib = ctypes.CDLL(path)
    lib.AXIsProcessTrusted.argtypes = []
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return lib


def _ax_trusted() -> bool | None:
    """Recheck permission without repeatedly loading the system framework."""
    try:
        return bool(_accessibility_api().AXIsProcessTrusted())
    except (OSError, AttributeError):
        return None


def _request_accessibility() -> bool:
    lib = _accessibility_api()
    cf = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    ptr = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [ptr, ctypes.POINTER(ptr), ctypes.POINTER(ptr),
                                    ctypes.c_ssize_t, ptr, ptr]
    cf.CFDictionaryCreate.restype = ptr
    cf.CFRelease.argtypes = [ptr]
    cf.CFRelease.restype = None
    lib.AXIsProcessTrustedWithOptions.argtypes = [ptr]
    lib.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
    key = ptr.in_dll(lib, "kAXTrustedCheckOptionPrompt")
    value = ptr.in_dll(cf, "kCFBooleanTrue")
    options = cf.CFDictionaryCreate(None, (ptr * 1)(key.value), (ptr * 1)(value.value), 1, None, None)
    if not options:
        raise ComputerError("could not create the Accessibility permission request")
    try:
        return bool(lib.AXIsProcessTrustedWithOptions(options))
    finally:
        cf.CFRelease(options)


def _system_binary(name: str, fallback: str) -> str | None:
    return shutil.which(name) or (fallback if Path(fallback).is_file() else None)


def _png_size(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ComputerError("screencapture did not return a PNG")
    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


# JXA scripts run under `osascript -l JavaScript -`; they need Accessibility.
_JXA_WINDOWS = r"""
function run(argv) {
  const se = Application('System Events');
  const out = [];
  let procs = [];
  procs = se.applicationProcesses.whose({ visible: true })();
  for (const p of procs) {
    let pid, name, front;
    try { pid = p.unixId(); name = p.name(); front = p.frontmost(); } catch (e) { continue; }
    let wins;
    try { wins = p.windows(); } catch (e) { continue; }
    for (let i = 0; i < wins.length; i++) {
      const w = wins[i];
      try {
        const pos = w.position();
        const size = w.size();
        let title = '';
        try { title = w.name() || ''; } catch (e) {}
        let minimized = false;
        try { minimized = !!w.attributes.byName('AXMinimized').value(); } catch (e) {}
        out.push({ id: pid + ':' + i, title: title, app: name, x: pos[0], y: pos[1],
                   w: size[0], h: size[1], active: !!front && i === 0, minimized: minimized });
      } catch (e) {}
    }
  }
  return JSON.stringify(out);
}
"""

_JXA_FOCUS = r"""
function run(argv) {
  const pid = parseInt(argv[0], 10);
  const index = parseInt(argv[1], 10);
  const se = Application('System Events');
  const procs = se.applicationProcesses.whose({ unixId: pid })();
  if (!procs.length) return 'no process ' + pid;
  const p = procs[0];
  const w = p.windows()[index];
  if (!w) return 'window no longer exists';
  p.frontmost = true;
  try { w.attributes.byName('AXMinimized').value = false; } catch (e) {}
  w.actions.byName('AXRaise').perform();
  return p.frontmost() ? 'ok' : 'application did not take focus';
}
"""

_JXA_SNAPSHOT = r"""
function run(argv) {
  const pid = parseInt(argv[0], 10);
  const index = parseInt(argv[1], 10);
  const limit = parseInt(argv[2], 10);
  const roles = JSON.parse(argv[3]);
  const se = Application('System Events');
  let procs;
  if (pid > 0) procs = se.applicationProcesses.whose({ unixId: pid })();
  else procs = se.applicationProcesses.whose({ frontmost: true })();
  if (!procs.length) return '[]';
  const p = procs[0];
  let wins;
  try { wins = p.windows(); } catch (e) { return '[]'; }
  const w = wins[index >= 0 && index < wins.length ? index : 0];
  const targets = [];
  if (w) targets.push(w);
  try { const mb = p.menuBars()[0]; if (mb) targets.push(mb); } catch (e) {}
  const out = [];
  for (const target of targets) {
    let els;
    try { els = target.entireContents(); } catch (e) { continue; }
    for (const el of els) {
      if (out.length >= limit) break;
      let role;
      try { role = el.role(); } catch (e) { continue; }
      if (!roles[role]) continue;
      let pos, size;
      try { pos = el.position(); size = el.size(); } catch (e) { continue; }
      if (!size || size[0] <= 0 || size[1] <= 0) continue;
      let name = '';
      try { name = el.name() || ''; } catch (e) {}
      if (!name) { try { name = el.description() || ''; } catch (e) {} }
      if (!name) { try { name = el.title() || ''; } catch (e) {} }
      let value = '';
      if (role !== 'AXSecureTextField') {
        try { const v = el.value(); if (v !== null && v !== undefined) value = String(v); } catch (e) {}
      }
      let enabled = true;
      try { enabled = el.enabled() !== false; } catch (e) {}
      let focused = false;
      try { focused = !!el.focused(); } catch (e) {}
      out.push({ role: String(role).replace(/^AX/, ''), name: String(name).slice(0, 120),
                 x: pos[0], y: pos[1], w: size[0], h: size[1], value: value.slice(0, 80),
                 enabled: enabled, focused: focused });
    }
  }
  return JSON.stringify(out);
}
"""

_AX_ROLES = {
    "AXButton": 1,
    "AXPopUpButton": 1,
    "AXMenuButton": 1,
    "AXMenuBarItem": 1,
    "AXMenuItem": 1,
    "AXCheckBox": 1,
    "AXRadioButton": 1,
    "AXTextField": 1,
    "AXSecureTextField": 1,
    "AXTextArea": 1,
    "AXComboBox": 1,
    "AXLink": 1,
    "AXSlider": 1,
    "AXIncrementor": 1,
    "AXDisclosureTriangle": 1,
    "AXTabGroup": 1,
    "AXCell": 1,
    "AXRow": 1,
    "AXStaticText": 1,
    "AXWebArea": 1,
}


class MacOSBackend(ComputerBackend):
    name = "macos"
    label = "macOS desktop"

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise NotSupportedError("the macOS backend only runs on macOS")
        try:
            self._cg = _load_coregraphics()
        except OSError as exc:
            raise NotSupportedError(f"CoreGraphics is not loadable: {exc}") from exc
        self._screencapture = _system_binary("screencapture", "/usr/sbin/screencapture")
        self._osascript = _system_binary("osascript", "/usr/bin/osascript")
        # Physical pixels per point, refined after each screenshot.
        self._px_per_point = self._backing_scale()
        self._held: set[str] = set()
        self._held_keys: dict[int, tuple[int, str | None]] = {}
        self._screen_cache: tuple[float, ScreenInfo] | None = None
        self._requested_permissions: set[str] = set()
        self._permission_prompts = True

    # ------------------------------------------------------------- geometry

    def _displays(self) -> list[tuple[int, CGRect, float]]:
        count = ctypes.c_uint32(0)
        ids = (ctypes.c_uint32 * 16)()
        if self._cg.CGGetActiveDisplayList(16, ids, ctypes.byref(count)) != 0:
            raise ComputerError("CGGetActiveDisplayList failed")
        out: list[tuple[int, CGRect, float]] = []
        for i in range(int(count.value)):
            did = int(ids[i])
            bounds = self._cg.CGDisplayBounds(did)
            width_pt = float(bounds.size.width) or 1.0
            scale = float(self._cg.CGDisplayPixelsWide(did)) / width_pt
            out.append((did, bounds, scale or 1.0))
        if not out:
            raise ComputerError("no active display")
        return out

    def _backing_scale(self) -> float:
        try:
            main = int(self._cg.CGMainDisplayID())
            bounds = self._cg.CGDisplayBounds(main)
            width_pt = float(bounds.size.width) or 1.0
            return max(1.0, float(self._cg.CGDisplayPixelsWide(main)) / width_pt)
        except Exception:  # noqa: BLE001
            return 1.0

    def _points_rect(self) -> tuple[int, int, int, int]:
        """Union of the displays, in points: (left, top, width, height)."""
        displays = self._displays()
        left = min(int(b.origin.x) for _, b, _ in displays)
        top = min(int(b.origin.y) for _, b, _ in displays)
        right = max(int(b.origin.x + b.size.width) for _, b, _ in displays)
        bottom = max(int(b.origin.y + b.size.height) for _, b, _ in displays)
        return left, top, right - left, bottom - top

    def screen(self) -> ScreenInfo:
        now = time.monotonic()
        if self._screen_cache and now - self._screen_cache[0] < 2.0:
            return self._screen_cache[1]
        main = int(self._cg.CGMainDisplayID())
        displays = self._displays()
        left, top, width, height = self._points_rect()
        s = self._px_per_point
        infos = tuple(
            DisplayInfo(
                index=i,
                left=int(round(b.origin.x * s)),
                top=int(round(b.origin.y * s)),
                width=int(round(b.size.width * s)),
                height=int(round(b.size.height * s)),
                primary=did == main,
                name=f"display {did}",
                scale=scale,
            )
            for i, (did, b, scale) in enumerate(displays)
        )
        info = ScreenInfo(
            width=int(round(width * s)),
            height=int(round(height * s)),
            left=int(round(left * s)),
            top=int(round(top * s)),
            displays=infos,
        )
        self._screen_cache = (now, info)
        return info

    def _to_points(self, x: float, y: float) -> CGPoint:
        s = self._px_per_point or 1.0
        return CGPoint(float(x) / s, float(y) / s)

    def _from_points(self, x: float, y: float) -> tuple[int, int]:
        s = self._px_per_point or 1.0
        return int(round(x * s)), int(round(y * s))

    # ------------------------------------------------------------ screenshot

    def _request_permission_once(self, kind: str) -> None:
        """First use opens the native prompt; diagnostics and retries stay quiet."""
        if not self._permission_prompts or kind in self._requested_permissions:
            return
        self._requested_permissions.add(kind)
        if kind == "screen_recording":
            request = getattr(self._cg, "CGRequestScreenCaptureAccess", None)
            if request is not None:
                request()
        elif kind == "accessibility":
            _request_accessibility()

    def screenshot(self) -> Screenshot:
        preflight = getattr(self._cg, "CGPreflightScreenCaptureAccess", None)
        if preflight is not None and not preflight():
            self._request_permission_once("screen_recording")
        if preflight is not None and not preflight():
            raise PermissionMissingError(
                "Screen Recording needs your permission. In Settings > Computer > Permissions, "
                "choose Screen and allow Navin in macOS System Settings. If you use the CLI, "
                "allow the terminal hosting Navin instead. Quit and reopen that app if macOS "
                "asks, then check again. Computer is already enabled; macOS requires your consent.",
                permission="screen_recording",
            )
        if not self._screencapture:
            raise NotSupportedError("screencapture binary not found")
        left, top, width, height = self._points_rect()
        fd, path = tempfile.mkstemp(prefix="navin-shot-", suffix=".png")
        os.close(fd)
        try:
            proc = subprocess.run(
                [
                    self._screencapture,
                    "-x",
                    "-t",
                    "png",
                    "-R",
                    f"{left},{top},{width},{height}",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                if "not permitted" in err.lower() or "could not create" in err.lower():
                    raise PermissionMissingError(
                        "Screen Recording is not granted to this process. Allow it in "
                        "Settings > Computer > Permissions > Screen, then quit and reopen "
                        "Navin if macOS asks.",
                        permission="screen_recording",
                    )
                raise ComputerError(f"screencapture failed: {err or proc.returncode}")
            try:
                png = open(path, "rb").read()  # noqa: SIM115 - short-lived
            except OSError as exc:
                raise ComputerError(f"screencapture wrote no file: {exc}") from exc
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        pw, ph = _png_size(png)
        if pw <= 0 or ph <= 0:
            raise ComputerError("empty screenshot")
        ratio = pw / float(width or 1)
        if ratio > 0 and abs(ratio - self._px_per_point) > 0.01:
            self._px_per_point = ratio
            self._screen_cache = None
        s = self._px_per_point
        return Screenshot(
            png=png, width=pw, height=ph, left=int(round(left * s)), top=int(round(top * s))
        )

    # ---------------------------------------------------------------- mouse

    def _require_accessibility(self) -> None:
        trusted = _ax_trusted()
        if trusted is False:
            self._request_permission_once("accessibility")
            trusted = _ax_trusted()
        if trusted is False:
            raise PermissionMissingError(
                "Accessibility needs your permission. Open Settings > Computer > Permissions "
                "and allow mouse and keyboard access for Navin (or the terminal hosting the CLI). "
                "Quit and reopen that app if macOS asks, then check again.",
                permission="accessibility",
            )

    def _post(self, event: int, *, releasing: bool = False) -> None:
        if not event:
            raise ComputerError("CGEvent creation failed")
        try:
            if not releasing:
                self._require_accessibility()
            self._cg.CGEventPost(CG_HID_EVENT_TAP, event)
        finally:
            self._cg.CFRelease(event)

    def cursor_position(self) -> tuple[int, int]:
        event = self._cg.CGEventCreate(None)
        if not event:
            raise ComputerError("CGEventCreate failed")
        try:
            point = self._cg.CGEventGetLocation(event)
        finally:
            self._cg.CFRelease(event)
        return self._from_points(point.x, point.y)

    def move(self, x: int, y: int) -> None:
        x, y = self.screen().clamp(x, y)
        point = self._to_points(x, y)
        if self._held:
            button = next(iter(self._held))
            kind, cg_button = _BUTTON_EVENTS[button][2], _BUTTON_EVENTS[button][3]
        else:
            kind, cg_button = CG_EVENT_MOUSE_MOVED, CG_MOUSE_BUTTON_LEFT
        self._post(self._cg.CGEventCreateMouseEvent(None, kind, point, cg_button))

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        spec = _BUTTON_EVENTS.get(button)
        if spec is None:
            raise ComputerError(f"unknown mouse button {button!r}")
        self._click_state(x, y, button, down=down, count=1)

    def _click_state(self, x: int, y: int, button: str, *, down: bool, count: int) -> None:
        spec = _BUTTON_EVENTS[button]
        kind = spec[0] if down else spec[1]
        point = self._to_points(*self.screen().clamp(x, y))
        event = self._cg.CGEventCreateMouseEvent(None, kind, point, spec[3])
        if event and count > 1:
            self._cg.CGEventSetIntegerValueField(event, CG_MOUSE_EVENT_CLICK_STATE, count)
        self._post(event, releasing=not down)
        if down:
            self._held.add(button)
        else:
            self._held.discard(button)

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        if button not in _BUTTON_EVENTS:
            raise ComputerError(f"unknown mouse button {button!r}")
        self.move(x, y)
        # macOS counts multi-clicks through the click-state field, not timing.
        for i in range(1, max(1, count) + 1):
            self._click_state(x, y, button, down=True, count=i)
            self._click_state(x, y, button, down=False, count=i)
            if i < count:
                time.sleep(0.04)

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.move(x, y)
        # Positive wheel1 scrolls up on macOS; our dy is positive downwards.
        event = self._cg.CGEventCreateScrollWheelEvent2(
            None, CG_SCROLL_EVENT_UNIT_LINE, 2, -int(dy), -int(dx), 0
        )
        self._post(event)

    # ------------------------------------------------------------- keyboard

    def _key_event(
        self, keycode: int, down: bool, *, flags: int = 0, text: str | None = None
    ) -> None:
        event = self._cg.CGEventCreateKeyboardEvent(None, keycode & 0xFFFF, bool(down))
        if not event:
            raise ComputerError("CGEventCreateKeyboardEvent failed")
        if text:
            units = text.encode("utf-16-le")
            count = len(units) // 2
            buf = (ctypes.c_uint16 * count).from_buffer_copy(units)
            self._cg.CGEventKeyboardSetUnicodeString(event, count, buf)
        self._cg.CGEventSetFlags(event, flags)
        self._post(event, releasing=not down)
        if down:
            self._held_keys[keycode] = (flags, text)
        else:
            self._held_keys.pop(keycode, None)

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        delay = max(0.0, delay_ms / 1000.0)
        for ch in text:
            if ch == "\n":
                self._key_event(_KEYCODES["enter"], True)
                self._key_event(_KEYCODES["enter"], False)
            elif ch == "\t":
                self._key_event(_KEYCODES["tab"], True)
                self._key_event(_KEYCODES["tab"], False)
            else:
                # Keycode 0 ("a") with the unicode string attached types any
                # character regardless of the active keyboard layout.
                self._key_event(0, True, text=ch)
                self._key_event(0, False, text=ch)
            if delay:
                time.sleep(delay)

    def _keycode_for(self, key: str) -> tuple[int, bool, str | None]:
        """(keycode, needs_shift, unicode) for a canonical key or character."""
        if key in _MOD_KEYCODES:
            return _MOD_KEYCODES[key], False, None
        if key in _KEYCODES:
            return _KEYCODES[key], False, key if len(key) == 1 else None
        low = key.lower()
        if len(key) == 1 and low in _KEYCODES:
            return _KEYCODES[low], key.isupper(), key
        if key in _SHIFTED:
            return _KEYCODES[_SHIFTED[key]], True, key
        if len(key) == 1:
            # Unknown layout character: keycode 0 plus unicode string.
            return 0, False, key
        raise NotSupportedError(f"key {key!r} has no macOS key code")

    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        keycode, needs_shift, text = self._keycode_for(combo.key)
        mods = list(combo.modifiers)
        if needs_shift and "shift" not in mods:
            mods.append("shift")
        flags = 0
        for mod in mods:
            flags |= _MOD_FLAGS[mod]
        # Modifier-only combos ("meta" opens Spotlight-like launchers) tap the
        # modifier itself.
        keys = [(_MOD_KEYCODES[mod], 0, None) for mod in mods] + [(keycode, flags, text)]
        if down is False:
            self._release_keys(keys)
            return
        pressed = []
        try:
            for code, _, value in keys:
                self._key_event(code, True, flags=flags, text=value)
                pressed.append((code, 0 if code in _MOD_KEYCODES.values() else flags, value))
        except BaseException:
            self._release_keys(pressed, strict=False)
            raise
        if down is None:
            self._release_keys(pressed)

    def _release_keys(self, keys: list[tuple[int, int, str | None]], *, strict: bool = True) -> None:
        error = None
        for code, flags, text in reversed(keys):
            try:
                self._key_event(code, False, flags=flags, text=text)
            except ComputerError as exc:
                error = error or exc
        if error is not None and strict:
            raise error

    # -------------------------------------------------------------- windows

    def _jxa(self, script: str, *args: str, timeout: float = 20.0) -> str:
        self._require_accessibility()
        if not self._osascript:
            raise NotSupportedError("osascript binary not found")
        try:
            proc = subprocess.run(
                [self._osascript, "-l", "JavaScript", "-", *args],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ComputerError(f"System Events did not answer within {timeout:.0f}s") from exc
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            if "-1743" in err or "not authorized to send Apple events" in err:
                raise PermissionMissingError(
                    "Automation is not granted. Allow Navin to control System Events in "
                    "macOS System Settings > Privacy & Security > Automation.",
                    permission="automation",
                )
            if "not allowed assistive access" in err or "-1719" in err or "-25211" in err:
                raise PermissionMissingError(
                    "Accessibility is not granted to this process. Allow it in System "
                    "Settings > Privacy & Security > Accessibility, then check again in Computer.",
                    permission="accessibility",
                )
            raise ComputerError(f"osascript failed: {err[-300:] or proc.returncode}")
        return (proc.stdout or "").strip()

    def windows(self) -> list[WindowInfo]:
        raw = self._jxa(_JXA_WINDOWS)
        try:
            rows: Any = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise ComputerError("System Events returned invalid JSON") from exc
        out: list[WindowInfo] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            left, top = self._from_points(float(row.get("x") or 0), float(row.get("y") or 0))
            width, height = self._from_points(float(row.get("w") or 0), float(row.get("h") or 0))
            out.append(
                WindowInfo(
                    id=str(row.get("id") or ""),
                    title=str(row.get("title") or ""),
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    app=str(row.get("app") or ""),
                    active=bool(row.get("active")),
                    minimized=bool(row.get("minimized")),
                )
            )
        return out

    def focus_window(self, window_id: str) -> bool:
        pid, _, index = str(window_id).partition(":")
        if not pid.isdigit() or (index and not index.isdigit()):
            raise ComputerError(f"window id must look like pid:index, got {window_id!r}")
        result = self._jxa(_JXA_FOCUS, pid, index or "0")
        return result == "ok"

    # ---------------------------------------------------------- accessibility

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        pid, index = "0", "0"
        if window_id:
            pid, _, index = str(window_id).partition(":")
        raw = self._jxa(
            _JXA_SNAPSHOT,
            pid or "0",
            index or "0",
            str(int(limit)),
            json.dumps(_AX_ROLES),
            timeout=45.0,
        )
        try:
            rows: Any = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise ComputerError("System Events returned invalid JSON") from exc
        elements: list[UIElement] = []
        for i, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            left, top = self._from_points(float(row.get("x") or 0), float(row.get("y") or 0))
            width, height = self._from_points(float(row.get("w") or 0), float(row.get("h") or 0))
            elements.append(
                UIElement(
                    ref=i,
                    role=str(row.get("role") or ""),
                    name=str(row.get("name") or ""),
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                    value=str(row.get("value") or ""),
                    enabled=bool(row.get("enabled", True)),
                    focused=bool(row.get("focused", False)),
                )
            )
        return elements

    # ----------------------------------------------------------------- doctor

    def permission_checks(self) -> list[Check]:
        preflight = getattr(self._cg, "CGPreflightScreenCaptureAccess", None)
        screen = bool(preflight()) if preflight is not None else True
        accessibility = _ax_trusted() is True
        return [
            Check("screen_recording", screen, "granted" if screen else "your consent is needed to view the Screen"),
            Check("accessibility", accessibility, "granted" if accessibility else "your consent is needed for mouse and keyboard control"),
        ]

    def request_permissions(self, kind: str = "screen_recording") -> list[Check]:
        if kind == "all":
            checks: list[Check] = []
            for permission in ("screen_recording", "accessibility", "automation"):
                result = self.request_permissions(permission)
                checks.extend(result)
                # The user handles one native dialog at a time. Continue with
                # the next missing permission when they return to Computer.
                if any(not check.ok for check in result):
                    break
            return checks
        panes = {"screen_recording": _SETTINGS_SCREEN, "accessibility": _SETTINGS_AX,
                 "automation": _SETTINGS_AUTOMATION}
        if kind not in panes:
            raise ComputerError(f"unknown macOS permission {kind!r}")
        self._requested_permissions.add(kind)
        granted = False
        if kind == "screen_recording":
            preflight = getattr(self._cg, "CGPreflightScreenCaptureAccess", None)
            request = getattr(self._cg, "CGRequestScreenCaptureAccess", None)
            granted = bool(preflight()) if preflight is not None else True
            if not granted and request is not None:
                request()
                granted = bool(preflight())
        elif kind == "accessibility":
            granted = _ax_trusted() is True
            if not granted:
                _request_accessibility()
                granted = _ax_trusted() is True
        elif kind == "automation":
            try:
                self.windows()
                granted = True
            except PermissionMissingError as exc:
                if exc.permission and exc.permission != kind:
                    raise
                granted = False
        if not granted:
            proc = subprocess.run(["/usr/bin/open", panes[kind]], capture_output=True,
                                  text=True, timeout=10, check=False)
            if proc.returncode:
                raise ComputerError(f"could not open macOS permissions: {proc.stderr.strip()}")
        return [Check(kind, granted, "granted" if granted else "awaiting your choice in System Settings")]

    def doctor(self) -> list[Check]:
        prompts = self._permission_prompts
        self._permission_prompts = False
        try:
            return self._doctor()
        finally:
            self._permission_prompts = prompts

    def _doctor(self) -> list[Check]:
        checks: list[Check] = []
        try:
            info = self.screen()
            scales = ", ".join(f"{d.name}@{d.scale:g}x" for d in info.displays)
            checks.append(Check("screen", True, f"{info.width}x{info.height} px ({scales})"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screen", False, str(exc)))
        preflight = getattr(self._cg, "CGPreflightScreenCaptureAccess", None)
        if preflight is not None:
            try:
                granted = bool(preflight())
            except Exception:  # noqa: BLE001
                granted = False
            checks.append(
                Check(
                    "screen_recording",
                    granted,
                    "granted" if granted else "your consent is needed to view the Screen",
                    fix=""
                    if granted
                    else "Settings > Computer > Permissions > Screen; reopen Navin if macOS asks",
                )
            )
        trusted = _ax_trusted()
        if trusted is not None:
            checks.append(
                Check(
                    "accessibility",
                    trusted,
                    "granted" if trusted else "not granted: clicks and keys are dropped",
                    fix=""
                    if trusted
                    else "Settings > Computer > Permissions > Mouse and keyboard",
                )
            )
        try:
            shot = self.screenshot()
            checks.append(
                Check(
                    "screenshot",
                    True,
                    f"{shot.width}x{shot.height}, {len(shot.png) // 1024} KiB PNG",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screenshot", False, str(exc)))
        try:
            x, y = self.cursor_position()
            self._require_accessibility()
            checks.append(Check("input", True, f"cursor at {x},{y}; Accessibility granted"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("input", False, str(exc)))
        try:
            wins = self.windows()
            checks.append(Check("windows", True, f"{len(wins)} window(s) via System Events"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("windows", False, str(exc)))
        checks.append(
            Check(
                "process",
                True,
                "permissions attach to the app hosting Navin (Terminal, iTerm, Navin.app)",
            )
        )
        return checks

    def close(self) -> None:
        self._release_keys([(code, 0, text) for code, (_, text) in self._held_keys.items()], strict=False)
        for button in list(self._held):
            try:
                x, y = self.cursor_position()
                self.button(x, y, button, down=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("macOS release button failed: {}", exc)
        self._held.clear()
