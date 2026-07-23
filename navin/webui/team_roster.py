"""Persisted WebUI team roster.

The roster describes the virtual organization shown in the Team studio:
members with a role, specialty, avatar, skills, and a reporting line.
Each member maps to a workflow command so chat calls (@member) route to
the right mission brief. UI-only metadata, stored next to the sidebar
state in the instance webui directory.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_webui_dir

TEAM_ROSTER_SCHEMA_VERSION = 1
_MAX_FILE_BYTES = 256 * 1024
_MAX_MEMBERS = 50
_MAX_NAME_LEN = 80
_MAX_ROLE_LEN = 120
_MAX_TEXT_LEN = 400
_MAX_SKILLS = 16
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_ALLOWED_COMMANDS = {
    "/team",
    "/forge",
    "/blueprint",
    "/inspect",
    "/fortify",
    "/probe",
    "/turbo",
    "/pulse",
    "/studio",
    "/campaign",
    "/seo",
    "/leads",
}


def team_roster_path() -> Path:
    return get_webui_dir() / "team-roster.json"


def default_team_roster() -> dict[str, Any]:
    """Starter organization: a chief of staff and six specialized leads."""
    members = [
        {
            "id": "chief",
            "name": "Atlas",
            "role": "Chief of Staff",
            "specialty": "Coordination, org design, priorities, virtual AI teams",
            "avatar": "🧭",
            "command": "/team",
            "skills": ["org-designer", "virtual-team-builder", "task-planner"],
            "manager": None,
        },
        {
            "id": "dev-lead",
            "name": "Forge",
            "role": "Engineering Lead",
            "specialty": "Architecture, code, reviews, security, performance",
            "avatar": "🛠️",
            "command": "/forge",
            "skills": ["code-review", "security-audit", "performance-analyst"],
            "manager": "chief",
        },
        {
            "id": "marketing-lead",
            "role": "Marketing Lead",
            "name": "Nova",
            "specialty": "Campaigns, ads, social content, product visuals, video",
            "avatar": "📣",
            "command": "/campaign",
            "skills": ["campaign-manager", "ad-creative-generator", "social-media-manager"],
            "manager": "chief",
        },
        {
            "id": "seo-lead",
            "name": "Vector",
            "role": "SEO Lead",
            "specialty": "Technical audits, keywords, optimized content, backlinks",
            "avatar": "📈",
            "command": "/seo",
            "skills": ["seo-technical-auditor", "keyword-research", "seo-content-writer"],
            "manager": "chief",
        },
        {
            "id": "sales-lead",
            "name": "Compass",
            "role": "Sales Lead",
            "specialty": "Prospecting, buying signals, qualification, outreach",
            "avatar": "🎯",
            "command": "/leads",
            "skills": ["lead-prospector", "buying-signals", "outreach-sequencer"],
            "manager": "chief",
        },
        {
            "id": "hr-lead",
            "name": "Mentor",
            "role": "HR & Talent Lead",
            "specialty": "Hiring, job descriptions, screening, onboarding, growth",
            "avatar": "🤝",
            "command": "/team",
            "skills": ["recruitment-agent", "candidate-screening", "job-description-writer"],
            "manager": "chief",
        },
        {
            "id": "docs-lead",
            "name": "Scribe",
            "role": "Documents Lead",
            "specialty": "Decks, reports, contracts, spreadsheets, templates",
            "avatar": "📄",
            "command": "/studio",
            "skills": ["pptx-generator", "docx-generator", "spreadsheet-analyst"],
            "manager": "chief",
        },
    ]
    return {
        "schema_version": TEAM_ROSTER_SCHEMA_VERSION,
        "members": members,
        "updated_at": None,
    }


def _clean_str(value: Any, *, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_member(raw: Any, seen_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    member_id = _clean_str(raw.get("id"), max_len=64)
    if member_id is None or not _ID_RE.match(member_id) or member_id in seen_ids:
        return None
    name = _clean_str(raw.get("name"), max_len=_MAX_NAME_LEN)
    role = _clean_str(raw.get("role"), max_len=_MAX_ROLE_LEN)
    if name is None or role is None:
        return None
    specialty = _clean_str(raw.get("specialty"), max_len=_MAX_TEXT_LEN) or ""
    avatar = _clean_str(raw.get("avatar"), max_len=8) or "👤"
    command = raw.get("command")
    if command not in _ALLOWED_COMMANDS:
        command = "/team"
    skills: list[str] = []
    raw_skills = raw.get("skills")
    if isinstance(raw_skills, list):
        for item in raw_skills[:_MAX_SKILLS]:
            cleaned = _clean_str(item, max_len=64)
            if cleaned and cleaned not in skills:
                skills.append(cleaned)
    manager = _clean_str(raw.get("manager"), max_len=64)
    seen_ids.add(member_id)
    return {
        "id": member_id,
        "name": name,
        "role": role,
        "specialty": specialty,
        "avatar": avatar,
        "command": command,
        "skills": skills,
        "manager": manager,
    }


def normalize_team_roster(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return default_team_roster()
    raw_members = raw.get("members")
    if not isinstance(raw_members, list):
        return default_team_roster()
    seen: set[str] = set()
    members: list[dict[str, Any]] = []
    for item in raw_members[:_MAX_MEMBERS]:
        cleaned = _clean_member(item, seen)
        if cleaned is not None:
            members.append(cleaned)
    # Drop dangling manager references (and self-references).
    ids = {m["id"] for m in members}
    for member in members:
        if member["manager"] is not None and (
            member["manager"] not in ids or member["manager"] == member["id"]
        ):
            member["manager"] = None
    updated_at = raw.get("updated_at")
    return {
        "schema_version": TEAM_ROSTER_SCHEMA_VERSION,
        "members": members,
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


def read_team_roster() -> dict[str, Any]:
    path = team_roster_path()
    if not path.is_file():
        return default_team_roster()
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            logger.warning("team roster too large, ignoring: {}", path)
            return default_team_roster()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read team roster failed {}: {}", path, e)
        return default_team_roster()
    return normalize_team_roster(raw)


def write_team_roster(raw: dict[str, Any]) -> dict[str, Any]:
    roster = normalize_team_roster(raw)
    roster["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(roster, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        raise ValueError("team roster is too large")

    path = team_roster_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return roster
