"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path

import yaml

from navin.agent.project_agents import HARNESS_DIRS, harness_dirs, home_harness_dirs

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Opening ---, YAML body (group 1), closing --- on its own line; supports CRLF.
_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)

# Frontmatter metadata per SKILL.md, validated by (mtime_ns, size). The
# context builder asks for every skill's metadata several times per turn
# (summary, description, always-flag, requirements); without this cache each
# lookup re-read and re-YAML-parsed the file, which showed up as real
# per-turn latency on workspaces with many skills. Module-level because the
# loader itself is rebuilt every turn.
_SKILL_META_CACHE: dict[str, tuple[int, int, dict | None]] = {}

# shutil.which per required binary, re-run for every skill on every turn, is
# a PATH scan each time. Binaries appear/disappear rarely, and this runs on
# the gateway event loop while a turn is being prepared: in a WSL guest the
# PATH carries every Windows directory over the /mnt/c 9P mount, where one
# stat can take a second, and the scan froze every open chat for ~2 s each
# time the cache expired. Keep the answer for 10 minutes and never probe
# Windows mounts for a skill's Linux binary.
_WHICH_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}
_WHICH_TTL_S = 600.0
_MISSING_WHICH_TTL_S = 2.0
_WHICH_PATH_CACHE: tuple[str, str] | None = None


def _which_search_path() -> str:
    """PATH for skill requirement checks, minus Windows mounts in WSL."""
    global _WHICH_PATH_CACHE
    raw = os.environ.get("PATH", "")
    if _WHICH_PATH_CACHE is not None and _WHICH_PATH_CACHE[0] == raw:
        return _WHICH_PATH_CACHE[1]
    from navin.utils import wsl

    path = raw
    if wsl.is_wsl_guest():
        kept = [
            entry
            for entry in raw.split(os.pathsep)
            if not re.match(r"^/mnt/[a-zA-Z](?:/|$)", entry)
        ]
        path = os.pathsep.join(kept) or raw
    _WHICH_PATH_CACHE = (raw, path)
    return path


_SKIP_LOOSE_SKILL_MD = frozenset({"skill.md", "readme.md", "changelog.md", "license.md"})
_NAVIN_NON_SKILL_CHILDREN = frozenset({
    "memory",
    "metadata",
    "checkpoints",
    "prompts",
    "agents",
    "agent",
    "board",
    "crm",
    "apps",
    "resources",
    "rules",
    "shadow",
    "quality",
    "optimize",
    "evolve",
    "continuity",
    "proofs",
    "team",
    # Skill drafts written by skills evolution (S2). They only become skills
    # once promoted into .navin/skills; the live loader never reads them.
    "skills-draft",
})


