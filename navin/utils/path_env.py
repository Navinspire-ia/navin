# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Make user-installed tools findable from the packaged desktop app.

A gateway launched from Finder / Explorer / a .desktop entry inherits the
minimal PATH of the graphical session (``/usr/bin:/bin:/usr/sbin:/sbin`` under
launchd, the machine-only variables under Windows depending on how the shell
was started). ``npx``, ``uvx``, ``bunx`` and friends live in per-user
directories that this PATH does not contain, so MCP stdio servers configured
with those commands fail with "command not found" in the desktop build while
working from a terminal.

:func:`augment_path_for_user_tools` appends the well-known per-user install
directories that actually exist on this machine to ``os.environ["PATH"]``.
Everything downstream benefits at once: the MCP SDK's default child
environment, ``shutil.which`` probes for MCP presets and skills prerequisites
(``requires.bins``), and plugin installs that need ``git``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _posix_candidates(home: Path) -> list[Path]:
    candidates = [
        home / ".local" / "bin",  # uv/uvx, pipx
        home / ".cargo" / "bin",
        home / ".bun" / "bin",
        home / ".deno" / "bin",
        home / ".volta" / "bin",
        home / ".npm-global" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/opt/homebrew/sbin"),
        Path("/usr/local/bin"),
        Path("/opt/local/bin"),  # MacPorts
        Path("/home/linuxbrew/.linuxbrew/bin"),
        # Version managers: their shims pick the right python/node/ruby.
        Path(os.environ.get("PYENV_ROOT") or home / ".pyenv") / "shims",
        Path(os.environ.get("ASDF_DATA_DIR") or home / ".asdf") / "shims",
        home / ".local" / "share" / "mise" / "shims",
        Path("/snap/bin"),
        # fnm : alias "default" vers la version node active.
        home / ".local" / "share" / "fnm" / "aliases" / "default" / "bin",
        home / "Library" / "Application Support" / "fnm" / "aliases" / "default" / "bin",
    ]
    # nvm n'expose pas d'alias stable sur le disque : prendre la version la
    # plus récente installée (c'est ce que `nvm use default` donne en pratique
    # pour la grande majorité des installs mono-version).
    nvm_versions = home / ".nvm" / "versions" / "node"
    try:
        installed = sorted(
            (d for d in nvm_versions.iterdir() if d.is_dir()),
            key=lambda d: [int(p) for p in d.name.lstrip("v").split(".") if p.isdigit()],
            reverse=True,
        )
    except OSError:
        installed = []
    # `nvm alias default 20` writes the wanted version: honour it first.
    try:
        wanted = (home / ".nvm" / "alias" / "default").read_text().strip().lstrip("v")
    except OSError:
        wanted = ""
    chosen = next(
        (d for d in installed if wanted and (d.name.lstrip("v") == wanted or d.name.lstrip("v").startswith(wanted + "."))),
        installed[0] if installed else None,
    )
    if chosen is not None:
        candidates.append(chosen / "bin")
    return candidates


def _windows_candidates(home: Path) -> list[Path]:
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    candidates = [
        home / ".local" / "bin",  # uv/uvx
        home / ".cargo" / "bin",
        home / ".bun" / "bin",
        home / ".deno" / "bin",
    ]
    if appdata:
        candidates.append(Path(appdata) / "npm")  # npx.cmd des installs npm -g
    if localappdata:
        candidates.append(Path(localappdata) / "Microsoft" / "WinGet" / "Links")
        candidates.append(Path(localappdata) / "Volta" / "bin")
        # uv / uvx from the official Windows installer or a portable unpack.
        candidates.append(Path(localappdata) / "uv")
        candidates.append(Path(localappdata) / "Programs" / "uv")
        # Git for Windows installed for the current user only.
        candidates.append(Path(localappdata) / "Programs" / "Git" / "cmd")
    candidates.append(home / "scoop" / "shims")
    if program_files:
        candidates.append(Path(program_files) / "nodejs")
        # Git for Windows (winget Git.Git or the official installer) puts
        # git.exe in Git\cmd. Explorer hands a desktop app the PATH of the
        # session start, so a Git installed later is invisible without this.
        candidates.append(Path(program_files) / "Git" / "cmd")
    return candidates


