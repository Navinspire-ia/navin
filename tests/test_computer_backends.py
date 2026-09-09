"""Platform backends that can be exercised without their desktop: macOS key
mapping, the Wayland portal protocol (fake helper) and the Wayland backend's
coordinate / keysym translation against a fake portal session."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from navin.computer import wayland as wl
from navin.computer import wayland_portal as portal
from navin.computer.base import ComputerError, PermissionMissingError, ScreenInfo
from navin.computer.keys import parse_combo
from navin.computer.macos import MacOSBackend, _png_size
from navin.computer.wayland_portal import PortalSession, PortalStream


def _png(width: int, height: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class MacKeyMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        # __init__ refuses to run off macOS; the key table does not need it.
        self.backend = MacOSBackend.__new__(MacOSBackend)

    def test_letters_digits_and_named_keys(self) -> None:
        self.assertEqual(self.backend._keycode_for("c"), (0x08, False, "c"))
        self.assertEqual(self.backend._keycode_for("C"), (0x08, True, "C"))
        self.assertEqual(self.backend._keycode_for("1"), (0x12, False, "1"))
        self.assertEqual(self.backend._keycode_for("enter"), (0x24, False, None))
        self.assertEqual(self.backend._keycode_for("f12"), (0x6F, False, None))
        self.assertEqual(self.backend._keycode_for("meta"), (0x37, False, None))

    def test_shifted_punctuation_uses_base_key_with_shift(self) -> None:
        self.assertEqual(self.backend._keycode_for("!"), (0x12, True, "!"))
        self.assertEqual(self.backend._keycode_for("?"), (0x2C, True, "?"))

    def test_layout_specific_character_falls_back_to_unicode(self) -> None:
        self.assertEqual(self.backend._keycode_for("é"), (0, False, "é"))

    def test_unknown_named_key_is_reported(self) -> None:
        with self.assertRaises(ComputerError):
            self.backend._keycode_for("playpause")

    def test_png_size(self) -> None:
        self.assertEqual(_png_size(_png(7, 3)), (7, 3))
        with self.assertRaises(ComputerError):
            _png_size(b"not a png")


_FAKE_HELPER = r"""
import json, sys
log = open(sys.argv[1], "a")
for line in sys.stdin:
    req = json.loads(line)
    log.write(line); log.flush()
    op = req.get("op")
    if op == "start":
        if req.get("restore_token") == "bad":
            print(json.dumps({"id": req["id"], "ok": False, "error": "portal request Start refused (code 1)"}), flush=True)
            continue
        print(json.dumps({"id": req["id"], "ok": True, "version": 2, "restore_token": "tok-123",
                          "streams": [{"node": 42, "x": 0, "y": 0, "w": 1920, "h": 1080},
                                      {"node": 43, "x": 1920, "y": 0, "w": 1280, "h": 720}]}), flush=True)
    elif op == "quit":
        break
    else:
        print(json.dumps({"id": req["id"], "ok": True}), flush=True)
