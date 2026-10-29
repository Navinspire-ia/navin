# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One timely nudge toward native apps, once the web project stands.

App Factory turns a web or full-stack project into desktop (Tauri 2) and
mobile (Capacitor 8) apps. Offered on the first message it is noise; offered
every turn it is nagging. The agent gets the hint exactly once per project,
and only when the project is clearly a web app that has already been worked
on (a few dozen commits, or a continuity brief from earlier sessions), has no
native target yet, and was never nudged before. The agent then closes its
reply with one sentence; the user decides, and the subject is never raised
again by Navin itself (Other > Mobile apps / Desktop apps stays in the rail).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.utils.proc import no_window_kwargs

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

# Dependencies that make a package.json a web front end.
WEB_FRAMEWORKS = (
    "react", "react-dom", "next", "vue", "nuxt", "svelte", "@sveltejs/kit", "vite",
    "@angular/core", "solid-js", "preact", "astro", "@remix-run/react", "@builder.io/qwik", "lit",
)
# Where that package.json usually lives (root first, then the common sub-folders).
FRONTEND_DIRS = ("", "webui", "frontend", "client", "web", "app", "site", "ui", "apps/web", "packages/web")
# A project that already ships natively needs no nudge.
NATIVE_MARKERS = (
    "os/android", "os/ios", "os/windows", "os/macos", "os/ubuntu", "os/centos", "os/appimage",
    "src-tauri", "android", "ios", "capacitor.config.ts", "capacitor.config.json",
    "capacitor.config.js", "tauri.conf.json", "electron-builder.yml", "pubspec.yaml",
)
# "Stands": enough history that the web app is a thing, not a scaffold.
MIN_COMMITS = 15
_GIT_TIMEOUT_S = 2.0
_COMMITS_TTL_S = 600.0

_commit_cache: dict[str, tuple[float, int | None]] = {}
_offered_cache: set[str] = set()
_lock = threading.Lock()


def marker_path() -> Path:
    """The user-level record of the projects already nudged (never inside the project)."""
    from navin.config.paths import get_data_dir

    return get_data_dir() / "appfactory-hints.json"


def web_frontend(root: Path) -> str | None:
    """The folder of the web front end ("" for the root), or None when there is none."""
    for rel in FRONTEND_DIRS:
        manifest = root / rel / "package.json" if rel else root / "package.json"
        try:
            if not manifest.is_file():
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        deps: set[str] = set()
        for key in ("dependencies", "devDependencies"):
            block = data.get(key)
            if isinstance(block, dict):
                deps.update(str(name) for name in block)
        if any(name in deps for name in WEB_FRAMEWORKS):
            return rel
    return None


def has_native_target(root: Path) -> bool:
    try:
        return any((root / rel).exists() for rel in NATIVE_MARKERS)
    except OSError:
        # An unreadable or vanished workspace: nothing to nudge about.
        return False


def _git_commit_count(root: Path) -> int | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return None


def commit_count(root: Path) -> int | None:
    """Commits on HEAD, cached ten minutes per project; None when not a git repo."""
    key = str(root)
    now = time.monotonic()
    with _lock:
        cached = _commit_cache.get(key)
        if cached is not None and now - cached[0] < _COMMITS_TTL_S:
            return cached[1]
    count = _git_commit_count(root)
    with _lock:
        _commit_cache[key] = (now, count)
    return count


def project_stands(root: Path, commits: int | None) -> bool:
    """Worked on for real: a few dozen commits, or a continuity brief from earlier sessions."""
    if commits is not None and commits >= MIN_COMMITS:
        return True
    try:
        return (root / ".navin" / "continuity" / "RESUME.md").is_file()
    except OSError:
        return False


def _read_marker(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def already_offered(root: Path, *, marker: Path | None = None) -> bool:
    key = str(root)
    with _lock:
        if key in _offered_cache:
            return True
    offered = key in _read_marker(marker or marker_path())
    if offered:
        with _lock:
            _offered_cache.add(key)
    return offered


def record_offered(root: Path, *, marker: Path | None = None) -> None:
    """Remember the nudge before it is shown, so a crash mid-turn cannot repeat it."""
    path = marker or marker_path()
    data = _read_marker(path)
    data[str(root)] = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning("App Factory hint: could not record the nudge in {}: {}", path, exc)
    with _lock:
        _offered_cache.add(str(root))


def appfactory_hint_lines(
    root: Path,
    *,
    commits: int | None = None,
    marker: Path | None = None,
) -> list[str]:
    """The one-time nudge for ``root``, or [] (not a web app, native already, too early, or done).

    ``commits`` overrides the git lookup (tests); ``marker`` the record file.
    Every step reads files or runs git and handles its own OSError / ValueError /
    SubprocessError, so a missing or unreadable project yields [] rather than
    an exception.
    """
    root = Path(root)
    if already_offered(root, marker=marker) or has_native_target(root):
        return []
    frontend = web_frontend(root)
    if frontend is None:
        return []
    count = commits if commits is not None else commit_count(root)
    if not project_stands(root, count):
        return []
    record_offered(root, marker=marker)
    where = f"in {frontend}/" if frontend else "at the project root"
    history = f"{count} commits" if count is not None and count >= MIN_COMMITS else "earlier sessions of work"
    return [
        "App Factory, one-time nudge (this line is shown once for this project and never again): "
        f"the project is a web app ({where}) that already stands ({history}) and has no native target yet.",
        "At the END of your reply, once the user's request is fully handled, offer in ONE sentence "
        "to turn it into desktop (Tauri 2: Windows, macOS, Linux) and mobile (Capacitor 8: Android, iOS) "
        "apps with the appfactory tool, without touching the website or the backend, and mention that "
        "Other > Mobile apps / Desktop apps in the Code rail does the same.",
        "Do not start anything, do not open a question card, do not repeat the offer in later turns: "
        "if the user wants it they will say so.",
    ]


async def appfactory_hint_context_provider(request: RequestContext) -> RuntimeContextBlock | None:
    """The nudge as a runtime-context block, computed off the loop.

    ``appfactory_hint_lines`` handles its own file and git errors and answers
    [] for them; anything else is a bug and surfaces like one.
    """
    workspace = request.workspace
    if workspace is None:
        return None
    lines = await asyncio.to_thread(appfactory_hint_lines, Path(workspace))
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="appfactory_hint", content=content)
