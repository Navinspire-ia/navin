# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Canonical locations of Navin-managed files inside a workspace / project.

Everything Navin creates for its own operation lives under ``.navin/`` so the
project tree the user sees stays clean:

- ``.navin/SOUL.md`` / ``.navin/USER.md`` - agent voice and user preferences
- ``.navin/AGENTS.md`` - Navin's scaffolded agent instructions
- ``.navin/HEARTBEAT.md`` - periodic background task list
- ``.navin/WORKSPACE.md`` - workspace notes
- ``.navin/memory/`` - MEMORY.md, history.jsonl, cursors
- ``.navin/prompts/`` - workspace prompt overrides (dream.md, ...)
- ``.navin/metadata/`` - project knowledge base (index.json, ARCHITECTURE.md)
- ``.navin/checkpoints/`` - session/code checkpoints
- ``.navin/apps/`` - installed app templates (plugin overlay: navin.json, agents)
- ``.navin/skills/`` - skills added from Install skill / Add skill (every Navin tool)

Legacy projects created these files at the project root. ``migrate_layout``
moves them into ``.navin/`` (idempotent, never overwrites), so old projects
keep their memory and settings without any manual step.

A root ``AGENTS.md`` is only migrated when it is still the untouched Navin
template: a customized root ``AGENTS.md`` is a cross-tool convention (Cursor,
Codex, ...) and other tools read it there, so Navin must not move it.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from loguru import logger

NAVIN_DIR_NAME = ".navin"

# Markdown files Navin scaffolds for its own operation. AGENTS.md is handled
# separately (see module docstring).
_PRIVATE_BRAIN_FILES = ("SOUL.md", "USER.md", "HEARTBEAT.md", "WORKSPACE.md")

# Files that mark a root ``memory/`` folder as Navin-owned (vs. a project's
# own unrelated "memory" directory, which must never be touched).
_MEMORY_MARKERS = (
    "MEMORY.md",
    "history.jsonl",
    "HISTORY.md",
    ".log_navin",
    ".cursor",
    ".dream_cursor",
)


def navin_dir(workspace: Path | str) -> Path:
    return Path(workspace).expanduser() / NAVIN_DIR_NAME


def brain_file(workspace: Path | str, name: str) -> Path:
    """Path of a Navin brain file (``SOUL.md``, ``USER.md``, ``HEARTBEAT.md``,
    ``WORKSPACE.md``, ``AGENTS.md``) under ``.navin/``."""
    return navin_dir(workspace) / name


def soul_file(workspace: Path | str) -> Path:
    return brain_file(workspace, "SOUL.md")


def user_file(workspace: Path | str) -> Path:
    return brain_file(workspace, "USER.md")


def heartbeat_file(workspace: Path | str) -> Path:
    return brain_file(workspace, "HEARTBEAT.md")


def agents_file(workspace: Path | str) -> Path:
    return brain_file(workspace, "AGENTS.md")


def memory_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / "memory"


def memory_file(workspace: Path | str) -> Path:
    return memory_dir(workspace) / "MEMORY.md"


def prompts_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / "prompts"


def skills_dir(workspace: Path | str) -> Path:
    """The only Navin skills folder: ``<workspace>/.navin/skills``."""
    return navin_dir(workspace) / "skills"


def legacy_skills_dir(workspace: Path | str) -> Path:
    """Leftover singular folder ``<workspace>/.navin/skill`` (merged, then removed)."""
    return navin_dir(workspace) / "skill"


def user_skills_dir() -> Path:
    """Skills that apply to every workspace: ``~/.navin/skills``."""
    return skills_dir(Path.home())


def coalesce_owned_skills(workspace: Path | str) -> Path:
    """Make ``.navin/skills`` the only folder. Merge leftover ``.navin/skill`` into it."""
    dest = skills_dir(workspace)
    leftover = legacy_skills_dir(workspace)
    dest.mkdir(parents=True, exist_ok=True)
    if leftover.is_dir():
        try:
            children = list(leftover.iterdir())
        except OSError:
            children = []
        for child in children:
            target = dest / child.name
            if not target.exists():
                with suppress(OSError):
                    child.replace(target)
        with suppress(OSError):
            leftover.rmdir()
    return dest