def _dedup_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def iter_skill_files(base: Path) -> list[tuple[str, Path]]:
    """Every playbook in a skill library folder.

    Accepts ``<name>/SKILL.md`` and a loose ``<name>.md`` dropped in the
    same folder (``.navin/skills/tata.md``). Folder wins if both exist.

    At the root of a harness folder itself (``.navin``, ``.claude``,
    ``.qwen``...) only the ``<name>/SKILL.md`` layout counts: the loose
    Markdown there is configuration and brain files (``AGENTS.md``,
    ``SOUL.md``, ``CLAUDE.md``, ``output-language.md``), which used to be
    offered to the model as playbooks named "AGENTS" or "SOUL".
    """
    if not base.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    try:
        children = sorted(base.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []
    harness_root = base.name.startswith(".")
    skip_dirs = _NAVIN_NON_SKILL_CHILDREN if base.name == ".navin" else frozenset()
    for child in children:
        if child.is_dir():
            if child.name in skip_dirs:
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file() or child.name in seen:
                continue
            seen.add(child.name)
            found.append((child.name, skill_file))
    if harness_root:
        return found
    for child in children:
        if not child.is_file() or child.suffix.lower() != ".md":
            continue
        if child.name.lower() in _SKIP_LOOSE_SKILL_MD:
            continue
        name = child.stem
        if not name or name in seen:
            continue
        seen.add(name)
        found.append((name, child))
    return found


def resolve_skill_file(root: Path, name: str) -> Path | None:
    """``root/<name>/SKILL.md`` first, then ``root/<name>.md``, then ``root/SKILL.md``."""
    if not _valid_skill_name(name):
        return None
    nested = root / name / "SKILL.md"
    if nested.is_file():
        return nested
    loose = root / f"{name}.md"
    if loose.is_file():
        return loose
    direct = root / "SKILL.md"
    if direct.is_file():
        folder = root.name[1:] if root.name.startswith(".") else root.name
        if folder == name:
            return direct
    return None


def _harness_skill_dirs(root: Path, harnesses: list[str]) -> list[Path]:
    """Every layout a ``.<tool>`` folder actually uses for skills.

    Flat ``<harness>/skills`` is the common one. OMP and a few others nest
    the library under ``agent/skills`` or ``agents/skills``. The harness
    folder itself is also a library, at the project root: ``.cursor/SKILL.md``,
    ``.claude/review/SKILL.md``, any ``.folder/SKILL.md`` or
    ``.folder/subdir/SKILL.md``.
    """
    dirs: list[Path] = []
    for harness in harnesses:
        dirs.append(root / harness)
        for leaf in ("skills", "skill"):
            dirs.append(root / harness / leaf)
            dirs.append(root / harness / "agent" / leaf)
            dirs.append(root / harness / "agents" / leaf)
    return dirs


# $HOME is scanned once per directory stamp, not once per
# SkillsLoader. A wave of subagents each built a loader, and 300 parallel
# scandirs of a real home were enough to miss the 60s drain in tests.
_HOME_SKILL_DIRS_CACHE: tuple[tuple[str, int], tuple[Path, ...]] | None = None
_HOME_DIRS_LOCK = threading.Lock()

# The name index walks every SKILL.md (YAML + requirement checks). Fifty
# subagents doing that at once serialize on the GIL and never reach the
# runner before the 60s drain. One in-flight build, then reuse until a
# skill directory's mtime changes.
_INDEX_CACHE: dict[tuple, tuple[float, str]] = {}
_INDEX_LOCK = threading.Lock()
_INDEX_CACHE_MAX = 64
_INDEX_TTL_S = 2.0

_PLUGIN_SKILL_DIRS_CACHE: tuple[float, tuple[tuple[str, Path], ...]] | None = None
_PLUGIN_SKILL_TTL_S = 5.0

# One directory scan serves a whole prompt build. Resolving a skill by name
# used to probe every root (~300 on a developer machine: each ~/.tool folder
# times seven layouts) three stat() calls at a time, for each of ~190 skills,
# several times per build: 670 000 stat() calls and 3 to 26 seconds before
# the first model call. The scan already knows where every skill lives.
_SCAN_TTL_S = 2.0
# Bumped by clear_skills_index_cache() so a skill installed from the WebUI
# shows up on the very next call, not after the TTL.
_SCAN_GENERATION = 0


_SKILL_MENTION_RE = re.compile(r"(?<![\w$])\$([\w][\w-]{0,127})(?![\w-])")
MAX_OWNED_PRELOAD = 32


def _valid_skill_name(name: str) -> bool:
    """A catalog name is one component on both POSIX and Windows."""
    return bool(name and name not in {".", ".."} and not any(c in name for c in "/\\:\0"))


def _search_text(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "and", "the", "for", "to", "of", "or", "in", "with", "use",
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "pour", "avec", "sur", "dans",
})

# Slim preload: keep the operating rules, drop recipes/appendices. Full
# playbooks stay behind `skill action=read`.
_CAPSULE_MAX_CHARS = 1800
_CAPSULE_STOP_HEADINGS = (
    "## Scaffolding",
    "## Super render",
    "## Official design",
    "## Scripts",
    "## Cookbook",
    "## Reference",
    "## Appendix",
    "## Catalog",
    "## Examples",
)


def is_owned_skill_source(source: str | None) -> bool:
    """True for skills the user added or installed (not bundled builtins)."""
    value = (source or "").strip()
    return value in {"workspace", "user"} or value.startswith("plugin:")


def clear_skills_index_cache() -> None:
    """Drop the compact skills index (tests that rewrite skill trees)."""
    global _PLUGIN_SKILL_DIRS_CACHE, _SCAN_GENERATION
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()
        _SCAN_GENERATION += 1
        _WHICH_CACHE.clear()
    _PLUGIN_SKILL_DIRS_CACHE = None


