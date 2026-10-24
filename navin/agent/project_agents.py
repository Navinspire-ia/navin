# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project-defined subagents, read natively from harness config folders.

Repositories that configure coding agents usually carry agent definitions as
markdown files with YAML frontmatter - the format Claude Code popularized and
OpenCode, Hermes and others read as well:

    .claude/agents/reviewer.md
    ---
    name: reviewer              # optional, defaults to the file stem
    description: Reviews diffs for security and style issues
    ---
    You are a meticulous code reviewer. ...   <- the agent's instructions

Navin discovers these automatically in every dot-folder of the workspace:
the well-known ones (``.navin``, ``.claude``, ``.opencode``, ``.codex``,
``.omp``, ``.agents``, ``.ai``, ``.cursor``) in a stable priority order, and
then **any other** ``.<tool>`` folder the repository carries - so the same
repository configures every tool, present or future, with no import step.
Discovery details, matching real-world repo layouts:

- both ``agents/`` and OpenCode's singular ``agent/`` subfolders are read;
- the scan is recursive, so canonical layouts with category subfolders
  (``.ai/agents/<category>/<name>.md``) work as-is;
- symlinked mirrors (e.g. ``.claude/agents/x.md`` -> ``.ai/agents/...``) are
  followed; duplicates of the same agent name collapse to the first hit;
- Codex CLI's TOML agent format (``.codex/agents/<name>.toml`` with ``name``,
  ``description`` and ``developer_instructions``) is parsed as well;
- the same folders under ``$HOME`` (``~/.navin/agents``, ``~/.claude/agents``,
  ...) hold the user's *personal* agents, visible from every workspace with
  lower priority: a project agent wins a name clash.

The definitions surface in two places:

- the system prompt lists them so the model knows they exist;
- ``spawn(agent="reviewer", ...)`` runs a subagent with the definition's
  instructions layered on top of the standard subagent prompt.
