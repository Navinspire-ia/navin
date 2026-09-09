# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Native desktop ABI, permission and input cleanup contracts on every CI OS."""

from __future__ import annotations

import ctypes
import io
import plistlib
import struct
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from navin.computer import macos, windows
from navin.computer.base import ComputerError, PermissionMissingError, ScreenInfo
from navin.computer.detect import detect_platform
from navin.computer.keys import parse_combo


class Win32ContractTest(unittest.TestCase):
    def setUp(self):
        self.user = mock.Mock()
        self.kernel = mock.Mock()
        self.gdi = mock.Mock()
        for name, value in (("user32", self.user), ("kernel32", self.kernel), ("gdi32", self.gdi)):
            patch = mock.patch.object(windows, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.user.MapVirtualKeyW.return_value = 30
        self.user.VkKeyScanW.return_value = 0x41
        self.user.SendInput.side_effect = lambda count, inputs, size: count
        self.backend = windows.WindowsBackend()

    def test_input_layout_matches_windows_abi_on_32_and_64_bit_hosts(self):
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(windows._INPUT), 40 if pointer_size == 8 else 28)
        self.assertEqual(ctypes.sizeof(windows._KEYBDINPUT), 24 if pointer_size == 8 else 16)
        self.assertEqual(ctypes.sizeof(windows._RECT), 16)
        self.assertEqual(ctypes.sizeof(windows._POINT), 8)
        self.assertEqual(windows._INPUT.u.offset, pointer_size)

    def test_win32_handles_and_callbacks_are_declared_as_pointer_sized(self):
        windows._configure_win32(self.user, self.kernel, self.gdi)
        for function in (self.user.GetForegroundWindow, self.user.GetDC,
                         self.kernel.OpenProcess, self.gdi.CreateCompatibleDC,
                         self.gdi.CreateCompatibleBitmap, self.gdi.SelectObject):
            self.assertIs(function.restype, ctypes.c_void_p)
        self.assertIs(self.user.GetWindowRect.argtypes[0], ctypes.c_void_p)
        self.assertIs(self.kernel.QueryFullProcessImageNameW.argtypes[0], ctypes.c_void_p)
        self.assertEqual(self.user.SendInput.argtypes[1], ctypes.POINTER(windows._INPUT))

    def _prepare_gdi(self):
        self.user.GetDC.return_value = 0x123456780001
        self.gdi.CreateCompatibleDC.return_value = 0x123456780002
        self.gdi.CreateCompatibleBitmap.return_value = 0x123456780003
        self.gdi.SelectObject.return_value = 0x123456780004
        self.gdi.BitBlt.return_value = 1

    def test_gdi_deselects_bitmap_before_reading_pixels_and_releases_handles(self):
        self._prepare_gdi()

        def read_pixels(dc, bitmap, start, height, buffer, info, usage):
            self.assertEqual(self.gdi.SelectObject.call_args.args, (dc, 0x123456780004))
            return height

        self.gdi.GetDIBits.side_effect = read_pixels
        png = self.backend._grab_gdi(ScreenInfo(7, 3, -7, -3))
        self.assertEqual(Image.open(io.BytesIO(png)).size, (7, 3))
        self.gdi.DeleteObject.assert_called_once_with(0x123456780003)
        self.gdi.DeleteDC.assert_called_once_with(0x123456780002)
        self.user.ReleaseDC.assert_called_once_with(None, 0x123456780001)

    def test_gdi_capture_failure_restores_original_bitmap(self):
        self._prepare_gdi()
        self.gdi.BitBlt.return_value = 0
        with self.assertRaisesRegex(ComputerError, "BitBlt"):
            self.backend._grab_gdi(ScreenInfo(7, 3))
        self.gdi.SelectObject.assert_called_with(0x123456780002, 0x123456780004)
        self.gdi.DeleteObject.assert_called_once_with(0x123456780003)
        self.user.ReleaseDC.assert_called_once()

    def test_gdi_allocation_failure_releases_only_allocated_resources(self):
        self._prepare_gdi()
        self.gdi.CreateCompatibleBitmap.return_value = 0
        with self.assertRaisesRegex(ComputerError, "CreateCompatibleBitmap"):
            self.backend._grab_gdi(ScreenInfo(7, 3))
        self.gdi.DeleteObject.assert_not_called()
        self.gdi.DeleteDC.assert_called_once()
        self.user.ReleaseDC.assert_called_once()

    def test_failed_shortcut_releases_pressed_modifiers(self):
        events = []

        def send(count, inputs, size):
            events.extend((item.ki.wVk, item.ki.dwFlags) for item in inputs)
            if len(events) == 3:
                return 0
            return count

        self.user.SendInput.side_effect = send
        with self.assertRaisesRegex(ComputerError, "SendInput"):
            self.backend.key(parse_combo("ctrl+shift+a"), down=True)
        self.assertEqual(events[-2:], [(0x10, windows.KEYEVENTF_KEYUP), (0x11, windows.KEYEVENTF_KEYUP)])
        self.assertFalse(self.backend._held_keys)

    def test_close_releases_held_keys_and_buttons(self):
        self.backend.key(parse_combo("ctrl+a"), down=True)
        self.backend.button(0, 0, "left", down=True)
        self.backend.close()
        self.assertFalse(self.backend._held_keys)
        self.assertFalse(self.backend._held_buttons)
        sent = self.user.SendInput.call_args.args[1][0]
        self.assertEqual(sent.mi.dwFlags, windows.MOUSEEVENTF_LEFTUP)

    def test_partial_unicode_injection_sends_the_missing_keyup(self):
        self.user.SendInput.side_effect = [1, 1]
        with self.assertRaisesRegex(ComputerError, "SendInput"):
            self.backend.type_text("é", delay_ms=0)
        count, inputs, size = self.user.SendInput.call_args.args
        self.assertEqual(count, 1)
        self.assertEqual(inputs[0].ki.wScan, ord("é"))
        self.assertEqual(inputs[0].ki.dwFlags, windows.KEYEVENTF_UNICODE | windows.KEYEVENTF_KEYUP)

    def test_non_bmp_text_sends_utf16_surrogates(self):
        self.backend.type_text("🚀", delay_ms=0)
        units = [call.args[1][0].ki.wScan for call in self.user.SendInput.call_args_list]
        self.assertEqual(units, [0xD83D, 0xDE80])

    def test_keyboard_layout_api_receives_a_wide_character(self):
        self.backend.key(parse_combo("ctrl+a"))
        self.user.VkKeyScanW.assert_called_once_with("a")

    def test_negative_monitor_origin_is_mapped_to_absolute_virtual_desktop(self):
        self.backend.screen = lambda: ScreenInfo(3840, 1080, left=-1920)
        self.backend.move(-1920, 0)
        item = self.user.SendInput.call_args.args[1][0]
        self.assertEqual((item.mi.dx, item.mi.dy), (0, 0))
        self.backend.move(1919, 1079)
        item = self.user.SendInput.call_args.args[1][0]
        self.assertEqual((item.mi.dx, item.mi.dy), (65535, 65535))

    def test_invalid_window_cannot_report_focus_success(self):
        self.user.IsWindow.return_value = False
        self.assertFalse(self.backend.focus_window("1234"))
        self.user.SetForegroundWindow.assert_not_called()


