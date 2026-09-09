# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Heavy tool schemas the workbench ships only when the turn can use them.

Measured 2026-09-02 over the 51 instantiable tools: their schemas cost
26 494 tokens, and 88 % of that weight sits in ``parameters``, not in the
descriptions. The reason is structural. The big tools are multiplexers: one
``action`` enum, then the union of every action's fields flattened into a
single property list, each field carrying a sentence about which action owns
it. ``git`` alone re-sends 25 such properties on a ``git status``.

Six of them are dead weight on an ordinary turn:

    board 1308, browser 1055, mobile 824, write_stdin 509,
    cron 480, notebook_edit 273, list_exec_sessions 89   ->  ~4.5k tokens

They are withheld, never removed: each comes back for the turn as soon as
something says it is relevant. Naming it is one signal, and it is not enough
on its own - a turn that says "build the app" in an Expo checkout needs
``mobile`` without ever writing the word - so the repository on disk and the
live exec sessions are read too. That is the whole difference between
trimming a prompt and losing a capability.

Rule-based on purpose, like :mod:`navin.agent.desk_intent`: the gating must
hold when the primary model has failed over to a smaller one, which is
exactly when a model-based router would drop it.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from navin.agent.desk_intent import normalize_intent_text

BOARD_TOOL = "board"
BROWSER_TOOL = "browser"
CRON_TOOL = "cron"
MOBILE_TOOL = "mobile"
NOTEBOOK_TOOL = "notebook_edit"
WRITE_STDIN_TOOL = "write_stdin"
LIST_EXEC_SESSIONS_TOOL = "list_exec_sessions"

#: Withheld until the turn, the repository or a live session asks for them.
ON_DEMAND_TOOLS: frozenset[str] = frozenset(
    {
        BOARD_TOOL,
        BROWSER_TOOL,
        CRON_TOOL,
        MOBILE_TOOL,
        NOTEBOOK_TOOL,
        WRITE_STDIN_TOOL,
        LIST_EXEC_SESSIONS_TOOL,
    }
)

# Nouns and slash commands that name each tool's subject. Narrow on purpose:
# a false positive only costs prompt tokens, but it costs them on every step
# of the turn.
_TOOL_NOUNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        BOARD_TOOL,
        re.compile(
            r"(/forge|/cruise|/board|\b("
            r"board|kanban|backlog|milestones?|jalons?|"
            r"missions?|ledger|journal de mission|"
            r"roadmaps?|feuilles? de route|"
            r"taches? (?:a faire|restantes?|ouvertes?)|"
            r"todo list|liste de taches|"
            r"chantiers?|lots? de travail|work ?items?|"
            r"epics?|sprints?"
            r")\b)"
        ),
    ),
    (
        BROWSER_TOOL,
        re.compile(
            r"(https?://|\b("
            r"browser|navigateur|chrome|chromium|firefox|webkit|"
            r"onglets?|tabs?|"
            r"screenshots?|captures? d ecran|"
            r"selecteurs?|selectors?|css selector|xpath|"
            r"dom|devtools|cdp|"
            r"clique[rz]?|click|scroll|"
            r"pages? web|web ?pages?|localhost|127 0 0 1|"
            r"formulaires?|forms?|"
            r"playwright|puppeteer|selenium"
            r")\b)"
        ),
    ),
    (
        CRON_TOOL,
        re.compile(
            r"\b("
            r"cron|crontab|"
            r"rappels?|reminders?|"
            r"planifie[rz]?|planification|schedules?|scheduling|"
            r"chaque (?:jour|matin|soir|semaine|mois|lundi|heure)|"
            r"tous les (?:jours|matins|soirs|lundis|mois)|"
            r"toutes les (?:heures|semaines|minutes)|"
            r"every (?:day|morning|hour|week|month|monday)|"
            r"daily|weekly|hourly|"
            r"periodiques?|recurrents?|recurring|"
            r"heartbeats?"
            r")\b"
        ),
    ),
    (
        MOBILE_TOOL,
        re.compile(
            r"\b("
            r"mobile|smartphones?|"
            r"android|ios|iphone|ipad|"
            r"expo|capacitor|react native|flutter|ionic|"
            r"apks?|aabs?|ipas?|"
            r"emulateurs?|emulators?|simulateurs?|simulators?|"
            r"gradle|xcode|cocoapods|podfile|"
            r"play store|app store|testflight"
            r")\b"
        ),
    ),
    (
        NOTEBOOK_TOOL,
        re.compile(
            r"\b("
            r"notebooks?|ipynb|jupyter|colab|"
            r"cellules?|cells?|"
            r"kernels?|noyaux?"
            r")\b"
        ),
    ),
    (
        WRITE_STDIN_TOOL,
        re.compile(
            r"\b("
            r"stdin|"
            r"arriere ?plans?|background|"
            r"sessions?|"
            r"repl|interactifs?|interactive|"
            r"processus (?:en cours|de fond)|long running|"
            r"watche(?:rs?|z)|watch mode|"
            r"serveurs? de dev|dev servers?|"
            r"tail|logs? en direct|follow the logs?"
            r")\b"
        ),
    ),
)

# write_stdin without list_exec_sessions is half a hand: the same signals open
# both, and a live session opens them whatever the turn says.
_PAIRED_WITH_STDIN: frozenset[str] = frozenset({LIST_EXEC_SESSIONS_TOOL})

