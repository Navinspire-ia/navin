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
  ``description`` and ``developer_instructions``) is parsed as well.

The definitions surface in two places:

- the system prompt lists them so the model knows they exist;
- ``spawn(agent="reviewer", ...)`` runs a subagent with the definition's
  instructions layered on top of the standard subagent prompt.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml
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

    Skills (and later agents) installed once under ``~/.claude``, ``~/.omp``,
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

_FRONTMATTER = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
_MAX_AGENT_FILE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProjectAgent:
    """One agent definition found in the project."""

    name: str
    description: str
    prompt: str
    source: str  # path of the markdown file, for display


def _parse_agent_file(path: Path) -> ProjectAgent | None:
    try:
        if path.stat().st_size > _MAX_AGENT_FILE_BYTES:
            return None
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.stem
    description = ""
    body = content
    match = _FRONTMATTER.match(content) if content.startswith("---") else None
    if match:
        body = content[match.end():]
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        if isinstance(meta, dict):
            if isinstance(meta.get("name"), str) and meta["name"].strip():
                name = meta["name"].strip()
            if isinstance(meta.get("description"), str):
                description = meta["description"].strip()

    body = body.strip()
    if not body:
        return None
    return ProjectAgent(name=name, description=description, prompt=body, source=str(path))


def _parse_agent_toml(path: Path) -> ProjectAgent | None:
    """Codex CLI agent file: name/description/developer_instructions in TOML."""
    try:
        if path.stat().st_size > _MAX_AGENT_FILE_BYTES:
            return None
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
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


def list_project_agents(workspace: Path) -> list[ProjectAgent]:
    """All agent definitions in the workspace, first folder wins per name."""
    root = workspace.expanduser()
    candidates: list[Path] = []
    for harness in harness_dirs(workspace):
        for subdir in _AGENT_SUBDIRS:
            agents_dir = root / harness / subdir
            if agents_dir.is_dir():
                candidates.extend(_iter_agent_files(agents_dir))

    fingerprint_parts: list[tuple[str, int, int]] = []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            fingerprint_parts.append((str(path), -1, -1))
            continue
        fingerprint_parts.append((str(path), stat.st_mtime_ns, stat.st_size))
    fingerprint = tuple(fingerprint_parts)

    cache_key = str(root)
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
        agents.append(agent)
    _AGENTS_CACHE[cache_key] = (fingerprint, agents)
    return list(agents)


def find_project_agent(workspace: Path, name: str) -> ProjectAgent | None:
    """Look an agent up by name (case-insensitive)."""
    wanted = name.strip().lower()
    if not wanted:
        return None
    for agent in list_project_agents(workspace):
        if agent.name.lower() == wanted:
            return agent
    return None


def project_agents_summary(workspace: Path) -> str:
    """Markdown list of project agents for the system prompt ('' when none)."""
    agents = list_project_agents(workspace)
    if not agents:
        return ""
    lines = [
        f"- **{agent.name}** - {agent.description or 'no description'}"
        for agent in agents
    ]
    logger.debug("Project agents discovered: {}", ", ".join(a.name for a in agents))
    return "\n".join(lines)
