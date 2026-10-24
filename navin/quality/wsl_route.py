# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run linters and test runners inside WSL for a project seen from Windows.

The Windows desktop opens a project living in a distribution through
``\\\\wsl.localhost\\<distro>\\...``. Its tools are Linux tools: the virtualenv
holds ELF binaries next to a ``python3`` symlink into ``/usr/bin``, and
``node_modules/.bin`` holds shell shims. Windows cannot launch any of them, so
``lint`` and ``test_run`` fell back to Navin's own Windows interpreter, which
either lacked the project's dependencies (pytest: "No module named ...") or read
every file through the 9P bridge. Inside the distribution the same command reads
ext4 directly and finds the project's own toolchain, so that is where it runs.

Binary resolution happens in one shell inside the distribution, in the same
order the Windows side uses: the spec's project paths, the project's
virtualenvs (root, then one level down), then the login shell's PATH. Exit code
127 means none was found.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from navin.utils import wsl

# Exit code of the resolver when the tool exists nowhere in the distribution.
MISSING_EXIT = 127

_VENV_NAMES = (".venv", "venv", "env")


def wsl_root(root: Path | str, *, platform: str | None = None) -> wsl.WslLocation | None:
    """The distribution location of ``root`` when it must run through wsl.exe."""
    if (platform or sys.platform) != "win32":
        return None
    location = wsl.parse_unc(str(root))
    if location is None or not wsl.wsl_executable():
        return None
    return location


def linux_path(path: Path | str, location: wsl.WslLocation) -> str:
    """Spell ``path`` the way the distribution sees it.

    A path under the project (UNC) becomes its Linux path, a Windows drive path
    (a report file in %TEMP%) becomes ``/mnt/<drive>/...``, and a relative path
    is left relative: the command runs from the project through ``--cd``.
    """
    text = str(path)
    inside = wsl.parse_unc(text)
    if inside is not None:
        return inside.posix
    mounted = wsl.drive_to_mount(text)
    if mounted is not None:
        return mounted
    return text.replace("\\", "/")


def _find_lines(spec: dict[str, Any], linux_root: str) -> list[str]:
    """Shell lines leaving the tool's path in ``$tool`` (empty when missing)."""
    binary = spec.get("binary") or {}
    name = str(binary.get("name") or "")
    module = str(binary.get("run_as_module") or "")
    python_env = bool(binary.get("python_env")) or bool(module)
    q = shlex.quote
    lines = ["tool="]
    for rel in binary.get("project_paths", []) or []:
        candidate = str(PurePosixPath(linux_root) / str(rel))
        lines.append(f'[ -z "$tool" ] && [ -x {q(candidate)} ] && tool={q(candidate)}')
    if python_env and name:
        # Root virtualenvs first, then one level down (backend/.venv).
        for venv in _VENV_NAMES:
            lines.append(
                f'[ -z "$tool" ] && [ -x {q(linux_root)}/{venv}/bin/{q(name)} ] '
                f"&& tool={q(linux_root)}/{venv}/bin/{q(name)}"
            )
        globs = " ".join(f"{q(linux_root)}/*/{venv}/bin/{q(name)}" for venv in _VENV_NAMES)
        lines.append(f'if [ -z "$tool" ]; then for c in {globs}; do [ -x "$c" ] && tool=$c && break; done; fi')
    if name:
        lines.append(f'[ -z "$tool" ] && tool=$(command -v {q(name)} 2>/dev/null)')
    return lines


def _module_pythons(linux_root: str) -> str:
    q = shlex.quote
    venvs = " ".join(f"{q(linux_root)}/{venv}/bin/python3" for venv in _VENV_NAMES)
    return f"{venvs} $(command -v python3 2>/dev/null)"


def _resolver_script(spec: dict[str, Any], linux_root: str) -> str:
    module = str((spec.get("binary") or {}).get("run_as_module") or "")
    q = shlex.quote
    lines = _find_lines(spec, linux_root)
    if module:
        # `python -m pytest` from the interpreter next to the tool keeps the
        # project's virtualenv (and the cwd on sys.path); a module installed
        # without its console script is still found through a venv python.
        lines.append(
            'if [ -n "$tool" ]; then d=$(dirname "$tool"); '
            'for py in "$d/python3" "$d/python"; do '
            f'[ -x "$py" ] && exec "$py" -m {q(module)} "$@"; done; fi'
        )
        lines.append(
            f'for py in {_module_pythons(linux_root)}; do '
            f'[ -x "$py" ] && "$py" -c {q("import " + module)} 2>/dev/null '
            f'&& exec "$py" -m {q(module)} "$@"; done'
        )
    lines.append(f'[ -n "$tool" ] || exit {MISSING_EXIT}')
    lines.append('exec "$tool" "$@"')
    return "export PYTHONUNBUFFERED=1\n" + "\n".join(lines)


def probe_script(specs: dict[str, dict[str, Any]], linux_root: str) -> str:
    """One shell printing the name of every spec whose tool the distribution has.

    One wsl.exe start for the whole table instead of one per tool.
    """
    q = shlex.quote
    lines: list[str] = []
    for key, spec in specs.items():
        lines.extend(_find_lines(spec, linux_root))
        module = str((spec.get("binary") or {}).get("run_as_module") or "")
        if module:
            lines.append(
                f'if [ -z "$tool" ]; then for py in {_module_pythons(linux_root)}; do '
                f'[ -x "$py" ] && "$py" -c {q("import " + module)} 2>/dev/null && tool=$py && break; '
                "done; fi"
            )
        lines.append(f'[ -n "$tool" ] && echo {q(key)}')
    lines.append("exit 0")
    return "\n".join(lines)


def available(
    specs: dict[str, dict[str, Any]], root: Path, location: wsl.WslLocation, *, timeout: float = 30,
) -> set[str] | None:
    """Names of ``specs`` whose tool exists in the distribution, or None on failure."""
    import subprocess

    from navin.utils.proc import no_window_kwargs

    distro = wsl.resolve_distro(location.distro) or location.distro
    argv = [*wsl.command_prefix(distro, location.path), "bash", "-lc", probe_script(specs, location.posix)]
    try:
        out = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=launch_cwd(), **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip() in specs}


def tool_argv(
    spec: dict[str, Any], root: Path, workdir: Path, args: list[str],
    *, location: wsl.WslLocation | None = None,
) -> list[str] | None:
    """Full argv running the spec's tool inside the distribution, or None.

    ``args`` are the tool's arguments, already spelled for Linux (see
    :func:`linux_path`). The login shell is what puts the distribution's own
    toolchain (nvm, pyenv, ~/.local/bin) on PATH.
    """
    location = location or wsl_root(root)
    if location is None:
        return None
    work = wsl.parse_unc(str(workdir))
    distro = wsl.resolve_distro(location.distro) or location.distro
    script = _resolver_script(spec, location.posix)
    return [
        *wsl.command_prefix(distro, work.path if work is not None else location.path),
        "bash", "-lc", script, "navin-tool", *args,
    ]


def launch_cwd() -> str | None:
    """Where wsl.exe itself starts: a Windows process cannot start in a UNC folder."""
    return os.environ.get("USERPROFILE") or os.environ.get("SYSTEMROOT") or None