def augment_path_for_user_tools() -> None:
    """Append existing per-user tool directories to this process's PATH.

    Idempotent and additive only: directories already on PATH keep their
    position (a user override stays an override), nothing is prepended, and
    directories that do not exist are skipped.
    """
    home = Path.home()
    candidates = (
        _windows_candidates(home) if os.name == "nt" else _posix_candidates(home)
    )

    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    # Windows compare les chemins sans tenir compte de la casse.
    fold = (lambda s: s.lower()) if os.name == "nt" else (lambda s: s)
    seen = {fold(os.path.normpath(p)) for p in parts}

    added = []
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
        except OSError:
            continue
        key = fold(os.path.normpath(str(candidate)))
        if key in seen:
            continue
        parts.append(str(candidate))
        seen.add(key)
        added.append(str(candidate))

    if added:
        os.environ["PATH"] = os.pathsep.join(parts)
        if "pytest" not in sys.modules:
            try:
                from loguru import logger

                logger.debug("PATH augmented with user tool dirs: {}", added)
            except Exception:
                pass


# Command-line tools Navin installs with winget on Windows when they are
# missing, rather than asking the user to.
#
# ripgrep: grep and find_files run ripgrep when it is on PATH and otherwise read
# every file from Python. On the Windows desktop of 2026-10-21 (no ripgrep, no
# native module) one grep over a 55 000-file project took ten minutes. The
# winget package is portable: a per-user unpack under
# %LOCALAPPDATA%\Microsoft\WinGet with rg.exe linked in WinGet\Links, so no
# administrator rights and no UAC prompt. NAVIN_NO_AUTO_RIPGREP=1 turns it off.
#
# git: the git tool, the Git panel, clones and worktrees all need git.exe, and
# a Windows machine has none by default. Git.Git lands in
# %ProgramFiles%\Git\cmd, which _windows_candidates adds to PATH once it
# exists. It is a machine-wide installer, so Windows may show a UAC prompt.
# NAVIN_NO_AUTO_GIT=1 turns it off.
@dataclass(frozen=True)
class WingetTool:
    """A command-line tool installed with winget when Windows lacks it."""

    package: str
    binary: str
    opt_out_env: str
    state_name: str
    timeout_s: int = 600


RIPGREP = WingetTool(
    package="BurntSushi.ripgrep.MSVC", binary="rg",
    opt_out_env="NAVIN_NO_AUTO_RIPGREP", state_name="ripgrep-install.json",
)
GIT = WingetTool(
    package="Git.Git", binary="git",
    opt_out_env="NAVIN_NO_AUTO_GIT", state_name="git-install.json", timeout_s=900,
)
RIPGREP_WINGET_ID = RIPGREP.package
RIPGREP_OPT_OUT_ENV = RIPGREP.opt_out_env
GIT_WINGET_ID = GIT.package
GIT_OPT_OUT_ENV = GIT.opt_out_env
# What to tell a user whose machine could not install Git on its own.
GIT_MANUAL_INSTALL = (
    "winget install --id Git.Git -e --silent --accept-package-agreements "
    "--accept-source-agreements, then check it with "
    'Test-Path "C:\\Program Files\\Git\\cmd\\git.exe" and restart Navin'
)
# One attempt per week at most: a machine without network, with winget
# disabled by policy, or a user who removed the tool must not pay it each start.
_RETRY_AFTER_S = 7 * 24 * 3600
_state_lock = threading.Lock()
# Two winget installs at once fight over the source cache: run them in turn.
_install_lock = threading.Lock()
_running: set[str] = set()


def winget_install_argv(winget: str, package: str) -> list[str]:
    """The unattended winget install of *package*."""
    return [
        winget, "install", "--exact", "--id", package, "--silent",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity",
    ]


def ripgrep_winget_argv(winget: str) -> list[str]:
    """The unattended winget install of ripgrep."""
    return winget_install_argv(winget, RIPGREP.package)


def winget_install_running(tool: WingetTool) -> bool:
    """Whether this process is installing *tool* right now."""
    with _state_lock:
        return tool.package in _running


def _tool_state_path(tool: WingetTool) -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("tools") / tool.state_name


def _ripgrep_state_path() -> Path:
    return _tool_state_path(RIPGREP)