def clear_home_skill_dir_cache() -> None:
    """Drop the cached home roots (tests that rewrite $HOME)."""
    global _HOME_SKILL_DIRS_CACHE
    with _HOME_DIRS_LOCK:
        _HOME_SKILL_DIRS_CACHE = None
    clear_skills_index_cache()


def _dir_stamp(path: Path) -> tuple[str, int, tuple[str, ...]]:
    """Identity of a skill root: the folder plus its immediate children.

    Editing ``SKILL.md`` updates the skill directory, not always the parent
    ``skills/`` folder. Child names are part of the key so two writes in the
    same filesystem-timestamp tick still bust the cache.
    """
    try:
        latest = path.stat().st_mtime_ns
    except OSError:
        return (str(path), 0, ())
    names: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                names.append(entry.name)
                try:
                    latest = max(latest, entry.stat().st_mtime_ns)
                except OSError:
                    continue
    except OSError:
        pass
    return (str(path), latest, tuple(sorted(names)))


def _home_skill_dirs() -> list[Path]:
    """User-level skill roots, shared by every workspace.

    Any ``~/.tool`` folder is scanned, not just the well-known list: the
    same rule as the workspace. Cache / VCS / OS trees stay out.
    """
    global _HOME_SKILL_DIRS_CACHE
    try:
        home = Path.home().expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return []
    try:
        key = (str(home), home.stat().st_mtime_ns)
    except OSError:
        return []
    cached = _HOME_SKILL_DIRS_CACHE
    if cached is not None and cached[0] == key:
        return list(cached[1])
    with _HOME_DIRS_LOCK:
        cached = _HOME_SKILL_DIRS_CACHE
        if cached is not None and cached[0] == key:
            return list(cached[1])
        dirs = tuple(_harness_skill_dirs(home, home_harness_dirs()))
        _HOME_SKILL_DIRS_CACHE = (key, dirs)
        return list(dirs)