class MacOSContractTest(unittest.TestCase):
    def setUp(self):
        self.backend = macos.MacOSBackend.__new__(macos.MacOSBackend)
        self.backend._cg = mock.Mock()
        self.backend._cg.CGEventCreateKeyboardEvent.return_value = 100
        self.backend._screencapture = "/usr/sbin/screencapture"
        self.backend._osascript = "/usr/bin/osascript"
        self.backend._held = set()
        self.backend._held_keys = {}
        self.backend._px_per_point = 1.0
        self.backend._screen_cache = None
        self.backend._requested_permissions = set()
        self.backend._permission_prompts = True
        self.trust = mock.patch.object(macos, "_ax_trusted", return_value=True)
        self.trusted = self.trust.start()
        self.addCleanup(self.trust.stop)
        request = mock.patch.object(macos, "_request_accessibility", return_value=False)
        self.request_accessibility = request.start()
        self.addCleanup(request.stop)

    def test_denied_screen_permission_never_captures_wallpaper_as_success(self):
        self.backend._cg.CGPreflightScreenCaptureAccess.return_value = False
        with mock.patch.object(macos.subprocess, "run") as run:
            with self.assertRaisesRegex(PermissionMissingError, "Screen Recording"):
                self.backend.screenshot()
            run.assert_not_called()
        self.backend._cg.CGRequestScreenCaptureAccess.assert_called_once()
        with self.assertRaises(PermissionMissingError) as denied:
            self.backend.screenshot()
        self.assertEqual(denied.exception.permission, "screen_recording")
        self.backend._cg.CGRequestScreenCaptureAccess.assert_called_once()

    def test_first_screen_request_continues_only_after_preflight_is_granted(self):
        self.backend._cg.CGPreflightScreenCaptureAccess.return_value = False

        def grant():
            self.backend._cg.CGPreflightScreenCaptureAccess.return_value = True
            return True

        self.backend._cg.CGRequestScreenCaptureAccess.side_effect = grant
        self.backend._points_rect = lambda: (0, 0, 8, 6)
        png = io.BytesIO()
        Image.new("RGB", (8, 6)).save(png, format="PNG")

        def capture(command, **kwargs):
            Path(command[-1]).write_bytes(png.getvalue())
            return SimpleNamespace(returncode=0)

        with mock.patch.object(macos.subprocess, "run", side_effect=capture):
            self.assertEqual(self.backend.screenshot().png, png.getvalue())
        self.backend._cg.CGRequestScreenCaptureAccess.assert_called_once()

    def test_diagnostics_do_not_open_first_use_permission_prompts(self):
        self.backend._cg.CGPreflightScreenCaptureAccess.return_value = False
        self.trusted.return_value = False
        with mock.patch.object(self.backend, "screen", return_value=ScreenInfo(8, 6)):
            checks = self.backend.doctor()
        self.assertFalse(next(check for check in checks if check.name == "screen_recording").ok)
        self.backend._cg.CGRequestScreenCaptureAccess.assert_not_called()
        self.request_accessibility.assert_not_called()
        self.assertTrue(self.backend._permission_prompts)

    def test_passive_permission_check_never_reads_windows_or_pixels(self):
        self.backend._cg.CGPreflightScreenCaptureAccess.return_value = False
        self.trusted.return_value = False
        with mock.patch.object(self.backend, "screenshot") as capture, mock.patch.object(self.backend, "windows") as windows_list:
            checks = self.backend.permission_checks()
        self.assertEqual([(check.name, check.ok) for check in checks], [("screen_recording", False), ("accessibility", False)])
        capture.assert_not_called()
        windows_list.assert_not_called()
        self.request_accessibility.assert_not_called()

    def test_denied_accessibility_releases_event_without_posting_it(self):
        self.trusted.return_value = False
        with self.assertRaisesRegex(PermissionMissingError, "Accessibility"):
            self.backend._post(100)
        self.backend._cg.CGEventPost.assert_not_called()
        self.backend._cg.CFRelease.assert_called_once_with(100)
        self.request_accessibility.assert_called_once()
        with self.assertRaises(PermissionMissingError) as denied:
            self.backend._post(101)
        self.assertEqual(denied.exception.permission, "accessibility")
        self.request_accessibility.assert_called_once()

    def test_key_release_still_runs_after_permission_is_revoked(self):
        self.backend._key_event(0x37, True)
        self.trusted.return_value = False
        self.backend.close()
        self.assertFalse(self.backend._held_keys)
        self.assertEqual(self.backend._cg.CGEventPost.call_count, 2)
        self.backend._cg.CGEventSetFlags.assert_called_with(100, 0)

    def test_failed_shortcut_releases_already_pressed_modifiers(self):
        calls = []
        original = self.backend._key_event

        def key_event(code, down, **kwargs):
            calls.append((code, down))
            if down and code == 0x08:
                raise ComputerError("cannot create event")
            return original(code, down, **kwargs)

        with mock.patch.object(self.backend, "_key_event", side_effect=key_event):
            with self.assertRaises(ComputerError):
                self.backend.key(parse_combo("cmd+shift+c"), down=True)
        self.assertEqual(calls[-2:], [(code, False) for code, _ in reversed(calls[:2])])
        self.assertFalse(self.backend._held_keys)

    def test_retina_capture_preserves_negative_origins_and_click_coordinates(self):
        self.backend._points_rect = lambda: (-120, -20, 440, 180)
        png = io.BytesIO()
        Image.new("RGB", (880, 360)).save(png, format="PNG")

        def capture(command, **kwargs):
            Path(command[-1]).write_bytes(png.getvalue())
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(macos.subprocess, "run", side_effect=capture):
            shot = self.backend.screenshot()
        self.assertEqual((shot.width, shot.height, shot.left, shot.top), (880, 360, -240, -40))
        point = self.backend._to_points(-120, 90)
        self.assertEqual((point.x, point.y), (-60, 45))

    def test_unicode_key_event_contains_both_surrogate_units(self):
        self.backend.type_text("🚀", delay_ms=0)
        _, count, units = self.backend._cg.CGEventKeyboardSetUnicodeString.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual(list(units), list(struct.unpack("<HH", "🚀".encode("utf-16-le"))))

    def test_automation_permission_failure_is_reported(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="Not authorized to send Apple events (-1743)")
        with mock.patch.object(macos.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(PermissionMissingError, "Automation"):
                self.backend.windows()

    def test_accessibility_request_opens_settings_when_permission_is_pending(self):
        self.trusted.return_value = False
        with mock.patch.object(macos, "_request_accessibility", return_value=False), \
             mock.patch.object(macos.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            checks = self.backend.request_permissions("accessibility")
        self.assertFalse(checks[0].ok)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/open", macos._SETTINGS_AX])

    def test_all_permissions_continue_from_the_first_missing_access(self):
        self.backend._cg.CGPreflightScreenCaptureAccess.return_value = False
        self.trusted.return_value = False
        with mock.patch.object(macos.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(self.backend, "windows", return_value=[]) as windows_list:
            first = self.backend.request_permissions("all")
            self.assertEqual([(check.name, check.ok) for check in first], [("screen_recording", False)])
            self.request_accessibility.assert_not_called()
            windows_list.assert_not_called()
            self.backend._cg.CGPreflightScreenCaptureAccess.return_value = True
            second = self.backend.request_permissions("all")
            self.assertEqual([(check.name, check.ok) for check in second], [("screen_recording", True), ("accessibility", False)])
            self.trusted.return_value = True
            final = self.backend.request_permissions("all")
        self.assertEqual([check.name for check in final], ["screen_recording", "accessibility", "automation"])
        self.assertTrue(all(check.ok for check in final))
        self.backend._cg.CGRequestScreenCaptureAccess.assert_called_once()
        self.request_accessibility.assert_called_once()
        windows_list.assert_called_once()

    def test_disappeared_window_does_not_report_focus_success(self):
        with mock.patch.object(self.backend, "_jxa", return_value="window no longer exists"):
            self.assertFalse(self.backend.focus_window("123:1"))
        with self.assertRaises(ComputerError):
            self.backend.focus_window("123:invalid")


class NativePackagingTest(unittest.TestCase):
    def test_tauri_and_sidecar_entitlements_include_application_automation(self):
        root = Path(__file__).resolve().parents[1]
        plist = root / "desktop/src-tauri/entitlements.plist"
        info = root / "desktop/src-tauri/Info.plist"
        self.assertTrue(plistlib.loads(plist.read_bytes())["com.apple.security.automation.apple-events"])
        self.assertTrue(plistlib.loads(info.read_bytes())["NSAppleEventsUsageDescription"])
        for path in ("packaging/linux/build-offline.sh", "packaging/macos/build-offline.sh",
                     "packaging/windows/build-offline.ps1"):
            self.assertIn("browser,computer,", (root / path).read_text())
        dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
        self.assertTrue(any(dep.startswith("pillow") for dep in dependencies))
        self.assertTrue(any(dep.startswith("python-xlib") and "linux" in dep for dep in dependencies))

    def test_explicit_foreign_backend_does_not_silently_control_a_different_desktop(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(detect_platform(preferred="windows").name, "none")
            self.assertEqual(detect_platform(preferred="macos").name, "none")
