# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Linux accessibility snapshot through AT-SPI (GNOME / KDE / GTK / Qt apps).

``gi.repository.Atspi`` rarely lives in Navin's own virtualenv, so the walk
also runs as a small script under the system ``python3`` when that one has
the binding. Applications only publish their tree when accessibility is on
(``gsettings set org.gnome.desktop.interface toolkit-accessibility true`` or
``QT_ACCESSIBILITY=1``), which the doctor line points out.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from navin.computer.base import ComputerError, UIElement

_INTERACTIVE_ROLES = {
    "push button",
    "toggle button",
    "check box",
    "radio button",
    "menu item",
    "check menu item",
    "radio menu item",
    "page tab",
    "list item",
    "link",
    "entry",
    "password text",
    "text",
    "combo box",
    "spin button",
    "slider",
    "tree item",
    "table cell",
    "document web",
    "document frame",
}

_SCRIPT = r"""
import json, sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
title = sys.argv[1] if len(sys.argv) > 1 else ""
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 300
interactive = set(json.loads(sys.argv[3])) if len(sys.argv) > 3 else set()
out = []
def rect(node):
    try:
        e = node.get_extents(Atspi.CoordType.SCREEN)
        return int(e.x), int(e.y), int(e.width), int(e.height)
    except Exception:
        return None
def walk(node, depth):
    if len(out) >= limit or depth > 40:
        return
    try:
        n = node.get_child_count()
    except Exception:
        return
    for i in range(min(n, 400)):
        try:
            child = node.get_child_at_index(i)
        except Exception:
            continue
        if child is None:
            continue
        try:
            states = child.get_state_set()
            if not states.contains(Atspi.StateType.SHOWING):
                continue
            role = child.get_role_name() or ""
            name = child.get_name() or ""
            value = ""
            try:
                t = child.get_text_iface()
                if t is not None and role in ("entry", "text", "password text"):
                    value = (t.get_text(0, min(80, t.get_character_count())) or "") if role != "password text" else ""
            except Exception:
                pass
            r = rect(child)
            if r and r[2] > 0 and r[3] > 0 and (name or value or role in interactive):
                out.append({
                    "role": role, "name": name[:120], "value": value[:80],
                    "x": r[0], "y": r[1], "w": r[2], "h": r[3],
                    "enabled": states.contains(Atspi.StateType.ENABLED),
                    "focused": states.contains(Atspi.StateType.FOCUSED),
                })
                if len(out) >= limit:
                    return
        except Exception:
            pass
        walk(child, depth + 1)
desktop = Atspi.get_desktop(0)
targets = []
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if app is None:
        continue
    for j in range(app.get_child_count()):
        win = app.get_child_at_index(j)
        if win is None:
            continue
        try:
            wname = win.get_name() or ""
            states = win.get_state_set()
            active = states.contains(Atspi.StateType.ACTIVE)
        except Exception:
            continue
        if title:
            if title.lower() in wname.lower():
                targets.append(win)
        elif active:
            targets.append(win)
for win in targets:
    walk(win, 0)
print(json.dumps(out))
"""


_WINDOWS_SCRIPT = r"""
import json, sys
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi
focus = sys.argv[1] if len(sys.argv) > 1 else ""
out = []
desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if app is None:
        continue
    try:
        app_name = app.get_name() or ""
    except Exception:
        app_name = ""
    for j in range(app.get_child_count()):
        win = app.get_child_at_index(j)
        if win is None:
            continue
        try:
            role = win.get_role_name() or ""
            if role not in ("frame", "window", "dialog", "file chooser", "alert"):
                continue
            states = win.get_state_set()
            if not states.contains(Atspi.StateType.SHOWING) and not states.contains(Atspi.StateType.ICONIFIED):
                continue
            e = win.get_extents(Atspi.CoordType.SCREEN)
            wid = "%d:%d" % (i, j)
            title = win.get_name() or ""
            if focus and focus == wid:
                try:
                    comp = win.get_component_iface()
                    if comp is not None:
                        comp.grab_focus()
                except Exception:
                    pass
            out.append({
                "id": wid, "title": title[:200], "app": app_name[:80],
                "x": int(e.x), "y": int(e.y), "w": int(e.width), "h": int(e.height),
                "active": states.contains(Atspi.StateType.ACTIVE),
                "minimized": states.contains(Atspi.StateType.ICONIFIED),
            })
        except Exception:
            continue
print(json.dumps(out))
"""