def apps_dir(workspace: Path | str) -> Path:
    """Installed app-template overlays (``navin.json``, agents, user overrides)."""
    return navin_dir(workspace) / "apps"


def workspace_app_dir(workspace: Path | str, slug: str) -> Path:
    return apps_dir(workspace) / slug


def metadata_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / "metadata"


def checkpoints_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / "checkpoints"


def workspace_skill_dir(workspace: Path | str, name: str) -> Path:
    """Directory of a named workspace skill.

    An existing copy wins wherever it lives (``.navin/skills`` first, then
    leftover ``.navin/skill``, then the legacy root ``skills/``). New skills
    are created under ``.navin/skills``.
    """
    root = Path(workspace).expanduser()
    coalesce_owned_skills(root)
    preferred = skills_dir(root) / name
    if preferred.exists():
        return preferred
    legacy_navin = legacy_skills_dir(root) / name
    if legacy_navin.exists():
        return legacy_navin
    legacy_root = root / "skills" / name
    if legacy_root.exists():
        return legacy_root
    return preferred


def read_with_root_fallback(workspace: Path | str, relative: str) -> Path:
    """Resolve a Navin file, preferring ``.navin/`` over the legacy root copy.

    Returns the ``.navin/`` path when it exists (or when neither exists), and
    the root path only when it alone exists. Callers that also *write* should
    run :func:`migrate_layout` first instead of using this helper.
    """
    root = Path(workspace).expanduser()
    new = navin_dir(root) / relative
    if new.exists():
        return new
    legacy = root / relative
    if legacy.exists():
        return legacy
    return new


def _move_into_navin(src: Path, dest: Path, moved: list[str]) -> None:
    """Move *src* to *dest* unless the destination already exists."""
    if not src.exists() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        src.replace(dest)
        moved.append(dest.name)


def _root_memory_is_navins(root_memory: Path) -> bool:
    if not root_memory.is_dir():
        return False
    return any((root_memory / marker).exists() for marker in _MEMORY_MARKERS)


def _root_skills_is_navins(root_skills: Path) -> bool:
    """Whether a root ``skills/`` folder holds only Navin skill packs.

    Navin's skills folder contains one directory per skill, each with a
    SKILL.md. Anything else (loose files, source code) means the folder
    belongs to the user's project and must not be moved.
    """
    if not root_skills.is_dir():
        return False
    try:
        entries = list(root_skills.iterdir())
    except OSError:
        return False
    return all(
        entry.is_dir() and (entry / "SKILL.md").is_file() for entry in entries
    )


