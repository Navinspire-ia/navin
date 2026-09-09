"""X11 backend: XTest for input, the root window for pixels, EWMH for windows.

``python-xlib`` is the preferred driver (pure Python, so it ships in the
packaged CLI). When it is missing, the ``xdotool`` command covers input and
window management, and screenshots fall back to Pillow's xcb grabber or a
command-line grabber. Every path is picked at first use and reported by
:meth:`X11Backend.doctor`.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import time
from typing import Any

from loguru import logger

from navin.computer.base import (
    Check,
    ComputerBackend,
    ComputerError,
    DisplayInfo,
    NotSupportedError,
    ScreenInfo,
    Screenshot,
    UIElement,
    WindowInfo,
)
from navin.computer.keys import KeyCombo

# canonical key name -> X keysym name
_KEYSYM_NAMES: dict[str, str] = {
    "enter": "Return",
    "escape": "Escape",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "capslock": "Caps_Lock",
    "numlock": "Num_Lock",
    "scrolllock": "Scroll_Lock",
    "printscreen": "Print",
    "pause": "Pause",
    "contextmenu": "Menu",
    "volumeup": "XF86AudioRaiseVolume",
    "volumedown": "XF86AudioLowerVolume",
    "volumemute": "XF86AudioMute",
    "playpause": "XF86AudioPlay",
    "ctrl": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "meta": "Super_L",
}
for _n in range(1, 25):
    _KEYSYM_NAMES[f"f{_n}"] = f"F{_n}"

_MOD_KEYSYMS: dict[str, str] = {
    "ctrl": "Control_L",
    "shift": "Shift_L",
    "alt": "Alt_L",
    "meta": "Super_L",
}

_BUTTONS: dict[str, int] = {"left": 1, "middle": 2, "right": 3}
_LEVEL3_KEYSYM = 0xFE03  # ISO_Level3_Shift (AltGr)
_MODE_SWITCH_KEYSYM = 0xFF7E


def _keysym_for_char(ch: str) -> int:
    code = ord(ch)
    if ch == "\n":
        return 0xFF0D  # Return
    if ch == "\t":
        return 0xFF09  # Tab
    if 0x20 <= code <= 0xFF:
        return code  # Latin-1 keysyms equal the code point
    return 0x01000000 | code  # Unicode keysym


class X11Backend(ComputerBackend):
    name = "x11"
    label = "X11 desktop"

    def __init__(self, display: str = ":0") -> None:
        self._display_name = display
        self._xlib: Any = None  # Xlib display when python-xlib is usable
        self._xlib_error: str = ""
        self._xtest_ok: bool | None = None
        self._xdotool = shutil.which("xdotool")
        self._spare_keycode: int | None = None
        self._spare_original: tuple[int, ...] | None = None
        self._remapped: int | None = None
        self._screen_cache: ScreenInfo | None = None
        self._screen_cache_at = 0.0
        # True once the root window refused get_image (rootless XWayland / WSLg):
        # screenshots are then composited from the mapped top-level windows.
        self._rootless = False

    # ------------------------------------------------------------------ setup

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["DISPLAY"] = self._display_name
        return env

    def _xd(self) -> Any:
        """The python-xlib Display, or None with ``_xlib_error`` explaining why."""
        if self._xlib is not None:
            return self._xlib
        if self._xlib_error:
            return None
        try:
            from Xlib import display as xdisplay  # type: ignore[import-not-found]
        except ImportError:
            self._xlib_error = "python-xlib is not installed (pip install python-xlib)"
            return None
        try:
            self._xlib = xdisplay.Display(self._display_name)
        except Exception as exc:  # noqa: BLE001
            self._xlib_error = f"cannot open display {self._display_name}: {exc}"
            return None
        return self._xlib

    def _xtest(self) -> bool:
        if self._xtest_ok is not None:
            return self._xtest_ok
        d = self._xd()
        if d is None:
            self._xtest_ok = False
            return False
        try:
            from Xlib.ext import xtest  # noqa: F401

            self._xtest_ok = d.query_extension("XTEST") is not None
        except Exception:  # noqa: BLE001
            self._xtest_ok = False
        return self._xtest_ok

    def _need_input(self) -> str:
        """'xlib' or 'xdotool', or raise when neither can send input."""
        if self._xtest():
            return "xlib"
        if self._xdotool:
            return "xdotool"
        raise NotSupportedError(
            "no way to send input on X11: install python-xlib (pip install python-xlib) "
            f"or xdotool. {self._xlib_error}".strip()
        )

    def _xdo(self, *args: str, timeout: float = 10.0) -> str:
        if not self._xdotool:
            raise NotSupportedError("xdotool is not installed")
        try:
            out = subprocess.run(
                [self._xdotool, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComputerError(f"xdotool failed: {exc}") from exc
        if out.returncode != 0:
            raise ComputerError(f"xdotool {' '.join(args[:2])} failed: {out.stderr.strip()[:200]}")
        return out.stdout

    # ----------------------------------------------------------------- screen

    def screen(self) -> ScreenInfo:
        now = time.monotonic()
        if self._screen_cache is not None and now - self._screen_cache_at < 5.0:
            return self._screen_cache
        info = self._screen_uncached()
        self._screen_cache = info
        self._screen_cache_at = now
        return info

    def _screen_uncached(self) -> ScreenInfo:
        d = self._xd()
        if d is not None:
            scr = d.screen()
            width, height = int(scr.width_in_pixels), int(scr.height_in_pixels)
            displays: list[DisplayInfo] = []
            try:
                from Xlib.ext import randr  # noqa: F401

                monitors = scr.root.xrandr_get_monitors().monitors  # RandR 1.5
                for i, m in enumerate(monitors):
                    try:
                        name = d.get_atom_name(m.name)
                    except Exception:  # noqa: BLE001
                        name = f"monitor{i}"
                    displays.append(
                        DisplayInfo(
                            index=i,
                            left=int(m.x),
                            top=int(m.y),
                            width=int(m.width_in_pixels),
                            height=int(m.height_in_pixels),
                            primary=bool(m.primary),
                            name=str(name),
                        )
                    )
            except Exception:  # noqa: BLE001 - RandR is optional
                displays = []
            if not displays:
                displays = [DisplayInfo(0, 0, 0, width, height, primary=True, name="screen")]
            return ScreenInfo(width=width, height=height, displays=tuple(displays))
        if self._xdotool:
            out = self._xdo("getdisplaygeometry").split()
            width, height = int(out[0]), int(out[1])
            return ScreenInfo(
                width=width,
                height=height,
                displays=(DisplayInfo(0, 0, 0, width, height, primary=True, name="screen"),),
            )
        raise NotSupportedError(f"cannot query the X screen: {self._xlib_error or 'no driver'}")

    def screenshot(self) -> Screenshot:
        info = self.screen()
        png = self._grab_pillow() or self._grab_xlib() or self._grab_cli()
        if png is None:
            raise ComputerError(
                "cannot capture the X11 screen: Pillow lacks xcb support and neither "
                "python-xlib, scrot, maim nor ImageMagick 'import' is available."
            )
        return Screenshot(png=png, width=info.width, height=info.height)

    def _grab_pillow(self) -> bytes | None:
        try:
            from PIL import ImageGrab, features

            if not features.check("xcb"):
                return None
            image = ImageGrab.grab(xdisplay=self._display_name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("x11 screenshot: pillow grab failed: {}", exc)
            return None
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    @staticmethod
    def _image_from_raw(raw: Any, width: int, height: int) -> Any:
        from PIL import Image

        depth = int(getattr(raw, "depth", 24))
        if depth in (24, 32):
            return Image.frombytes("RGBX", (width, height), raw.data, "raw", "BGRX").convert("RGB")
        if depth == 16:
            return Image.frombytes("RGB", (width, height), raw.data, "raw", "BGR;16")
        raise ComputerError(f"unsupported X visual depth {depth}")

    def _grab_xlib(self) -> bytes | None:
        d = self._xd()
        if d is None:
            return None
        from Xlib import X

        root = d.screen().root
        geom = root.get_geometry()
        image = None
        try:
            raw = root.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
            image = self._image_from_raw(raw, geom.width, geom.height)
        except Exception as exc:  # noqa: BLE001
            # A rootless X server (WSLg, XWayland) has no drawable root: paint
            # each mapped top-level window onto a canvas in stacking order.
            logger.debug("x11 screenshot: root get_image failed ({}); compositing windows", exc)
            image = self._composite_windows(d, root, geom.width, geom.height)
            self._rootless = image is not None
        if image is None:
            return None
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    def _composite_windows(self, d: Any, root: Any, width: int, height: int) -> Any:
        from PIL import Image
        from Xlib import X

        canvas = Image.new("RGB", (width, height), (24, 24, 24))
        painted = 0
        try:
            children = root.query_tree().children
        except Exception:  # noqa: BLE001
            return None
        for child in children:
            try:
                attrs = child.get_attributes()
                if attrs.map_state != X.IsViewable or attrs.win_class != X.InputOutput:
                    continue
                g = child.get_geometry()
                if g.width <= 1 or g.height <= 1:
                    continue
                coords = root.translate_coords(child, 0, 0)
                raw = child.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
                tile = self._image_from_raw(raw, g.width, g.height)
                canvas.paste(tile, (int(coords.x), int(coords.y)))
                painted += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("x11 screenshot: window skipped: {}", exc)
        return canvas if painted or not children else canvas

    def _grab_cli(self) -> bytes | None:
        candidates: list[list[str]] = []
        if shutil.which("maim"):
            candidates.append(["maim", "/dev/stdout"])
        if shutil.which("scrot"):
            candidates.append(["scrot", "-o", "/dev/stdout"])
        if shutil.which("import"):
            candidates.append(["import", "-window", "root", "png:-"])
        for cmd in candidates:
            try:
                out = subprocess.run(
                    cmd, capture_output=True, timeout=15, env=self._env(), check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if out.returncode == 0 and out.stdout.startswith(b"\x89PNG"):
                return out.stdout
        return None

    def cursor_position(self) -> tuple[int, int]:
        d = self._xd()
        if d is not None:
            p = d.screen().root.query_pointer()
            return int(p.root_x), int(p.root_y)
        out = self._xdo("getmouselocation", "--shell")
        values = dict(line.split("=", 1) for line in out.split() if "=" in line)
        return int(values.get("X", 0)), int(values.get("Y", 0))

    # ---------------------------------------------------------------- pointer

    def move(self, x: int, y: int) -> None:
        driver = self._need_input()
        x, y = self.screen().clamp(x, y)
        if driver == "xlib":
            from Xlib import X
            from Xlib.ext import xtest

            d = self._xd()
            xtest.fake_input(d, X.MotionNotify, x=x, y=y)
            d.sync()
        else:
            self._xdo("mousemove", "--sync", str(x), str(y))

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        code = _BUTTONS.get(button)
        if code is None:
            raise ComputerError(f"unknown mouse button {button!r}")
        driver = self._need_input()
        if driver == "xlib":
            from Xlib import X
            from Xlib.ext import xtest

            d = self._xd()
            xtest.fake_input(d, X.ButtonPress if down else X.ButtonRelease, detail=code)
            d.sync()
        else:
            self._xdo("mousedown" if down else "mouseup", str(code))

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.move(x, y)
        driver = self._need_input()
        events: list[int] = []
        events += [5 if dy > 0 else 4] * min(abs(int(dy)), 50)
        events += [7 if dx > 0 else 6] * min(abs(int(dx)), 50)
        if driver == "xlib":
            from Xlib import X
            from Xlib.ext import xtest

            d = self._xd()
            for code in events:
                xtest.fake_input(d, X.ButtonPress, detail=code)
                xtest.fake_input(d, X.ButtonRelease, detail=code)
                d.sync()
                time.sleep(0.01)
        else:
            for code in events:
                self._xdo("click", str(code))

    # --------------------------------------------------------------- keyboard

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        if not text:
            return
        driver = self._need_input()
        if driver == "xdotool":
            self._xdo("type", "--delay", str(max(1, delay_ms)), "--", text, timeout=120)
            return
        try:
            for ch in text:
                self._tap_keysym(_keysym_for_char(ch))
                if delay_ms:
                    time.sleep(delay_ms / 1000)
        finally:
            if self._remapped is not None:
                self._restore_spare()

    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        driver = self._need_input()
        if driver == "xdotool":
            parts = [_KEYSYM_NAMES.get(m, m) for m in combo.modifiers]
            parts.append(self._xdo_key_name(combo.key))
            name = "+".join(parts)
            if down is None:
                self._xdo("key", "--clearmodifiers", name)
            elif down:
                self._xdo("keydown", name)
            else:
                self._xdo("keyup", name)
            return

        d = self._xd()
        from Xlib import X
        from Xlib.ext import xtest

        mod_codes = [self._keycode_for_keysym_name(_MOD_KEYSYMS[m]) for m in combo.modifiers]
        key_code, level = self._resolve_key(combo.key)
        extra: list[int] = []
        if level & 1 and "shift" not in combo.modifiers:
            extra.append(self._keycode_for_keysym_name("Shift_L"))
        if level & 2:
            extra.append(self._level3_keycode())
        press = [c for c in mod_codes + extra if c]
        if down is None or down:
            for code in press:
                xtest.fake_input(d, X.KeyPress, detail=code)
            xtest.fake_input(d, X.KeyPress, detail=key_code)
            d.sync()
        if down is None:
            time.sleep(0.012)
        if down is None or not down:
            xtest.fake_input(d, X.KeyRelease, detail=key_code)
            for code in reversed(press):
                xtest.fake_input(d, X.KeyRelease, detail=code)
            d.sync()
        if down is None and self._remapped is not None:
            self._restore_spare()

    @staticmethod
    def _xdo_key_name(key: str) -> str:
        if key in _KEYSYM_NAMES:
            return _KEYSYM_NAMES[key]
        if len(key) == 1:
            from Xlib import XK  # type: ignore[import-not-found]

            code = ord(key)
            if 0x20 <= code <= 0xFF:
                name = XK.keysym_to_string(code)
                return name if name else key
            return f"U{code:04X}"
        return key

    # -- keysym / keycode plumbing (python-xlib) --

    def _keycode_for_keysym_name(self, name: str) -> int:
        from Xlib import XK

        keysym = XK.string_to_keysym(name)
        if not keysym:
            raise ComputerError(f"unknown X keysym {name!r}")
        d = self._xd()
        code = d.keysym_to_keycode(keysym)
        if not code:
            raise ComputerError(f"key {name!r} has no keycode on this keyboard map")
        return int(code)

    def _level3_keycode(self) -> int:
        d = self._xd()
        for keysym in (_LEVEL3_KEYSYM, _MODE_SWITCH_KEYSYM):
            code = d.keysym_to_keycode(keysym)
            if code:
                return int(code)
        return 0

    def _resolve_key(self, key: str) -> tuple[int, int]:
        """(keycode, level) where level bit0 = shift, bit1 = AltGr."""
        if key in _KEYSYM_NAMES:
            return self._keycode_for_keysym_name(_KEYSYM_NAMES[key]), 0
        if len(key) != 1:
            raise ComputerError(f"unknown key {key!r}")
        return self._keycode_for_keysym(_keysym_for_char(key))

    def _keycode_for_keysym(self, keysym: int) -> tuple[int, int]:
        d = self._xd()
        code = d.keysym_to_keycode(keysym)
        if code:
            for level in range(4):
                try:
                    if d.keycode_to_keysym(code, level) == keysym:
                        return int(code), level
                except Exception:  # noqa: BLE001
                    break
            return int(code), 0
        return self._remap_spare(keysym), 0

    def _find_spare_keycode(self) -> int:
        if self._spare_keycode is not None:
            return self._spare_keycode
        d = self._xd()
        info = d.display.info
        lo, hi = int(info.min_keycode), int(info.max_keycode)
        mapping = d.get_keyboard_mapping(lo, hi - lo + 1)
        for offset, syms in enumerate(mapping):
            if all(int(s) == 0 for s in syms):
                self._spare_keycode = lo + offset
                return self._spare_keycode
        # Nothing free: borrow the last keycode and restore it afterwards.
        self._spare_keycode = hi
        return hi

    def _remap_spare(self, keysym: int) -> int:
        d = self._xd()
        code = self._find_spare_keycode()
        if self._remapped != keysym:
            if self._remapped is None:
                self._spare_original = tuple(int(sym) for sym in d.get_keyboard_mapping(code, 1)[0])
            d.change_keyboard_mapping(code, [(keysym, keysym, keysym, keysym)])
            d.sync()
            self._remapped = keysym
            time.sleep(0.02)  # the server needs a moment to publish the new map
        return code

    def _restore_spare(self) -> None:
        d = self._xd()
        if self._spare_keycode is None or d is None:
            return
        try:
            d.change_keyboard_mapping(self._spare_keycode, [self._spare_original or (0, 0, 0, 0)])
            d.sync()
        except Exception:  # noqa: BLE001
            pass
        self._remapped = None
        self._spare_keycode = None
        self._spare_original = None

    def _tap_keysym(self, keysym: int) -> None:
        from Xlib import X
        from Xlib.ext import xtest

        d = self._xd()
        code, level = self._keycode_for_keysym(keysym)
        held: list[int] = []
        if level & 1:
            held.append(self._keycode_for_keysym_name("Shift_L"))
        if level & 2:
            l3 = self._level3_keycode()
            if l3:
                held.append(l3)
        for h in held:
            xtest.fake_input(d, X.KeyPress, detail=h)
        xtest.fake_input(d, X.KeyPress, detail=code)
        xtest.fake_input(d, X.KeyRelease, detail=code)
        for h in reversed(held):
            xtest.fake_input(d, X.KeyRelease, detail=h)
        d.sync()

    # ---------------------------------------------------------------- windows

    def windows(self) -> list[WindowInfo]:
        d = self._xd()
        if d is None:
            return self._windows_xdotool()
        from Xlib import X

        root = d.screen().root
        client_list = self._root_property(root, "_NET_CLIENT_LIST")
        active = self._root_property(root, "_NET_ACTIVE_WINDOW")
        active_id = int(active[0]) if active else 0
        ids: list[int]
        if client_list:
            ids = [int(w) for w in client_list]
        else:
            # No _NET_CLIENT_LIST (bare Xvfb, WSLg's XWM): walk the mapped
            # top-level windows; a reparenting WM hides the client one level
            # down inside an unnamed frame.
            ids = []
            try:
                for child in root.query_tree().children:
                    try:
                        attrs = child.get_attributes()
                    except Exception:  # noqa: BLE001
                        continue
                    if attrs.map_state != X.IsViewable:
                        continue
                    client = self._find_client(child, depth=0)
                    ids.append(int((client or child).id))
            except Exception:  # noqa: BLE001
                ids = []
        out: list[WindowInfo] = []
        for wid in ids:
            info = self._window_info(d, wid, active_id)
            if info is not None:
                out.append(info)
        return out

    @staticmethod
    def _find_client(window: Any, *, depth: int) -> Any:
        """The named client window inside a WM frame, or None."""
        try:
            if window.get_wm_name() or window.get_wm_class():
                return window
        except Exception:  # noqa: BLE001
            return None
        if depth >= 3:
            return None
        try:
            children = window.query_tree().children
        except Exception:  # noqa: BLE001
            return None
        for child in children:
            found = X11Backend._find_client(child, depth=depth + 1)
            if found is not None:
                return found
        return None

    def _root_property(self, root: Any, name: str) -> Any:
        d = self._xd()
        try:
            from Xlib import X

            prop = root.get_full_property(d.intern_atom(name), X.AnyPropertyType)
        except Exception:  # noqa: BLE001
            return None
        return prop.value if prop is not None else None

    def _window_info(self, d: Any, wid: int, active_id: int) -> WindowInfo | None:
        from Xlib import X

        try:
            w = d.create_resource_object("window", wid)
            title = ""
            try:
                prop = w.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
                if prop is not None and prop.value:
                    value = prop.value
                    title = (
                        value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
                    )
            except Exception:  # noqa: BLE001
                title = ""
            if not title:
                try:
                    name = w.get_wm_name()
                    title = (
                        name.decode("utf-8", "replace") if isinstance(name, bytes) else (name or "")
                    )
                except Exception:  # noqa: BLE001
                    title = ""
            app = ""
            try:
                cls = w.get_wm_class()
                if cls:
                    app = str(cls[1] or cls[0] or "")
            except Exception:  # noqa: BLE001
                app = ""
            geom = w.get_geometry()
            root = d.screen().root
            try:
                coords = root.translate_coords(w, 0, 0)
                left, top = int(coords.x), int(coords.y)
            except Exception:  # noqa: BLE001
                left, top = int(geom.x), int(geom.y)
            minimized = False
            try:
                state = w.get_full_property(d.intern_atom("_NET_WM_STATE"), X.AnyPropertyType)
                if state is not None and state.value:
                    hidden = d.intern_atom("_NET_WM_STATE_HIDDEN")
                    minimized = hidden in list(state.value)
            except Exception:  # noqa: BLE001
                minimized = False
            if not title and not app:
                return None
            return WindowInfo(
                id=str(wid),
                title=title,
                left=left,
                top=top,
                width=int(geom.width),
                height=int(geom.height),
                app=app,
                active=wid == active_id,
                minimized=minimized,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("x11 window {} skipped: {}", wid, exc)
            return None

    def _windows_xdotool(self) -> list[WindowInfo]:
        if not self._xdotool:
            raise NotSupportedError(f"cannot list windows: {self._xlib_error or 'no driver'}")
        ids = self._xdo("search", "--onlyvisible", "--name", "").split()
        try:
            active = self._xdo("getactivewindow").strip()
        except ComputerError:
            active = ""
        out: list[WindowInfo] = []
        for wid in ids[:200]:
            try:
                title = self._xdo("getwindowname", wid).strip()
                geo = self._xdo("getwindowgeometry", "--shell", wid)
            except ComputerError:
                continue
            values = dict(line.split("=", 1) for line in geo.split() if "=" in line)
            if not title:
                continue
            out.append(
                WindowInfo(
                    id=wid,
                    title=title,
                    left=int(values.get("X", 0)),
                    top=int(values.get("Y", 0)),
                    width=int(values.get("WIDTH", 0)),
                    height=int(values.get("HEIGHT", 0)),
                    active=wid == active,
                )
            )
        return out

    def focus_window(self, window_id: str) -> bool:
        d = self._xd()
        if d is None:
            self._xdo("windowactivate", "--sync", window_id)
            return True
        from Xlib import X, protocol

        try:
            wid = int(window_id, 0)
        except ValueError as exc:
            raise ComputerError(f"window id must be numeric, got {window_id!r}") from exc
        root = d.screen().root
        w = d.create_resource_object("window", wid)
        try:
            ev = protocol.event.ClientMessage(
                window=w,
                client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                data=(32, [2, X.CurrentTime, 0, 0, 0]),
            )
            root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            w.map()
            w.configure(stack_mode=X.Above)
            w.set_input_focus(X.RevertToParent, X.CurrentTime)
            d.sync()
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(f"cannot focus window {window_id}: {exc}") from exc
        return True

    # ---------------------------------------------------------- accessibility

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        from navin.computer.atspi import atspi_snapshot

        return atspi_snapshot(window_title=self._title_for(window_id), limit=limit)

    def _title_for(self, window_id: str | None) -> str | None:
        if not window_id:
            active = self.active_window()
            return active.title if active else None
        for w in self.windows():
            if w.id == window_id:
                return w.title
        return window_id

    # ----------------------------------------------------------------- doctor

    def doctor(self) -> list[Check]:
        checks: list[Check] = []
        d = self._xd()
        if d is not None:
            checks.append(Check("display", True, f"{self._display_name} via python-xlib"))
        elif self._xdotool:
            checks.append(
                Check(
                    "display",
                    True,
                    f"{self._display_name} via xdotool ({self._xlib_error})",
                    fix="pip install python-xlib for the native driver",
                )
            )
        else:
            checks.append(
                Check(
                    "display",
                    False,
                    self._xlib_error or "no driver",
                    fix="pip install python-xlib (or apt install xdotool)",
                )
            )
        try:
            info = self.screen()
            checks.append(
                Check(
                    "screen", True, f"{info.width}x{info.height}, {len(info.displays)} display(s)"
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screen", False, str(exc)))
        try:
            if self._xtest():
                checks.append(Check("input", True, "XTest extension"))
            elif self._xdotool:
                checks.append(Check("input", True, "xdotool"))
            else:
                checks.append(
                    Check("input", False, "no XTest / xdotool", fix="pip install python-xlib")
                )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("input", False, str(exc)))
        try:
            shot = self.screenshot()
            detail = f"{len(shot.png) // 1024} KiB PNG"
            if self._rootless:
                detail += " (rootless X server: composited from top-level windows)"
            checks.append(Check("screenshot", True, detail))
        except Exception as exc:  # noqa: BLE001
            checks.append(
                Check(
                    "screenshot", False, str(exc), fix="apt install scrot (or maim / imagemagick)"
                )
            )
        try:
            wins = self.windows()
            checks.append(Check("windows", True, f"{len(wins)} window(s)"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("windows", False, str(exc)))
        try:
            from navin.computer.atspi import atspi_available

            ok, detail = atspi_available()
            checks.append(
                Check(
                    "accessibility",
                    ok,
                    detail,
                    fix="" if ok else "apt install python3-gi gir1.2-atspi-2.0 and enable AT-SPI",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("accessibility", False, str(exc)))
        return checks

    def close(self) -> None:
        if self._xlib is not None:
            try:
                if self._remapped is not None:
                    self._restore_spare()
                self._xlib.close()
            except Exception:  # noqa: BLE001
                pass
            self._xlib = None
