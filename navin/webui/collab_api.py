# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings > Team: read and save the Collaboration Mode team."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from navin.collab.team import (
    ALL_TOOLS,
    AVATAR_KEYS,
    PATTERN_KEYS,
    ROLE_DEFAULT_EFFORT,
    ROLE_DEFAULT_SANDBOX,
    ROLE_KEYS,
    ROLE_PROMPTS,
    ROLE_SKILLS,
    ROLE_TOOLS,
    SANDBOX_KEYS,
    default_team,
    slugify,
)
from navin.config.loader import load_config, save_config
from navin.config.schema import CollabConfig
from navin.webui.settings_api import WebUISettingsError

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")
_MAX_MEMBERS = 32
_MAX_PROMPT = 8000
_MAX_TEAMS = 24


def _skills_catalog(workspace: Any) -> list[dict[str, str]]:
    from navin.agent.skills import SkillsLoader

    try:
        loader = SkillsLoader(workspace)
        rows = []
        for entry in loader.list_skills(filter_unavailable=False):
            name = entry.get("name") or ""
            meta = loader.get_skill_metadata(name) or {}
            rows.append({"name": name, "description": str(meta.get("description") or "")[:200]})
        return sorted(rows, key=lambda r: r["name"])
    except Exception:  # noqa: BLE001 - the editor still works with typed names
        return []


def collab_payload() -> dict[str, Any]:
    config = load_config()
    presets = [{"name": "default", "label": "Main model", "model": config.agents.defaults.model}]
    for name, preset in config.model_presets.items():
        if getattr(preset, "modality", "text") != "text" or not preset.enabled:
            continue
        presets.append({"name": name, "label": preset.label or name, "model": preset.model})
    from navin.collab.mail import smtp_status

    return {
        "collab": config.collab.model_dump(),
        "email": smtp_status(),
        "catalog": {
            "roles": [
                {
                    "key": role,
                    "tools": list(ROLE_TOOLS[role]),
                    "skills": list(ROLE_SKILLS[role]),
                    "prompt": ROLE_PROMPTS[role],
                    "sandbox": ROLE_DEFAULT_SANDBOX[role],
                    "effort": ROLE_DEFAULT_EFFORT[role],
                }
                for role in ROLE_KEYS
            ],
            "tools": list(ALL_TOOLS),
            "skills": _skills_catalog(config.workspace_path),
            "presets": presets,
            "avatars": list(AVATAR_KEYS),
            "sandboxes": list(SANDBOX_KEYS),
            "patterns": list(PATTERN_KEYS),
            "efforts": ["", "low", "medium", "high"],
        },
    }


