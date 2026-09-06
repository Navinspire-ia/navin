"""Skill catalog + workspace CRUD for the WebUI."""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from navin import workspace_layout
from navin.agent.skills import (
    SkillsLoader,
    clear_skills_index_cache,
    iter_skill_files,
    resolve_skill_file,
)

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
    from navin.webui.skills_setup import skill_setup_payload

    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    entries = loader.list_skills(filter_unavailable=False)
    entry = next((item for item in entries if item["name"] == name), None)
    if entry is None:
        return None
    return {
        **_skill_payload(loader, entry),
        "requirements": loader.get_skill_requirements(name),
        "setup": skill_setup_payload(loader, name),
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
    """Create a custom skill under ``<workspace>/.navin/skills/<name>/SKILL.md``."""
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

    skill_dir = workspace_layout.workspace_skill_dir(workspace_path, skill_name)
    if skill_dir.exists():
        raise SkillsApiError(f"skill directory already exists: {skill_name}", status=409)

    content = (markdown or "").strip() or _default_skill_markdown(skill_name, desc)
    _validate_skill_markdown(content, expected_name=skill_name)
    skill_dir.mkdir(parents=True, exist_ok=False)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    clear_skills_index_cache()

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

    skill_dir = workspace_layout.workspace_skill_dir(workspace_path, skill_name)
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
    clear_skills_index_cache()
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
    skill_dir = workspace_layout.workspace_skill_dir(workspace_path, skill_name)
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
        raise SkillsApiError("workspace skill not found", status=404)

    # Refuse to delete anything that looks like a path escape / weird name.
    allowed_bases = (
        workspace_layout.skills_dir(workspace_path).resolve(),
        workspace_layout.legacy_skills_dir(workspace_path).resolve(),
        (workspace_path / "skills").resolve(),
    )
    resolved = skill_dir.resolve()
    if not any(
        base == resolved.parent or base in resolved.parents
        for base in allowed_bases
    ):
        raise SkillsApiError("invalid skill path", status=400)

    shutil.rmtree(skill_dir)
    clear_skills_index_cache()
    return webui_skills_payload(workspace_path, disabled_skills=disabled_skills)


def resolve_skill_scan_root(raw: str | None, fallback: Path) -> Path:
    """Resolve the project folder to scan for ``.<tool>/skill(s)`` trees."""
    from navin.utils.host import normalize_host_path

    text = (raw or "").strip() or str(fallback)
    try:
        root = normalize_host_path(text)
    except ValueError as exc:
        raise SkillsApiError("workspace folder not found", status=404) from exc
    if not root.is_dir():
        raise SkillsApiError("workspace folder not found", status=404)
    return root


def resolve_skill_apply_root(
    *,
    scope: str | None,
    workspace: Path | None,
    fallback: Path,
) -> Path:
    """Where Install skill writes: everywhere = ``~/.navin/skills``, else the project."""
    kind = (scope or "workspace").strip().lower()
    if kind in {"everywhere", "user", "home", "all"}:
        return Path.home()
    if workspace is not None:
        return workspace
    return fallback


def owned_skill_dest(apply_root: Path) -> Path:
    return workspace_layout.coalesce_owned_skills(apply_root)


def skill_already_present(apply_root: Path, name: str) -> bool:
    for base in (
        workspace_layout.skills_dir(apply_root),
        workspace_layout.legacy_skills_dir(apply_root),
        apply_root / "skills",
    ):
        if resolve_skill_file(base, name) is not None:
            return True
    return False


def copy_skill_tree(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=_ignore_skill_copy)


def publish_plugin_skills(
    plugin_name: str,
    apply_root: Path,
    *,
    plugins_dir: Path | None = None,
) -> list[str]:
    """Copy a pack's ``skills/<name>`` folders into ``.navin/skills``."""
    from navin.plugins.manager import plugins_root

    src_root = (plugins_dir or plugins_root()) / plugin_name / "skills"
    dest_root = owned_skill_dest(apply_root)
    copied: list[str] = []
    if not src_root.is_dir():
        return copied
    dest_root.mkdir(parents=True, exist_ok=True)
    for child in sorted(src_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or not (child / "SKILL.md").is_file():
            continue
        dest = dest_root / child.name
        if dest.is_dir() and (dest / "SKILL.md").is_file():
            continue
        if dest.exists():
            continue
        copy_skill_tree(child, dest)
        copied.append(child.name)
    return copied


def discover_workspace_skills(
    scan_root: Path,
    *,
    catalog_workspace: Path,
    apply_root: Path | None = None,
) -> dict[str, Any]:
    """List SKILL.md playbooks found under the project, without importing them."""
    found = _discover_skill_entries(scan_root)
    dest_root = apply_root or catalog_workspace
    skills = []
    for entry in found:
        skills.append(
            {
                "name": entry["name"],
                "description": entry["description"],
                "origin": entry["origin"],
                "already": skill_already_present(dest_root, entry["name"]),
                "markdown": entry.get("markdown") or "",
            }
        )
    return {
        "root": str(scan_root),
        "dest": str(owned_skill_dest(dest_root)),
        "skills": skills,
    }


def import_workspace_skills(
    catalog_workspace: Path,
    *,
    scan_root: Path,
    names: list[str] | None = None,
    apply_root: Path | None = None,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Copy selected project skills into ``.navin/skills`` so the agent owns them."""
    wanted = {normalize_skill_name(name) for name in (names or []) if str(name).strip()}
    found = _discover_skill_entries(scan_root)
    imported: list[str] = []
    skipped: list[dict[str, str]] = []
    dest_workspace = apply_root or scan_root
    dest_root = owned_skill_dest(dest_workspace)

    for entry in found:
        name = entry["name"]
        if wanted and name not in wanted:
            continue
        src = Path(entry["path"])
        if not _is_under(scan_root, src):
            skipped.append({"name": name, "reason": "outside workspace"})
            continue
        dest = dest_root / name
        if resolve_skill_file(dest_root, name) is not None:
            skipped.append({"name": name, "reason": "already added"})
            continue
        dest_root.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / "SKILL.md")
        else:
            copy_skill_tree(src, dest)
        imported.append(name)

    if wanted:
        known = {entry["name"] for entry in found}
        for name in sorted(wanted):
            if name not in known and name not in imported:
                skipped.append({"name": name, "reason": "not found"})

    clear_skills_index_cache()
    from navin.plugins import PluginManager

    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "dest": str(dest_root),
        "previews": skill_previews_from_dest(dest_root, imported),
        "plugins": PluginManager().list(),
        **webui_skills_payload(catalog_workspace, disabled_skills=disabled_skills),
    }


def _discover_skill_entries(scan_root: Path) -> list[dict[str, str]]:
    from navin.agent.project_agents import harness_dirs
    from navin.agent.skills import _dedup_paths, _harness_skill_dirs

    bases = _dedup_paths(
        [
            scan_root / "skills",
            scan_root / "skill",
            *_harness_skill_dirs(scan_root, harness_dirs(scan_root)),
        ]
    )
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for base in bases:
        if not base.is_dir():
            continue
        direct = base / "SKILL.md"
        if direct.is_file():
            entry = _entry_from_skill_file(scan_root, base, direct)
            if entry and entry["name"] not in seen:
                seen.add(entry["name"])
                found.append(entry)
        for _child_name, skill_file in iter_skill_files(base):
            skill_dir = skill_file.parent if skill_file.name.lower() == "skill.md" else skill_file
            entry = _entry_from_skill_file(scan_root, skill_dir, skill_file)
            if entry and entry["name"] not in seen:
                seen.add(entry["name"])
                found.append(entry)
    return found


def _entry_from_skill_file(
    scan_root: Path, skill_dir: Path, skill_file: Path
) -> dict[str, str] | None:
    loose = skill_file.is_file() and skill_file.name.lower() != "skill.md"
    default_name = skill_file.stem if loose else skill_dir.name
    stored = skill_file if loose else skill_dir
    meta = _read_skill_frontmatter(skill_file)
    raw_name = meta.get("name") if isinstance(meta.get("name"), str) else default_name
    try:
        name = normalize_skill_name(str(raw_name))
    except SkillsApiError:
        return None
    description = meta.get("description")
    desc = description.strip() if isinstance(description, str) and description.strip() else name
    try:
        origin = str(stored.relative_to(scan_root)).replace("\\", "/")
    except ValueError:
        origin = default_name
    markdown = ""
    try:
        markdown = skill_file.read_text(encoding="utf-8")
    except OSError:
        markdown = ""
    if len(markdown.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        markdown = markdown[:_MAX_MARKDOWN_BYTES]
    return {
        "name": name,
        "description": desc,
        "origin": origin,
        "path": str(stored),
        "markdown": markdown,
    }


def skill_preview_from_file(skill_file: Path) -> dict[str, str] | None:
    """Safe preview payload: name, description, SKILL.md body."""
    if not skill_file.is_file():
        return None
    try:
        markdown = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(markdown.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        markdown = markdown[:_MAX_MARKDOWN_BYTES]
    meta = _read_skill_frontmatter(skill_file)
    raw_name = meta.get("name") if isinstance(meta.get("name"), str) else skill_file.parent.name
    try:
        name = normalize_skill_name(str(raw_name))
    except SkillsApiError:
        name = re.sub(r"[^a-z0-9]+", "-", skill_file.parent.name.lower()).strip("-") or "skill"
    description = meta.get("description")
    desc = description.strip() if isinstance(description, str) and description.strip() else name
    return {"name": name, "description": desc, "markdown": markdown}


def skill_previews_from_dest(dest_root: Path, names: list[str]) -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []
    for name in names:
        preview = skill_preview_from_file(dest_root / name / "SKILL.md")
        if preview:
            previews.append(preview)
    return previews


def _read_skill_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", text, re.DOTALL)
    if not match:
        return {}
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _is_under(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _ignore_skill_copy(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {".git", "__pycache__", "node_modules", ".venv", ".DS_Store"}
    }


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
    navin_meta = loader._get_skill_meta(name)
    default_for = navin_meta.get("default_for")
    return {
        "name": name,
        "description": _description(metadata, name),
        "source": source,
        "category": _category(loader, name, source),
        "default_for": default_for.strip().lower()
        if isinstance(default_for, str) and default_for.strip()
        else None,
        "available": available,
        "unavailable_reason": unavailable_reason,
        "editable": source == "workspace",
        "deletable": source == "workspace",
    }


def _category(loader: SkillsLoader, name: str, source: str) -> str:
    navin_meta = loader._get_skill_meta(name)
    category = navin_meta.get("category")
    if isinstance(category, str) and category.strip():
        raw = category.strip().lower()
        return "careers" if raw == "career" else raw
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
