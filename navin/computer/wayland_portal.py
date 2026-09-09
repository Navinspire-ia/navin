"""xdg-desktop-portal RemoteDesktop + ScreenCast session, kept alive in a helper.

Wayland compositors do not let an arbitrary client inject input or read the
screen; the sanctioned way is the desktop portal: one D-Bus session that, after
the user's consent dialog, exposes absolute pointer motion, buttons, keysyms
and a PipeWire stream per monitor. The session dies with the D-Bus connection
that created it, so it lives in a small helper process (system ``python3`` with
PyGObject, or this interpreter when it has ``gi``) driven over stdin / stdout
JSON lines. A ``restore_token`` (portal v2, GNOME 43+, KDE 5.27+) is saved so
later runs skip the dialog.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.computer.base import ComputerError, PermissionMissingError

_HELPER = r"""
import json, sys, threading, secrets
import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

BUS = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"
SC = "org.freedesktop.portal.ScreenCast"

try:
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
except Exception as exc:
    # Answer the first request with the reason instead of dying silently.
    first = sys.stdin.readline()
    try:
        rid = json.loads(first).get("id")
    except Exception:
        rid = None
    print(json.dumps({"id": rid, "ok": False, "error": "session bus unreachable: %s" % exc}), flush=True)
    sys.exit(0)
sender = conn.get_unique_name()[1:].replace(".", "_")
loop = GLib.MainLoop()
threading.Thread(target=loop.run, daemon=True).start()


def call(iface, method, params, reply_type=None, timeout=120000):
    return conn.call_sync(BUS, PATH, iface, method, params, reply_type,
                          Gio.DBusCallFlags.NONE, timeout, None)


def request(iface, method, args, options):
    token = "navin" + secrets.token_hex(6)
    options = dict(options)
    options["handle_token"] = GLib.Variant("s", token)
    handle = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender, token)
    done = threading.Event()
    result = {}

    def on_response(c, s, p, i, sig, params):
        result["code"] = params[0]
        result["results"] = params[1]
        done.set()

    sub = conn.signal_subscribe(BUS, "org.freedesktop.portal.Request", "Response", handle,
                                None, Gio.DBusSignalFlags.NONE, on_response)
    try:
        call(iface, method, GLib.Variant.new_tuple(*args, GLib.Variant("a{sv}", options)))
        if not done.wait(180):
            raise RuntimeError("portal request timed out (dialog not answered?)")
    finally:
        conn.signal_unsubscribe(sub)
    if result.get("code", 1) != 0:
        raise RuntimeError("portal request %s refused (code %s)" % (method, result.get("code")))
    return result["results"]


def prop(iface, name):
    try:
        v = conn.call_sync(BUS, PATH, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", (iface, name)), None,
                           Gio.DBusCallFlags.NONE, 5000, None)
        return v[0]
    except Exception:
        return None


session = None
streams = []


def start(restore_token, persist):
    global session, streams
    rd_version = prop(RD, "version") or 1
    res = request(RD, "CreateSession", [],
                  {"session_handle_token": GLib.Variant("s", "navin" + secrets.token_hex(4))})
    session = res["session_handle"]
    devices = {"types": GLib.Variant("u", 1 | 2)}
    if rd_version >= 2 and persist:
        devices["persist_mode"] = GLib.Variant("u", 2)
        if restore_token:
            devices["restore_token"] = GLib.Variant("s", restore_token)
    request(RD, "SelectDevices", [GLib.Variant("o", session)], devices)
    request(SC, "SelectSources", [GLib.Variant("o", session)],
            {"types": GLib.Variant("u", 1), "multiple": GLib.Variant("b", True),
             "cursor_mode": GLib.Variant("u", 2)})
    res = request(RD, "Start", [GLib.Variant("o", session), GLib.Variant("s", "")], {})
    streams = []
    for node, props in res.get("streams", []):
        pos = props.get("position", (0, 0))
        size = props.get("size", (0, 0))
        streams.append({"node": int(node), "x": int(pos[0]), "y": int(pos[1]),
                        "w": int(size[0]), "h": int(size[1])})
    return {"streams": streams, "restore_token": res.get("restore_token", ""),
            "version": int(rd_version)}


def notify(method, sig, *values):
    call(RD, method, GLib.Variant("(oa{sv}" + sig + ")", (session, {}, *values)), None, 10000)


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        op = req.get("op")
        if op == "start":
            out = start(req.get("restore_token") or "", bool(req.get("persist", True)))
        elif op == "move":
            notify("NotifyPointerMotionAbsolute", "udd", int(req["node"]), float(req["x"]), float(req["y"]))
            out = {}
        elif op == "button":
            notify("NotifyPointerButton", "iu", int(req["code"]), 1 if req.get("down") else 0)
            out = {}
        elif op == "axis":
            notify("NotifyPointerAxisDiscrete", "ui", int(req.get("axis", 0)), int(req["steps"]))
            out = {}
        elif op == "keysym":
            notify("NotifyKeyboardKeysym", "iu", int(req["keysym"]), 1 if req.get("down") else 0)
            out = {}
        elif op == "screenshot":
            res = request("org.freedesktop.portal.Screenshot", "Screenshot", [GLib.Variant("s", "")],
                          {"interactive": GLib.Variant("b", False)})
            out = {"uri": str(res.get("uri", ""))}
        elif op == "quit":
            break
        else:
            raise RuntimeError("unknown op %r" % op)
        print(json.dumps({"id": req.get("id"), "ok": True, **out}), flush=True)
    except Exception as exc:
        print(json.dumps({"id": req.get("id") if isinstance(req, dict) else None,
                          "ok": False, "error": str(exc)}), flush=True)