def _gi_here() -> bool:
    try:
        import gi  # type: ignore[import-not-found]  # noqa: F401

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _system_python_with_gi() -> str | None:
    for candidate in ("/usr/bin/python3", shutil.which("python3")):
        if not candidate or candidate == sys.executable:
            continue
        try:
            out = subprocess.run(
                [
                    candidate,
                    "-c",
                    "import gi; gi.require_version('Atspi','2.0'); from gi.repository import Atspi",
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


def atspi_available() -> tuple[bool, str]:
    if _gi_here():
        return True, "AT-SPI via gi (this interpreter)"
    python = _system_python_with_gi()
    if python:
        return True, f"AT-SPI via {python}"
    return False, "python3-gi with Atspi 2.0 is not installed"


def atspi_windows(*, focus_id: str | None = None, timeout: float = 20.0) -> list[dict[str, Any]]:
    """Top-level frames known to the accessibility bus (works under Wayland).

    ``focus_id`` asks the toolkit to raise that frame (``Component.grab_focus``),
    which GTK and Qt honour when the compositor lets them.
    """
    python = sys.executable if _gi_here() else _system_python_with_gi()
    if not python:
        raise ComputerError("listing windows needs AT-SPI: apt install python3-gi gir1.2-atspi-2.0")
    try:
        out = subprocess.run(
            [python, "-c", _WINDOWS_SCRIPT, focus_id or ""],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComputerError(f"AT-SPI window list failed: {exc}") from exc
    if out.returncode != 0:
        raise ComputerError(f"AT-SPI window list failed: {(out.stderr or '').strip()[-300:]}")
    try:
        data: Any = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ComputerError("AT-SPI window list returned invalid JSON") from exc
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def atspi_snapshot(
    *, window_title: str | None, limit: int = 300, timeout: float = 30.0
) -> list[UIElement]:
    python = sys.executable if _gi_here() else _system_python_with_gi()
    if not python:
        raise ComputerError(
            "accessibility snapshot needs AT-SPI: apt install python3-gi gir1.2-atspi-2.0, "
            "then enable toolkit accessibility (gsettings set org.gnome.desktop.interface "
            "toolkit-accessibility true, or QT_ACCESSIBILITY=1)."
        )
    try:
        out = subprocess.run(
            [
                python,
                "-c",
                _SCRIPT,
                window_title or "",
                str(int(limit)),
                json.dumps(sorted(_INTERACTIVE_ROLES)),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComputerError(f"AT-SPI snapshot failed: {exc}") from exc
    if out.returncode != 0:
        raise ComputerError(f"AT-SPI snapshot failed: {(out.stderr or '').strip()[-300:]}")
    try:
        data: Any = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ComputerError("AT-SPI snapshot returned invalid JSON") from exc
    elements: list[UIElement] = []
    for i, row in enumerate(data if isinstance(data, list) else []):
        if not isinstance(row, dict):
            continue
        elements.append(
            UIElement(
                ref=i,
                role=str(row.get("role") or ""),
                name=str(row.get("name") or ""),
                left=int(row.get("x") or 0),
                top=int(row.get("y") or 0),
                width=int(row.get("w") or 0),
                height=int(row.get("h") or 0),
                value=str(row.get("value") or ""),
                enabled=bool(row.get("enabled", True)),
                focused=bool(row.get("focused", False)),
            )
        )
    return elements
