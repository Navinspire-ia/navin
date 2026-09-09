"""Windows backend: ``SendInput`` for the pointer and keyboard, GDI for pixels.

Pure ``ctypes`` so it ships inside the PyInstaller build with nothing to
install. The process is made per-monitor DPI aware first thing, otherwise the
screenshot Windows hands back is a scaled copy and every click lands short of
its target on a 125 % / 150 % display.
"""

from __future__ import annotations

import ctypes
import io
import sys
import time
from contextlib import suppress
from ctypes import wintypes
from typing import Any

from loguru import logger

from navin.computer.base import (
    Check,
    ComputerBackend,
    ComputerError,
    DisplayInfo,
    PermissionMissingError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.keys import KeyCombo

if sys.platform != "win32":  # pragma: no cover - imported for docs / tests only
    user32 = None  # type: ignore[assignment]
    kernel32 = None
    gdi32 = None
else:  # pragma: no cover - exercised on Windows only
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
WORD = ctypes.c_uint16
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
LPARAM = ctypes.c_ssize_t
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class _POINT(ctypes.Structure):
    _fields_ = (("x", LONG), ("y", LONG))


class _RECT(ctypes.Structure):
    _fields_ = (("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG))


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT),
        ("dwFlags", DWORD), ("szDevice", wintypes.WCHAR * 32),
    )


_MONITOR_ENUM_PROC = _WINFUNCTYPE(BOOL, HANDLE, HANDLE, ctypes.POINTER(_RECT), LPARAM)
_WINDOW_ENUM_PROC = _WINFUNCTYPE(BOOL, HANDLE, LPARAM)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SW_RESTORE = 9
DWMWA_CLOAKED = 14

_BUTTON_FLAGS: dict[str, tuple[int, int]] = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# canonical key -> virtual-key code
_VK: dict[str, int] = {
    "enter": 0x0D,
    "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "numlock": 0x90,
    "scrolllock": 0x91,
    "printscreen": 0x2C,
    "pause": 0x13,
    "contextmenu": 0x5D,
    "volumeup": 0xAF,
    "volumedown": 0xAE,
    "volumemute": 0xAD,
    "playpause": 0xB3,
    "ctrl": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "meta": 0x5B,
}
for _n in range(1, 25):
    _VK[f"f{_n}"] = 0x70 + _n - 1

_EXTENDED: frozenset[str] = frozenset(
    {
        "insert",
        "delete",
        "home",
        "end",
        "pageup",
        "pagedown",
        "up",
        "down",
        "left",
        "right",
        "numlock",
        "printscreen",
        "meta",
        "contextmenu",
        "volumeup",
        "volumedown",
        "volumemute",
        "playpause",
    }
)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", DWORD),
        ("wParamL", WORD),
        ("wParamH", WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", DWORD), ("u", _INPUTUNION))


def _signature(library: Any, name: str, args: list[Any], result: Any) -> Any:
    function = getattr(library, name)
    function.argtypes = args
    function.restype = result
    return function


def _configure_win32(user: Any, kernel: Any, gdi: Any) -> None:
    """Declare every ABI: ctypes defaults truncate 64-bit handles to an int."""
    pointer = ctypes.POINTER
    for name, args, result in (
        ("GetSystemMetrics", [ctypes.c_int], ctypes.c_int),
        ("GetDC", [HANDLE], HANDLE),
        ("ReleaseDC", [HANDLE, HANDLE], ctypes.c_int),
        ("GetCursorPos", [pointer(_POINT)], BOOL),
        ("SetCursorPos", [ctypes.c_int, ctypes.c_int], BOOL),
        ("SendInput", [DWORD, pointer(_INPUT), ctypes.c_int], DWORD),
        ("MapVirtualKeyW", [DWORD, DWORD], DWORD),
        ("VkKeyScanW", [wintypes.WCHAR], ctypes.c_int16),
        ("EnumDisplayMonitors", [HANDLE, pointer(_RECT), _MONITOR_ENUM_PROC, LPARAM], BOOL),
        ("GetMonitorInfoW", [HANDLE, pointer(_MONITORINFOEXW)], BOOL),
        ("EnumWindows", [_WINDOW_ENUM_PROC, LPARAM], BOOL),
        ("GetForegroundWindow", [], HANDLE),
        ("IsWindow", [HANDLE], BOOL),
        ("IsWindowVisible", [HANDLE], BOOL),
        ("IsIconic", [HANDLE], BOOL),
        ("GetWindowTextLengthW", [HANDLE], ctypes.c_int),
        ("GetWindowTextW", [HANDLE, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        ("GetWindowRect", [HANDLE, pointer(_RECT)], BOOL),
        ("GetWindowThreadProcessId", [HANDLE, pointer(DWORD)], DWORD),
        ("ShowWindow", [HANDLE, ctypes.c_int], BOOL),
        ("SetForegroundWindow", [HANDLE], BOOL),
        ("BringWindowToTop", [HANDLE], BOOL),
        ("OpenInputDesktop", [DWORD, BOOL, DWORD], HANDLE),
        ("CloseDesktop", [HANDLE], BOOL),
        ("SetProcessDPIAware", [], BOOL),
    ):
        _signature(user, name, args, result)
    if hasattr(user, "SetProcessDpiAwarenessContext"):
        _signature(user, "SetProcessDpiAwarenessContext", [HANDLE], BOOL)
    for name, args, result in (
        ("OpenProcess", [DWORD, BOOL, DWORD], HANDLE),
        ("CloseHandle", [HANDLE], BOOL),
        ("QueryFullProcessImageNameW", [HANDLE, DWORD, wintypes.LPWSTR, pointer(DWORD)], BOOL),
    ):
        _signature(kernel, name, args, result)
    for name, args, result in (
        ("CreateCompatibleDC", [HANDLE], HANDLE),
        ("CreateCompatibleBitmap", [HANDLE, ctypes.c_int, ctypes.c_int], HANDLE),
        ("SelectObject", [HANDLE, HANDLE], HANDLE),
        ("DeleteObject", [HANDLE], BOOL),
        ("DeleteDC", [HANDLE], BOOL),
        ("BitBlt", [HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    HANDLE, ctypes.c_int, ctypes.c_int, DWORD], BOOL),
        ("GetDIBits", [HANDLE, HANDLE, DWORD, DWORD, ctypes.c_void_p, ctypes.c_void_p, DWORD], ctypes.c_int),
    ):
        _signature(gdi, name, args, result)


if user32 is not None:
    _configure_win32(user32, kernel32, gdi32)


def _make_dpi_aware() -> str:
    """Best effort; the first call that succeeds wins. Returns what happened."""
    if user32 is None:
        return "not windows"
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor v2"
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        _signature(shcore, "SetProcessDpiAwareness", [ctypes.c_int], LONG)
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "already set or unavailable"


class WindowsBackend(ComputerBackend):
    name = "windows"
    label = "Windows desktop"

    def __init__(self) -> None:
        if user32 is None:
            raise ComputerError("the Windows backend only runs on Windows")
        self._dpi = _make_dpi_aware()
        self._input_size = ctypes.sizeof(_INPUT)
        self._held_buttons: set[str] = set()
        self._held_keys: dict[tuple[int, bool], None] = {}

    # ----------------------------------------------------------------- screen

    def screen(self) -> ScreenInfo:
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            raise ComputerError("no active Windows display; open a desktop session")
        displays = tuple(self._displays()) or (
            DisplayInfo(0, left, top, width, height, primary=True, name="display"),
        )
        return ScreenInfo(width=width, height=height, left=left, top=top, displays=displays)

    def _displays(self) -> list[DisplayInfo]:
        out: list[DisplayInfo] = []

        def _cb(hmon: Any, hdc: Any, rect: Any, lparam: Any) -> bool:
            info = _MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcMonitor
                scale = 1.0
                try:
                    shcore = ctypes.WinDLL("shcore")
                    _signature(shcore, "GetDpiForMonitor", [HANDLE, ctypes.c_int,
                               ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)], LONG)
                    dpi_x = DWORD()
                    dpi_y = DWORD()
                    if (
                        shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                        == 0
                    ):
                        scale = round(dpi_x.value / 96.0, 2)
                except (AttributeError, OSError):
                    pass
                out.append(
                    DisplayInfo(
                        index=len(out),
                        left=int(r.left),
                        top=int(r.top),
                        width=int(r.right - r.left),
                        height=int(r.bottom - r.top),
                        primary=bool(info.dwFlags & 1),
                        name=str(info.szDevice),
                        scale=scale,
                    )
                )
            return True

        try:
            user32.EnumDisplayMonitors(None, None, _MONITOR_ENUM_PROC(_cb), 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("EnumDisplayMonitors failed: {}", exc)
        return out

    def screenshot(self) -> Screenshot:
        info = self.screen()
        png = self._grab_pillow(info)
        if png is None:
            png = self._grab_gdi(info)
        return Screenshot(
            png=png,
            width=info.width,
            height=info.height,
            left=info.left,
            top=info.top,
        )

    def _grab_pillow(self, info: ScreenInfo) -> bytes | None:
        try:
            from PIL import Image, ImageGrab

            image = ImageGrab.grab(all_screens=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("windows screenshot: pillow grab failed: {}", exc)
            return None
        if image.size != (info.width, info.height):
            # A DPI-virtualised grab: resample to the physical size the input
            # coordinates use so the mapping stays one ratio.
            image = image.resize((info.width, info.height), Image.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    def _grab_gdi(self, info: ScreenInfo) -> bytes:
        """BitBlt the virtual screen into a DIB and encode it as PNG with zlib.

        Pillow-free path for a frozen build without imaging libraries.
        """
        import struct
        import zlib

        width, height = info.width, info.height
        srccopy_captureblt = 0x00CC0020 | 0x40000000

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = (
                ("biSize", DWORD),
                ("biWidth", LONG),
                ("biHeight", LONG),
                ("biPlanes", WORD),
                ("biBitCount", WORD),
                ("biCompression", DWORD),
                ("biSizeImage", DWORD),
                ("biXPelsPerMeter", LONG),
                ("biYPelsPerMeter", LONG),
                ("biClrUsed", DWORD),
                ("biClrImportant", DWORD),
            )

        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            raise ComputerError("GetDC failed")
        hdc_mem = bitmap = original = None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                raise ComputerError("CreateCompatibleDC failed")
            bitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not bitmap:
                raise ComputerError("CreateCompatibleBitmap failed")
            original = gdi32.SelectObject(hdc_mem, bitmap)
            if not original or original == ctypes.c_void_p(-1).value:
                original = None
                raise ComputerError("SelectObject failed")
            if not gdi32.BitBlt(
                hdc_mem, 0, 0, width, height, hdc_screen, info.left, info.top, srccopy_captureblt
            ):
                raise ComputerError("BitBlt failed (locked screen or protected content?)")
            # GetDIBits and DeleteObject require the bitmap to be deselected.
            if not gdi32.SelectObject(hdc_mem, original):
                raise ComputerError("could not restore the capture device context")
            original = None
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # top-down rows
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buffer = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(bmi), 0)
            if lines != height:
                raise ComputerError("GetDIBits failed")
        finally:
            if original and hdc_mem:
                gdi32.SelectObject(hdc_mem, original)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)

        # BGRA -> RGBA with C-speed slice assignment, then filter byte 0 per row.
        raw = buffer.raw
        rgba = bytearray(raw)
        rgba[0::4] = raw[2::4]
        rgba[2::4] = raw[0::4]
        rgba[3::4] = b"\xff" * (width * height)
        stride = width * 4
        rows = bytearray()
        for y in range(height):
            rows += b"\x00"
            rows += rgba[y * stride : (y + 1) * stride]

        def chunk(tag: bytes, payload: bytes) -> bytes:
            crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
            return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b"")
        )

    def cursor_position(self) -> tuple[int, int]:
        point = _POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ComputerError("GetCursorPos failed")
        return int(point.x), int(point.y)

    # ---------------------------------------------------------------- pointer

    def _send(self, *inputs: _INPUT) -> None:
        array = (_INPUT * len(inputs))(*inputs)
        sent = user32.SendInput(len(inputs), array, self._input_size)
        if sent != len(inputs):
            err = getattr(ctypes, "get_last_error", lambda: 0)()
            raise ComputerError(
                f"SendInput delivered {sent}/{len(inputs)} events (error {err}). "
                "An elevated (UAC) window may have the focus; Navin cannot drive it "
                "unless it runs as administrator too."
            )

    @staticmethod
    def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        inp.mi = _MOUSEINPUT(dx, dy, ctypes.c_uint32(data & 0xFFFFFFFF).value, flags, 0, 0)
        return inp

    def move(self, x: int, y: int) -> None:
        info = self.screen()
        x, y = info.clamp(x, y)
        # Absolute coordinates on the virtual desktop are normalised to 0..65535.
        nx = int(round((x - info.left) * 65535 / max(1, info.width - 1)))
        ny = int(round((y - info.top) * 65535 / max(1, info.height - 1)))
        self._send(
            self._mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)
        )
        # SendInput's absolute move rounds; SetCursorPos pins the exact pixel.
        if not user32.SetCursorPos(int(x), int(y)):
            raise PermissionMissingError("SetCursorPos failed; unlock the Windows desktop")

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        flags = _BUTTON_FLAGS.get(button)
        if flags is None:
            raise ComputerError(f"unknown mouse button {button!r}")
        self._send(self._mouse(flags[0] if down else flags[1]))
        if down:
            self._held_buttons.add(button)
        else:
            self._held_buttons.discard(button)

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.move(x, y)
        if dy:
            self._send(self._mouse(MOUSEEVENTF_WHEEL, data=-int(dy) * WHEEL_DELTA))
        if dx:
            self._send(self._mouse(MOUSEEVENTF_HWHEEL, data=int(dx) * WHEEL_DELTA))

    # --------------------------------------------------------------- keyboard

    @staticmethod
    def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> _INPUT:
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        return inp

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        for ch in text:
            if ch == "\n":
                self._tap_vk(_VK["enter"], False)
            elif ch == "\t":
                self._tap_vk(_VK["tab"], False)
            else:
                for unit in self._utf16_units(ch):
                    release = self._key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
                    try:
                        self._send(self._key_input(0, unit, KEYEVENTF_UNICODE), release)
                    except ComputerError:
                        with suppress(ComputerError):
                            self._send(release)
                        raise
            if delay_ms:
                time.sleep(delay_ms / 1000)

    @staticmethod
    def _utf16_units(ch: str) -> list[int]:
        data = ch.encode("utf-16-le")
        return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]

    def _vk_for(self, key: str) -> tuple[int, bool, int]:
        """(vk, extended, shift_state) for one canonical key or character."""
        if key in _VK:
            return _VK[key], key in _EXTENDED, 0
        if len(key) != 1 or ord(key) > 0xFFFF:
            raise ComputerError(f"unknown key {key!r}")
        result = int(user32.VkKeyScanW(key)) & 0xFFFF
        if result == 0xFFFF:
            raise ComputerError(f"key {key!r} is not on the current keyboard layout; use type")
        vk = result & 0xFF
        shift_state = (result >> 8) & 0xFF
        return vk, False, shift_state

    def _tap_vk(self, vk: int, extended: bool, *, down: bool | None = None) -> None:
        flags = KEYEVENTF_EXTENDEDKEY if extended else 0
        scan = user32.MapVirtualKeyW(vk, 0)
        if down is None or down:
            self._send(self._key_input(vk, scan, flags))
            self._held_keys[(vk, extended)] = None
        if down is None or not down:
            self._send(self._key_input(vk, scan, flags | KEYEVENTF_KEYUP))
            self._held_keys.pop((vk, extended), None)

    def _release_keys(self, keys: list[tuple[int, bool]], *, strict: bool = True) -> None:
        error = None
        for vk, extended in reversed(keys):
            try:
                self._tap_vk(vk, extended, down=False)
            except ComputerError as exc:
                error = error or exc
        if error is not None and strict:
            raise error

    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        vk, extended, shift_state = self._vk_for(combo.key)
        mods = list(combo.modifiers)
        if shift_state & 1 and "shift" not in mods:
            mods.append("shift")
        if shift_state & 2 and "ctrl" not in mods:
            mods.append("ctrl")
        if shift_state & 4 and "alt" not in mods:
            mods.append("alt")
        keys = [(_VK[mod], mod in _EXTENDED) for mod in mods] + [(vk, extended)]
        if down is False:
            self._release_keys(keys)
            return
        pressed: list[tuple[int, bool]] = []
        try:
            for code, ext in keys:
                self._tap_vk(code, ext, down=True)
                pressed.append((code, ext))
            if down is None:
                time.sleep(0.012)
        except BaseException:
            self._release_keys(pressed, strict=False)
            raise
        if down is None:
            self._release_keys(pressed)

    # ---------------------------------------------------------------- windows

    def windows(self) -> list[WindowInfo]:
        out: list[WindowInfo] = []
        foreground = user32.GetForegroundWindow()

        def _cb(hwnd: Any, lparam: Any) -> bool:
            info = self._window_info(hwnd, foreground)
            if info is not None:
                out.append(info)
            return True

        if not user32.EnumWindows(_WINDOW_ENUM_PROC(_cb), 0):
            raise ComputerError("EnumWindows failed")
        return out

    def _window_info(self, hwnd: Any, foreground: Any) -> WindowInfo | None:
        if not user32.IsWindowVisible(hwnd):
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return None
        try:
            dwmapi = ctypes.WinDLL("dwmapi")
            _signature(dwmapi, "DwmGetWindowAttribute", [HANDLE, DWORD, ctypes.c_void_p, DWORD], LONG)
            cloaked = DWORD()
            if (
                dwmapi.DwmGetWindowAttribute(
                    hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                )
                == 0
                and cloaked.value
            ):
                return None
        except (AttributeError, OSError):
            pass
        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        app = ""
        try:
            pid = DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
            if handle:
                try:
                    size = DWORD(1024)
                    path = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                        app = path.value.rsplit("\\", 1)[-1]
                finally:
                    kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            app = ""
        return WindowInfo(
            id=str(int(hwnd)),
            title=title,
            left=int(rect.left),
            top=int(rect.top),
            width=int(rect.right - rect.left),
            height=int(rect.bottom - rect.top),
            app=app,
            active=int(hwnd) == int(foreground or 0),
            minimized=bool(user32.IsIconic(hwnd)),
        )

    def focus_window(self, window_id: str) -> bool:
        try:
            hwnd = wintypes.HWND(int(window_id, 0))
        except ValueError as exc:
            raise ComputerError(f"window id must be numeric, got {window_id!r}") from exc
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        if user32.SetForegroundWindow(hwnd):
            return user32.GetForegroundWindow() == hwnd.value
        # Windows refuses foreground changes from a background process unless
        # the process just sent input; tapping ALT satisfies that rule.
        self._tap_vk(_VK["alt"], False)
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd)) and user32.GetForegroundWindow() == hwnd.value

    # ---------------------------------------------------------- accessibility

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        from navin.computer.uia_windows import uia_snapshot

        hwnd = int(window_id, 0) if window_id else int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            raise ComputerError("no foreground window to inspect")
        return uia_snapshot(hwnd, limit=limit)

    # ----------------------------------------------------------------- doctor

    def doctor(self) -> list[Check]:
        checks: list[Check] = [Check("dpi", True, self._dpi)]
        try:
            info = self.screen()
            scales = ", ".join(f"{d.name}@{d.scale}x" for d in info.displays)
            checks.append(Check("screen", True, f"{info.width}x{info.height} ({scales})"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screen", False, str(exc)))
        try:
            shot = self.screenshot()
            checks.append(Check("screenshot", True, f"{len(shot.png) // 1024} KiB PNG"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screenshot", False, str(exc), fix="pip install pillow"))
        try:
            desktop = user32.OpenInputDesktop(0, False, 0x0100)
            if not desktop:
                raise PermissionMissingError("Windows desktop is locked or a secure UAC screen is active")
            user32.CloseDesktop(desktop)
            x, y = self.cursor_position()
            checks.append(Check("input", True, f"interactive desktop; cursor at {x},{y}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("input", False, str(exc)))
        try:
            wins = self.windows()
            checks.append(Check("windows", True, f"{len(wins)} window(s)"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("windows", False, str(exc)))
        try:
            from navin.computer.uia_windows import uia_available

            ok, detail = uia_available()
            checks.append(Check("accessibility", ok, detail))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("accessibility", False, str(exc)))
        try:
            import ctypes as _ct

            elevated = bool(_ct.windll.shell32.IsUserAnAdmin())
            checks.append(
                Check(
                    "elevation",
                    True,
                    "administrator" if elevated else "standard user",
                    fix="" if elevated else "UAC prompts and admin windows cannot be driven",
                )
            )
        except Exception:  # noqa: BLE001
            pass
        return checks

    def close(self) -> None:
        self._release_keys(list(self._held_keys), strict=False)
        for button in list(self._held_buttons):
            with suppress(ComputerError):
                self.button(0, 0, button, down=False)