def _clean_member(raw: Any, index: int, presets: set[str], seen: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WebUISettingsError("invalid teammate")
    name = str(raw.get("name") or "").strip()[:40]
    if not name:
        raise WebUISettingsError(f"Teammate {index + 1} needs a name", field="name")
    member_id = slugify(str(raw.get("id") or "")) or slugify(name) or f"member-{index + 1}"
    base, n = member_id, 2
    while member_id in seen:
        member_id, n = f"{base}-{n}", n + 1
    seen.add(member_id)
    email = str(raw.get("email") or "").strip()[:254]
    if email and not _EMAIL.match(email):
        raise WebUISettingsError(f"{name}: invalid email", field="email")
    color = str(raw.get("color") or "").strip()
    preset = str(raw.get("model_preset") or raw.get("modelPreset") or "default").strip() or "default"
    if preset not in presets:
        raise WebUISettingsError(f"{name}: unknown model {preset!r}", field="model_preset")
    prompt = str(raw.get("prompt") or "")
    if len(prompt) > _MAX_PROMPT:
        raise WebUISettingsError(f"{name}: prompt is too long ({_MAX_PROMPT} characters max)", field="prompt")

    def names(key: str) -> list[str]:
        values = raw.get(key) or []
        if not isinstance(values, list):
            return []
        out: list[str] = []
        for value in values:
            text = str(value).strip()[:80]
            if text and text not in out:
                out.append(text)
        return out[:80]

    effort = raw.get("reasoning_effort", raw.get("reasoningEffort"))
    return {
        "id": member_id,
        "name": name,
        "role": raw.get("role") if raw.get("role") in ROLE_KEYS else "custom",
        "title": str(raw.get("title") or "").strip()[:60],
        "specialty": str(raw.get("specialty") or "").strip()[:200],
        "email": email,
        "avatar": raw.get("avatar") if raw.get("avatar") in AVATAR_KEYS else "round",
        "color": color if _COLOR.match(color) else "#6b7280",
        "model_preset": preset,
        "reasoning_effort": (str(effort).strip() or None) if effort else None,
        "sandbox": raw.get("sandbox") if raw.get("sandbox") in SANDBOX_KEYS else "workspace",
        "prompt": prompt,
        "tools": [t for t in names("tools") if t in ALL_TOOLS],
        "skills": names("skills"),
        "enabled": bool(raw.get("enabled", True)),
    }


def _clean_teams(raw: Any, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Named teams: unique ids, known teammates only (a renamed or deleted
    teammate simply leaves the teams it was in)."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > _MAX_TEAMS:
        raise WebUISettingsError(f"0 to {_MAX_TEAMS} teams")
    known = {m["id"] for m in members}
    teams: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, team in enumerate(raw):
        if not isinstance(team, dict):
            raise WebUISettingsError("invalid team")
        name = str(team.get("name") or "").strip()[:40]
        if not name:
            raise WebUISettingsError(f"Team {index + 1} needs a name", field="team")
        team_id = slugify(str(team.get("id") or "")) or slugify(name) or f"team-{index + 1}"
        base, n = team_id, 2
        while team_id in seen or team_id == "off":
            team_id, n = f"{base}-{n}", n + 1
        seen.add(team_id)
        ids = []
        for member_id in team.get("members") or []:
            member_id = str(member_id)
            if member_id in known and member_id not in ids:
                ids.append(member_id)
        teams.append({"id": team_id, "name": name, "members": ids})
    return teams


def update_collab(values: dict[str, Any]) -> dict[str, Any]:
    """Validate and save the whole team (the editor always sends all of it)."""
    config = load_config()
    if values.get("reset"):
        # Back to the default teammates and teams; the switch and the rules stay.
        fresh = CollabConfig()
        config.collab.members = fresh.members
        config.collab.teams = fresh.teams
        config.collab.default_team = fresh.default_team
        save_config(config)
        return collab_payload()
    current = config.collab.model_dump()
    presets = {"default", *config.model_presets.keys()}
    if "members" in values:
        raw_members = values.get("members")
        if not isinstance(raw_members, list) or len(raw_members) > _MAX_MEMBERS:
            raise WebUISettingsError(f"A team has 0 to {_MAX_MEMBERS} teammates")
        seen: set[str] = set()
        current["members"] = [_clean_member(m, i, presets, seen) for i, m in enumerate(raw_members)]
        lowered = [m["name"].lower() for m in current["members"]]
        if len(set(lowered)) != len(lowered):
            raise WebUISettingsError("Two teammates cannot share a name", field="name")
    for key, alias in (("enabled", "enabled"), ("auto_feedback", "autoFeedback"),
                       ("human_approval_critical", "humanApprovalCritical")):
        if key in values or alias in values:
            current[key] = bool(values.get(key, values.get(alias)))
    for key, alias, limit in (("manager_name", "managerName", 80), ("manager_email", "managerEmail", 254)):
        if key in values or alias in values:
            current[key] = str(values.get(key, values.get(alias)) or "").strip()[:limit]
    if current.get("manager_email") and not _EMAIL.match(current["manager_email"]):
        raise WebUISettingsError("invalid manager email", field="manager_email")
    if "email_allow_any" in values or "emailAllowAny" in values:
        current["email_allow_any"] = bool(values.get("email_allow_any", values.get("emailAllowAny")))
    if "email_contacts" in values or "emailContacts" in values:
        raw = values.get("email_contacts", values.get("emailContacts")) or []
        if not isinstance(raw, list) or len(raw) > 50:
            raise WebUISettingsError("0 to 50 email contacts")
        contacts: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip()[:254]
            name = str(item.get("name") or "").strip()[:80]
            if not email and not name:
                continue
            if not _EMAIL.match(email):
                raise WebUISettingsError(f"{name or email}: invalid email", field="email_contacts")
            if all(c["email"].lower() != email.lower() for c in contacts):
                contacts.append({"name": name or email, "email": email})
        current["email_contacts"] = contacts
    if "pattern" in values:
        if values["pattern"] not in PATTERN_KEYS:
            raise WebUISettingsError("unknown collaboration mode", field="pattern")
        current["pattern"] = values["pattern"]
    for key, alias, low, high in (("max_rounds", "maxRounds", 1, 20), ("budget_tokens", "budgetTokens", 0, 50_000_000)):
        if key in values or alias in values:
            try:
                number = int(values.get(key, values.get(alias)))
            except (TypeError, ValueError) as exc:
                raise WebUISettingsError(f"invalid {key}", field=key) from exc
            current[key] = max(low, min(high, number))
    if "teams" in values or "members" in values:
        current["teams"] = _clean_teams(values.get("teams", current.get("teams")), current["members"])
    if "default_team" in values or "defaultTeam" in values:
        current["default_team"] = str(values.get("default_team", values.get("defaultTeam")) or "")
    team_ids = [team["id"] for team in current["teams"]]
    if current.get("default_team") not in team_ids:
        current["default_team"] = team_ids[0] if team_ids else ""
    try:
        config.collab = CollabConfig.model_validate(current)
    except ValidationError as exc:
        raise WebUISettingsError(f"invalid team settings: {exc.errors()[0].get('msg')}") from exc
    save_config(config)
    return collab_payload()


__all__ = ["collab_payload", "default_team", "update_collab"]


def send_test_email() -> dict[str, Any]:
    """Settings > Team: prove the team can really reach the manager."""
    from navin.collab.mail import CollabMailError, send_manager_email

    collab = load_config().collab
    try:
        to = send_manager_email(
            collab,
            sender={"id": "lead", "name": "Navin", "title": "Orchestrator"},
            subject="Navin team: test email",
            body=(
                "Hello,\n\nThis is a test from your Navin team. Teammates will write to this "
                "address for finished deliverables, decisions that need a human, and blockers."
            ),
            session_key="settings",
        )
    except CollabMailError as exc:
        raise WebUISettingsError(str(exc), status=409) from exc
    return {"ok": True, "to": to}
