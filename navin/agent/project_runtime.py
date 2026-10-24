# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic command resolver: which command does each action run, and why.

The model must not invent commands. This turns what the project declares
(project_runbook detectors) into standard actions - install, build, start,
stop, test, lint, typecheck, migrate... - per unit (the root, a package of a
monorepo, a backend folder), ranks the candidates and keeps where each one
comes from:

1. validated - ran successfully here before (``.navin/commands.json``), as
   long as the unit's manifests did not change since;
2. declared - a script the project defines (package.json, Makefile,
   Taskfile, justfile, deno tasks, compose, start.sh...);
3. framework - the official convention of the detected framework;
4. ci - what the project's CI runs;
5. docs - what the README shows.

Each candidate also says whether its program is installed, so a missing
``pnpm`` is reported with how to get it instead of failing three times.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from navin.agent import project_runbook as rb

ACTIONS = (
    "install", "build", "start", "stop", "test", "e2e", "lint", "typecheck", "format",
    "migrate", "preview", "status", "logs", "deploy",
)
_RANK = {"validated": 0, "declared": 1, "framework": 2, "ci": 3, "docs": 4}
_CONFIDENCE = {"validated": "validated", "declared": "high", "framework": "medium", "ci": "medium", "docs": "low"}
_DECLARED_STACKS = frozenset({"Node", "Deno", "Make/just", "Taskfile", "Scripts", "Docker Compose", "Monorepo", "Tauri"})
_TARGET_ACTIONS = {
    "dev": "start", "run": "start", "serve": "start", "start": "start", "up": "start",
    "build": "build", "all": "build", "test": "test", "check": "test", "lint": "lint", "format": "format",
    "fmt": "format", "typecheck": "typecheck", "install": "install", "setup": "install",
    "deps": "install", "stop": "stop", "down": "stop", "migrate": "migrate",
    "deploy": "deploy", "logs": "logs",
}
_LABEL_ACTIONS = {
    "install": "install", "configure": "install", "dev server": "start", "run": "start",
    "start services": "start", "desktop app dev": "start", "build": "build",
    "desktop app build": "build", "test": "test", "e2e": "e2e", "lint": "lint",
    "type check": "typecheck", "format": "format", "migrate": "migrate",
    "preview build": "preview", "stop services": "stop", "status": "status", "logs": "logs",
    "deploy": "deploy", "kubernetes status": "status",
}
INSTALL_HINTS = {
    "pnpm": "corepack enable pnpm (or npm install -g pnpm)",
    "yarn": "corepack enable yarn (or npm install -g yarn)",
    "bun": "curl -fsSL https://bun.sh/install | bash",
    "npm": "install Node.js (https://nodejs.org) - npm comes with it",
    "npx": "install Node.js (https://nodejs.org) - npx comes with it",
    "node": "install Node.js (https://nodejs.org)",
    "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh (or pip install uv)",
    "poetry": "pipx install poetry",
    "pipenv": "pipx install pipenv",
    "hatch": "pipx install hatch",
    "python": "install Python 3 (https://www.python.org)",
    "go": "install Go (https://go.dev/dl)",
    "cargo": "curl https://sh.rustup.rs -sSf | sh",
    "mvn": "install Maven (https://maven.apache.org) or add the ./mvnw wrapper",
    "gradle": "install Gradle (https://gradle.org) or add the ./gradlew wrapper",
    "dotnet": "install the .NET SDK (https://dot.net)",
    "php": "install PHP (https://www.php.net)",
    "composer": "install Composer (https://getcomposer.org)",
    "bundle": "gem install bundler",
    "deno": "curl -fsSL https://deno.land/install.sh | sh",
    "flutter": "install Flutter (https://docs.flutter.dev/get-started/install)",
    "dart": "install the Dart SDK (https://dart.dev/get-dart)",
    "mix": "install Elixir (https://elixir-lang.org/install.html)",
    "docker": "install Docker (https://docs.docker.com/get-docker)",
    "make": "install make (apt install make / xcode-select --install / choco install make)",
    "just": "cargo install just (or the package manager of the OS)",
    "task": "go install github.com/go-task/task/v3/cmd/task@latest (or brew install go-task)",
    "cmake": "install CMake (https://cmake.org/download)",
    "ctest": "install CMake (https://cmake.org/download)",
    "kubectl": "install kubectl (https://kubernetes.io/docs/tasks/tools)",
    "helm": "install Helm (https://helm.sh/docs/intro/install)",
    "terraform": "install Terraform (https://developer.hashicorp.com/terraform/install)",
}
_SHELL_WORDS = frozenset({"cd", "bash", "sh", "pwsh", "powershell", "cmd", "env"})
STORE_NAME = "commands.json"
# shutil.which walks every PATH entry; under WSL that includes the mounted
# Windows folders and costs ~50 ms per program. Cached for a while.
_WHICH_TTL_S = 600.0
_WHICH_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}


