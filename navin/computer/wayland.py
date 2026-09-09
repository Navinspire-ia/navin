"""Wayland backend: the desktop portal first, compositor tools as fallbacks.

Wayland has no global "post an event" or "read the framebuffer" call; the
compositor decides. What is available, in order of preference:

* **xdg-desktop-portal** RemoteDesktop + ScreenCast (GNOME, KDE, wlroots with
  ``xdg-desktop-portal-wlr``): precise absolute pointer, keysyms, and one
  PipeWire stream per monitor that ``gst-launch-1.0 pipewiresrc`` turns into a
  PNG. One consent dialog, then silent thanks to the restore token.
* **Compositor CLIs**: ``grim`` / ``spectacle`` for pixels, ``swaymsg`` /
  ``hyprctl`` for windows, ``ydotool`` (uinput daemon) or ``wtype`` for input
  when the portal is unavailable. ``ydotool`` moves are relative under pointer
  acceleration, so they are last resort.
* **AT-SPI** for the accessibility snapshot and, on GNOME, the window list.

Screen units are screenshot pixels; logical (scaled) coordinates from the
portal and the compositors are converted with the stream's scale.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

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

# X keysyms (the portal and wtype speak xkb keysyms).
_KEYSYMS: dict[str, int] = {
    "enter": 0xFF0D,
    "escape": 0xFF1B,
    "tab": 0xFF09,
    "space": 0x20,
    "backspace": 0xFF08,
    "delete": 0xFFFF,
    "insert": 0xFF63,
    "home": 0xFF50,
    "end": 0xFF57,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "up": 0xFF52,
    "down": 0xFF54,
    "left": 0xFF51,
    "right": 0xFF53,
    "capslock": 0xFFE5,
    "numlock": 0xFF7F,
    "scrolllock": 0xFF14,
    "printscreen": 0xFF61,
    "pause": 0xFF13,
    "contextmenu": 0xFF67,
    "volumeup": 0x1008FF13,
    "volumedown": 0x1008FF11,
    "volumemute": 0x1008FF12,
    "playpause": 0x1008FF14,
    "ctrl": 0xFFE3,
    "shift": 0xFFE1,
    "alt": 0xFFE9,
    "meta": 0xFFEB,
}
for _n in range(1, 25):
    _KEYSYMS[f"f{_n}"] = 0xFFBE + _n - 1

# Linux evdev codes for ydotool.
_BTN_CODES: dict[str, int] = {"left": 0x110, "right": 0x111, "middle": 0x112}
_YDOTOOL_BUTTONS: dict[str, int] = {"left": 0x00, "right": 0x01, "middle": 0x02}
_LINUX_KEYCODES: dict[str, int] = {
    "escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10,
    "0": 11, "-": 12, "=": 13, "backspace": 14, "tab": 15, "q": 16, "w": 17, "e": 18,
    "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25, "[": 26, "]": 27,
    "enter": 28, "ctrl": 29, "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35,
    "j": 36, "k": 37, "l": 38, ";": 39, "'": 40, "`": 41, "shift": 42, "\\": 43,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50, ",": 51, ".": 52,
    "/": 53, "alt": 56, "space": 57, "capslock": 58, "f1": 59, "f2": 60, "f3": 61,
    "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68, "numlock": 69,
    "scrolllock": 70, "f11": 87, "f12": 88, "home": 102, "up": 103, "pageup": 104,
    "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109, "insert": 110,
    "delete": 111, "volumemute": 113, "volumedown": 114, "volumeup": 115, "pause": 119,
    "meta": 125, "contextmenu": 127, "printscreen": 99, "playpause": 164,
}  # fmt: skip


def _keysym_for_char(ch: str) -> int:
    code = ord(ch)
    if ch == "\n":
        return 0xFF0D
    if ch == "\t":
        return 0xFF09
    if 0x20 <= code <= 0xFF:
        return code
    return 0x01000000 | code


def _png_size(png: bytes) -> tuple[int, int]:
    import struct

    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ComputerError("screenshot is not a PNG")
    w, h = struct.unpack(">II", png[16:24])
    return int(w), int(h)


class WaylandBackend(ComputerBackend):
    name = "wayland"
    label = "Wayland desktop"

    def __init__(self) -> None:
        self._portal: Any = None
        self._portal_shot: Any = None  # helper without RemoteDesktop, screenshots only
        self._portal_error = ""
        self._scale = 1.0
        self._cursor: tuple[int, int] = (0, 0)
        self._held: set[str] = set()
        self._screen_cache: ScreenInfo | None = None
        self._shot_driver = ""
        self._input_driver = ""
        self._gst = shutil.which("gst-launch-1.0")
        self._grim = shutil.which("grim")
        self._spectacle = shutil.which("spectacle")
        self._ydotool = shutil.which("ydotool")
        self._wtype = shutil.which("wtype")
        self._swaymsg = shutil.which("swaymsg") if os.environ.get("SWAYSOCK") else None
        self._hyprctl = (
            shutil.which("hyprctl") if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") else None
        )

    # --------------------------------------------------------------- portal

    def _portal_session(self) -> Any:
        """The live portal session, or None with ``_portal_error`` set."""
        if self._portal is not None and self._portal.alive:
            return self._portal
        if self._portal_error:
            return None
        try:
            from navin.computer.wayland_portal import PortalSession, portal_python
            from navin.config.paths import get_runtime_subdir
        except Exception as exc:  # noqa: BLE001
            self._portal_error = str(exc)
            return None
        if not portal_python():
            self._portal_error = "PyGObject (python3-gi) is missing, portal unavailable"
            return None
        session = PortalSession(get_runtime_subdir("computer") / "wayland-restore-token")
        try:
            session.start()
        except ComputerError as exc:
            self._portal_error = str(exc)
            logger.debug("Wayland portal unavailable: {}", exc)
            if session.alive:
                # Keep the helper for the Screenshot portal only.
                self._portal_shot = session
            return None
        self._portal = session
        self._screen_cache = None
        return session

    def _stream_for(self, x: int, y: int) -> Any:
        session = self._portal_session()
        if session is None:
            return None
        lx, ly = x / self._scale, y / self._scale
        for stream in session.streams:
            if (
                stream.left <= lx < stream.left + stream.width
                and stream.top <= ly < stream.top + stream.height
            ):
                return stream
        return session.streams[0]

    # ---------------------------------------------------------------- screen

    def _logical_layout(self) -> list[tuple[int, int, int, int, str]]:
        """(left, top, width, height, name) per output in logical pixels."""
        session = self._portal_session()
        if session is not None and session.streams:
            return [(s.left, s.top, s.width, s.height, f"stream {s.node}") for s in session.streams]
        if self._swaymsg:
            try:
                data = json.loads(self._run([self._swaymsg, "-t", "get_outputs"]))
                outs = [
                    (
                        o["rect"]["x"],
                        o["rect"]["y"],
                        o["rect"]["width"],
                        o["rect"]["height"],
                        o.get("name", ""),
                    )
                    for o in data
                    if o.get("active")
                ]
                if outs:
                    return outs
            except Exception as exc:  # noqa: BLE001
                logger.debug("swaymsg outputs failed: {}", exc)
        if self._hyprctl:
            try:
                data = json.loads(self._run([self._hyprctl, "monitors", "-j"]))
                outs = []
                for m in data:
                    scale = float(m.get("scale") or 1.0)
                    outs.append(
                        (
                            int(m["x"]),
                            int(m["y"]),
                            int(round(m["width"] / scale)),
                            int(round(m["height"] / scale)),
                            m.get("name", ""),
                        )
                    )
                if outs:
                    return outs
            except Exception as exc:  # noqa: BLE001
                logger.debug("hyprctl monitors failed: {}", exc)
        return []

    def screen(self) -> ScreenInfo:
        if self._screen_cache is not None:
            return self._screen_cache
        layout = self._logical_layout()
        if not layout:
            # Last resort: the screenshot defines the screen, scale 1.
            shot = self.screenshot()
            self._scale = 1.0
            info = ScreenInfo(width=shot.width, height=shot.height)
            self._screen_cache = info
            return info
        s = self._scale
        left = min(o[0] for o in layout)
        top = min(o[1] for o in layout)
        right = max(o[0] + o[2] for o in layout)
        bottom = max(o[1] + o[3] for o in layout)
        displays = tuple(
            DisplayInfo(
                index=i,
                left=int(round(o[0] * s)),
                top=int(round(o[1] * s)),
                width=int(round(o[2] * s)),
                height=int(round(o[3] * s)),
                primary=i == 0,
                name=o[4],
                scale=s,
            )
            for i, o in enumerate(layout)
        )
        info = ScreenInfo(
            width=int(round((right - left) * s)),
            height=int(round((bottom - top) * s)),
            left=int(round(left * s)),
            top=int(round(top * s)),
            displays=displays,
        )
        self._screen_cache = info
        return info

    # ------------------------------------------------------------ screenshot

    @staticmethod
    def _run(cmd: list[str], *, timeout: float = 20.0, binary: bool = False) -> Any:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()[-300:]
            raise ComputerError(f"{Path(cmd[0]).name} failed: {err or proc.returncode}")
        return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")

    def _grab_pipewire(self, node: int) -> bytes:
        if not self._gst:
            raise ComputerError("gst-launch-1.0 is not installed")
        fd, path = tempfile.mkstemp(prefix="navin-shot-", suffix=".png")
        os.close(fd)
        try:
            self._run(
                [
                    self._gst,
                    "-q",
                    "pipewiresrc",
                    f"path={node}",
                    "num-buffers=1",
                    "!",
                    "videoconvert",
                    "!",
                    "video/x-raw,format=RGB",
                    "!",
                    "pngenc",
                    "!",
                    "filesink",
                    f"location={path}",
                ],
                timeout=25,
            )
            return Path(path).read_bytes()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _grab_portal(self) -> Screenshot | None:
        session = self._portal_session()
        if session is None or not session.streams:
            return None
        frames: list[tuple[Any, bytes, int, int]] = []
        for stream in session.streams:
            try:
                png = self._grab_pipewire(stream.node)
            except ComputerError as exc:
                logger.debug("pipewire grab failed for node {}: {}", stream.node, exc)
                return None
            w, h = _png_size(png)
            frames.append((stream, png, w, h))
        first = frames[0]
        scale = first[2] / float(first[0].width or first[2] or 1)
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self._screen_cache = None
        self._shot_driver = "portal/pipewire"
        if len(frames) == 1:
            return Screenshot(
                png=first[1],
                width=first[2],
                height=first[3],
                left=int(round(first[0].left * scale)),
                top=int(round(first[0].top * scale)),
            )
        try:
            from PIL import Image
        except ImportError as exc:
            raise ComputerError("several monitors need Pillow to composite the screenshot") from exc
        info = self.screen()
        canvas = Image.new("RGB", (info.width, info.height), (0, 0, 0))
        for stream, png, _, _ in frames:
            with Image.open(io.BytesIO(png)) as im:
                canvas.paste(
                    im.convert("RGB"),
                    (
                        int(round(stream.left * scale)) - info.left,
                        int(round(stream.top * scale)) - info.top,
                    ),
                )
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return Screenshot(
            png=buf.getvalue(), width=info.width, height=info.height, left=info.left, top=info.top
        )

    def _grab_cli(self) -> Screenshot | None:
        candidates: list[tuple[str, list[str], bool]] = []
        if self._grim:
            candidates.append(("grim", [self._grim, "-t", "png", "-"], True))
        if self._spectacle:
            candidates.append(
                ("spectacle", [self._spectacle, "-b", "-n", "-f", "-o", "{path}"], False)
            )
        gnome = shutil.which("gnome-screenshot")
        if gnome:
            candidates.append(("gnome-screenshot", [gnome, "-f", "{path}"], False))
        for name, cmd, stdout in candidates:
            try:
                if stdout:
                    png = self._run(cmd, binary=True)
                else:
                    fd, path = tempfile.mkstemp(prefix="navin-shot-", suffix=".png")
                    os.close(fd)
                    try:
                        self._run([c.replace("{path}", path) for c in cmd])
                        png = Path(path).read_bytes()
                    finally:
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                w, h = _png_size(png)
            except Exception as exc:  # noqa: BLE001
                logger.debug("{} screenshot failed: {}", name, exc)
                continue
            self._shot_driver = name
            layout = self._logical_layout()
            if layout:
                logical_w = max(o[0] + o[2] for o in layout) - min(o[0] for o in layout)
                scale = w / float(logical_w or w)
                if abs(scale - self._scale) > 0.01:
                    self._scale = scale
                    self._screen_cache = None
            return Screenshot(png=png, width=w, height=h)
        return None

    def _grab_portal_screenshot(self) -> Screenshot | None:
        session = self._portal_session() or self._portal_shot
        if session is None or not session.alive:
            return None
        try:
            uri = session.screenshot_uri()
        except ComputerError as exc:
            logger.debug("portal Screenshot failed: {}", exc)
            return None
        if not uri:
            return None
        path = Path(unquote(urlparse(uri).path))
        try:
            png = path.read_bytes()
        except OSError as exc:
            raise ComputerError(f"portal screenshot file unreadable: {exc}") from exc
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        w, h = _png_size(png)
        self._shot_driver = "portal/screenshot"
        return Screenshot(png=png, width=w, height=h)

    def screenshot(self) -> Screenshot:
        for grab in (self._grab_portal, self._grab_cli, self._grab_portal_screenshot):
            shot = grab()
            if shot is not None:
                return shot
        raise NotSupportedError(
            "no way to capture the Wayland screen: install xdg-desktop-portal for your desktop plus "
            "python3-gi and gstreamer1.0-pipewire, or grim (wlroots) / spectacle (KDE)."
            + (f" Portal: {self._portal_error}" if self._portal_error else "")
        )

    # ----------------------------------------------------------------- input

    def _ydotool_ok(self) -> bool:
        if not self._ydotool:
            return False
        socket = os.environ.get("YDOTOOL_SOCKET") or f"/run/user/{os.getuid()}/.ydotool_socket"
        return os.path.exists(socket) or os.path.exists("/tmp/.ydotool_socket")  # noqa: S108

    def cursor_position(self) -> tuple[int, int]:
        # Wayland hides the pointer from clients; the last position we set is
        # the best answer (so user-takeover detection is blind here).
        return self._cursor

    def move(self, x: int, y: int) -> None:
        x, y = self.screen().clamp(x, y)
        stream = self._stream_for(x, y)
        if stream is not None:
            self._portal.move(
                stream.node, x / self._scale - stream.left, y / self._scale - stream.top
            )
            self._input_driver = "portal"
        elif self._ydotool_ok():
            self._run(
                [
                    self._ydotool,
                    "mousemove",
                    "--absolute",
                    "-x",
                    str(int(x / self._scale)),
                    "-y",
                    str(int(y / self._scale)),
                ],
                timeout=5,
            )
            self._input_driver = "ydotool"
        else:
            raise self._no_input()
        self._cursor = (x, y)

    def _no_input(self) -> ComputerError:
        return NotSupportedError(
            "no way to move the pointer on this Wayland session: allow the portal 'Remote control' "
            "dialog (needs python3-gi), or run ydotoold (uinput)."
            + (f" Portal: {self._portal_error}" if self._portal_error else "")
        )

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        if button not in _BTN_CODES:
            raise ComputerError(f"unknown mouse button {button!r}")
        if (x, y) != self._cursor:
            self.move(x, y)
        if self._portal is not None and self._portal.alive:
            self._portal.button(_BTN_CODES[button], down)
        elif self._ydotool_ok():
            code = _YDOTOOL_BUTTONS[button] | (0x40 if down else 0x80)
            self._run([self._ydotool, "click", f"{code:#04x}"], timeout=5)
        else:
            raise self._no_input()
        if down:
            self._held.add(button)
        else:
            self._held.discard(button)

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self.move(x, y)
        if self._portal is not None and self._portal.alive:
            if dy:
                self._portal.axis(0, int(dy))
            if dx:
                self._portal.axis(1, int(dx))
        elif self._ydotool_ok():
            self._run(
                [self._ydotool, "mousemove", "--wheel", "-x", str(int(dx)), "-y", str(-int(dy))],
                timeout=5,
            )
        else:
            raise self._no_input()

    def _keysym_for(self, key: str) -> int:
        if key in _KEYSYMS:
            return _KEYSYMS[key]
        if len(key) == 1:
            return _keysym_for_char(key)
        raise NotSupportedError(f"key {key!r} has no keysym")

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        delay = max(0.0, delay_ms / 1000.0)
        session = self._portal_session()
        if session is not None:
            for ch in text:
                keysym = _keysym_for_char(ch)
                session.keysym(keysym, True)
                session.keysym(keysym, False)
                if delay:
                    time.sleep(delay)
            self._input_driver = "portal"
            return
        if self._wtype:
            self._run(
                [self._wtype, "-d", str(int(delay_ms)), "--", text], timeout=30 + len(text) * delay
            )
            self._input_driver = "wtype"
            return
        if self._ydotool_ok():
            self._run(
                [self._ydotool, "type", "-d", str(int(delay_ms)), "--", text],
                timeout=30 + len(text) * delay,
            )
            self._input_driver = "ydotool"
            return
        raise NotSupportedError(
            "no way to type on this Wayland session: portal, wtype or ydotool needed"
        )

    def key(self, combo: KeyCombo, *, down: bool | None = None) -> None:
        session = self._portal_session()
        if session is not None:
            mods = [_KEYSYMS[m] for m in combo.modifiers]
            keysym = self._keysym_for(combo.key)
            if down is None or down:
                for m in mods:
                    session.keysym(m, True)
                session.keysym(keysym, True)
            if down is None or not down:
                session.keysym(keysym, False)
                for m in reversed(mods):
                    session.keysym(m, False)
            return
        if self._ydotool_ok():
            codes = [_LINUX_KEYCODES[m] for m in combo.modifiers]
            low = combo.key.lower() if len(combo.key) == 1 else combo.key
            code = _LINUX_KEYCODES.get(low)
            if code is None:
                raise NotSupportedError(f"key {combo.key!r} has no Linux key code for ydotool")
            if (
                len(combo.key) == 1
                and combo.key.isupper()
                and _LINUX_KEYCODES["shift"] not in codes
            ):
                codes.append(_LINUX_KEYCODES["shift"])
            seq: list[str] = []
            if down is None or down:
                seq += [f"{c}:1" for c in codes] + [f"{code}:1"]
            if down is None or not down:
                seq += [f"{code}:0"] + [f"{c}:0" for c in reversed(codes)]
            self._run([self._ydotool, "key", *seq], timeout=5)
            return
        if self._wtype and down is None:
            args: list[str] = []
            for m in combo.modifiers:
                args += ["-M", {"ctrl": "ctrl", "shift": "shift", "alt": "alt", "meta": "logo"}[m]]
            args += [
                "-k",
                combo.key if combo.key not in _KEYSYMS else _WTYPE_NAMES.get(combo.key, combo.key),
            ]
            for m in reversed(combo.modifiers):
                args += ["-m", {"ctrl": "ctrl", "shift": "shift", "alt": "alt", "meta": "logo"}[m]]
            self._run([self._wtype, *args], timeout=5)
            return
        raise NotSupportedError(
            "no way to press keys on this Wayland session: portal, ydotool or wtype needed"
        )

    # --------------------------------------------------------------- windows

    def _px(self, v: float) -> int:
        return int(round(v * self._scale))

    def windows(self) -> list[WindowInfo]:
        if self._swaymsg:
            tree = json.loads(self._run([self._swaymsg, "-t", "get_tree"]))
            out: list[WindowInfo] = []

            def walk(node: dict[str, Any]) -> None:
                if node.get("pid") and node.get("type") in ("con", "floating_con"):
                    rect = node.get("rect") or {}
                    props = node.get("window_properties") or {}
                    out.append(
                        WindowInfo(
                            id=str(node.get("id")),
                            title=str(node.get("name") or ""),
                            left=self._px(rect.get("x", 0)),
                            top=self._px(rect.get("y", 0)),
                            width=self._px(rect.get("width", 0)),
                            height=self._px(rect.get("height", 0)),
                            app=str(node.get("app_id") or props.get("class") or ""),
                            active=bool(node.get("focused")),
                        )
                    )
                for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
                    walk(child)

            walk(tree)
            return out
        if self._hyprctl:
            clients = json.loads(self._run([self._hyprctl, "clients", "-j"]))
            try:
                active = json.loads(self._run([self._hyprctl, "activewindow", "-j"])).get("address")
            except Exception:  # noqa: BLE001
                active = None
            return [
                WindowInfo(
                    id=str(c.get("address")),
                    title=str(c.get("title") or ""),
                    left=self._px(c.get("at", [0, 0])[0]),
                    top=self._px(c.get("at", [0, 0])[1]),
                    width=self._px(c.get("size", [0, 0])[0]),
                    height=self._px(c.get("size", [0, 0])[1]),
                    app=str(c.get("class") or ""),
                    active=c.get("address") == active,
                )
                for c in clients
                if c.get("mapped", True)
            ]
        from navin.computer.atspi import atspi_windows

        return [
            WindowInfo(
                id=str(row.get("id") or ""),
                title=str(row.get("title") or ""),
                left=self._px(float(row.get("x") or 0)),
                top=self._px(float(row.get("y") or 0)),
                width=self._px(float(row.get("w") or 0)),
                height=self._px(float(row.get("h") or 0)),
                app=str(row.get("app") or ""),
                active=bool(row.get("active")),
                minimized=bool(row.get("minimized")),
            )
            for row in atspi_windows()
        ]

    def focus_window(self, window_id: str) -> bool:
        if self._swaymsg:
            self._run([self._swaymsg, f"[con_id={int(window_id)}]", "focus"])
            return True
        if self._hyprctl:
            self._run([self._hyprctl, "dispatch", "focuswindow", f"address:{window_id}"])
            return True
        from navin.computer.atspi import atspi_windows

        rows = atspi_windows(focus_id=window_id)
        return any(str(r.get("id")) == window_id for r in rows)

    # ---------------------------------------------------------- accessibility

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[UIElement]:
        from navin.computer.atspi import atspi_snapshot

        title = None
        if window_id:
            for w in self.windows():
                if w.id == window_id:
                    title = w.title
                    break
        elements = atspi_snapshot(window_title=title, limit=limit)
        if abs(self._scale - 1.0) < 0.01:
            return elements
        return [
            UIElement(
                ref=e.ref,
                role=e.role,
                name=e.name,
                left=self._px(e.left),
                top=self._px(e.top),
                width=self._px(e.width),
                height=self._px(e.height),
                value=e.value,
                enabled=e.enabled,
                focused=e.focused,
            )
            for e in elements
        ]

    # ----------------------------------------------------------------- doctor

    def doctor(self) -> list[Check]:
        checks: list[Check] = []
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or "unknown desktop"
        checks.append(
            Check("session", True, f"{desktop} on {os.environ.get('WAYLAND_DISPLAY', '?')}")
        )
        try:
            from navin.computer.wayland_portal import portal_python

            python = portal_python()
            checks.append(
                Check(
                    "pygobject",
                    bool(python),
                    python or "python3-gi missing",
                    fix="" if python else "apt install python3-gi gir1.2-glib-2.0",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("pygobject", False, str(exc)))
        session = self._portal_session()
        checks.append(
            Check(
                "portal",
                session is not None,
                f"RemoteDesktop v{session.version}, {len(session.streams)} stream(s)"
                if session is not None
                else self._portal_error or "not started",
                fix=""
                if session is not None
                else "install xdg-desktop-portal-{gnome,kde,wlr} and accept the remote control dialog",
            )
        )
        checks.append(
            Check(
                "pipewire",
                bool(self._gst),
                "gst-launch-1.0 present" if self._gst else "gst-launch-1.0 missing",
                fix="" if self._gst else "apt install gstreamer1.0-tools gstreamer1.0-pipewire",
            )
        )
        try:
            shot = self.screenshot()
            checks.append(
                Check("screenshot", True, f"{shot.width}x{shot.height} via {self._shot_driver}")
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screenshot", False, str(exc)))
        try:
            info = self.screen()
            checks.append(
                Check("screen", True, f"{info.width}x{info.height} px, scale {self._scale:g}")
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("screen", False, str(exc)))
        if session is not None:
            checks.append(Check("input", True, "portal RemoteDesktop (absolute pointer + keysyms)"))
        elif self._ydotool_ok():
            checks.append(Check("input", True, "ydotool (relative moves; expect imprecision)"))
        elif self._wtype:
            checks.append(
                Check(
                    "input",
                    False,
                    "wtype only: keyboard without pointer",
                    fix="run ydotoold or allow the portal",
                )
            )
        else:
            checks.append(
                Check("input", False, "no input driver", fix="allow the portal or run ydotoold")
            )
        try:
            wins = self.windows()
            source = "swaymsg" if self._swaymsg else "hyprctl" if self._hyprctl else "AT-SPI"
            checks.append(Check("windows", True, f"{len(wins)} window(s) via {source}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("windows", False, str(exc)))
        try:
            from navin.computer.atspi import atspi_available

            ok, detail = atspi_available()
            checks.append(Check("accessibility", ok, detail))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("accessibility", False, str(exc)))
        checks.append(
            Check(
                "takeover",
                True,
                "pointer position is hidden on Wayland: use the WebUI button to take over",
            )
        )
        return checks

    def close(self) -> None:
        for button in list(self._held):
            try:
                self.button(*self._cursor, button, down=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Wayland release button failed: {}", exc)
        self._held.clear()
        for attr in ("_portal", "_portal_shot"):
            session = getattr(self, attr)
            if session is not None:
                session.close()
                setattr(self, attr, None)


_WTYPE_NAMES: dict[str, str] = {
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
    "printscreen": "Print",
    "contextmenu": "Menu",
}
for _n in range(1, 25):
    _WTYPE_NAMES[f"f{_n}"] = f"F{_n}"