if session:
    try:
        conn.call_sync(BUS, session, "org.freedesktop.portal.Session", "Close",
                       None, None, Gio.DBusCallFlags.NONE, 5000, None)
    except Exception:
        pass
"""


@dataclass(frozen=True, slots=True)
class PortalStream:
    node: int
    left: int
    top: int
    width: int
    height: int


def _gi_here() -> bool:
    try:
        import gi  # type: ignore[import-not-found]  # noqa: F401

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _system_python_with_gio() -> str | None:
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if not candidate or candidate == sys.executable:
            continue
        try:
            out = subprocess.run(
                [
                    candidate,
                    "-c",
                    "import gi; gi.require_version('Gio','2.0'); from gi.repository import Gio",
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.returncode == 0:
            return candidate
    return None


def portal_python() -> str | None:
    """Interpreter able to run the helper, or None when PyGObject is missing."""
    if _gi_here():
        return sys.executable
    return _system_python_with_gio()


class PortalSession:
    """Client side of the helper: one RemoteDesktop + ScreenCast session."""

    def __init__(self, token_file: Path | None) -> None:
        self._token_file = token_file
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self.streams: list[PortalStream] = []
        self.version = 1

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _request(self, timeout: float = 30.0, **payload: Any) -> dict[str, Any]:
        if (
            not self.alive
            or self._proc is None
            or self._proc.stdin is None
            or self._proc.stdout is None
        ):
            raise ComputerError("portal helper is not running")
        with self._lock:
            self._next_id += 1
            payload["id"] = self._next_id
            try:
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise ComputerError(f"portal helper went away: {exc}") from exc
            reply = self._read_line(timeout)
        try:
            data: dict[str, Any] = json.loads(reply)
        except json.JSONDecodeError as exc:
            raise ComputerError(f"portal helper spoke garbage: {reply[:200]!r}") from exc
        if not data.get("ok"):
            error = str(data.get("error") or "portal request failed")
            if (
                "No such interface" in error
                or "ServiceUnknown" in error
                or "was not provided" in error
            ):
                raise ComputerError(
                    "no RemoteDesktop portal on this desktop: install xdg-desktop-portal-gnome, "
                    "-kde or -wlr (and restart the session)."
                )
            if "refused" in error or "timed out" in error:
                raise PermissionMissingError(
                    f"{error}. Accept the 'Remote control' / screen share dialog the desktop shows."
                )
            raise ComputerError(error)
        return data

    def _read_line(self, timeout: float) -> str:
        assert self._proc is not None and self._proc.stdout is not None
        result: list[str] = []

        def reader() -> None:
            try:
                result.append(self._proc.stdout.readline())  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                result.append("")

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive() or not result or not result[0]:
            if thread.is_alive():
                self.close()
                raise ComputerError("portal helper did not answer in time")
            stderr = ""
            try:
                if self._proc.stderr is not None:
                    lines = self._proc.stderr.read().strip().splitlines()
                    stderr = lines[-1][-300:] if lines else ""
            except Exception:  # noqa: BLE001
                pass
            self.close()
            raise ComputerError(f"portal helper exited: {stderr or 'no output'}")
        return result[0]

    def start(self) -> list[PortalStream]:
        python = portal_python()
        if not python:
            raise ComputerError(
                "the Wayland portal needs PyGObject: apt install python3-gi (and xdg-desktop-portal "
                "for your desktop: -gnome, -kde or -wlr)."
            )
        self.close()
        self._proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
            [python, "-c", _HELPER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        token = ""
        if self._token_file and self._token_file.exists():
            token = self._token_file.read_text(encoding="utf-8").strip()
        try:
            data = self._request(timeout=200.0, op="start", restore_token=token, persist=True)
        except ComputerError:
            # The helper stays up: the plain Screenshot portal may still work
            # even when RemoteDesktop is missing (WSLg, minimal portals).
            self.streams = []
            raise
        self.version = int(data.get("version") or 1)
        self.streams = [
            PortalStream(
                node=int(s.get("node") or 0),
                left=int(s.get("x") or 0),
                top=int(s.get("y") or 0),
                width=int(s.get("w") or 0),
                height=int(s.get("h") or 0),
            )
            for s in data.get("streams") or []
        ]
        if not self.streams:
            self.close()
            raise ComputerError(
                "the portal session has no screen; pick at least one monitor in the dialog"
            )
        new_token = str(data.get("restore_token") or "")
        if new_token and self._token_file:
            try:
                self._token_file.parent.mkdir(parents=True, exist_ok=True)
                self._token_file.write_text(new_token, encoding="utf-8")
                self._token_file.chmod(0o600)
            except OSError as exc:
                logger.debug("cannot save portal restore token: {}", exc)
        return self.streams

    def move(self, node: int, x: float, y: float) -> None:
        self._request(op="move", node=node, x=x, y=y)

    def button(self, code: int, down: bool) -> None:
        self._request(op="button", code=code, down=down)

    def axis(self, axis: int, steps: int) -> None:
        self._request(op="axis", axis=axis, steps=steps)

    def keysym(self, keysym: int, down: bool) -> None:
        self._request(op="keysym", keysym=keysym, down=down)

    def screenshot_uri(self) -> str:
        """``org.freedesktop.portal.Screenshot`` (may show a consent dialog once)."""
        return str(self._request(timeout=120.0, op="screenshot").get("uri") or "")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is None:
            proc.kill()
        self.streams = []
