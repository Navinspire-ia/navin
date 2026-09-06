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

import os
import sys
from pathlib import Path


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
        Path("/home/linuxbrew/.linuxbrew/bin"),
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
    if installed:
        candidates.append(installed[0] / "bin")
    return candidates


def _windows_candidates(home: Path) -> list[Path]:
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    candidates = [
        home / ".local" / "bin",  # uv/uvx
        home / ".cargo" / "bin",
        home / ".bun" / "bin",
    ]
    if appdata:
        candidates.append(Path(appdata) / "npm")  # npx.cmd des installs npm -g
    if localappdata:
        candidates.append(Path(localappdata) / "Microsoft" / "WinGet" / "Links")
        candidates.append(Path(localappdata) / "Volta" / "bin")
        # uv / uvx from the official Windows installer or a portable unpack.
        candidates.append(Path(localappdata) / "uv")
        candidates.append(Path(localappdata) / "Programs" / "uv")
    candidates.append(home / "scoop" / "shims")
    if program_files:
        candidates.append(Path(program_files) / "nodejs")
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