"""

from __future__ import annotations

import dataclasses
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# Well-known harness folders, listed first so their priority order is stable
# when two folders define the same agent name. Discovery is NOT limited to
# this list: any other dot-folder in the workspace root is scanned too (see
# harness_dirs), so a repo using .myharness/agents works with no code change.
HARNESS_DIRS = (
    ".navin",
    ".agents",
    ".claude",
    ".cursor",
    ".opencode",
    ".codex",
    ".omp",
    ".ai",
)

# Dot-folders that can never carry agent definitions and are not worth the
# extra stats (VCS internals, caches, environments).
_IGNORED_DOT_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", ".env", ".tox", ".nox",
    ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".next", ".turbo", ".idea", ".DS_Store",
})

# $HOME also has OS / package-manager trees that will never hold a SKILL.md.
# They are skipped only on the home scan so a repo can still name a folder
# after one of them if it wants to.
_HOME_IGNORED_DOT_DIRS = _IGNORED_DOT_DIRS | {
    ".local",
    ".config",
    ".Trash",
    ".thumbnails",
    ".npm",
    ".cargo",
    ".rustup",
    ".nvm",
    ".pyenv",
    ".sdkman",
    ".gradle",
    ".m2",
    ".docker",
    ".kube",
    ".ssh",
    ".gnupg",
    ".mozilla",
    ".var",
    ".snap",
}


def _dot_dirs_on(root: Path, *, ignored: frozenset[str]) -> list[str]:
    extras: list[str] = []
    try:
        with os.scandir(root) as scan:
            for entry in scan:
                name = entry.name
                if (
                    name.startswith(".")
                    and name not in ignored
                    and name not in HARNESS_DIRS
                    and entry.is_dir()
                ):
                    extras.append(name)
    except OSError:
        pass
    return extras


def harness_dirs(workspace: Path) -> list[str]:
    """Every dot-folder of the workspace worth scanning, known ones first.

    The known list pins the priority order; whatever other ``.<tool>``
    folders the repository carries come after, alphabetically. Users name
    these folders after tools that do not exist yet, so a whitelist would
    always be one tool behind.
    """
    return [*HARNESS_DIRS, *sorted(_dot_dirs_on(workspace.expanduser(), ignored=_IGNORED_DOT_DIRS))]


def home_harness_dirs() -> list[str]:
    """Same discovery as ``harness_dirs``, applied to ``$HOME``.

    Skills and agents installed once under ``~/.claude``, ``~/.omp``,
    or any other ``.<tool>`` folder must be visible from every workspace.
    """
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return list(HARNESS_DIRS)
    return [*HARNESS_DIRS, *sorted(_dot_dirs_on(home, ignored=_HOME_IGNORED_DOT_DIRS))]

# Subfolder names carrying agent definitions. OpenCode documents the singular
# ``agent/``; every other tool uses ``agents/``.
_AGENT_SUBDIRS = ("agents", "agent")

_MAX_AGENT_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProjectAgent:
    """One agent definition found in the project."""

    name: str
    description: str
    prompt: str
    source: str  # path of the markdown file, for display
    # "project" for the workspace's folders, "personal" for the user's home
    # folders (~/.navin/agents, ~/.claude/agents, ...) shared by every project,
    # "plugin" for enabled plugins, "builtin" for agents shipped with Navin.
    scope: str = "project"
    # Tool allowlist from the ``tools`` frontmatter (Navin names); None = all.
    tools: tuple[str, ...] | None = None
    # Tool denylist from ``disallowedTools`` (Navin names), removed even when
    # ``tools`` is absent and the agent otherwise inherits every tool.
    disallowed: tuple[str, ...] = ()
    model: str = ""
    color: str = ""
    plugin: str = ""
    # ``permissionMode``: "default", "plan" (read-only), "acceptEdits" (file
    # edits approved without asking) or "bypassPermissions" (nothing asked).
    permission_mode: str = "default"


PERMISSION_MODES = {
    "default": "default", "plan": "plan", "acceptedits": "acceptEdits",
    "bypasspermissions": "bypassPermissions", "dontask": "bypassPermissions",
}


def normalize_permission_mode(value: object) -> str:
    """Canonical permission mode; anything unknown stays on the default."""
    key = str(value or "").strip().replace("-", "").replace("_", "").lower()
    return PERMISSION_MODES.get(key, "default")


# Agents shipped with Navin (plugin-validator, skill-reviewer, ...): always
# available, shadowed by any project, personal or plugin agent of that name.
BUILTIN_AGENTS_DIR = Path(__file__).resolve().parent.parent / "builtin_agents"


def _parse_agent_file(path: Path) -> ProjectAgent | None:
    try:
        if path.stat().st_size > _MAX_AGENT_FILE_BYTES:
            return None
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None

    from navin.agent.frontmatter import split_frontmatter

    name = path.stem
    description = ""
    body = content
    tools: tuple[str, ...] | None = None
    disallowed: tuple[str, ...] = ()
    model = color = ""
    permission_mode = "default"
    meta, parsed_body, _error = split_frontmatter(content)
    if meta is not None:
        body = parsed_body
        if isinstance(meta, dict):
            if isinstance(meta.get("name"), str) and meta["name"].strip():
                name = meta["name"].strip()
            if isinstance(meta.get("description"), str):
                description = meta["description"].strip()
            if meta.get("tools") not in (None, "", []):
                from navin.agent.custom_commands import normalize_tools

                normalized = normalize_tools(meta.get("tools"))
                tools = tuple(normalized) if normalized else None
            denied_raw = meta.get("disallowedTools", meta.get("disallowed-tools"))
            if denied_raw not in (None, "", []):
                from navin.agent.custom_commands import normalize_tools

                disallowed = tuple(normalize_tools(denied_raw) or ())
            model = str(meta.get("model") or "").strip()
            color = str(meta.get("color") or "").strip()
            permission_mode = normalize_permission_mode(
                meta.get("permissionMode", meta.get("permission-mode"))
            )

    body = body.strip()
    if not body:
        return None
    return ProjectAgent(
        name=name, description=description, prompt=body, source=str(path),
        tools=tools, disallowed=disallowed, model=model, color=color,
        permission_mode=permission_mode,
    )


def _parse_agent_toml(path: Path) -> ProjectAgent | None:
    """Codex CLI agent file: name/description/developer_instructions in TOML."""
    try:
        if path.stat().st_size > _MAX_AGENT_FILE_BYTES:
            return None
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    name = data.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else path.stem
    description = data.get("description")
    description = description.strip() if isinstance(description, str) else ""
    body = data.get("developer_instructions") or data.get("instructions")
    body = body.strip() if isinstance(body, str) else ""
    if not body:
        return None
    return ProjectAgent(name=name, description=description, prompt=body, source=str(path))


def _iter_agent_files(agents_dir: Path) -> list[Path]:
    """Agent definition files under one folder, recursively, sorted.

    Recursion matters for canonical layouts with category subfolders
    (``.ai/agents/<category>/<name>.md``); symlinked files - the mirrors
    sync scripts generate - are followed like regular files.
    """
    try:
        files = [
            p
            for p in agents_dir.rglob("*")
            if p.suffix in (".md", ".toml")
            # README files document the folder (the .navin scaffold ships
            # one); they are not agent definitions.
            and p.stem.lower() != "readme"
            and p.is_file()
        ]
    except OSError:
        return []
    return sorted(files)


# Parsed agents per workspace, validated by the stats of every definition
# file. The context builder runs this scan on every turn; the directory walk
# is cheap but re-reading and re-YAML/TOML-parsing every file was not.
_AGENTS_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], list[ProjectAgent]]] = {}


def _agent_candidates(root: Path, dirs: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for harness in dirs:
        for subdir in _AGENT_SUBDIRS:
            agents_dir = root / harness / subdir
            if agents_dir.is_dir():
                candidates.extend(_iter_agent_files(agents_dir))
    return candidates


def _personal_agent_candidates(workspace_root: Path) -> list[Path]:
    """Agent files under ``$HOME`` harness folders (~/.navin/agents, ...).

    Skipped when the workspace *is* the home folder: its files are already
    the project's, and listing them twice would only shadow themselves.
    """
    try:
        home = Path.home().expanduser()
    except (OSError, RuntimeError):
        return []
    try:
        if home.resolve(strict=False) == workspace_root.resolve(strict=False):
            return []
    except OSError:
        return []
    return _agent_candidates(home, home_harness_dirs())


def _plugin_agent_candidates() -> list[tuple[Path, str]]:
    """Agent files of enabled plugins: ``<plugin>/agents`` + manifest paths."""
    try:
        from navin.plugins.manager import plugin_component_dirs

        dirs = plugin_component_dirs("agents")
    except Exception:  # noqa: BLE001 - a broken plugin never hides project agents
        return []
    found: list[tuple[Path, str]] = []
    for plugin, folder in dirs:
        if folder.is_file() and folder.suffix == ".md":
            found.append((folder, plugin))
        elif folder.is_dir():
            found.extend((p, plugin) for p in _iter_agent_files(folder))
    return found


def list_project_agents(workspace: Path, *, include_extensions: bool = False) -> list[ProjectAgent]:
    """All agent definitions visible from the workspace, first hit wins per name.

    Project folders come first, then the user's personal folders under
    ``$HOME``: a project agent always wins a name clash with a personal one.
    With *include_extensions*, agents of enabled plugins and the ones shipped
    with Navin follow, in that order.
    """
    root = workspace.expanduser()
    project_files = _agent_candidates(root, harness_dirs(workspace))
    personal_files = _personal_agent_candidates(root)
    project_set = set(project_files)
    # A file reachable both ways (odd symlink layouts) stays a project agent.
    personal_files = [p for p in personal_files if p not in project_set]
    plugin_pairs = _plugin_agent_candidates() if include_extensions else []
    plugin_of = {path: plugin for path, plugin in plugin_pairs}
    builtin_files = (
        _iter_agent_files(BUILTIN_AGENTS_DIR)
        if include_extensions and BUILTIN_AGENTS_DIR.is_dir() else []
    )
    builtin_set = set(builtin_files)
    candidates = [*project_files, *personal_files, *plugin_of, *builtin_files]
    personal_set = set(personal_files)

    fingerprint_parts: list[tuple[str, int, int]] = []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            fingerprint_parts.append((str(path), -1, -1))
            continue
        fingerprint_parts.append((str(path), stat.st_mtime_ns, stat.st_size))
    fingerprint = tuple(fingerprint_parts)

    cache_key = f"{root}|{int(include_extensions)}"
    cached = _AGENTS_CACHE.get(cache_key)
    if cached is not None and cached[0] == fingerprint:
        return list(cached[1])

    agents: list[ProjectAgent] = []
    seen: set[str] = set()
    for path in candidates:
        agent = (
            _parse_agent_toml(path)
            if path.suffix == ".toml"
            else _parse_agent_file(path)
        )
        if agent is None:
            continue
        key = agent.name.lower()
        if key in seen:
            continue
        seen.add(key)
        if path in personal_set:
            agent = dataclasses.replace(agent, scope="personal")
        elif path in plugin_of:
            agent = dataclasses.replace(agent, scope="plugin", plugin=plugin_of[path])
        elif path in builtin_set:
            agent = dataclasses.replace(agent, scope="builtin")
        agents.append(agent)
    _AGENTS_CACHE[cache_key] = (fingerprint, agents)
    return list(agents)


def list_agents(workspace: Path) -> list[ProjectAgent]:
    """Project, personal, plugin and built-in agents: what spawn can run."""
    return list_project_agents(workspace, include_extensions=True)


def find_project_agent(workspace: Path, name: str) -> ProjectAgent | None:
    """Look an agent up by name (case-insensitive)."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for agent in list_agents(workspace):
        if agent.name.lower() == wanted:
            return agent
    return None