"""


class PortalSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "log.jsonl"
        self.token_file = Path(self.tmp.name) / "token"
        self._patches = [
            mock.patch.object(
                portal, "_HELPER", _FAKE_HELPER.replace("sys.argv[1]", repr(str(self.log)))
            ),
            mock.patch.object(portal, "portal_python", lambda: sys.executable),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _requests(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def test_start_parses_streams_and_saves_restore_token(self) -> None:
        session = PortalSession(self.token_file)
        streams = session.start()
        try:
            self.assertEqual([s.node for s in streams], [42, 43])
            self.assertEqual(
                streams[1], PortalStream(node=43, left=1920, top=0, width=1280, height=720)
            )
            self.assertEqual(session.version, 2)
            self.assertEqual(self.token_file.read_text(), "tok-123")
            if os.name == "posix":
                self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
            session.move(42, 10.5, 20.0)
            session.button(0x110, True)
            session.axis(0, 3)
            session.keysym(0xFF0D, False)
        finally:
            session.close()
        ops = [
            (r["op"], r.get("node"), r.get("code"), r.get("steps"), r.get("keysym"), r.get("down"))
            for r in self._requests()
        ]
        self.assertEqual(ops[0][0], "start")
        self.assertEqual(ops[1], ("move", 42, None, None, None, None))
        self.assertEqual(ops[2], ("button", None, 0x110, None, None, True))
        self.assertEqual(ops[3], ("axis", None, None, 3, None, None))
        self.assertEqual(ops[4], ("keysym", None, None, None, 0xFF0D, False))
        self.assertEqual(ops[5][0], "quit")
        self.assertFalse(session.alive)

    def test_start_reuses_saved_token(self) -> None:
        self.token_file.write_text("previous")
        session = PortalSession(self.token_file)
        session.start()
        session.close()
        self.assertEqual(self._requests()[0]["restore_token"], "previous")

    def test_refusal_becomes_permission_error(self) -> None:
        self.token_file.write_text("bad")
        session = PortalSession(self.token_file)
        with self.assertRaises(PermissionMissingError):
            session.start()
        session.close()

    def test_requests_without_helper_fail_cleanly(self) -> None:
        session = PortalSession(None)
        with self.assertRaises(ComputerError):
            session.move(1, 0, 0)


class FakePortal:
    alive = True
    version = 2

    def __init__(self) -> None:
        self.streams = [
            PortalStream(node=42, left=0, top=0, width=1920, height=1080),
            PortalStream(node=43, left=1920, top=0, width=1280, height=720),
        ]
        self.calls: list[tuple] = []

    def move(self, node, x, y):
        self.calls.append(("move", node, round(x, 3), round(y, 3)))

    def button(self, code, down):
        self.calls.append(("button", code, down))

    def axis(self, axis, steps):
        self.calls.append(("axis", axis, steps))

    def keysym(self, keysym, down):
        self.calls.append(("keysym", keysym, down))

    def close(self):
        self.calls.append(("close",))
        self.alive = False


class WaylandBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = wl.WaylandBackend()
        self.portal = FakePortal()
        self.backend._portal = self.portal
        self.backend._scale = 2.0  # HiDPI: screenshot pixels are 2x logical

    def test_screen_is_union_of_streams_in_pixels(self) -> None:
        info = self.backend.screen()
        self.assertEqual((info.width, info.height, info.left, info.top), (6400, 2160, 0, 0))
        self.assertEqual(len(info.displays), 2)
        self.assertEqual(info.displays[1].left, 3840)

    def test_move_targets_the_right_stream_in_logical_coordinates(self) -> None:
        self.backend.move(400, 300)
        self.assertEqual(self.portal.calls[-1], ("move", 42, 200.0, 150.0))
        self.backend.move(4000, 200)
        self.assertEqual(self.portal.calls[-1], ("move", 43, 80.0, 100.0))
        self.assertEqual(self.backend.cursor_position(), (4000, 200))

    def test_click_scroll_and_keys_go_through_the_portal(self) -> None:
        self.backend.click(10, 10, "right")
        self.assertIn(("button", 0x111, True), self.portal.calls)
        self.assertIn(("button", 0x111, False), self.portal.calls)
        self.portal.calls.clear()
        self.backend.scroll(10, 10, 0, 3)
        self.assertEqual(self.portal.calls[-1], ("axis", 0, 3))
        self.portal.calls.clear()
        self.backend.key(parse_combo("ctrl+shift+t"))
        self.assertEqual(
            self.portal.calls,
            [
                ("keysym", 0xFFE3, True),
                ("keysym", 0xFFE1, True),
                ("keysym", ord("t"), True),
                ("keysym", ord("t"), False),
                ("keysym", 0xFFE1, False),
                ("keysym", 0xFFE3, False),
            ],
        )
        self.portal.calls.clear()
        self.backend.type_text("é\n", delay_ms=0)
        self.assertEqual(
            self.portal.calls,
            [
                ("keysym", 0xE9, True),
                ("keysym", 0xE9, False),
                ("keysym", 0xFF0D, True),
                ("keysym", 0xFF0D, False),
            ],
        )

    def test_close_releases_held_buttons_and_portal(self) -> None:
        self.backend.button(5, 5, "left", down=True)
        self.backend.close()
        self.assertIn(("button", 0x110, False), self.portal.calls)
        self.assertEqual(self.portal.calls[-1], ("close",))
        self.assertIsNone(self.backend._portal)

    def test_no_driver_gives_actionable_error(self) -> None:
        backend = wl.WaylandBackend()
        backend._portal_error = "PyGObject missing"
        backend._ydotool = None
        backend._screen_cache = ScreenInfo(width=100, height=100)
        with self.assertRaises(ComputerError) as ctx:
            backend.move(1, 1)
        self.assertIn("ydotoold", str(ctx.exception))
        self.assertIn("PyGObject missing", str(ctx.exception))


class WaylandDetectTest(unittest.TestCase):
    def test_requested_wayland_backend_instantiates_lazily(self) -> None:
        if sys.platform != "linux":
            self.skipTest("Linux only")
        from navin.computer.detect import create_backend, detect_platform

        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            choice = detect_platform(preferred="wayland")
            self.assertEqual(choice.name, "wayland")
            backend = create_backend(type("Cfg", (), {"backend": "wayland", "display": None})())
            self.assertIsInstance(backend, wl.WaylandBackend)
            # Nothing talked to D-Bus yet: the portal starts on first use.
            self.assertIsNone(backend._portal)


if __name__ == "__main__":
    unittest.main()