_PATH_INDEX: dict[str, tuple[float, dict[str, list[str]]]] = {}


def _path_index(path_value: str) -> dict[str, list[str]]:
    """Program name -> PATH folders holding it, listed once per PATH and TTL.

    One listing per folder instead of one stat per folder and per program:
    on WSL the Windows folders (/mnt/c...) answer slowly, and the 31 cloud CLI
    lookups took 3.4 s (the project map then missed its 2.5 s budget and was
    dropped) against 0.44 s for listing the whole PATH once.
    """
    now = time.monotonic()
    hit = _PATH_INDEX.get(path_value)
    if hit is not None and now - hit[0] < _WHICH_TTL_S:
        return hit[1]
    index: dict[str, list[str]] = {}
    for folder in path_value.split(os.pathsep):
        if not folder:
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            index.setdefault(name, []).append(folder)
    _PATH_INDEX.clear()
    _PATH_INDEX[path_value] = (now, index)
    return index


def which(program: str) -> str | None:
    path_value = os.environ.get("PATH", "")
    key = (program, path_value)
    now = time.monotonic()
    hit = _WHICH_CACHE.get(key)
    if hit is not None and now - hit[0] < _WHICH_TTL_S:
        return hit[1]
    found = _find_program(program, path_value)
    _WHICH_CACHE[key] = (now, found)
    return found


def _find_program(program: str, path_value: str) -> str | None:
    if os.name == "nt" or os.sep in program:
        return shutil.which(program)  # PATHEXT rules, or an explicit path
    for folder in _path_index(path_value).get(program, ()):
        candidate = os.path.join(folder, program)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


@dataclass(frozen=True)
class Candidate:
    action: str
    command: str
    unit: str
    source: str
    label: str = ""
    note: str = ""
    missing_tool: str = ""
    # Folder to run from when it is not the unit itself (deploy commands that
    # already carry their path, e.g. terraform -chdir=deploy/aws, run from ".").
    cwd: str = ""

    @property
    def run_from(self) -> str:
        return self.cwd or self.unit

    @property
    def confidence(self) -> str:
        return _CONFIDENCE.get(self.source, "low")

    @property
    def install_hint(self) -> str:
        return INSTALL_HINTS.get(self.missing_tool, "") if self.missing_tool else ""


@dataclass
class Unit:
    path: str
    kinds: list[str]
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)
    dev_port: int | None = None
    needs_install: bool = False

    def best(self, action: str) -> Candidate | None:
        options = self.candidates.get(action) or []
        return options[0] if options else None


@dataclass
class Profile:
    root: Path
    host: str
    units: list[Unit]
    readme: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)

    def unit(self, path: str | None) -> Unit | None:
        if not path:
            return None
        wanted = path.strip().strip("/") or "."
        return next((u for u in self.units if u.path == wanted), None)

    def unit_for(self, action: str, path: str | None = None) -> Unit | None:
        """The unit an action targets: the one named, else the most likely."""
        if path:
            return self.unit(path)
        having = [u for u in self.units if u.best(action)]
        if not having:
            return None
        if action == "start":
            # A web dev server is what "start the app" means most of the time.
            web = [u for u in having if u.dev_port]
            if web:
                return web[0]
        root = next((u for u in having if u.path == "."), None)
        return root or having[0]


# --- helpers -----------------------------------------------------------------------