#: Longest description kept in the prompt. The full text stays in the agent
#: file and reaches the subagent itself; the list only has to route. Full
#: descriptions cost ~1.1k prompt tokens on every model call.
_SUMMARY_MAX_CHARS = 140


def _one_line(description: str) -> str:
    """First sentence of ``description``, cut on a word near the cap."""
    text = " ".join(description.split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= _SUMMARY_MAX_CHARS:
        return first
    cut = first.rfind(" ", 0, _SUMMARY_MAX_CHARS - 3)
    return first[: cut if cut > 40 else _SUMMARY_MAX_CHARS - 3].rstrip(" ,;:") + "..."


def project_agents_summary(workspace: Path) -> str:
    """Markdown list of project agents for the system prompt ('' when none)."""
    agents = list_agents(workspace)
    if not agents:
        return ""
    from navin.agent.frontmatter import strip_examples

    def marker(agent: ProjectAgent) -> str:
        if agent.scope == "personal":
            return " (personal)"
        if agent.scope == "plugin":
            return f" (plugin {agent.plugin})"
        if agent.scope == "builtin":
            return " (built-in)"
        return ""

    lines = [
        f"- **{agent.name}**{marker(agent)}"
        f" - {_one_line(strip_examples(agent.description)) or 'no description'}"
        for agent in agents
    ]
    logger.debug("Project agents discovered: {}", ", ".join(a.name for a in agents))
    return "\n".join(lines)
