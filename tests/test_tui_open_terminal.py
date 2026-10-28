# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The sidebar's "open in a new terminal tab": one more navin-cli on a chat."""

from __future__ import annotations

import os
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

from navin.tui import open_terminal
from navin.tui.open_terminal import (
    Launch,
    NoTerminalError,
    cli_argv,
    launch_candidates,
    open_in_terminal_tab,
    tab_title,
)

CWD = "/home/me/my proj"
COMMAND = ["/opt/navin/navin-cli", CWD, "--session", "cli:ab12"]


def _which(*names: str):
    """A PATH holding only ``names``, at /usr/bin."""
    found = set(names)
    return lambda name: f"/usr/bin/{name}" if name in found else None


def _program(code: int, stderr: str = "", sleep_s: int = 0) -> tuple[str, ...]:
    """A real small program standing in for a terminal client: the shell of the OS."""
    if os.name == "nt":
        parts = [f"echo {stderr} 1>&2"] if stderr else []
        if sleep_s:
            parts.append(f"ping -n {sleep_s + 1} 127.0.0.1 > nul")
        parts.append(f"exit {code}")
        return (os.environ.get("COMSPEC", "cmd.exe"), "/c", " & ".join(parts))
    parts = [f"echo {shlex.quote(stderr)} >&2"] if stderr else []
    if sleep_s:
        parts.append(f"sleep {sleep_s}")
    parts.append(f"exit {code}")
    return ("/bin/sh", "-c", "; ".join(parts))


def _candidates(env: dict[str, str], *, platform: str = "linux", which=_which(), exists=lambda path: False, title: str = "Fix login"):
    return launch_candidates(CWD, COMMAND, title=title, env=env, platform=platform, which=which, exists=exists)


class CliCommandTests(unittest.TestCase):
    def test_runs_the_navin_cli_the_user_started_else_this_navin(self) -> None:
        started = cli_argv(Path(CWD), "cli:1", executable=Path("/opt/navin/navin-cli"))
        self.assertEqual(started, ["/opt/navin/navin-cli", CWD, "--session", "cli:1"])
        with mock.patch.object(sys, "frozen", False, create=True):
            source = cli_argv(Path(CWD), "cli:1", config_path=Path("/etc/navin.yaml"))
        self.assertEqual(source, [sys.executable, "-m", "navin", "tui", CWD, "--session", "cli:1", "--config", "/etc/navin.yaml"])
        with mock.patch.object(sys, "frozen", True, create=True):
            packaged = cli_argv(Path(CWD), "cli:1")
        self.assertEqual(packaged, [sys.executable, "tui", CWD, "--session", "cli:1"])
        for argv in (started, source, packaged):
            self.assertTrue(Path(argv[0]).is_absolute(), argv)

    def test_tab_titles_carry_no_shell_or_cmd_syntax(self) -> None:
        self.assertEqual(tab_title('Fix login & signup | "now"'), "Fix login signup now")
        self.assertEqual(tab_title(""), "navin")
        self.assertEqual(tab_title("%PATH% (x)^!"), "PATH x")
        self.assertEqual(tab_title("-- fix it"), "fix it")  # never read as an option
        self.assertLessEqual(len(tab_title("word " * 30)), open_terminal.TITLE_MAX)


