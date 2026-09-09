# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Open an https URL in the user's real system browser.

The desktop shell is a WebView: ``window.open`` from the WebUI often does
nothing there. The gateway (host process) must open the OS browser instead.

Windows and macOS keep their native launchers (``os.startfile`` / ``open``).
Linux (AppImage, deb, rpm) must call the host ``xdg-open`` itself:
``webbrowser.open`` often returns True without spawning anything, and a
frozen sidecar's ``LD_LIBRARY_PATH`` makes the host opener fail for Brave
and other non-default browsers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

from loguru import logger

from navin.utils.proc import no_window_kwargs

# Loader / interpreter overrides a frozen Linux sidecar inherits. Host
# xdg-open must not see them (AppImage squashfs, deb/rpm/pacman PyInstaller).
_LINUX_BUNDLE_ENV = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "QT_PLUGIN_PATH",
    "GTK_PATH",
    "GI_TYPELIB_PATH",
)


def open_external_url(url: str) -> bool:
    """Open ``url`` in the default browser. True when a launcher was started."""
    target = (url or "").strip()
    if not target.startswith(("https://", "http://")):
        return False

    try:
        from navin.utils import wsl

        if wsl.is_wsl_guest():
            if wsl.open_url_on_host(target):
                return True
    except Exception as exc:
        logger.debug("WSL browser open skipped: {}", exc)

    if sys.platform == "win32":
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except OSError as exc:
            logger.debug("os.startfile failed: {}", exc)

    linux = sys.platform.startswith("linux")
    for argv in _launchers(target):
        if shutil.which(argv[0]) is None and not os.path.isfile(argv[0]):
            continue
        try:
            # Spelled out at the call site rather than merged into one mapping:
            # test_no_console_window reads the spawn sites with ast and only
            # recognizes a literal **no_window_kwargs().
            extra: dict[str, object] = {}
            if sys.platform != "win32":
                extra["start_new_session"] = True
            if linux:
                extra["env"] = _linux_host_env()
            subprocess.Popen(  # noqa: S603
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
                **extra,  # type: ignore[arg-type]
            )
            return True
        except OSError as exc:
            logger.debug("browser launcher {} failed: {}", argv[0], exc)

    try:
        return bool(webbrowser.open(target))
    except Exception as exc:
        logger.warning("could not open browser for {}: {}", target, exc)
        return False


def _linux_from_bundle() -> bool:
    if os.environ.get("APPIMAGE") or os.environ.get("APPDIR"):
        return True
    return bool(getattr(sys, "frozen", False))


def _linux_host_env() -> dict[str, str]:
    """Environment for host xdg-open / gio on Linux packages.

    Source checkouts keep the caller's env. Frozen AppImage / deb / rpm
    drop bundle library paths so the system opener can start Brave or
    whatever the user set as default.
    """
    env = dict(os.environ)
    if not _linux_from_bundle():
        return env
    for key in _LINUX_BUNDLE_ENV:
        original = env.pop(f"{key}_ORIG", None)
        if original:
            env[key] = original
        else:
            env.pop(key, None)
    return env


def _launchers(target: str) -> list[list[str]]:
    if sys.platform == "win32":
        return [["cmd", "/c", "start", "", target]]
    if sys.platform == "darwin":
        return [["open", target]]
    # Absolute host paths first: AppImage PATH can shadow xdg-open.
    return [
        ["/usr/bin/xdg-open", target],
        ["/bin/xdg-open", target],
        ["/usr/bin/gio", "open", target],
        ["xdg-open", target],
        ["gio", "open", target],
    ]
