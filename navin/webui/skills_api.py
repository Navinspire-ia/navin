"""Skill catalog + workspace CRUD for the WebUI."""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from navin.agent.skills import SkillsLoader

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SKILL_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_MAX_MARKDOWN_BYTES = 200_000
_SKILL_BODY_HEADER = "X-Navin-Skill-Body"
_RESERVED_PREFIXES = ("cli-app-",)


class SkillsApiError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def webui_skills_payload(
    workspace_path: Path,
    *,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Return agent skills without leaking local filesystem paths."""
    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = sorted(
        loader.list_skills(filter_unavailable=False),
        key=lambda entry: (entry.get("source") != "workspace", entry["name"]),
    )
    return {"skills": [_skill_payload(loader, entry) for entry in entries]}


def webui_skill_detail_payload(
    workspace_path: Path,
    name: str,
    *,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any] | None:
    """Return a single skill's safe detail payload."""
    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = loader.list_skills(filter_unavailable=False)
    entry = next((item for item in entries if item["name"] == name), None)
    if entry is None:
        return None
    return {
        **_skill_payload(loader, entry),
        "requirements": loader.get_skill_requirements(name),
        "raw_markdown": loader.load_skill(name) or "",
    }


def create_workspace_skill(
    workspace_path: Path,
    *,
    name: str,
    description: str,
    markdown: str | None = None,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Create a custom skill under ``<workspace>/skills/<name>/SKILL.md``."""
    skill_name = normalize_skill_name(name)
    desc = (description or "").strip()
    if not desc:
        raise SkillsApiError("description is required")
    if len(desc) > _MAX_DESCRIPTION_LENGTH:
        raise SkillsApiError("description is too long")

    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    existing = {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}
    if skill_name in existing:
        raise SkillsApiError(f"skill already exists: {skill_name}", status=409)

    skill_dir = workspace_path / "skills" / skill_name
    if skill_dir.exists():
        raise SkillsApiError(f"skill directory already exists: {skill_name}", status=409)

    content = (markdown or "").strip() or _default_skill_markdown(skill_name, desc)
    _validate_skill_markdown(content, expected_name=skill_name)
    skill_dir.mkdir(parents=True, exist_ok=False)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")

    detail = webui_skill_detail_payload(
        workspace_path, skill_name, disabled_skills=disabled_skills
    )
    if detail is None:
        raise SkillsApiError("failed to load created skill", status=500)
    return detail


def update_workspace_skill(
    workspace_path: Path,
    *,
    name: str,
    markdown: str,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Overwrite a workspace skill's SKILL.md. Builtin skills cannot be edited."""
    skill_name = normalize_skill_name(name)
    content = (markdown or "").strip()
    if not content:
        raise SkillsApiError("markdown body is required")
    _validate_skill_markdown(content, expected_name=skill_name)

    skill_dir = workspace_path / "skills" / skill_name
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        # Creating via update is allowed only when no builtin owns the name.
        loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
        builtin = loader.builtin_skills / skill_name / "SKILL.md" if loader.builtin_skills else None
        if builtin and builtin.is_file() and not skill_file.is_file():
            raise SkillsApiError(
                "cannot overwrite a built-in skill; create a custom skill with a different name",
                status=403,
            )
        skill_dir.mkdir(parents=True, exist_ok=True)

    skill_file.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    detail = webui_skill_detail_payload(
        workspace_path, skill_name, disabled_skills=disabled_skills
    )
    if detail is None:
        raise SkillsApiError("failed to load updated skill", status=500)
    return detail


def delete_workspace_skill(
    workspace_path: Path,
    *,
    name: str,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Delete a custom workspace skill directory. Builtin skills are protected."""
    skill_name = normalize_skill_name(name)
    skill_dir = workspace_path / "skills" / skill_name
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
        raise SkillsApiError("workspace skill not found", status=404)

    # Refuse to delete anything that looks like a path escape / weird name.
    try:
        skill_dir.resolve().relative_to((workspace_path / "skills").resolve())
    except ValueError as exc:
        raise SkillsApiError("invalid skill path", status=400) from exc

    shutil.rmtree(skill_dir)
    return webui_skills_payload(workspace_path, disabled_skills=disabled_skills)


def skill_body_from_headers(headers: Any) -> str | None:
    """Read optional skill markdown from ``X-Navin-Skill-Body`` (JSON string)."""
    try:
        raw = headers.get(_SKILL_BODY_HEADER)
    except Exception:
        raw = None
    if raw is None:
        try:
            raw = headers.get(_SKILL_BODY_HEADER.lower())
        except Exception:
            raw = None
    if not raw:
        return None
    text = str(raw)
    if text.startswith("b64:"):
        try:
            text = base64.b64decode(text[4:], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SkillsApiError("invalid skill body encoding") from exc
    if len(text.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        raise SkillsApiError("skill body is too large")
    # Allow either raw markdown or a JSON-encoded string.
    stripped = text.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SkillsApiError("invalid skill body encoding") from exc
        if not isinstance(decoded, str):
            raise SkillsApiError("skill body must be a string")
        if len(decoded.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
            raise SkillsApiError("skill body is too large")
        return decoded
    return text


def normalize_skill_name(name: str) -> str:
    raw = (name or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized or not _SKILL_NAME_RE.fullmatch(normalized):
        raise SkillsApiError(
            "invalid skill name (use lowercase letters, numbers, hyphens)"
        )
    if len(normalized) > _MAX_SKILL_NAME_LENGTH:
        raise SkillsApiError("skill name is too long")
    if any(normalized.startswith(prefix) for prefix in _RESERVED_PREFIXES):
        raise SkillsApiError(f"skill name reserved: {normalized}")
    return normalized


def _skill_payload(loader: SkillsLoader, entry: dict[str, str]) -> dict[str, Any]:
    name = entry["name"]
    metadata = loader.get_skill_metadata(name)
    available, unavailable_reason = loader.get_skill_availability(name)
    source = entry.get("source", "unknown")
    return {
        "name": name,
        "description": _description(metadata, name),
        "source": source,
        "category": _category(loader, name, source),
        "available": available,
        "unavailable_reason": unavailable_reason,
        "editable": source == "workspace",
        "deletable": source == "workspace",
    }


def _category(loader: SkillsLoader, name: str, source: str) -> str:
    navin_meta = loader._get_skill_meta(name)
    category = navin_meta.get("category")
    if isinstance(category, str) and category.strip():
        return category.strip().lower()
    if source == "workspace":
        return "custom"
    if source.startswith("plugin:"):
        return "plugin"
    return "core"


def _description(metadata: dict[str, Any] | None, fallback: str) -> str:
    if metadata is None:
        return fallback
    value = metadata.get("description")
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _default_skill_markdown(name: str, description: str) -> str:
    title = " ".join(part.capitalize() for part in name.split("-"))
    return (
        f"---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        f'metadata: {{"navin":{{"category":"custom"}}}}\n'
        f"---\n\n"
        f"# {title}\n\n"
        f"## Overview\n\n"
        f"{description}\n\n"
        f"## When to use\n\n"
        f"- Trigger this skill when the user request matches the description above.\n\n"
        f"## Workflow\n\n"
        f"1. Clarify the goal and constraints.\n"
        f"2. Gather the minimum context needed (files, tools, prior decisions).\n"
        f"3. Execute step by step; verify intermediate results.\n"
        f"4. Deliver a concise result with next actions if useful.\n"
    )


def _validate_skill_markdown(content: str, *, expected_name: str) -> None:
    if len(content.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        raise SkillsApiError("skill body is too large")
    if not content.lstrip().startswith("---"):
        raise SkillsApiError("SKILL.md must start with YAML frontmatter")
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", content, re.DOTALL)
    if not match:
        raise SkillsApiError("invalid SKILL.md frontmatter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillsApiError("invalid YAML frontmatter") from exc
    if not isinstance(meta, dict):
        raise SkillsApiError("frontmatter must be a mapping")
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or normalize_skill_name(name) != expected_name:
        raise SkillsApiError(f"frontmatter name must be '{expected_name}'")
    if not isinstance(description, str) or not description.strip():
        raise SkillsApiError("frontmatter description is required")
    body = content[match.end() :].strip()
    if not body:
        raise SkillsApiError("SKILL.md body cannot be empty")
