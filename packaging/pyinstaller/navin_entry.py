"""PyInstaller entry point for the standalone navin binary."""

import json
import multiprocessing
import socket
import sys
import urllib.request
from pathlib import Path


def _is_windows_desktop() -> bool:
    """True when the Windows binary was started as an application, not a CLI.

    The one executable serves both uses, exactly like the macOS bundle: the
    name alone must not decide, or ``navin --version`` in PowerShell or WSL
    has its stdout silently redirected to the desktop log and prints nothing.
    An application launch (double click, or the Tauri shell) passes no
    arguments and attaches no terminal; a CLI call has one or the other.
    """
    if sys.platform != "win32" or Path(sys.executable).stem.lower() != "navin":
        return False
    if len(sys.argv) > 1:
        return False
    try:
        return sys.stdout is None or not sys.stdout.isatty()
    except ValueError:  # already-closed stream
        return True


def _is_linux_desktop() -> bool:
    """True when the desktop entry started this process.

    Not guessed from the absence of a terminal: a systemd user service has no
    terminal either, and its output belongs in the journal rather than in a log
    file nobody asked for. The ``.desktop`` files set this variable, so only a
    launch from an application menu or an icon takes the windowed path.
    """
    import os

    return sys.platform.startswith("linux") and os.environ.get("NAVIN_DESKTOP_LAUNCH") == "1"


def _is_macos_app_bundle() -> bool:
    """True when this process is the executable inside Navin.app."""
    return sys.platform == "darwin" and ".app/Contents/MacOS/" in sys.executable


def _strip_launch_services_args() -> None:
    """Drop the process serial number Finder can append to a bundle launch.

    ``-psn_0_123456`` is not a navin option: left in place it turns a double
    click on Navin.app into a CLI usage error instead of starting the WebUI.
    """
    if sys.platform != "darwin":
        return
    sys.argv[1:] = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]


def _is_macos_finder_launch() -> bool:
    """True when Navin.app was opened from Finder, the Dock or Spotlight.

    The bundle ships a single executable for both uses. Opened from Finder it is
    a windowed application with nowhere to print, while
    ``Navin.app/Contents/MacOS/Navin --version`` in a terminal has to keep
    behaving like the CLI. Finder passes no arguments and attaches no terminal,
    which tells the two apart without guessing.
    """
    if not _is_macos_app_bundle() or len(sys.argv) > 1:
        return False
    try:
        return sys.stdout is None or not sys.stdout.isatty()
    except ValueError:  # already-closed stream
        return True


def _configure_desktop_logging() -> Path | None:
    """Give the windowed executable durable stdout/stderr instead of ``None``.

    Finder and the Dock give an application no terminal, so every diagnostic a
    failed start would print goes nowhere unless it is written to a file.
    """
    if not _DESKTOP_LAUNCH:
        return None
    log_path = Path.home() / ".navin" / "logs" / "desktop-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    return log_path


_strip_launch_services_args()
# Decided once, before anything else can change the arguments: the desktop path
# below appends its own, which would make the same question answer differently.
_DESKTOP_LAUNCH = _is_windows_desktop() or _is_macos_finder_launch() or _is_linux_desktop()
_DESKTOP_LOG = _configure_desktop_logging()

from navin.cli.commands import run  # noqa: E402


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _health_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _desktop_config() -> tuple[int, int, str | None]:
    """Read persisted desktop ports and bootstrap secret, if available."""
    path = Path.home() / ".navin" / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return 8766, 18791, None

    websocket = (data.get("channels") or {}).get("websocket") or {}
    gateway = data.get("gateway") or {}
    webui_port = int(websocket.get("port") or 8766)
    gateway_port = int(gateway.get("port") or 18791)
    raw_secret = websocket.get("tokenIssueSecret") or websocket.get("token_issue_secret")
    try:
        from navin.config.secrets import unlock_stored_secret

        secret = unlock_stored_secret(raw_secret, path)
    except Exception:
        secret = "" if str(raw_secret or "").startswith("enc:v1:") else raw_secret

    # Migrate source/development defaults to dedicated desktop ports. This lets
    # an installed Navin coexist with a source checkout or WSL gateway.
    if webui_port == 8765:
        webui_port = 8766
    if gateway_port == 18790:
        gateway_port = 18791
    return webui_port, gateway_port, str(secret) if secret else None


def _prepare_desktop_launch() -> bool:
    """Open an existing desktop instance or configure free dedicated ports.

    Returns ``True`` when an existing instance was opened and no new gateway
    should be started.
    """
    webui_port, gateway_port, secret = _desktop_config()
    if _health_ready(webui_port):
        from navin.cli.commands import _open_webui_browser

        url = f"http://127.0.0.1:{webui_port}/"
        if secret:
            url += f"#/?bootstrapSecret={secret}"
        _open_webui_browser(url, wait=False)
        return True

    if not _port_is_free(webui_port) or not _port_is_free(gateway_port):
        _report_desktop_failure(
            "Navin is already running, or port 8766 / 18791 is in use. "
            "Close the other copy, then open the app again."
        )
        return True

    sys.argv.extend(
        [
            "webui",
            "--yes",
            "--port",
            str(webui_port),
            "--gateway-port",
            str(gateway_port),
        ]
    )
    return False


def _report_desktop_failure(message: str) -> None:
    """Show a failed start to someone who launched Navin by double-clicking it.

    A windowed launch has no terminal, so without a dialog the application would
    simply never appear and leave nothing on screen to act on.
    """
    if not _DESKTOP_LAUNCH:
        return
    if _DESKTOP_LOG:
        message += f"\n\nStartup log:\n{_DESKTOP_LOG}"
    if _is_windows_desktop():
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "Navin", 0x10)
        return
    import subprocess

    if _is_linux_desktop():
        # Whichever of these the desktop happens to have. A machine with none of
        # them still has the log file named in the message, and a failed dialog
        # must not replace the failure it was reporting.
        for argv in (
            ["zenity", "--error", "--title=Navin", f"--text={message}"],
            ["kdialog", "--title", "Navin", "--error", message],
            ["notify-send", "--urgency=critical", "Navin", message],
            ["xmessage", "-center", message],
        ):
            try:
                if subprocess.run(argv, check=False).returncode == 0:
                    return
            except (OSError, ValueError):
                continue
        return

    subprocess.run(
        [
            "osascript",
            "-e",
            f"display dialog {json.dumps(message)} with title \"Navin\" with icon stop",
        ],
        check=False,
    )


if __name__ == "__main__":
    # Required for frozen executables that spawn worker processes.
    multiprocessing.freeze_support()
    # Double-clicked with no arguments → behave like the launcher: start the
    # WebUI (which prepares the WebSocket channel, starts the gateway, and
    # opens the browser). With arguments, behave like the normal navin CLI.
    if len(sys.argv) == 1:
        if _prepare_desktop_launch():
            raise SystemExit(0)
    try:
        run()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            _report_desktop_failure("Navin could not start.")
        raise
    except Exception:
        if _DESKTOP_LAUNCH:
            import traceback

            traceback.print_exc()
        _report_desktop_failure("Navin encountered an unexpected startup error.")
        raise