def _cached_which(command: str) -> bool:
    import time

    now = time.monotonic()
    search_path = _which_search_path()
    key = (command, search_path)
    hit = _WHICH_CACHE.get(key)
    ttl = _WHICH_TTL_S if hit and hit[1] else _MISSING_WHICH_TTL_S
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    try:
        found = shutil.which(command, path=search_path) is not None
    except (OSError, ValueError):
        found = False
    _WHICH_CACHE[key] = (now, found)
    return found


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    # Project config folders shared across coding harnesses. Navin reads them
    # natively - the same repository configures Claude Code, OpenCode, Codex,
    # Cursor, OMP and Navin without any import step. The well-known folders
    # come first (priority order); any other dot-folder the repository carries
    # is scanned after them, so unknown tools work too.
    HARNESS_DIRS = HARNESS_DIRS

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None, disabled_skills: set[str] | None = None):
        from navin import workspace_layout

        self.workspace = workspace
        # Discovery must also work in read-only projects. Installation and
        # layout migration own writes; the loader reads both legacy layouts.
        self.workspace_skills = workspace_layout.skills_dir(workspace)
        candidates = [
            self.workspace_skills,
            workspace_layout.legacy_skills_dir(workspace),
            workspace / "skills",
            *_harness_skill_dirs(workspace, harness_dirs(workspace)),
        ]
        self.workspace_skill_dirs = _dedup_paths(candidates)
        # Skills the user keeps for every project, the way OMP and Claude Code
        # do (~/.omp/agent/skills, ~/.claude/skills, ...). Without these, a
        # library installed once in $HOME was invisible to every workspace.
        self.user_skill_dirs = [
            path
            for path in _dedup_paths(_home_skill_dirs())
            if path not in self.workspace_skill_dirs
        ]
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.disabled_skills = disabled_skills or set()
        self._scan_cache: tuple[float, int, list[dict[str, str]], dict[str, Path]] | None = None

    def _scan(self) -> tuple[list[dict[str, str]], dict[str, Path]]:
        """Every skill under every root in priority order, plus name -> file.

        Cached for :data:`_SCAN_TTL_S`: a prompt build asks for the catalog
        four or five times, and a skill dropped into a folder still shows up
        within two seconds without a restart.
        """
        now = time.monotonic()
        cached = self._scan_cache
        if (
            cached is not None
            and cached[1] == _SCAN_GENERATION
            and now - cached[0] < _SCAN_TTL_S
        ):
            return cached[2], cached[3]
        # A harness folder can be added while the agent is running. Refresh
        # roots at the bounded scan cadence, not only on process startup.
        from navin import workspace_layout

        self.workspace_skill_dirs = _dedup_paths([
            self.workspace_skills,
            workspace_layout.legacy_skills_dir(self.workspace),
            self.workspace / "skills",
            *_harness_skill_dirs(self.workspace, harness_dirs(self.workspace)),
        ])
        self.user_skill_dirs = [
            path for path in _dedup_paths(_home_skill_dirs())
            if path not in self.workspace_skill_dirs
        ]
        skills: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for workspace_skills in self.workspace_skill_dirs:
            entries = self._skill_entries_from_dir(
                workspace_skills,
                "workspace",
                skip_names=seen_names,
            )
            skills.extend(entries)
            seen_names.update(entry["name"] for entry in entries)
        for user_skills in self.user_skill_dirs:
            user_entries = self._skill_entries_from_dir(
                user_skills,
                "user",
                skip_names=seen_names,
            )
            skills.extend(user_entries)
            seen_names.update(entry["name"] for entry in user_entries)
        for plugin_name, skills_dir in self._plugin_skill_dirs():
            plugin_entries = self._skill_entries_from_dir(
                skills_dir, f"plugin:{plugin_name}", skip_names=seen_names,
            )
            skills.extend(plugin_entries)
            seen_names.update(entry["name"] for entry in plugin_entries)
        if self.builtin_skills and self.builtin_skills.exists():
            skills.extend(
                self._skill_entries_from_dir(self.builtin_skills, "builtin", skip_names=seen_names)
            )
        by_name = {entry["name"]: Path(entry["path"]) for entry in skills}
        self._scan_cache = (now, _SCAN_GENERATION, skills, by_name)
        return skills, by_name

    def invalidate_scan(self) -> None:
        """Forget the cached directory scan (after writing a skill file)."""
        self._scan_cache = None

    def _skill_entries_from_dir(self, base: Path, source: str, *, skip_names: set[str] | None = None) -> list[dict[str, str]]:
        if not base.exists():
            return []
        blocked = skip_names or set()
        entries = [
            {"name": name, "path": str(skill_file), "source": source}
            for name, skill_file in iter_skill_files(base)
            if name not in blocked
        ]
        direct = base / "SKILL.md"
        if direct.is_file():
            name = base.name[1:] if base.name.startswith(".") else base.name
            if name and name not in blocked and name not in {item["name"] for item in entries}:
                entries.append({"name": name, "path": str(direct), "source": source})
        entries.sort(key=lambda entry: entry["name"].lower())
        return entries

    @staticmethod
    def _plugin_skill_dirs() -> list[tuple[str, Path]]:
        """Skill directories contributed by enabled plugin packs.

        Cached for a few seconds: a wave of subagents used to construct a
        PluginManager on every index key. New installs still appear on the
        next TTL tick without a gateway restart.
        """
        global _PLUGIN_SKILL_DIRS_CACHE
        now = time.monotonic()
        cached = _PLUGIN_SKILL_DIRS_CACHE
        if cached is not None and now - cached[0] < _PLUGIN_SKILL_TTL_S:
            return list(cached[1])
        try:
            from navin.plugins import enabled_plugin_skill_dirs

            dirs = tuple(enabled_plugin_skill_dirs())
        except Exception:
            dirs = ()
        _PLUGIN_SKILL_DIRS_CACHE = (now, dirs)
        return list(dirs)

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        scanned, _ = self._scan()
        skills = list(scanned)

        if self.disabled_skills:
            skills = [s for s in skills if s["name"] not in self.disabled_skills]

        if filter_unavailable:
            return [skill for skill in skills if self._check_requirements(self._get_skill_meta(skill["name"]))]
        return skills

    def owned_skills(self, *, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """Workspace, user-home, and installed plugin skills, in discovery order."""
        return [
            entry
            for entry in self.list_skills(filter_unavailable=filter_unavailable)
            if is_owned_skill_source(entry.get("source"))
        ]

    def mentioned_skill_names(self, text: str) -> list[str]:
        """Explicit $mentions and distinct skill slugs in prose, in order.

        Bare generic names such as git or memory are ordinary task words;
        only compound slugs or quoted names count without the $ prefix.
        """
        known = {entry["name"].lower(): entry["name"] for entry in self.list_skills(filter_unavailable=False)}
        matches: dict[str, int] = {}
        for match in _SKILL_MENTION_RE.finditer(text or ""):
            name = known.get(match.group(1).lower())
            if name:
                matches.setdefault(name, match.start())
        for key, name in known.items():
            escaped = re.escape(key)
            pattern = (
                rf"(?<![\w$-]){escaped}(?![\w-])"
                if "-" in key or "_" in key
                else rf"[`\"]{escaped}[`\"]"
            )
            match = re.search(pattern, text or "", re.IGNORECASE)
            if match:
                matches[name] = min(matches.get(name, match.start()), match.start())
        return sorted(matches, key=matches.get)

    def _skill_path(self, name: str) -> Path | None:
        """Resolve the SKILL.md that wins for *name*, or None."""
        if not _valid_skill_name(name):
            return None
        _, by_name = self._scan()
        known = by_name.get(name)
        if known is not None and known.is_file():
            return known
        # Not in the scan: a skill written a moment ago, a name the scan
        # skips on purpose (README.md style), or a stale entry. Probe the roots.
        roots = list(self.workspace_skill_dirs)
        roots.extend(self.user_skill_dirs)
        roots.extend(skills_dir for _, skills_dir in self._plugin_skill_dirs())
        if self.builtin_skills:
            roots.append(self.builtin_skills)
        for root in roots:
            path = resolve_skill_file(root, name)
            if path is not None:
                return path
        return None

    def skill_dir(self, name: str) -> Path | None:
        """The on-disk folder of a skill (where scripts/ and data/ live)."""
        path = self._skill_path(name)
        return path.parent if path else None

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        path = self._skill_path(name)
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None

    def load_skills_for_context(
        self,
        skill_names: list[str],
        *,
        slim: bool = False,
        full_body_names: set[str] | frozenset[str] | None = None,
    ) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.
            slim: When True, only full_body_names (plus builtin memory) get
                a short capsule of SKILL.md; others are one-line pointers.
            full_body_names: Names that keep a capsule (or a tiny full body)
                even when slim=True.

        Returns:
            Formatted skills content.
        """
        keep_full = set(full_body_names or ())
        if slim:
            # always=true used to dump every matching SKILL.md (heartbeat,
            # soul, workspace playbooks, ...). Keep the tiny builtin only.
            keep_full.update(
                name for name in self.get_always_skills() if name == "memory"
            )
        parts: list[str] = []
        for name in skill_names:
            if name in self.disabled_skills:
                continue
            markdown = self.load_skill(name)
            if not markdown:
                continue
            # The real folder path: skills bundle helper files (scripts/,
            # data/) that exec can only reach through an absolute path.
            # as_posix, not str: a Windows backslash path echoed back by the
            # model into commands or JSON reads as an escape sequence.
            folder = self.skill_dir(name)
            prefix = f"(Skill folder: {folder.as_posix()})\n\n" if folder else ""
            if slim and name not in keep_full:
                desc = self._get_skill_description(name) or name
                parts.append(
                    f"### Skill: {name}\n\n"
                    f"{desc}\n\n"
                    f"(Slim preload. MANDATORY: before doing work in this "
                    f"skill's domain, load it with "
                    f"`skill action=read name={name}` and follow it. Do not "
                    f"improvise what the playbook already prescribes.)"
                )
                continue
            body = self._strip_frontmatter(markdown)
            if slim:
                capsule = self._capsule_body(body)
                if capsule != body:
                    parts.append(
                        f"### Skill: {name}\n\n{prefix}{capsule}\n\n"
                        f"(Capsule. Load the rest with "
                        f"`skill action=read name={name}` before following "
                        f"recipes not shown above.)"
                    )
                    continue
            parts.append(f"### Skill: {name}\n\n{prefix}{body}")
        return "\n\n---\n\n".join(parts)

    def _index_cache_key(self, exclude: set[str] | None) -> tuple:
        stamps: list[tuple[str, int, tuple[str, ...]]] = []
        for path in (
            *self.workspace_skill_dirs,
            *self.user_skill_dirs,
            self.builtin_skills,
        ):
            if path is None:
                continue
            stamps.append(_dir_stamp(path))
        for plugin_name, skills_dir in self._plugin_skill_dirs():
            stamp = _dir_stamp(skills_dir)
            stamps.append((f"plugin:{plugin_name}:{stamp[0]}", stamp[1], stamp[2]))
        return (
            tuple(stamps),
            frozenset(self.disabled_skills),
            frozenset(exclude or ()),
        )

    def build_skills_index(self, exclude: set[str] | None = None) -> str:
        """Name-only skill index for the system prompt.

        The full catalog (164+ skills x a multi-line description each) costs
        ~10K tokens on every single turn; the names alone carry enough signal
        to know a playbook exists, and the `skill` tool serves descriptions
        and bodies on demand. Unavailable skills are listed separately so the
        model does not promise a playbook whose dependencies are missing.

        Directory changes invalidate immediately. A short TTL also refreshes
        availability after package installs or environment changes, while a
        fan-out of subagents still shares one catalog build.
        """
        key = self._index_cache_key(exclude)
        with _INDEX_LOCK:
            hit = _INDEX_CACHE.get(key)
            if hit is not None and time.monotonic() - hit[0] < _INDEX_TTL_S:
                return hit[1]
            # The directory stamps already proved this catalog changed.
            # Do not rebuild a fresh index from a still-cached old scan.
            self.invalidate_scan()
            available: list[str] = []
            unavailable: list[str] = []
            for entry in self.list_skills(filter_unavailable=False):
                name = entry["name"]
                if exclude and name in exclude:
                    continue
                meta = self._get_skill_meta(name)
                bucket = available if self._check_requirements(meta) else unavailable
                bucket.append(name)
            lines: list[str] = []
            if available:
                lines.append(", ".join(sorted(available)))
            if unavailable:
                lines.append(
                    "Unavailable (missing dependencies): " + ", ".join(sorted(unavailable))
                )
            result = "\n\n".join(lines)
            if key not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_MAX:
                _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
            _INDEX_CACHE[key] = (time.monotonic(), result)
            return result

    def search_skills(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Rank skills against free-text keywords (name + description match).

        Returns dicts with name, description, path (display), source and
        availability - what the `skill` tool shows the model so it can pick
        one and read its SKILL.md.
        """
        normalized = _search_text(query or "").strip()
        tokens = list(dict.fromkeys(
            t for t in re.findall(r"[^\W_]+", normalized) if t not in _SEARCH_STOP_WORDS
        ))
        if not tokens:
            return []
        scored: list[tuple[int, dict[str, str]]] = []
        for entry in self.list_skills(filter_unavailable=False):
            name = entry["name"]
            desc = self._get_skill_description(name)
            name_l = _search_text(name)
            desc_l = _search_text(desc)
            name_tokens = set(re.findall(r"[^\W_]+", name_l))
            desc_tokens = set(re.findall(r"[^\W_]+", desc_l))
            score = 100 if normalized.lstrip("$") == name_l else 0
            for token in tokens:
                if token in name_tokens:
                    score += 6
                elif len(token) >= 3 and token in name_l:
                    score += 2
                if token in desc_tokens:
                    score += 1
            if score <= 0:
                continue
            meta = self._get_skill_meta(name)
            scored.append((
                score,
                {
                    "name": name,
                    "description": desc,
                    "source": entry["source"],
                    "available": self._check_requirements(meta),
                    "missing": self._get_missing_requirements(meta),
                },
            ))
        scored.sort(key=lambda pair: (
            -pair[0],
            0 if is_owned_skill_source(pair[1]["source"]) else 1,
            pair[1]["name"],
        ))
        return [row for _score, row in scored[:limit]]

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Args:
            exclude: Set of skill names to omit from the summary.

        Returns:
            Markdown-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        lines: list[str] = []
        for entry in all_skills:
            skill_name = entry["name"]
            if exclude and skill_name in exclude:
                continue
            meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(meta)
            desc = self._get_skill_description(skill_name)
            if entry["source"] == "workspace":
                try:
                    # as_posix, not str: on Windows a native path would show the
                    # model `skills\name\SKILL.md`, which it then echoes back into
                    # tool calls and prompts where the backslash reads as an escape.
                    display_path = (
                        Path(entry["path"])
                        .relative_to(self.workspace.expanduser().resolve(strict=False))
                        .as_posix()
                    )
                except ValueError:
                    display_path = f"skills/{skill_name}/SKILL.md"
            elif entry["source"] == "builtin":
                # Never teach the model an installation path outside the active
                # project. read_file maps this stable virtual path to bundled
                # skills when no workspace override exists.
                display_path = f"skills/{skill_name}/SKILL.md"
            else:
                display_path = entry["path"]
            if available:
                lines.append(f"- **{skill_name}** - {desc}  `{display_path}`")
            else:
                missing = self._get_missing_requirements(meta)
                suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
                lines.append(f"- **{skill_name}** - {desc}{suffix}  `{display_path}`")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"CLI: {command_name}" for command_name in required_bins if not _cached_which(command_name)]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def get_skill_availability(self, name: str) -> tuple[bool, str]:
        """Return whether a skill can run and why not when it cannot."""
        meta = self._get_skill_meta(name)
        available = self._check_requirements(meta)
        return available, "" if available else self._get_missing_requirements(meta)

    def get_skill_requirements(self, name: str) -> dict[str, list[str]]:
        """Return explicit command/env requirements and currently missing entries."""
        requires = self._get_skill_meta(name).get("requires", {})
        bins = [str(value) for value in requires.get("bins", [])]
        env = [str(value) for value in requires.get("env", [])]
        return {
            "bins": bins,
            "env": env,
            "missing_bins": [value for value in bins if not _cached_which(value)],
            "missing_env": [value for value in env if not os.environ.get(value)],
        }

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and isinstance(meta.get("description"), str) and meta["description"].strip():
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    @staticmethod
    def _capsule_body(body: str, *, max_chars: int = _CAPSULE_MAX_CHARS) -> str:
        """Hard defaults / operating loop only; drop scaffolding appendices."""
        text = (body or "").strip()
        if not text:
            return text
        stops: list[int] = []
        for heading in _CAPSULE_STOP_HEADINGS:
            idx = text.find(heading)
            if idx >= 400:
                stops.append(idx)
        if stops:
            text = text[: min(stops)].rstrip()
        if len(text) <= max_chars:
            return text
        cut = text.rfind("\n", 0, max_chars)
        return text[: cut if cut > 500 else max_chars].rstrip()

    def _parse_navin_metadata(self, raw: object) -> dict:
        """Extract navin/openclaw metadata from a frontmatter field.

        ``raw`` may be a dict (already parsed by yaml.safe_load) or a JSON str.
        """
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("navin", data.get("openclaw", {}))
        if not isinstance(payload, dict):
            return {}
        payload = dict(payload)
        requires = payload.get("requires")
        requires = requires if isinstance(requires, dict) else {}
        normalized: dict[str, list[str]] = {}
        for key in ("bins", "env"):
            values = requires.get(key, [])
            if isinstance(values, str):
                values = [values]
            normalized[key] = [
                value.strip() for value in values
                if isinstance(value, str) and value.strip()
            ] if isinstance(values, (list, tuple)) else []
        payload["requires"] = {**requires, **normalized}
        return payload

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(_cached_which(cmd) for cmd in required_bins) and all(
            os.environ.get(var) for var in required_env_vars
        )

    def _get_skill_meta(self, name: str) -> dict:
        """Get navin metadata for a skill (cached in frontmatter)."""
        raw_meta = self.get_skill_metadata(name) or {}
        return self._parse_navin_metadata(raw_meta.get("metadata"))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        def enabled(value: object) -> bool:
            return value is True or (isinstance(value, str) and value.strip().lower() == "true")

        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self.get_skill_metadata(entry["name"]) or {})
            and (
                enabled(self._parse_navin_metadata(meta.get("metadata")).get("always"))
                or enabled(meta.get("always"))
            )
        ]

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        path = self._skill_path(name)
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        key = str(path)
        cached = _SKILL_META_CACHE.get(key)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        metadata = self._parse_skill_frontmatter(path)
        _SKILL_META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, metadata)
        return metadata

    @staticmethod
    def _parse_skill_frontmatter(path: Path) -> dict | None:
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None
        if not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict):
            return None
        # yaml.safe_load returns native types (int, bool, list, etc.);
        # keep values as-is so downstream consumers get correct types.
        metadata: dict[str, object] = {}
        for key, value in parsed.items():
            metadata[str(key)] = value
        return metadata
