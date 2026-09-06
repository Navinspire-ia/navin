"""Put the ``navin`` command where a shell will find it.

The Debian and RPM packages symlink ``/usr/bin/navin`` at install time, so the
documented commands work on Linux. The macOS disk image cannot: it only copies an
application bundle, and the executable ends up buried in
``/Applications/Navin.app/Contents/MacOS``. The Windows standalone installer adds
its own directory to the user PATH, but the Tauri desktop installers (MSI/NSIS,
DMG, AppImage) install a full CLI without touching any PATH.

Two entry points:

- :func:`install_cli_link` - explicit ``navin install-cli``;
- :func:`ensure_cli_on_path` - best-effort self-heal called at WebUI startup in
  packaged builds: if no ``navin`` resolves in a shell, it creates the shims
  (Windows: ``navin.cmd`` for cmd/PowerShell + an extensionless POSIX shim for
  WSL/Git Bash, plus the user PATH registry entry) or the symlink (macOS/Linux).

On POSIX it never edits shell configuration files: it writes into a directory
that is already meant for user commands, and says so when that directory is not
on PATH.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LinkResult:
    """What was done, and what the user still has to know."""

    path: Path | None
    created: bool
    message: str
    on_path: bool = True


def _candidate_dirs() -> list[Path]:
    """Directories meant for user commands, best first."""
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/usr/local/bin"), home / ".local" / "bin"]
    return [home / ".local" / "bin", Path("/usr/local/bin")]


def _path_entries() -> set[str]:
    return {
        str(Path(entry).expanduser())
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry
    }


def _writable(directory: Path) -> bool:
    if directory.is_dir():
        return os.access(directory, os.W_OK)
    parent = directory.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _posix_cli_target() -> Path:
    """The executable a POSIX shim should point at.

    An AppImage runs from a squashfs mounted under ``/tmp/.mount_*`` for the
    lifetime of the process, so a link into it dies when the app closes and is
    rebuilt just as dead on the next launch. The runtime exports ``APPIMAGE``
    with the path of the image itself: the only stable thing to point at.
    """
    image = os.environ.get("APPIMAGE", "").strip()
    if image and Path(image).is_file():
        return Path(image).resolve()
    return Path(sys.executable).resolve()


def _on_ephemeral_mount(path: Path) -> bool:
    return any(part.startswith(".mount_") for part in path.parts)


def _is_packaged_navin(path: Path) -> bool:
    """Whether a resolved ``navin`` belongs to a packaged build of this app.

    Used to tell an installation of ours apart from a `pip install -e .` one,
    which is the user's and must never be relinked behind their back.
    """
    text = str(path).lower()
    return (
        ".app/contents/" in text
        or "navin-dist" in text
        or text.endswith(".appimage")
        or text.startswith("/usr/lib/navin/")
    )


def _stale_posix_link() -> Path | None:
    """A ``navin`` on PATH left behind by a previous install of this app.

    A reinstall that lands elsewhere (an admin-less copy in ``~/Applications``,
    an AppImage saved under a new name) leaves the old link resolving to the
    build the user just replaced, so the terminal keeps running the old
    version while the window runs the new one.
    """
    import shutil

    found = shutil.which("navin")
    if not found:
        return None
    link = Path(found)
    try:
        resolved = link.resolve()
    except OSError:
        return link
    if resolved == _posix_cli_target():
        return None
    return link if _is_packaged_navin(resolved) else None


def _windows_cli_target() -> Path:
    """The executable the Windows shims should run.

    The standalone layout ships a windowed ``Navin.exe`` next to a console
    ``navin-cli.exe``; the desktop sidecar is a single console ``navin.exe``.
    Prefer the console binary when both exist.
    """
    exe = Path(sys.executable).resolve()
    sibling = exe.with_name("navin-cli.exe")
    return sibling if sibling.exists() else exe


def _wsl_shim_text(target: Path, subcommand: str = "") -> str:
    """POSIX shim run through WSL interop (and Git Bash) as plain ``navin``.

    Translates filesystem path arguments so ``navin .`` inside WSL opens the
    current folder as ``\\\\wsl.localhost\\...`` on the Windows backend.
    ``subcommand`` is prepended to the arguments (``tui`` for ``navin-cli``).
    """
    sub = f'"{subcommand}" ' if subcommand else ""
    return (
        "#!/bin/sh\n"
        "# Navin from WSL / Git Bash: forwards to the Windows CLI, translating\n"
        "# path arguments so `navin .` opens the current folder in the editor.\n"
        f"WIN_EXE='{target}'\n"
        'if command -v wslpath >/dev/null 2>&1; then\n'
        '  exe="$(wslpath -u "$WIN_EXE" 2>/dev/null || printf %s "$WIN_EXE")"\n'
        '  n=$#; i=0\n'
        '  while [ "$i" -lt "$n" ]; do\n'
        '    a="$1"; shift\n'
        '    case "$a" in\n'
        '      -*) ;;\n'
        '      *) if [ -e "$a" ]; then w="$(wslpath -w "$a" 2>/dev/null)" && a="$w"; fi ;;\n'
        '    esac\n'
        '    set -- "$@" "$a"\n'
        '    i=$((i+1))\n'
        '  done\n'
        'else\n'
        '  exe="$WIN_EXE"\n'
        'fi\n'
        f'exec "$exe" {sub}"$@"\n'
    )


TUI_COMMAND = "navin-cli"
_TUI_SHIM_MARK = "# navin-cli: Navin terminal UI"


def _tui_shim_text(target: Path) -> str:
    """``navin-cli`` opens the terminal UI in the current folder."""
    quoted = "'" + str(target).replace("'", "'\\''") + "'"
    return (
        "#!/bin/sh\n"
        f"{_TUI_SHIM_MARK}, started in the current folder (same engine as navin).\n"
        f'exec {quoted} tui "$@"\n'
    )


def _is_tui_shim(path: Path) -> bool:
    try:
        return _TUI_SHIM_MARK in path.read_text(encoding="utf-8", errors="ignore")[:400]
    except OSError:
        return False


def write_tui_shim(directory: Path, target: Path, *, force: bool = False) -> Path | None:
    """Write ``navin-cli`` next to ``navin``. Never overwrites a foreign file."""
    shim = directory / TUI_COMMAND
    if shim.exists() and not shim.is_symlink() and not _is_tui_shim(shim) and not force:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    if shim.exists() or shim.is_symlink():
        shim.unlink()
    shim.write_text(_tui_shim_text(target), encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _add_to_user_path_windows(directory: str) -> bool:
    """Append ``directory`` to the HKCU PATH; True when it was added."""
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        entries = [entry.strip() for entry in str(current).split(";") if entry.strip()]
        if any(entry.lower().rstrip("\\") == directory.lower().rstrip("\\") for entry in entries):
            return False
        joined = ";".join([*entries, directory])
        winreg.SetValueEx(key, "Path", 0, kind or winreg.REG_EXPAND_SZ, joined)
    try:
        # Tell running shells/Explorer the environment moved (WM_SETTINGCHANGE).
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
        )
    except Exception:
        pass
    return True


def _windows_bin_dir() -> Path:
    """The stable directory our Windows shims live in."""
    local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_appdata) / "Navin" / "bin"


def _windows_shims_current(bin_dir: Path) -> bool:
    """Whether navin.cmd still runs the CLI this process was started from."""
    try:
        content = (bin_dir / "navin.cmd").read_text(encoding="utf-8")
    except OSError:
        return False
    return str(_windows_cli_target()) in content


def _install_windows_shims() -> LinkResult:
    """Create ``navin.cmd`` + WSL shim in a stable bin dir on the user PATH."""
    target = _windows_cli_target()
    bin_dir = _windows_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)

    cmd_shim = bin_dir / "navin.cmd"
    cmd_shim.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
    # navin-cli: the terminal UI, started in the current folder.
    (bin_dir / f"{TUI_COMMAND}.cmd").write_text(f'@echo off\r\n"{target}" tui %*\r\n', encoding="utf-8")
    # LF endings on purpose: these are executed by sh inside WSL.
    (bin_dir / "navin").write_bytes(_wsl_shim_text(target).encode("utf-8"))
    (bin_dir / TUI_COMMAND).write_bytes(_wsl_shim_text(target, "tui").encode("utf-8"))

    added = _add_to_user_path_windows(str(bin_dir))
    message = f"Installed the navin and {TUI_COMMAND} commands in {bin_dir} (runs {target})."
    if added:
        message += " Open a new terminal for PATH changes to apply."
    return LinkResult(path=cmd_shim, created=True, message=message, on_path=not added)


def install_cli_link(*, force: bool = False) -> LinkResult:
    """Make ``navin`` resolvable from a shell (symlink or Windows shims)."""
    from navin.python_runtime import packaged

    if not packaged():
        return LinkResult(
            path=None,
            created=False,
            message="A source install already provides the navin command through pip.",
        )
    if sys.platform == "win32":
        return _install_windows_shims()

    executable = _posix_cli_target()
    if _on_ephemeral_mount(executable):
        return LinkResult(
            path=None,
            created=False,
            message=(
                "This AppImage did not expose its own path (APPIMAGE is unset), "
                "so the navin command would point inside a temporary mount that "
                "disappears when the app closes. Run the .AppImage file directly."
            ),
        )
    for directory in _candidate_dirs():
        if not _writable(directory):
            continue
        link = directory / "navin"
        if link.exists() or link.is_symlink():
            if not force and not link.is_symlink():
                return LinkResult(
                    path=link,
                    created=False,
                    message=f"{link} exists and is not a link; pass --force to replace it.",
                )
            if link.resolve() == executable and not force:
                write_tui_shim(directory, executable)
                return LinkResult(
                    path=link,
                    created=False,
                    message=f"{link} already points here.",
                    on_path=str(directory) in _path_entries(),
                )
            link.unlink()
        directory.mkdir(parents=True, exist_ok=True)
        link.symlink_to(executable)
        write_tui_shim(directory, executable, force=force)
        on_path = str(directory) in _path_entries()
        message = f"Linked {link} to {executable} (and {TUI_COMMAND} for the terminal UI)."
        if not on_path:
            message += f" Add {directory} to your PATH to use it."
        return LinkResult(path=link, created=True, message=message, on_path=on_path)

    tried = ", ".join(str(directory) for directory in _candidate_dirs())
    return LinkResult(
        path=None,
        created=False,
        message=(
            f"No writable directory for the command (tried {tried}). "
            f"Add this to your shell instead: alias navin='{executable}'"
        ),
    )


def _ensure_tui_command(navin_path: Path) -> None:
    """``navin`` resolves (package symlink, older install): add ``navin-cli`` too.

    Distro packages only ship ``/usr/bin/navin``; the shim goes next to it when
    that directory is writable, else into the first user bin directory.
    """
    import shutil

    if not str(navin_path) or shutil.which(TUI_COMMAND):
        return
    try:
        target = navin_path.resolve()
    except OSError:
        return
    for directory in [navin_path.parent, *_candidate_dirs()]:
        if _writable(directory):
            try:
                write_tui_shim(directory, target)
            except OSError:
                continue
            return


def ensure_cli_on_path() -> LinkResult | None:
    """Best-effort self-heal so ``navin`` works in fresh terminals.

    Called at WebUI/gateway startup in packaged builds. The desktop installers
    (Tauri MSI/NSIS, DMG, AppImage) ship a full CLI but never touch the PATH;
    the first launch repairs that. No-op when ``navin`` already resolves, when
    running from source, or when anything fails (never blocks startup).
    """
    from navin.python_runtime import packaged

    if not packaged():
        return None
    import shutil

    if sys.platform == "win32":
        # Don't trust `which navin` alone: an older standalone install's
        # navin.cmd resolves in cmd/PowerShell but does nothing for WSL or
        # Git Bash (bash never matches .cmd files). Heal whenever our own
        # shim pair is missing - or stale: an upgrade can move the CLI (the
        # one-file navin.exe became navin-dist\navin.exe), leaving shims that
        # point at a binary that no longer exists.
        bin_dir = _windows_bin_dir()
        if (
            shutil.which("navin")
            and (bin_dir / "navin.cmd").exists()
            and (bin_dir / "navin").exists()
            and _windows_shims_current(bin_dir)
        ):
            return None
    elif shutil.which("navin"):
        # Present is not enough: a reinstall elsewhere leaves a link that still
        # resolves, straight to the build that was just replaced.
        stale = _stale_posix_link()
        if stale is None:
            _ensure_tui_command(Path(shutil.which("navin") or ""))
            return None
        try:
            if _writable(stale.parent) and (stale.is_symlink() or stale.is_file()):
                stale.unlink()
        except OSError:
            pass
    try:
        return install_cli_link()
    except Exception:
        return None
