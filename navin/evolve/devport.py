# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The port a project's dev server listens on, read from the project itself.

Evolve suggests a probe URL before it has booted the app. The engine's
framework table only knows generic defaults (React -> 3000), which is wrong
for a Vite app pinned to 5180 in its config. This reads, in order: the start
command, the package.json script it runs, the Vite config, the env files,
then the defaults of the dev server the folder actually depends on.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# `--port 5180`, `--port=5180`, `-p 5180`, `PORT=5180`, `-b 0.0.0.0:8021`, `--bind :8021`.
_PORT_FLAGS = (
    re.compile(r"(?:^|\s)--port[=\s]+(\d{2,5})\b"),
    re.compile(r"(?:^|\s)-p\s+(\d{2,5})\b"),
    re.compile(r"(?:^|[\s;&])(?:[A-Z_]*_)?PORT=(\d{2,5})\b"),
    re.compile(r"(?:^|\s)(?:-b|--bind)[=\s]+[\w.\-\[\]]*:(\d{2,5})\b"),
)
# `server: { ..., port: 5180 }` in a Vite config (and `preview` is not the dev server).
_VITE_SERVER_PORT = re.compile(r"\bserver\s*:\s*\{[^{}]*?\bport\s*:\s*(\d{2,5})", re.S)
_ENV_PORT = re.compile(r"^\s*(?:export\s+)?(?:VITE_)?PORT\s*=\s*[\"']?(\d{2,5})", re.M)
_CD_PREFIX = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*&&\s*(.+)$", re.S)
_RUN_SCRIPT = re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?([\w:.\-]+)")

# Dev servers by the dependency that provides them, most specific first.
_DEPENDENCY_PORTS: tuple[tuple[str, int], ...] = (
    ("next", 3000),
    ("nuxt", 3000),
    ("@remix-run/dev", 3000),
    ("astro", 4321),
    ("@angular/core", 4200),
    ("@sveltejs/kit", 5173),
    ("vite", 5173),
    ("react-scripts", 3000),
)
_PYTHON_PORTS: tuple[tuple[str, int], ...] = (
    ("uvicorn", 8000),
    ("gunicorn", 8000),
    ("manage.py runserver", 8000),
    ("flask", 5000),
)


@dataclass(frozen=True, slots=True)
class DevPort:
    port: int
    #: Read from the project (command, script, config, env): not a guess.
    explicit: bool
    source: str


def split_cd(command: str | None) -> tuple[str | None, str]:
    """(`equipe`, `npm run dev`) for `cd equipe && npm run dev`."""
    if not command:
        return None, ""
    match = _CD_PREFIX.match(command)
    if not match:
        return None, command.strip()
    folder = match.group(1).strip("\"'")
    return folder, match.group(2).strip()


def _port_in(text: str) -> int | None:
    for pattern in _PORT_FLAGS:
        found = pattern.search(text)
        if found:
            return int(found.group(1))
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _package(folder: Path) -> dict:
    try:
        data = json.loads(_read(folder / "package.json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def detect_dev_port(folder: Path, command: str | None) -> DevPort | None:
    """The dev server port for `command` run in `folder`, best evidence first."""
    command = command or ""
    port = _port_in(command)
    if port:
        return DevPort(port, True, "start command")

    package = _package(folder)
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    script_text = ""
    run = _RUN_SCRIPT.match(command.strip())
    if run:
        name = run.group(1)
        script_text = str(scripts.get(name) or "")
    elif command.strip() in {"npm start", "yarn start", "pnpm start", "bun start"}:
        script_text = str(scripts.get("start") or "")
    if script_text:
        port = _port_in(script_text)
        if port:
            return DevPort(port, True, "package.json script")

    for name in ("vite.config.ts", "vite.config.mts", "vite.config.js", "vite.config.mjs", "vite.config.cjs"):
        found = _VITE_SERVER_PORT.search(_read(folder / name))
        if found:
            return DevPort(int(found.group(1)), True, name)

    for name in (".env.development.local", ".env.local", ".env.development", ".env"):
        found = _ENV_PORT.search(_read(folder / name))
        if found:
            return DevPort(int(found.group(1)), True, name)

    deps: dict = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(package.get(key), dict):
            deps.update(package[key])
    runner = f"{command} {script_text}"
    for dependency, default in _DEPENDENCY_PORTS:
        if dependency in deps:
            return DevPort(default, False, dependency)
    for tool, default in _PYTHON_PORTS:
        if tool in runner:
            return DevPort(default, False, tool)
    return None


def start_folder(root: Path, start: str | None) -> Path:
    """The folder a start command runs in: its `cd` target inside `root`, else `root`."""
    folder, _ = split_cd(start)
    if folder:
        candidate = (root / folder).resolve(strict=False)
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            return root
        if candidate.is_dir():
            return candidate
    return root


def quote_folder(folder: str) -> str:
    return shlex.quote(folder) if re.search(r"\s", folder) else folder