# Repository markers, checked at the root only. Each answers one question:
# would this tool have anything to act on here?
_MOBILE_MARKERS: tuple[str, ...] = (
    "capacitor.config.ts",
    "capacitor.config.js",
    "capacitor.config.json",
    "app.json",
    "app.config.js",
    "app.config.ts",
    "metro.config.js",
    "pubspec.yaml",
    "ionic.config.json",
)
_MOBILE_DIRS: tuple[str, ...] = ("android", "ios")
_WEB_MARKERS: tuple[str, ...] = (
    "package.json",
    "index.html",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.ts",
    "nuxt.config.ts",
    "svelte.config.js",
    "angular.json",
    "astro.config.mjs",
)
_WEB_DIRS: tuple[str, ...] = ("public", "webui", "frontend", "www")

# The scan is a handful of stat() calls, but it runs on every turn of every
# session; a short cache keeps it off the hot path without ever going stale
# enough to matter (a checkout does not become a mobile app mid-minute).
_FACTS_TTL_S = 30.0
_FACTS_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
# Depth of the .ipynb hunt. Notebooks live at the root or one directory down
# (notebooks/, analysis/); walking a monorepo to prove otherwise would cost
# more than the 273 tokens at stake.
_NOTEBOOK_DEPTH = 2


def _has_notebook(root: Path, depth: int = _NOTEBOOK_DEPTH) -> bool:
    """True when a ``.ipynb`` sits within ``depth`` levels of ``root``."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return False
    subdirs: list[Path] = []
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False):
                if entry.name.endswith(".ipynb"):
                    return True
            elif depth > 1 and entry.is_dir(follow_symlinks=False):
                if not entry.name.startswith(".") and entry.name not in (
                    "node_modules",
                    "target",
                    "dist",
                    "build",
                    "venv",
                ):
                    subdirs.append(Path(entry.path))
        except OSError:
            continue
    return any(_has_notebook(child, depth - 1) for child in subdirs)


def project_facts(workspace: str | os.PathLike[str] | None) -> frozenset[str]:
    """Tags describing what ``workspace`` holds: ``mobile``, ``web``, ``notebooks``, ``board``.

    Cached for :data:`_FACTS_TTL_S`; an unreadable or missing root yields no
    tags, which only means the on-demand tools wait for the text to name them.
    """
    if workspace is None:
        return frozenset()
    root = Path(workspace).expanduser()
    key = str(root)
    now = time.monotonic()
    cached = _FACTS_CACHE.get(key)
    if cached is not None and now - cached[0] < _FACTS_TTL_S:
        return cached[1]

    tags: set[str] = set()
    try:
        if not root.is_dir():
            _FACTS_CACHE[key] = (now, frozenset())
            return frozenset()
        if (root / ".navin" / "board").is_dir():
            tags.add("board")
        if any((root / name).is_file() for name in _MOBILE_MARKERS) or all(
            (root / name).is_dir() for name in _MOBILE_DIRS
        ):
            tags.add("mobile")
        if any((root / name).is_file() for name in _WEB_MARKERS) or any(
            (root / name).is_dir() for name in _WEB_DIRS
        ):
            tags.add("web")
        if _has_notebook(root):
            tags.add("notebooks")
    except OSError:
        tags.clear()

    resolved = frozenset(tags)
    _FACTS_CACHE[key] = (now, resolved)
    return resolved


def clear_project_facts_cache() -> None:
    """Forget the cached repository tags (tests, and a workspace switch)."""
    _FACTS_CACHE.clear()


def on_demand_tools_for_text(text: str | None) -> frozenset[str]:
    """On-demand tools this turn named, empty when it named none."""
    normalized = normalize_intent_text(text)
    if not normalized:
        return frozenset()
    wanted: set[str] = set()
    for tool, pattern in _TOOL_NOUNS:
        if pattern.search(normalized):
            wanted.add(tool)
    if WRITE_STDIN_TOOL in wanted:
        wanted.update(_PAIRED_WITH_STDIN)
    return frozenset(wanted)


def on_demand_tools_for_facts(
    workspace: str | os.PathLike[str] | None,
    *,
    exec_sessions_open: bool = False,
) -> frozenset[str]:
    """On-demand tools the repository or a live session makes relevant.

    This is what keeps the gating lossless. A turn that says "build the app"
    in an Expo checkout never writes "mobile", and a turn that has just left a
    build running in the background will want ``write_stdin`` next.
    """
    wanted: set[str] = set()
    if exec_sessions_open:
        wanted.add(WRITE_STDIN_TOOL)
        wanted.update(_PAIRED_WITH_STDIN)
    tags = project_facts(workspace)
    if "board" in tags:
        wanted.add(BOARD_TOOL)
    if "mobile" in tags:
        wanted.add(MOBILE_TOOL)
    if "notebooks" in tags:
        wanted.add(NOTEBOOK_TOOL)
    # A backend or CLI checkout has no page to open; a web one may need a
    # screenshot the user never asked for by name.
    if "web" in tags:
        wanted.add(BROWSER_TOOL)
    return frozenset(wanted)


def exec_sessions_are_open() -> bool:
    """True when a background ``exec`` session is alive in this process."""
    try:
        from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

        return DEFAULT_EXEC_SESSION_MANAGER.has_open_sessions()
    except Exception:
        # A missing manager must never decide that a tool disappears.
        return True


__all__ = [
    "ON_DEMAND_TOOLS",
    "clear_project_facts_cache",
    "exec_sessions_are_open",
    "on_demand_tools_for_facts",
    "on_demand_tools_for_text",
    "project_facts",
]