# The AGENTS.md template as it shipped before brain files moved under .navin/
# (root paths in the wording). Root files matching it byte-for-byte are still
# recognized as untouched scaffolds and migrated.
_LEGACY_AGENTS_TEMPLATE = """# Agent Instructions

## Workspace Guidance

Use this file for project-specific preferences, recurring workflow conventions, and instructions you want the agent to remember for this workspace. Keep durable facts about the user in `USER.md`, personality/style guidance in `SOUL.md`, and long-term memory in `memory/MEMORY.md`.

## Project Continuity

This project carries a `.navin/` continuity pack shared by every module (Code, Documents, Marketing, SEO, ...):

- Before starting substantial work, check `.navin/continuity/RESUME.md` and the board (`board` tool) to see where the project stands.
- Hard rules live in `memory/MEMORY.md` under `## Constraints` - never violate them without an explicit user request.
- After a significant work session, update `.navin/continuity/RESUME.md` (state, risks, next action) and append durable choices to `.navin/continuity/DECISIONS.md`.

## Product quality (always)

- No Unicode em dash (U+2014) or en dash (U+2013) in code, UI copy, i18n, docs, or comments. Use `-` or rephrase.
- Web UI work must lock one official design system (Google MUI, Microsoft Fluent, or IBM Carbon; ask if missing), load skills `ui-ux-pro-max` and `make-interfaces-feel-better`, install/use `framer-motion` plus `three` + `@react-three/fiber` + `@react-three/drei` (designed 3D layer, not wallpaper), and ship working controls (no stub buttons / blank dashboards). Never default to Tailwind / shadcn / a homemade kit.
- Architecture, diagrams, PPT visuals, tender/RFP technical answers, and Markdown plans/explanations use skill `archify` by default (HTML + SVG, not a Mermaid dump).
- Do not mark an app done until Preview shows a working happy path and `verify` is clean.

## Scheduled Reminders

- Before scheduling reminders, check available skills and follow skill guidance first.
- Use the built-in `cron` tool to create/list/remove jobs (do not call `navin cron` via `exec`).
- Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).
- Cron jobs run as scheduled turns in the origin chat/session and normally deliver the result back to that channel. Do not use cron for background checks that should stay silent when there is nothing useful to report; use `HEARTBEAT.md` instead.

**Do NOT just write reminders to MEMORY.md** - that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked periodically by the protected heartbeat cron job that `navin gateway` registers when `gateway.heartbeat.enabled` is true. Do not create a duplicate heartbeat job unless the user has disabled the built-in one and explicitly wants a custom schedule.

- Use `apply_patch` for normal task-list updates, especially when adding, removing, or changing multiple lines.
- Use `edit_file` only for small exact replacements copied from the current `HEARTBEAT.md`.
- Use `write_file` for first creation or intentional full-file rewrites.

When the user asks for a recurring/periodic heartbeat task, or for a periodic background check that should only notify on actionable changes, update `HEARTBEAT.md` instead of creating a one-time reminder. Use the built-in `cron` tool for explicit reminders, scheduled tasks that should report every run, or custom schedules that should not be part of the heartbeat task list."""


def _agents_md_is_untouched_template(path: Path) -> bool:
    from navin.utils.helpers import load_bundled_template

    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    if content == _LEGACY_AGENTS_TEMPLATE.strip():
        return True
    template = load_bundled_template("AGENTS.md")
    return template is not None and content == template.strip()


def migrate_layout(workspace: Path | str) -> list[str]:
    """Move legacy root-level Navin files into ``.navin/`` (idempotent).

    Returns the names of the entries that were moved. Never overwrites an
    existing ``.navin/`` copy and never touches user content that merely
    shares a name (root ``memory/`` without Navin markers, customized root
    ``AGENTS.md``).
    """
    root = Path(workspace).expanduser()
    if not root.is_dir():
        return []
    base = navin_dir(root)
    moved: list[str] = []
    try:
        for name in _PRIVATE_BRAIN_FILES:
            _move_into_navin(root / name, base / name, moved)

        legacy_agents = root / "AGENTS.md"
        if legacy_agents.is_file() and _agents_md_is_untouched_template(legacy_agents):
            _move_into_navin(legacy_agents, base / "AGENTS.md", moved)

        legacy_memory = root / "memory"
        if _root_memory_is_navins(legacy_memory):
            new_memory = base / "memory"
            if not new_memory.exists():
                base.mkdir(parents=True, exist_ok=True)
                with suppress(OSError):
                    legacy_memory.replace(new_memory)
                    moved.append("memory")

        legacy_skills = root / "skills"
        if _root_skills_is_navins(legacy_skills):
            new_skills = base / "skills"
            already = new_skills.exists()
            if not already:
                base.mkdir(parents=True, exist_ok=True)
                with suppress(OSError):
                    legacy_skills.replace(new_skills)
                    moved.append("skills")
        coalesce_owned_skills(root)

        # Navin-owned dot-directories: safe to relocate as a whole.
        for legacy_name, new_name in ((".metadata", "metadata"), (".checkpoints", "checkpoints")):
            legacy_dir = root / legacy_name
            new_dir = base / new_name
            if legacy_dir.is_dir() and not new_dir.exists():
                base.mkdir(parents=True, exist_ok=True)
                with suppress(OSError):
                    legacy_dir.replace(new_dir)
                    moved.append(new_name)
    except OSError:
        logger.exception("workspace layout migration failed under {}", root)
    if moved:
        logger.info("Moved legacy Navin files into {}: {}", base, ", ".join(moved))
    return moved
