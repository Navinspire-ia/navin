"""X11 backend against a throwaway Xvfb: real XTest input, real pixels.

Skipped when Xvfb or python-xlib is missing (Windows / macOS / minimal CI).
"""

from __future__ import annotations

import shutil
import subprocess
import time
import unittest

DISPLAY = ":96"


def _xlib_available() -> bool:
    try:
        import Xlib  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(shutil.which("Xvfb") and _xlib_available(), "needs Xvfb and python-xlib")
class X11BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xvfb = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.0)
        from Xlib import X
        from Xlib import display as xdisplay

        cls.d = xdisplay.Display(DISPLAY)
        root = cls.d.screen().root
        cls.win = root.create_window(
            100,
            100,
            400,
            300,
            0,
            cls.d.screen().root_depth,
            X.InputOutput,
            X.CopyFromParent,
            background_pixel=cls.d.screen().white_pixel,
            event_mask=X.KeyPressMask | X.ButtonPressMask,
        )
        cls.win.set_wm_name("Navin Probe")
        cls.win.set_wm_class("probe", "NavinProbe")
        cls.win.map()
        cls.win.set_input_focus(X.RevertToParent, X.CurrentTime)
        cls.d.sync()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.d.close()
        finally:
            cls.xvfb.terminate()
            cls.xvfb.wait(timeout=5)

    def _events(self) -> list[tuple]:
        from Xlib import XK, X

        out: list[tuple] = []
        self.d.sync()
        while self.d.pending_events():
            ev = self.d.next_event()
            if ev.type == X.KeyPress:
                keysym = self.d.keycode_to_keysym(ev.detail, 0)
                out.append(("key", XK.keysym_to_string(keysym) or hex(keysym), ev.state))
            elif ev.type == X.ButtonPress:
                out.append(("button", ev.detail, ev.event_x, ev.event_y))
        return out

    def test_pointer_keyboard_screenshot_and_windows(self) -> None:
        from navin.computer.keys import parse_combo
        from navin.computer.x11 import X11Backend

        backend = X11Backend(display=DISPLAY)
        try:
            info = backend.screen()
            self.assertEqual((info.width, info.height), (1280, 800))

            shot = backend.screenshot()
            self.assertTrue(shot.png.startswith(b"\x89PNG"))
            self.assertEqual((shot.width, shot.height), (1280, 800))

            backend.move(300, 250)
            self.assertEqual(backend.cursor_position(), (300, 250))

            backend.click(300, 250, "left")
            backend.click(310, 260, "right")
            backend.scroll(300, 250, 0, 1)
            backend.type_text("Hé!", delay_ms=1)
            backend.key(parse_combo("ctrl+s"))
            backend.key(parse_combo("Return"))
            time.sleep(0.2)

            events = self._events()
            buttons = [e for e in events if e[0] == "button"]
            self.assertEqual([b[1] for b in buttons[:3]], [1, 3, 5])
            self.assertEqual(
                (buttons[0][2], buttons[0][3]), (200, 150), "window-relative click point"
            )
            keys = [e for e in events if e[0] == "key"]
            names = [k[1] for k in keys]
            self.assertIn("h", names)
            self.assertIn("s", names)
            ctrl_s = [k for k in keys if k[1] == "s"][0]
            self.assertTrue(ctrl_s[2] & 4, "Control was held for ctrl+s")
            self.assertIn("\r", names, "Return was pressed")

            windows = backend.windows()
            probe = [w for w in windows if w.title == "Navin Probe"]
            self.assertEqual(len(probe), 1)
            self.assertEqual((probe[0].left, probe[0].top), (100, 100))
            self.assertTrue(backend.focus_window(probe[0].id))

            checks = {c.name: c.ok for c in backend.doctor()}
            self.assertTrue(checks["screen"] and checks["input"] and checks["screenshot"])

            # Rootless servers (WSLg / XWayland) refuse root.get_image; the
            # composite path must still show the white probe window.
            d = backend._xd()
            canvas = backend._composite_windows(d, d.screen().root, 1280, 800)
            self.assertIsNotNone(canvas)
            self.assertEqual(canvas.getpixel((300, 250)), (255, 255, 255))
            self.assertNotEqual(canvas.getpixel((50, 50)), (255, 255, 255))
        finally:
            backend.close()

    def test_tool_end_to_end_maps_model_pixels_onto_the_real_window(self) -> None:
        import asyncio

        from navin.agent.tools.computer import _SESSIONS, ComputerTool, ComputerToolConfig
        from navin.agent.tools.context import RequestContext, request_context
        from navin.computer.x11 import X11Backend

        _SESSIONS.clear()
        self._events()  # drain
        config = ComputerToolConfig(
            enabled=True,
            ask="never",
            settle_ms=0,
            audit_log=False,
            audit_screenshots=False,
            live_view=False,
        )
        tool = ComputerTool(config=config, backend_factory=lambda: X11Backend(display=DISPLAY))
        ctx = RequestContext(channel="test", chat_id="x", session_key="x11:e2e", turn_id="t1")

        async def drive() -> None:
            with request_context(ctx):
                shot = await tool.execute(action="screenshot")
                text = " ".join(b.get("text", "") for b in shot if b.get("type") == "text")
                # 1280x800 fits the 1366x768 box at 0.96 -> 1229x768.
                assert "screenshot 1229x768 = screen 1280x800" in text, text
                # Window centre (300, 250) on screen is (288, 240) in the screenshot.
                result = await tool.execute(
                    action="left_click", coordinate=[288, 240], screenshot=False
                )
                assert "left_click at (288, 240)" in str(result), result
                await tool.execute(action="type", text="ok", screenshot=False)
                await tool.execute(action="close")

        asyncio.run(drive())
        time.sleep(0.2)
        events = self._events()
        buttons = [e for e in events if e[0] == "button"]
        self.assertTrue(buttons, events)
        self.assertLessEqual(abs(buttons[-1][2] - 200), 1)
        self.assertLessEqual(abs(buttons[-1][3] - 150), 1)
        self.assertIn("o", [e[1] for e in events if e[0] == "key"])