class CandidateTests(unittest.TestCase):
    def test_inside_tmux_a_tmux_window_comes_first_whatever_the_terminal(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,0", "WT_SESSION": "x", "WSL_DISTRO_NAME": "Ubuntu", "KITTY_WINDOW_ID": "1"}
        found = _candidates(env, which=_which("tmux", "cmd.exe", "kitty"))
        self.assertEqual(found[0].label, "tmux window")
        self.assertEqual(found[0].argv[:5], ("/usr/bin/tmux", "new-window", "-c", CWD, "-n"))
        self.assertEqual(found[0].argv[5], "Fix login")
        self.assertEqual(found[0].argv[6], shlex.join(COMMAND))  # tmux runs it through a shell
        self.assertEqual([c.label for c in found], ["tmux window", "Windows Terminal tab", "kitty tab"])

    def test_from_wsl_windows_terminal_gets_a_tab_back_in_the_same_distribution(self) -> None:
        env = {"WSL_DISTRO_NAME": "Ubuntu", "SHELL": "/bin/zsh", "WT_SESSION": "abc"}
        found = _candidates(env, which=_which("cmd.exe"), exists=lambda path: path == "/mnt/c")
        self.assertEqual(len(found), 1)
        launch = found[0]
        self.assertEqual(launch.label, "Windows Terminal tab")
        self.assertEqual(launch.cwd, "/mnt/c")  # cmd.exe cannot start in a WSL folder
        self.assertEqual(
            launch.argv,
            (
                "/usr/bin/cmd.exe", "/c", "wt.exe", "-w", "0", "new-tab", "--title", "Fix login",
                "wsl.exe", "-d", "Ubuntu", "--cd", CWD, "--exec", "/bin/zsh", "-l", "-c", shlex.join(COMMAND),
            ),
        )
        # Without WT_SESSION (conhost, an editor's terminal) the tab still goes
        # to Windows Terminal: -w 0 is the last window used, or a new one.
        self.assertEqual([c.label for c in _candidates({"WSL_DISTRO_NAME": "Ubuntu"}, which=_which("cmd.exe"))], ["Windows Terminal tab"])
        # cmd.exe off PATH: the fixed Windows location, when it exists.
        fixed = _candidates({"WSL_DISTRO_NAME": "Ubuntu"}, exists=lambda path: path == open_terminal._WINDOWS_CMD)
        self.assertEqual(fixed[0].argv[0], open_terminal._WINDOWS_CMD)
        self.assertEqual(_candidates({"WSL_DISTRO_NAME": "Ubuntu"}), [])

    def test_windows_terminal_never_reads_a_semicolon_as_its_next_command(self) -> None:
        # wt.exe splits any argument at an unescaped ";" (seen 2026-10-28:
        # a -c "x; y" reached the shell as -c "x"); "\;" is its escape.
        command = ["/opt/navin/navin-cli", "/srv/a;b", "--session", "cli:1"]
        wsl = launch_candidates("/srv/a;b", command, title="x", env={"WSL_DISTRO_NAME": "Ubuntu"}, platform="linux", which=_which("cmd.exe"))
        joined = wsl[0].argv[-1]
        self.assertEqual(joined, shlex.join(command).replace(";", "\\;"))
        self.assertEqual(wsl[0].argv[wsl[0].argv.index("--cd") + 1], "/srv/a\\;b")
        native = launch_candidates("/srv/a;b", command, title="x", env={}, platform="win32", which=_which("wt.exe"))
        self.assertEqual(native[0].argv[native[0].argv.index("-d") + 1], "/srv/a\\;b")
        self.assertIn("/srv/a\\;b", native[0].argv)
        self.assertNotIn("/srv/a;b", native[0].argv)
        # other terminals take the argument as it is
        kitty = launch_candidates("/srv/a;b", command, title="x", env={"KITTY_WINDOW_ID": "1"}, platform="linux", which=_which("kitty"))
        self.assertIn("/srv/a;b", kitty[0].argv)

    def test_on_windows_a_windows_terminal_tab_then_a_console_window(self) -> None:
        env = {"WT_SESSION": "abc"}
        found = _candidates(env, platform="win32", which=_which("wt.exe", "cmd.exe"))
        self.assertEqual([c.label for c in found], ["Windows Terminal tab", "console window"])
        self.assertEqual(found[0].argv, ("/usr/bin/wt.exe", "-w", "0", "new-tab", "--title", "Fix login", "-d", CWD, *COMMAND))
        self.assertTrue(found[0].hidden)
        self.assertEqual(found[1].argv, ("/usr/bin/cmd.exe", "/c", "start", "navin Fix login", *COMMAND))
        self.assertEqual(found[1].cwd, CWD)
        # no Windows Terminal installed: the console window alone
        self.assertEqual([c.label for c in _candidates({}, platform="win32", which=_which("cmd.exe"))], ["console window"])

    def test_kitty_wezterm_gnome_and_konsole_each_get_their_tab_command(self) -> None:
        kitty = _candidates({"KITTY_WINDOW_ID": "3"}, which=_which("kitty"))
        self.assertEqual(kitty[0].argv, ("/usr/bin/kitty", "@", "launch", "--type=tab", "--cwd", CWD, "--tab-title", "Fix login", *COMMAND))
        wezterm = _candidates({"WEZTERM_PANE": "0"}, which=_which("wezterm"))
        self.assertEqual(wezterm[0].argv, ("/usr/bin/wezterm", "cli", "spawn", "--cwd", CWD, "--", *COMMAND))
        gnome = _candidates({"VTE_VERSION": "7600"}, which=_which("gnome-terminal"))
        self.assertEqual(gnome[0].argv, ("/usr/bin/gnome-terminal", "--tab", f"--working-directory={CWD}", "--", *COMMAND))
        konsole = _candidates({"KONSOLE_VERSION": "230800"}, which=_which("konsole"))
        self.assertEqual(konsole[0].argv, ("/usr/bin/konsole", "--new-tab", "--workdir", CWD, "-e", *COMMAND))
        for found in (kitty, wezterm, gnome, konsole):
            self.assertTrue(found[0].client)

    def test_on_macos_iterm_gets_a_tab_and_apple_terminal_a_window(self) -> None:
        iterm = _candidates({"ITERM_SESSION_ID": "w0t1p0", "TERM_PROGRAM": "iTerm.app"}, platform="darwin", which=_which("osascript"))
        self.assertEqual(iterm[0].label, "iTerm2 tab")
        script = iterm[0].argv[2]
        self.assertIn('tell application "iTerm2"', script)
        self.assertIn("create tab with default profile", script)
        # the command is typed into the new tab's shell, folder first
        self.assertIn(f"write text \"cd {shlex.quote(CWD)} && {shlex.join(COMMAND)}\"", script)
        apple = _candidates({"TERM_PROGRAM": "Apple_Terminal"}, platform="darwin", which=_which("osascript"))
        self.assertEqual(apple[0].label, "Terminal window")
        self.assertIn('tell application "Terminal" to do script "cd', apple[0].argv[2])
        # an unknown macOS terminal: nothing to drive, no X11 fallback
        self.assertEqual(_candidates({"TERM_PROGRAM": "ghostty"}, platform="darwin", which=_which("osascript", "xterm")), [])

    def test_an_unknown_x11_terminal_opens_a_window_with_the_preferred_program(self) -> None:
        env = {"DISPLAY": ":0", "TERMINAL": "alacritty"}
        found = _candidates(env, which=_which("alacritty", "xterm"))
        self.assertEqual([c.label for c in found], ["alacritty window"])
        self.assertEqual(found[0].argv, ("/usr/bin/alacritty", "--working-directory", CWD, "-e", *COMMAND))
        self.assertFalse(found[0].client)
        plain = _candidates({"WAYLAND_DISPLAY": "wayland-0"}, which=_which("xterm"))
        self.assertEqual(plain[0].argv, ("/usr/bin/xterm", "-e", *COMMAND))
        # ssh without a display or multiplexer: nothing here takes a tab
        self.assertEqual(_candidates({"TERM": "xterm-256color"}, which=_which("xterm")), [])


class OpenTests(unittest.TestCase):
    def test_the_first_way_that_works_wins_and_failures_are_named(self) -> None:
        failing = Launch(_program(1, "remote control is disabled"), "kitty tab")
        working = Launch(_program(0), "tmux window")
        where = open_in_terminal_tab(CWD, COMMAND, title="x", candidates=[failing, working])
        self.assertEqual(where, "tmux window")
        with self.assertRaises(NoTerminalError) as caught:
            open_in_terminal_tab(CWD, COMMAND, title="x", candidates=[failing])
        self.assertIn("kitty tab: remote control is disabled", str(caught.exception))
        missing = Launch(("/nonexistent/terminal-program", "-e", "x"), "xterm window", client=False)
        with self.assertRaises(NoTerminalError) as caught:
            open_in_terminal_tab(CWD, COMMAND, title="x", candidates=[missing])
        self.assertIn("xterm window", str(caught.exception))

    def test_a_terminal_still_running_after_a_moment_counts_as_opened(self) -> None:
        terminal = Launch(_program(0, sleep_s=3), "xterm window", client=False)
        with mock.patch.object(open_terminal, "DETACHED_SETTLE_S", 0.2):
            self.assertEqual(open_in_terminal_tab(CWD, COMMAND, title="x", candidates=[terminal]), "xterm window")

    def test_nothing_detected_is_said_plainly(self) -> None:
        with mock.patch.object(open_terminal, "launch_candidates", return_value=[]):
            with self.assertRaises(NoTerminalError) as caught:
                open_in_terminal_tab(CWD, COMMAND, title="x")
        self.assertIn("No terminal here opens a tab", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