def _git_state_path() -> Path:
    return _tool_state_path(GIT)


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _install(
    tool: WingetTool, winget: str, state_path: Path, run: Callable[..., subprocess.CompletedProcess]
) -> bool:
    from navin.utils.proc import no_window_kwargs

    detail = ""
    try:
        with _install_lock:
            proc = run(
                winget_install_argv(winget, tool.package), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=tool.timeout_s, **no_window_kwargs(),
            )
        returncode: int | None = proc.returncode
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-400:]
    except (OSError, subprocess.SubprocessError) as exc:
        returncode = None
        detail = str(exc)[-400:]
    # The install folder (WinGet\Links, Program Files\Git\cmd) may only exist
    # now: put it on PATH for this process, then check the binary is there.
    augment_path_for_user_tools()
    found = shutil.which(tool.binary)
    _write_state(state_path, {
        "attempted_at": time.time(), "ok": bool(found), "returncode": returncode,
        tool.binary: found or "", "detail": detail,
    })
    try:
        from loguru import logger

        if found:
            logger.info("{} installed with winget: {}", tool.package, found)
        else:
            logger.warning("winget could not install {} (exit {}): {}", tool.package, returncode, detail)
    except Exception:
        pass
    return bool(found)


def _ensure_winget_tool(
    tool: WingetTool,
    state_path_of: Callable[[], Path],
    *,
    background: bool,
    platform: str | None,
    run: Callable[..., subprocess.CompletedProcess],
    now: float | None,
) -> str:
    if (platform or sys.platform) != "win32":
        return "unsupported"
    if os.environ.get(tool.opt_out_env, "").strip().lower() in {"1", "true", "yes", "on"}:
        return "disabled"
    augment_path_for_user_tools()
    if shutil.which(tool.binary):
        return "present"
    winget = shutil.which("winget") or shutil.which("winget.exe")
    if not winget:
        return "no-winget"
    with _state_lock:
        if tool.package in _running:
            return "already-running"
        try:
            state_path = state_path_of()
        except OSError:
            return "failed"
        state = _read_state(state_path)
        clock = time.time() if now is None else now
        last = state.get("attempted_at")
        if isinstance(last, (int, float)) and clock - last < _RETRY_AFTER_S:
            return "recent-attempt"
        # Recorded before the install runs, so a second process (desktop and
        # CLI started together) does not launch winget a second time.
        _write_state(state_path, {"attempted_at": clock, "ok": None})
        _running.add(tool.package)

    def work() -> bool:
        try:
            return _install(tool, winget, state_path, run)
        finally:
            with _state_lock:
                _running.discard(tool.package)

    if not background:
        return "installed" if work() else "failed"
    threading.Thread(target=work, name=f"navin-{tool.binary}-install", daemon=True).start()
    return "started"


def ensure_ripgrep(
    *,
    background: bool = True,
    platform: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    now: float | None = None,
) -> str:
    """Install ripgrep with winget on Windows when it is missing.

    Returns what happened: ``present``, ``started`` (background install),
    ``installed``, ``failed``, ``unsupported`` (not Windows), ``disabled``,
    ``no-winget``, ``recent-attempt`` or ``already-running``. Never raises and
    never blocks the caller when ``background`` is true.
    """
    return _ensure_winget_tool(
        RIPGREP, lambda: _ripgrep_state_path(),
        background=background, platform=platform, run=run, now=now,
    )


def ensure_git(
    *,
    background: bool = True,
    platform: str | None = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    now: float | None = None,
) -> str:
    """Install Git for Windows with winget when no git is reachable.

    Same contract and return values as :func:`ensure_ripgrep`.
    """
    return _ensure_winget_tool(
        GIT, lambda: _git_state_path(),
        background=background, platform=platform, run=run, now=now,
    )


def git_missing_hint(*, platform: str | None = None) -> str:
    """What to add to "git is not installed" for the user, or ``""``.

    On Windows it also starts the background install when none ran this week,
    so the next git command works without the user doing anything.
    """
    status = ensure_git(platform=platform)
    if status in {"unsupported", "present"}:
        return ""
    if status in {"started", "already-running"}:
        return (
            "Navin is installing Git for Windows with winget in the background; "
            "retry in a minute or two (accept the Windows prompt if one appears)."
        )
    return f"Install Git for Windows: {GIT_MANUAL_INSTALL}."
