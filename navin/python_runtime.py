# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Access to the Python interpreter navin runs on, packaged or not.

A source install hands the agent a real interpreter: the ``exec`` tool prepends
``.venv/bin`` to PATH, so ``python3 report.py`` inside a skill sees python-docx,
openpyxl, reportlab and everything else navin depends on. A packaged build has
the same libraries - PyInstaller embeds CPython and the whole dependency set -
but no interpreter to name, because ``sys.executable`` is the navin executable.

That gap is what makes document generation, spreadsheet analysis and every skill
that writes a short script fail in a packaged build while working from source.
The ``navin python`` subcommand closes it by running scripts inside this process,
and the shims below let ``python`` and ``python3`` mean exactly that on PATH.

Installing packages is a different question: see ``external_python()``, which
looks for an interpreter that owns a writable environment.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

_SHIM_NAMES = ("python", "python3")


def packaged() -> bool:
    """Whether this process is a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def python_command() -> list[str]:
    """The argv prefix that runs Python code with navin's own libraries."""
    if not packaged():
        return [sys.executable]
    executable = Path(sys.executable)
    if os.name == "nt":
        # The Windows build ships two executables from one bundle. Navin.exe is
        # windowed, which means no stdio at all: a script run through it would
        # print into nothing. navin-cli.exe is the console one.
        console = executable.with_name("navin-cli.exe")
        if console.is_file():
            executable = console
    return [str(executable), "python"]


def bundled_tool(name: str) -> str | None:
    """Path of a helper program shipped inside a packaged build, or None.

    Some quality tools are native programs rather than importable modules - ruff
    is a Rust binary that ``python -m ruff`` merely locates and executes - so the
    build copies them into a ``tools`` directory inside the bundle. A tool found
    on PATH always wins over this one: it belongs to the project being worked on
    and matches its configuration.
    """
    if not packaged():
        return None
    root = getattr(sys, "_MEIPASS", "")
    if not root:
        return None
    candidate = Path(root) / "tools" / (f"{name}.exe" if os.name == "nt" else name)
    return str(candidate) if candidate.is_file() else None


def bundled_node_package(name: str) -> Path | None:
    """Root of a node package shipped inside a packaged build, or None.

    Some tooling navin drives is JavaScript rather than a program or a Python
    module: ``tsc`` is a script the interpreter runs. The build stages those
    under ``tools/node_modules`` so that they are laid out like a real package
    root, which also lets ``require()`` resolve them with ``tools`` as a module
    directory. Like every other bundled tool, a copy belonging to the project
    wins over this one.
    """
    if not packaged():
        return None
    root = getattr(sys, "_MEIPASS", "")
    if not root:
        return None
    candidate = Path(root) / "tools" / "node_modules" / name
    return candidate if (candidate / "package.json").is_file() else None


def module_available(module: str) -> bool:
    """Whether ``python -m <module>`` can run in this installation."""
    if not module:
        return False
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def external_python() -> str | None:
    """An interpreter that can install packages, or None.

    Navin's own interpreter cannot: a packaged build has no ``pip`` and unpacks
    itself read-only into a temporary directory, so anything installed there is
    gone at the next start. Installing a CLI tool for the user belongs to the
    user's own Python anyway.
    """
    if not packaged():
        return sys.executable
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and Path(found).resolve() != Path(sys.executable).resolve():
            return found
    return None


def _shim_body(command: list[str]) -> str:
    if os.name == "nt":
        quoted = " ".join(f'"{part}"' for part in command)
        return f"@echo off\r\n{quoted} %*\r\n"
    quoted = " ".join(f'"{part}"' for part in command)
    return f'#!/bin/sh\nexec {quoted} "$@"\n'


def interpreter_shim_dir(root: Path) -> Path | None:
    """Write ``python``/``python3`` shims that forward to this build.

    Returns the directory to prepend to PATH, or None when the current
    installation already exposes a usable interpreter directory (a source
    install, where ``.venv/bin`` does the job).

    The shims are rewritten on every call: they name an absolute executable
    path, and that path changes when the user moves or updates the application.
    """
    if not packaged():
        return None
    command = python_command()
    suffix = ".cmd" if os.name == "nt" else ""
    try:
        root.mkdir(parents=True, exist_ok=True)
        for name in _SHIM_NAMES:
            shim = root / f"{name}{suffix}"
            shim.write_text(_shim_body(command), encoding="utf-8")
            if os.name != "nt":
                shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        # A read-only or full home directory is not a reason to refuse to run a
        # command; the agent simply gets the PATH it had before.
        return None
    return root
