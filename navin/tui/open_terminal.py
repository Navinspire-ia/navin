# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Open a chat in a second navin-cli, in a new tab of the terminal in use.

The sidebar's "open in a new terminal tab" (the arrow on a session row, or the
entry of its menu) starts another navin-cli on that chat next to this one. The
terminal is recognised from what it leaves in the environment: a tmux window
when inside tmux, a Windows Terminal tab (also from WSL, back in the same
distribution), a kitty, WezTerm, iTerm2, GNOME Terminal or Konsole tab. Apple
Terminal, Alacritty and the X11 fallbacks have no tab command: a new window.
When nothing here can be driven (ssh without tmux, an editor's terminal),
:class:`NoTerminalError` says so and the caller hands the command to the user.

Only absolute programs go in the command: a tab opened by tmux or Windows
Terminal starts from their environment, not from this process, and PATH there
may not know navin-cli.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from navin.utils.proc import no_window_kwargs

# A client (tmux, wt.exe, kitty @, osascript...) hands the request to the
# running terminal and exits: its exit code says whether the tab opened.
# The terminal itself (xterm, a first konsole) keeps running: it opened when
# it is still alive after a moment.
CLIENT_TIMEOUT_S = 20.0
DETACHED_SETTLE_S = 1.0
TITLE_MAX = 40
# Characters cmd.exe, wt.exe or a shell would read as syntax in a tab title.
_TITLE_UNSAFE = re.compile(r'[&|<>^()%!"`$;\\\x00-\x1f]')
_WINDOWS_CMD = "/mnt/c/Windows/System32/cmd.exe"
_WINDOWS_MOUNT = "/mnt/c"


class NoTerminalError(RuntimeError):
    """No terminal here can be asked for a new tab."""


@dataclass(frozen=True)
class Launch:
    """One way of opening the tab: what to start, where, and what it is."""

    argv: tuple[str, ...]
    label: str
    cwd: str | None = None
    # A client exits once the tab is requested; otherwise argv is the terminal.
    client: bool = True
    # Windows: the helper must not flash a console of its own.
    hidden: bool = False


def cli_argv(
    workspace: Path | str,
    session_key: str,
    *,
    config_path: Path | None = None,
    executable: Path | None = None,
) -> list[str]:
    """The command that opens ``session_key`` of ``workspace`` in a new navin-cli.

    ``executable`` is the navin-cli the user started (when known); a packaged
    navin runs ``navin tui``; from source it is this interpreter's navin.
    """
    if executable is not None:
        argv = [str(executable), str(workspace), "--session", session_key]
    elif getattr(sys, "frozen", False):
        argv = [sys.executable, "tui", str(workspace), "--session", session_key]
    else:
        argv = [sys.executable, "-m", "navin", "tui", str(workspace), "--session", session_key]
    if config_path is not None:
        argv.extend(["--config", str(config_path)])
    return argv


def tab_title(title: str) -> str:
    """A session title made safe for a tab name on every terminal."""
    clean = " ".join(_TITLE_UNSAFE.sub(" ", title).split())
    # A leading dash would read as an option of the tab command.
    clean = clean.lstrip("-").strip()
    return clean[:TITLE_MAX].rstrip() or "navin"


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _wt_tokens(tokens: Iterable[str]) -> list[str]:
    """Tokens for a Windows Terminal command line.

    wt.exe cuts every argument, quoted or not, at an unescaped semicolon to
    start the next command (``wt new-tab a ; new-tab b``) and turns ``\\;``
    back into ``;``. Checked here on 2026-10-28: a ``-c "x; y"`` reached the
    shell as ``-c "x"``.
    """
    return [token.replace(";", "\\;") for token in tokens]


def launch_candidates(
    cwd: Path | str,
    command: Sequence[str],
    *,
    title: str,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    exists: Callable[[str], bool] | None = None,
) -> list[Launch]:
    """Ways of opening ``command`` in a new tab here, best first (maybe none).

    Pure: the environment, platform and lookups can be given, so every
    terminal can be checked without being installed.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which
    exists = os.path.exists if exists is None else exists
    folder = str(cwd)
    name = tab_title(title)
    command = [str(part) for part in command]
    joined = shlex.join(command)
    windows = platform == "win32"
    out: list[Launch] = []

    # Inside a multiplexer the terminal of record is the multiplexer.
    if env.get("TMUX") and (tmux := which("tmux")):
        out.append(Launch((tmux, "new-window", "-c", folder, "-n", name, joined), "tmux window"))
    if env.get("STY") and (screen := which("screen")):
        out.append(Launch((screen, "-X", "screen", "-t", name, *command), "screen window"))

    # Windows Terminal: from Windows itself, or from a WSL distribution. Its
    # execution alias is not reachable from WSL, cmd.exe is (its own docs).
    # -w 0 is the window in use, or the last one used, or a new one.
    if windows and (wt := which("wt.exe") or which("wt")):
        out.append(
            Launch(
                (wt, "-w", "0", "new-tab", "--title", name, "-d", *_wt_tokens([folder, *command])),
                "Windows Terminal tab",
                hidden=True,
            ),
        )
    distro = "" if windows else env.get("WSL_DISTRO_NAME", "")
    if distro:
        cmd_exe = which("cmd.exe") or (_WINDOWS_CMD if exists(_WINDOWS_CMD) else None)
        if cmd_exe:
            # Back in this distribution, through the user's login shell so the
            # tab has the PATH and locale of a terminal the user opened.
            shell = env.get("SHELL") or "/bin/sh"
            out.append(
                Launch(
                    (
                        cmd_exe, "/c", "wt.exe", "-w", "0", "new-tab", "--title", name,
                        *_wt_tokens(["wsl.exe", "-d", distro, "--cd", folder, "--exec", shell, "-l", "-c", joined]),
                    ),
                    "Windows Terminal tab",
                    # cmd.exe refuses a WSL folder as its own working directory.
                    cwd=_WINDOWS_MOUNT if exists(_WINDOWS_MOUNT) else None,
                )
            )

    # Terminals with a remote-control client.
    if env.get("KITTY_WINDOW_ID") and (kitty := which("kitty")):
        out.append(Launch((kitty, "@", "launch", "--type=tab", "--cwd", folder, "--tab-title", name, *command), "kitty tab"))
    if env.get("WEZTERM_PANE") and (wezterm := which("wezterm")):
        out.append(Launch((wezterm, "cli", "spawn", "--cwd", folder, "--", *command), "WezTerm tab"))
    program = env.get("TERM_PROGRAM", "").lower()
    if platform == "darwin" and (osascript := which("osascript")):
        # Typed into the new tab's shell: it stays open with the exit summary.
        line = _applescript(f"cd {shlex.quote(folder)} && {joined}")
        if env.get("ITERM_SESSION_ID") or program == "iterm.app":
            script = (
                'tell application "iTerm2" to tell current window\n'
                "  set newTab to (create tab with default profile)\n"
                f"  tell current session of newTab to write text {line}\n"
                "end tell"
            )
            out.append(Launch((osascript, "-e", script), "iTerm2 tab"))
        if program == "apple_terminal":
            out.append(Launch((osascript, "-e", f'tell application "Terminal" to do script {line}'), "Terminal window"))
    if (env.get("GNOME_TERMINAL_SCREEN") or env.get("VTE_VERSION")) and (gnome := which("gnome-terminal")):
        out.append(Launch((gnome, "--tab", f"--working-directory={folder}", "--", *command), "GNOME Terminal tab"))
    if (env.get("KONSOLE_VERSION") or env.get("KONSOLE_DBUS_SESSION")) and (konsole := which("konsole")):
        out.append(Launch((konsole, "--new-tab", "--workdir", folder, "-e", *command), "Konsole tab"))

    # No tab command here: a new window.
    if windows:
        # `start` reads its first quoted argument as the window title; the
        # space keeps it quoted on the way through cmd.exe.
        cmd_exe = which("cmd.exe") or "cmd.exe"
        out.append(Launch((cmd_exe, "/c", "start", f"navin {name}", *command), "console window", cwd=folder, hidden=True))
    elif platform != "darwin" and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        for candidate in (env.get("TERMINAL", ""), "x-terminal-emulator", "kitty", "wezterm", "alacritty", "xterm"):
            exe = which(candidate) if candidate else None
            if not exe:
                continue
            program_name = Path(exe).name
            if program_name == "wezterm":
                argv = (exe, "start", "--cwd", folder, "--", *command)
            elif program_name == "kitty":
                argv = (exe, "--directory", folder, *command)
            elif program_name == "alacritty":
                argv = (exe, "--working-directory", folder, "-e", *command)
            else:
                argv = (exe, "-e", *command)
            out.append(Launch(argv, f"{program_name} window", cwd=folder, client=False))
            break
    return out


def _run_client(launch: Launch) -> str | None:
    """None when the client reported success, else what went wrong."""
    try:
        done = subprocess.run(
            launch.argv,
            cwd=launch.cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=CLIENT_TIMEOUT_S,
            check=False,
            **(no_window_kwargs() if launch.hidden else {}),
        )
    except subprocess.TimeoutExpired:
        return "did not answer"
    except OSError as exc:
        return str(exc)
    if done.returncode == 0:
        return None
    err = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return (err[0][:160] if err else f"exit code {done.returncode}")


def _run_detached(launch: Launch) -> str | None:
    """Start the terminal itself; None when it is alive (or done cleanly)."""
    try:
        proc = subprocess.Popen(
            launch.argv,
            cwd=launch.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return str(exc)
    try:
        code = proc.wait(timeout=DETACHED_SETTLE_S)
    except subprocess.TimeoutExpired:
        return None
    return None if code == 0 else f"exit code {code}"


def open_in_terminal_tab(
    cwd: Path | str,
    command: Sequence[str],
    *,
    title: str,
    candidates: Iterable[Launch] | None = None,
) -> str:
    """Open ``command`` in a new tab of the terminal in use; returns what opened.

    Tries each way in turn (``candidates`` overrides the detection) and raises
    :class:`NoTerminalError` when none took the tab. Blocking: call it off
    the event loop.
    """
    launches = list(launch_candidates(cwd, command, title=title) if candidates is None else candidates)
    if not launches:
        raise NoTerminalError(
            "No terminal here opens a tab (not in tmux, Windows Terminal, kitty, WezTerm, iTerm2, GNOME Terminal or Konsole)."
        )
    failures: list[str] = []
    for launch in launches:
        problem = _run_client(launch) if launch.client else _run_detached(launch)
        if problem is None:
            return launch.label
        failures.append(f"{launch.label}: {problem}")
    raise NoTerminalError("No terminal took the tab (" + "; ".join(failures) + ").")
