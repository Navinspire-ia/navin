# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Find the new frontend screens a turn created but never wired into the app.

A full-stack request ("add a dashboard to the billing module") often ends with
the API, the client service and the screen all written, and the screen imported
by nothing: no route, no menu entry, no parent component. Tests and the build
stay green, so the agent reports done and the user finds a feature they cannot
open. This module answers one narrow question before the final answer: which
component files created during this turn does no other source file import?

Misses are the safe side. A bare or aliased import whose last segment matches
counts as wired, file-routed frameworks and auto-imported components are
skipped, tests do not count as wiring, and a tree too large or too slow to scan
within the budget returns nothing rather than a guess.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path

from navin.index.service import is_bridged_root
from navin.utils.proc import no_window_kwargs

# Files that render something a user opens. Plain .ts/.js modules (API
# clients, hooks, stores) are reached through these, so checking the screens
# is enough to catch the unwired feature without flagging every helper.
_COMPONENT_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte")
_MODULE_SUFFIXES = (
    ".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte",
)
_IMPORTER_SUFFIXES = _MODULE_SUFFIXES + (".astro", ".mdx")
_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", "out", "coverage", "storybook-static",
    "target", "vendor", "venv", "__pycache__",
})
_TEST_DIRS = frozenset({
    "__tests__", "__mocks__", "test", "tests", "e2e", "cypress", "playwright", "stories",
})
_TEST_MARKERS = (".test.", ".spec.", ".stories.", ".story.", ".cy.", ".e2e.")
# Entry points are loaded by the bundler or the platform, not by an import.
_ENTRY_STEMS = frozenset({
    "main", "app", "_app", "_document", "root", "entry", "entry-client", "entry-server",
})
# Frameworks whose pages/app/routes folders are wired by the file system.
_FILE_ROUTED_DEPS = frozenset({
    "next", "nuxt", "astro", "gatsby", "@sveltejs/kit", "@remix-run/react",
    "@remix-run/dev", "@react-router/dev", "expo-router", "vite-plugin-pages",
    "unplugin-vue-router", "@tanstack/router-plugin", "@tanstack/router-vite-plugin",
    "@tanstack/react-start", "@solidjs/start", "@analogjs/platform", "vitepress",
    "@docusaurus/core",
})
_FILE_ROUTED_DIRS = frozenset({"pages", "app", "routes"})
# Components registered without an import statement anywhere.
_AUTO_IMPORT_DEPS = frozenset({"nuxt", "@nuxt/kit", "unplugin-vue-components"})