def _action_for(label: str) -> str | None:
    lowered = label.strip().lower()
    if lowered in _LABEL_ACTIONS:
        return _LABEL_ACTIONS[lowered]
    if lowered.endswith((" deploy", " apply")):
        return "deploy"  # Helm/Kubernetes/Terraform/Vercel/Fly/... deploy rows
    for prefix in ("make ", "just ", "task "):
        if lowered.startswith(prefix):
            return _TARGET_ACTIONS.get(lowered[len(prefix):].split(":")[0])
    if lowered.startswith("all packages "):
        return _TARGET_ACTIONS.get(lowered.split()[-1])
    if lowered.startswith("task "):
        return _TARGET_ACTIONS.get(lowered.split()[-1])
    if lowered.startswith("script "):
        stem = lowered.split()[-1].split(".")[0]
        return _TARGET_ACTIONS.get(stem)
    return None


def program_of(command: str) -> str:
    """The program a command needs first (``pnpm`` for ``pnpm run dev``)."""
    for part in command.replace("&&", ";").split(";"):
        words = part.split()
        while words and ("=" in words[0] and not words[0].startswith(("-", "/", "."))):
            words.pop(0)  # FOO=bar prefixes
        if not words or words[0] in _SHELL_WORDS:
            continue
        word = words[0]
        if word in ("python", "python3") and len(words) > 2 and words[1] == "-m" and words[2] == "venv":
            return "python"
        return word
    return ""


def _runs_in_wsl(folder: Path) -> bool:
    if sys.platform != "win32":
        return False
    from navin.utils import wsl

    return wsl.parse_unc(str(folder)) is not None


def _missing(command: str, folder: Path) -> str:
    """The program the command needs and the host lacks, or ""."""
    program = program_of(command)
    if not program:
        return ""
    if _runs_in_wsl(folder):
        # Windows Desktop + \\wsl.localhost project: exec runs the command in
        # the distro, so the Windows PATH says nothing about npm/pnpm/cargo
        # there. Let it run; a real miss comes back as "command not found".
        return ""
    if program.startswith((".", "/")) or "\\" in program:
        return "" if (folder / program).exists() or Path(program).exists() else program
    if program in ("bin/rails",):
        return "" if (folder / "bin" / "rails").exists() else "rails"
    if program in ("python", "python3") and (which("python") or which("python3") or which("py")):
        return ""
    return "" if which(program) else program


def unit_signature(folder: Path) -> str:
    """What makes a validated command trustworthy: the unit's manifests, unchanged."""
    digest = hashlib.sha1()
    for name in rb.MANIFESTS + ("pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb", "uv.lock", "poetry.lock"):
        path = folder / name
        if not path.is_file():
            continue
        digest.update(name.encode())
        if name.endswith(("lock.yaml", ".lock", "lock.json", ".lockb")):
            continue  # presence is enough: lockfiles churn on every install
        try:
            digest.update(path.read_bytes()[:200_000])
        except OSError:
            continue
    return digest.hexdigest()[:16]


# --- validated commands (.navin/commands.json) ---------------------------------------


def store_path(root: Path) -> Path:
    return root / ".navin" / STORE_NAME


def _load_store(root: Path) -> dict[str, Any]:
    try:
        data = json.loads(store_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "units": {}}
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict):
        return {"version": 1, "units": {}}
    return data


