# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Open an https URL in the user's real system browser, or a local file with
its default app (an HTML report, a PDF, an image the terminal cannot show).

The desktop shell is a WebView: ``window.open`` from the WebUI often does
nothing there. The gateway (host process) must open the OS browser instead.

Windows and macOS keep their native launchers (``os.startfile`` / ``open``).
Linux (AppImage, deb, rpm) must call the host ``xdg-open`` itself:
``webbrowser.open`` often returns True without spawning anything, and a
frozen sidecar's ``LD_LIBRARY_PATH`` makes the host opener fail for Brave
and other non-default browsers.

Every launcher runs detached with its output discarded, because the CLI is a
full-screen terminal app and an opener's stderr lands over the chat. For the
same reason ``webbrowser`` is never the fallback: on Linux it runs
``www-browser`` on the caller's own terminal and waits for it (under WSL that
is wslview, which printed its "Exec format error" lines over the CLI), or a
console browser that takes the screen over.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

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

    launchers: list[list[str]] | None = None
    try:
        from navin.utils import wsl

        if wsl.is_wsl_guest():
            if wsl.open_url_on_host(target):
                return True
            if not wsl.host_interop_available():
                # The stock Linux openers of a WSL install (xdg-open, the https
                # handler, x-www-browser, BROWSER) all are wslview, which cannot
                # reach Windows either and still reports success. Only a real
                # Linux browser on the WSLg display can show the page.
                browser = _wslg_browser()
                if browser is None:
                    logger.warning("could not open {}: {}", target, wsl.INTEROP_OFF)
                    return False
                launchers = [[browser, target]]
    except Exception as exc:
        logger.debug("WSL browser open skipped: {}", exc)

    if sys.platform == "win32" and _start_with_windows_shell(target):
        return True
    return _start_first(launchers or _launchers(target), target)


def open_local_file(path: str) -> bool:
    """Open a local file with the system's default app. True when a launcher started.

    For what a terminal cannot show: an HTML report opens in the browser, a
    PDF in the PDF reader. The same launchers as :func:`open_external_url`;
    under WSL the file goes to its Windows default app by its Windows path.
    """
    raw = (path or "").strip()
    if not raw:
        return False
    target = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isfile(target):
        return False

    from navin.utils import wsl

    launchers: list[list[str]] | None = None
    if wsl.is_wsl_guest():
        if wsl.open_path_on_host(target):
            return True
        if not wsl.host_interop_available():
            # Same as for a link: the Linux openers of a stock WSL are wslview.
            browser = _wslg_browser()
            if browser is None:
                logger.warning("could not open {}: {}", target, wsl.INTEROP_OFF)
                return False
            launchers = [[browser, target]]

    if sys.platform == "win32":
        return _start_with_windows_shell(target)
    return _start_first(launchers or _launchers(target), target)


def file_unavailable_reason() -> str:
    """Why a local file did not open, in a sentence the user can act on."""
    from navin.utils import wsl

    if wsl.is_wsl_guest() and not wsl.host_interop_available():
        return wsl.INTEROP_OFF
    return "No browser or file viewer could be opened from here."


def _start_with_windows_shell(target: str) -> bool:
    """Use the native Windows ShellExecute entry point for URL or file."""
    import ctypes

    try:
        result = ctypes.windll.shell32.ShellExecuteW(None, "open", target, None, None, 1)
        if result <= 32:
            raise OSError(f"ShellExecuteW failed with code {result} for {target}")
        return True
    except (AttributeError, OSError) as exc:
        logger.debug("Windows shell opener failed: {}", exc)
        return False


def _start_first(launchers: list[list[str]], target: str) -> bool:
    """Start the first launcher that exists, detached and silent."""
    linux = sys.platform.startswith("linux")
    for argv in launchers:
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

    logger.warning("no system launcher could open {}", target)
    return False


def unavailable_reason(url: str) -> str:
    """Why ``url`` did not open, in a sentence the user can act on."""
    if not (url or "").strip().startswith(("https://", "http://")):
        return "Only web links open in the browser."
    from navin.utils import wsl

    if wsl.is_wsl_guest() and not wsl.host_interop_available():
        return wsl.INTEROP_OFF
    return "No browser could be opened from here."


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# Linux browsers a WSLg display can show, best first. x-www-browser closes the
# list: on a stock WSL it is wslview, which the check below refuses.
_LINUX_BROWSERS = (
    "firefox",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
    "x-www-browser",
)


def _wslg_browser() -> str | None:
    """A real Linux browser for the WSLg display, never wslview.

    ``BROWSER`` comes first when it names a plain program; a template with
    ``%s`` or arguments is left out rather than parsed.
    """
    if not _has_display():
        return None
    chosen = [
        name
        for name in os.environ.get("BROWSER", "").split(os.pathsep)
        if name and "%" not in name and " " not in name
    ]
    for name in (*chosen, *_LINUX_BROWSERS):
        path = shutil.which(name)
        if path and os.path.basename(os.path.realpath(path)) != "wslview":
            return path
    return None


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
        # Explorer hands the link to the default browser without a shell parse.
        # ``cmd /c start "" <url>`` let cmd read the ``&`` of a query string as
        # a command separator: ``https://a/?x=1&calc`` ran ``calc``.
        return [["explorer.exe", target]]
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