_SPECIFIER = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|\bimport\s+)(["'`])([^"'`\n]+?)\1"""
)
_MAX_IMPORTERS = 20_000
_MAX_READ_BYTES = 512 * 1024
_MAX_REPORTED = 10

# A package.json naming one of these ships screens a user opens.
_UI_FRAMEWORK_DEPS = frozenset({
    "react", "react-dom", "vue", "svelte", "@angular/core", "solid-js", "preact",
    "next", "nuxt", "@sveltejs/kit", "react-native", "expo", "lit", "astro",
    "@builder.io/qwik",
})
# Folders below the root searched for a frontend package.json. A WSL tree seen
# from Windows pays about 20 ms per disk call, so it only looks one level down.
_UNIT_SCAN_DEPTH = 2
_BRIDGED_UNIT_SCAN_DEPTH = 1
_UNIT_SCAN_FOLDERS = 400
_UNITS_TTL_S = 600.0
_units_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
# Backend sources whatever folder they sit in.
_BACKEND_SUFFIXES = (
    ".py", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".ex", ".exs", ".scala",
    ".sql",
)
# Frontend sources whatever folder they sit in.
_FRONTEND_SUFFIXES = _COMPONENT_SUFFIXES + (".css", ".scss", ".sass", ".less", ".html")
_SCRIPT_SUFFIXES = (".ts", ".js", ".mjs", ".cjs", ".mts", ".cts")
# Script folders that run on a server even inside a frontend package.
_SERVER_DIRS = frozenset({
    "server", "servers", "backend", "functions", "supabase", "migrations", "prisma",
})


def unwired_frontend_files(
    root: Path | str,
    changed: Iterable[str],
    *,
    since: float | None = None,
    budget_s: float = 4.0,
) -> list[str]:
    """New component files among ``changed`` that no other source imports.

    ``changed`` holds the paths this turn wrote (relative to ``root`` or
    absolute). A path counts as new when git does not track it, or when its
    first commit is newer than ``since`` (the turn start), so a task committed
    mid-turn is still checked. Returns root-relative POSIX paths, sorted.
    """
    root = Path(root)
    if is_bridged_root(root):
        # A WSL tree seen from Windows: walking it through 9P costs minutes.
        return []
    started = time.monotonic()
    candidates = _only_new(root, _candidates(root, changed), since)
    if not candidates:
        return []
    remaining = {path: _module_keys(path) for path in candidates}
    scanned = 0
    for current, directories, names in os.walk(root):
        directories[:] = [
            name for name in directories if name not in _SKIP_DIRS and not name.startswith(".")
        ]
        rel_dir = os.path.relpath(current, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in names:
            if not name.endswith(_IMPORTER_SUFFIXES):
                continue
            importer = f"{rel_dir}/{name}" if rel_dir else name
            if _is_test(importer):
                continue
            scanned += 1
            if scanned > _MAX_IMPORTERS or time.monotonic() - started > budget_s:
                return []
            text = _read(root / importer)
            if not text:
                continue
            for match in _SPECIFIER.finditer(text):
                for path in _hits(importer, match.group(2), remaining):
                    remaining.pop(path, None)
            if not remaining:
                return []
    return sorted(remaining)[:_MAX_REPORTED]


def build_integration_nudge(paths: list[str]) -> str:
    """System nudge naming the unwired screens and how to finish the job."""
    listed = "\n".join(f"- {path}" for path in paths)
    return (
        "Integration check before the final answer: these frontend files were created "
        "in this turn and no other source file imports them, so the user cannot reach "
        f"them in the app:\n{listed}\n"
        "Wire each one in now, the way the existing screens are registered: router "
        "entry, navigation or menu link, parent screen or module registration. Then "
        "rebuild that frontend unit. If one is reached another way (file-based "
        "routing, a plugin registry, intentionally standalone), say so in one line "
        "instead of editing."
    )


def frontend_units(root: Path | str) -> tuple[str, ...]:
    """Root-relative folders ("" for the root) whose package.json ships a UI framework.

    Cached for ten minutes per root: projects rarely gain a frontend mid-session,
    and both the turn-start reminder and the end-of-turn check ask.
    """
    root = Path(root)
    key = str(root)
    now = time.monotonic()
    cached = _units_cache.get(key)
    if cached is not None and now - cached[0] < _UNITS_TTL_S:
        return cached[1]
    depth_limit = _BRIDGED_UNIT_SCAN_DEPTH if is_bridged_root(root) else _UNIT_SCAN_DEPTH
    found: list[str] = []
    level: list[tuple[str, Path]] = [("", root)]
    for depth in range(depth_limit + 1):
        deeper: list[tuple[str, Path]] = []
        for rel, folder in level:
            manifest = folder / "package.json"
            if manifest.is_file() and _UI_FRAMEWORK_DEPS & _dependencies(manifest):
                found.append(rel)
            if depth == depth_limit:
                continue
            try:
                entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name in _SKIP_DIRS or name in _TEST_DIRS or name.startswith("."):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    deeper.append((f"{rel}/{name}" if rel else name, Path(entry.path)))
        level = deeper[:_UNIT_SCAN_FOLDERS]
    units = tuple(found)
    _units_cache[key] = (now, units)
    return units


def frontend_gap(root: Path | str, changed: Iterable[str]) -> list[str]:
    """Backend files of a turn that changed no frontend file at all.

    Empty when the project has no frontend unit, when any frontend source
    changed, or when no backend source changed. Tests count as neither side.
    """
    root = Path(root)
    units = frontend_units(root)
    if not units:
        return []
    backend: list[str] = []
    for raw in changed:
        rel = _relative(root, str(raw))
        if not rel or _is_test(rel):
            continue
        side = _side(rel, units)
        if side == "front":
            return []
        if side == "back" and rel not in backend:
            backend.append(rel)
    return sorted(backend)[:_MAX_REPORTED]


def build_frontend_gap_nudge(terms: Iterable[str], backend: list[str]) -> str:
    """System nudge for a user-facing request whose turn wrote only backend code."""
    named = ", ".join(terms) or "a screen"
    listed = "\n".join(f"- {path}" for path in backend)
    return (
        f"Integration check before the final answer: the request asks for a user-facing "
        f"part ({named}), and this turn changed backend files only:\n{listed}\n"
        "The user cannot see or click a backend. Finish the frontend side now, the way "
        "the existing features are built: the client call to the new API, the screen or "
        "component, its route and its menu or navigation entry, every button and action "
        "the request implies wired to a real handler, loading, empty and error states, "
        "and the i18n strings in every language the project ships. Then build the "
        "frontend unit. If the existing frontend already covers it with no change, say "
        "so in one line and name the file that does."
    )


def _side(rel: str, units: tuple[str, ...]) -> str:
    """"front", "back" or "" (config, docs, data) for one root-relative path."""
    if rel.endswith(_FRONTEND_SUFFIXES):
        return "front"
    if rel.endswith(_BACKEND_SUFFIXES):
        return "back"
    if not rel.endswith(_SCRIPT_SUFFIXES):
        return ""
    unit = next(
        (unit for unit in units if not unit or rel == unit or rel.startswith(f"{unit}/")),
        None,
    )
    if unit is None:
        # A Node service beside the frontend (backend/, api/ with its own manifest).
        return "back"
    folders = (rel[len(unit) + 1:] if unit else rel).split("/")[:-1]
    if _SERVER_DIRS.intersection(folders):
        return "back"
    # Next.js and similar keep their API routes inside the frontend package.
    for index, folder in enumerate(folders[:-1]):
        if folder in {"app", "pages"} and folders[index + 1] == "api":
            return "back"
    return "front"


def _candidates(root: Path, changed: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in changed:
        rel = _relative(root, str(raw))
        if not rel or rel in out or not rel.endswith(_COMPONENT_SUFFIXES) or _is_test(rel):
            continue
        parts = rel.split("/")
        if any(part in _SKIP_DIRS or part.startswith(".") for part in parts[:-1]):
            continue
        name = parts[-1]
        stem = _strip_ext(name)
        if name.startswith(("+", "_")) or stem.lower() in _ENTRY_STEMS:
            continue
        package_dir, deps = _package_info(root, rel)
        if deps & _AUTO_IMPORT_DEPS:
            continue
        inner = rel[len(package_dir) + 1:] if package_dir else rel
        folders = inner.split("/")[:-1]
        if stem == "index" and folders in ([], ["src"]):
            continue
        if deps & _FILE_ROUTED_DEPS and _FILE_ROUTED_DIRS.intersection(folders):
            continue
        if (root / rel).is_file():
            out.append(rel)
    return out


def _relative(root: Path, raw: str) -> str:
    path = Path(raw)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            return ""
    rel = path.as_posix()
    while rel.startswith("./"):
        rel = rel[2:]
    return "" if rel.startswith("../") or rel == "." else rel


def _only_new(root: Path, paths: list[str], since: float | None) -> list[str]:
    if not paths:
        return []
    tracked = _git_paths(root, ["ls-files", "-z", "--", *paths])
    if tracked is None:
        return paths
    if since is not None and tracked:
        # Git's raw "@<epoch> <tz>" form: an ISO stamp with microseconds was
        # misread and counted the base commit as added this turn.
        added = _git_paths(
            root,
            ["log", f"--since=@{int(since)} +0000", "--diff-filter=A", "--name-only", "-z",
             "--relative", "--format=", "--", *sorted(tracked)],
        )
        tracked -= added or set()
    return [path for path in paths if path not in tracked]


def _git_paths(root: Path, args: list[str]) -> set[str] | None:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args], cwd=root, capture_output=True, timeout=5, check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {
        os.fsdecode(item).strip().replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item.strip()
    }


def _package_info(root: Path, rel: str) -> tuple[str, frozenset[str]]:
    """Nearest package.json folder (root-relative) and its dependency names."""
    parts = rel.split("/")[:-1]
    while True:
        package_dir = "/".join(parts)
        manifest = root / package_dir / "package.json" if package_dir else root / "package.json"
        if manifest.is_file():
            return package_dir, _dependencies(manifest)
        if not parts:
            return "", frozenset()
        parts.pop()


def _dependencies(manifest: Path) -> frozenset[str]:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    names: set[str] = set()
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(field)
        if isinstance(block, dict):
            names.update(key for key in block if isinstance(key, str))
    return frozenset(names)


def _module_keys(rel: str) -> tuple[frozenset[str], str]:
    """Exact import targets for a file, plus the name a bare or alias import ends with."""
    base = _strip_ext(rel)
    parent, _, stem = base.rpartition("/")
    if stem == "index" and parent:
        return frozenset({base, parent}), parent.rpartition("/")[2]
    return frozenset({base}), stem


def _hits(
    importer: str, specifier: str, remaining: dict[str, tuple[frozenset[str], str]],
) -> list[str]:
    spec = specifier.split("?", 1)[0].strip()
    if not spec:
        return []
    if spec.startswith("."):
        # Relative imports resolve exactly, so `../api/dashboard` never passes
        # for `pages/Dashboard.tsx`.
        folder = importer.rpartition("/")[0]
        target = _strip_ext(posixpath.normpath(posixpath.join(folder, spec)))
        return [
            path for path, (keys, _) in remaining.items() if path != importer and target in keys
        ]
    # Aliases (@/, ~/, src/, a workspace package) depend on bundler config, so
    # match on the last segment: a miss here only means a missed warning.
    trimmed = _strip_ext(spec.rstrip("/"))
    head, _, name = trimmed.rpartition("/")
    if name == "index" and head:
        name = head.rpartition("/")[2]
    return [path for path, (_, stem) in remaining.items() if path != importer and stem == name]


def _strip_ext(path: str) -> str:
    for suffix in _MODULE_SUFFIXES:
        if path.endswith(suffix) and len(path) > len(suffix):
            return path[: -len(suffix)]
    return path


def _is_test(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return any(marker in name for marker in _TEST_MARKERS) or bool(
        _TEST_DIRS.intersection(parts[:-1])
    )


def _read(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(_MAX_READ_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return ""