def _save_store(root: Path, data: dict[str, Any]) -> None:
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def remember(root: Path | str, unit: str, action: str, command: str) -> None:
    """Record a command that just worked, tied to the unit's current manifests."""
    root = Path(root)
    folder = root if unit == "." else root / unit
    data = _load_store(root)
    entry = data["units"].setdefault(unit, {})
    signature = unit_signature(folder)
    if entry.get("signature") != signature:
        entry.clear()
        entry["signature"] = signature
    entry.setdefault("actions", {})[action] = {
        "command": command,
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_store(root, data)


def forget(root: Path | str, unit: str, action: str) -> None:
    """Drop a validated command that failed: it is no longer proven."""
    root = Path(root)
    data = _load_store(root)
    actions = (data["units"].get(unit) or {}).get("actions") or {}
    if actions.pop(action, None) is not None:
        _save_store(root, data)


def _validated(root: Path, unit: str, folder: Path) -> dict[str, str]:
    entry = _load_store(root)["units"].get(unit) or {}
    if entry.get("signature") != unit_signature(folder):
        return {}  # the manifests changed since: re-resolve
    return {
        action: str(item.get("command") or "")
        for action, item in (entry.get("actions") or {}).items()
        if isinstance(item, dict) and item.get("command")
    }


# --- resolution ------------------------------------------------------------------------


def _clean(command: str) -> tuple[str, str]:
    """Split the runbook's inline note ("cmd   <- note") from the command."""
    if "   <- " in command:
        command, note = command.split("   <- ", 1)
        return command.strip(), note.strip()
    return command.strip(), ""


def _next_needs_build(folder: Path, command: str) -> bool:
    if not command.endswith(" start") and command != "npm start":
        return False
    scripts = rb._json(folder / "package.json").get("scripts") or {}
    start = str(scripts.get("start") or "") if isinstance(scripts, dict) else ""
    return "next start" in start and not (folder / ".next").is_dir()


def resolve(root: Path | str) -> Profile:
    """Every unit of the project with its ranked candidates per action."""
    root = Path(root).expanduser().resolve()
    return rb.bridged_memo("profile", root, lambda: _resolve_uncached(root))


def _resolve_uncached(root: Path) -> Profile:
    units: list[Unit] = []
    for folder in rb._project_dirs(root):
        where = "." if folder == root else folder.relative_to(root).as_posix()
        unit = Unit(path=where, kinds=[])
        found: list[Candidate] = []
        for name, markers, detector in rb._STACKS:
            if not rb._has_any(folder, markers):
                continue
            rows = detector(folder)
            if not rows:
                continue
            unit.kinds.append(name)
            for label, raw in rows:
                action = _action_for(label)
                if not action:
                    continue
                command, note = _clean(raw)
                if "<app-name>" in command or command.startswith("render.yaml"):
                    continue
                source = "declared" if name in _DECLARED_STACKS and not command.startswith("npx ") else "framework"
                if action == "install" and note:
                    unit.needs_install = True
                if action == "start" and _next_needs_build(folder, command):
                    note = "production server: needs a build first (or use the dev script)"
                found.append(Candidate(action, command, where, source, label=label, note=note,
                                       missing_tool=_missing(command, folder)))
        if rb._dotnet_present(folder):
            unit.kinds.append(".NET")
            for label, command in rb._dotnet(folder):
                action = _action_for(label)
                if action:
                    found.append(Candidate(action, command, where, "framework", label=label,
                                           missing_tool=_missing(command, folder)))
        validated = _validated(root, where, folder)
        if not unit.kinds and not validated:
            continue
        if not unit.kinds:
            unit.kinds.append("validated commands")
        for action, command in validated.items():
            found = [c for c in found if not (c.action == action and c.command == command)]
            found.append(Candidate(action, command, where, "validated", label="validated",
                                   missing_tool=_missing(command, folder)))
        # A port only for real dev servers: a worker or a script is started in
        # the background, not waited on as a web page.
        if any(c.action == "start" and c.label in ("Dev server", "validated") for c in found):
            port = rb._dev_port(folder)
            unit.dev_port = int(port) if port.isdigit() else None
        for candidate in found:
            unit.candidates.setdefault(candidate.action, []).append(candidate)
        for action, options in unit.candidates.items():
            # Stable sort: the detector's own order stays the tie-breaker.
            options.sort(key=lambda c: (_RANK.get(c.source, 9), bool(c.missing_tool)))
        units.append(unit)
    by_path = {unit.path: unit for unit in units}
    for where, rows in rb._infra_rows(root):
        for label, raw in rows:
            action = _action_for(label)
            if action not in ("deploy", "status"):
                continue
            command, note = _clean(raw)
            if "<app-name>" in command:
                continue
            unit = by_path.get(where)
            if unit is None:
                unit = by_path[where] = Unit(path=where, kinds=["infrastructure"])
                units.append(unit)
            elif "infrastructure" not in unit.kinds:
                unit.kinds.append("infrastructure")
            folder = root if where == "." else root / where
            carries_path = where != "." and (f" {where}" in command or f"={where}" in command)
            unit.candidates.setdefault(action, []).append(
                Candidate(action, command, where, "framework", label=label, note=note,
                          missing_tool=_missing(command, root if carries_path else folder),
                          cwd="." if carries_path else "")
            )
    return Profile(
        root=root,
        host=rb.host_line(),
        units=units,
        readme=rb.readme_commands(root),
        ci=rb._ci_commands(root),
    )


def with_source(candidate: Candidate, source: str) -> Candidate:
    return replace(candidate, source=source)
