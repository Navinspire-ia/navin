# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings > Team in the terminal: the forms behind the hub's Team section.

The desktop's Settings > Team edits the config key ``collab`` through
``navin.webui.collab_api.update_collab``; the terminal builds the very same
payloads here (teammates, named teams, contacts), so both editors validate and
save one team the same way. Pure helpers only: the screen lives in
``navin.tui.settings``.
"""

from __future__ import annotations

import re
from typing import Any

from navin.collab.team import (
    AVATAR_KEYS,
    MEMBER_EFFORTS,
    PATTERN_KEYS,
    ROLE_DEFAULT_EFFORT,
    ROLE_KEYS,
    ROLE_PROMPTS,
    ROLE_SKILLS,
    ROLE_TOOLS,
    default_member,
)
from navin.tui.screens import FormField

# Same names and order as the desktop's pattern picker.
PATTERN_LABELS: dict[str, str] = {
    "dev_tester": "Developer and Tester",
    "dual_planning": "Dual Planning",
    "adversarial_review": "Adversarial Review",
    "parallel_solutions": "Parallel Solutions",
    "pipeline": "Architect, Developer, Reviewer",
    "debate": "Consensus and Debate",
}
PATTERN_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, PATTERN_LABELS.get(key, key.replace("_", " ").capitalize())) for key in PATTERN_KEYS
)
SANDBOX_OPTIONS: tuple[tuple[str, str], ...] = (
    ("workspace", "Shared project"),
    ("worktree", "Own git worktree"),
    ("readonly", "Read only"),
)
# Every effort the team accepts (the defaults use xhigh and max), as the desktop lists them.
EFFORT_LABELS: dict[str, str] = {
    "": "Auto", "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max",
}
EFFORT_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, EFFORT_LABELS.get(key, key.capitalize())) for key in MEMBER_EFFORTS
)
GENDER_OPTIONS: tuple[tuple[str, str], ...] = (("", "Not set"), ("female", "Female"), ("male", "Male"))
ROLE_OPTIONS: tuple[tuple[str, str], ...] = tuple((role, role.capitalize()) for role in ROLE_KEYS)
AVATAR_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, "Drawn face" if key == "person" else key.capitalize()) for key in AVATAR_KEYS
)

_CONTACT = re.compile(r"^\s*(?P<name>[^<]*?)\s*<\s*(?P<email>[^>]+?)\s*>\s*$")


def split_names(text: str) -> list[str]:
    """``a, b ,a`` -> ``["a", "b"]``."""
    out: list[str] = []
    for part in str(text or "").split(","):
        value = part.strip()
        if value and value not in out:
            out.append(value)
    return out


def format_contacts(contacts: Any) -> str:
    """``[{name, email}]`` -> ``Ana <ana@x.io>, bob@y.io``."""
    parts: list[str] = []
    for item in contacts or []:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip()
        name = str(item.get("name") or "").strip()
        if not email:
            continue
        parts.append(email if not name or name == email else f"{name} <{email}>")
    return ", ".join(parts)


def parse_contacts(text: str) -> list[dict[str, str]]:
    """``Ana <ana@x.io>, bob@y.io`` -> ``[{name, email}]`` (checked by update_collab)."""
    out: list[dict[str, str]] = []
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        match = _CONTACT.match(part)
        if match:
            email = match["email"].strip()
            out.append({"name": match["name"].strip() or email, "email": email})
        else:
            out.append({"name": part, "email": part})
    return out


def preset_options(catalog: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    rows = catalog.get("presets") or [{"name": "default", "label": "Main model", "model": ""}]
    out: list[tuple[str, str]] = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name:
            continue
        label = str(row.get("label") or name)
        model = str(row.get("model") or "")
        out.append((name, f"{label} / {model}" if model else label))
    return tuple(out)


def member_line(member: dict[str, Any], presets: dict[str, str]) -> str:
    """``Senior Developer · Main model`` (+ ``· off`` when not available)."""
    job = str(member.get("title") or "").strip() or str(member.get("role") or "custom").capitalize()
    preset = str(member.get("model_preset") or "default")
    bits = [job, presets.get(preset, preset)]
    if not member.get("enabled", True):
        bits.append("off")
    return " · ".join(bits)


def member_form_fields(
    member: dict[str, Any] | None,
    catalog: dict[str, Any],
    *,
    teams: list[dict[str, Any]] | None = None,
    default_team: str = "",
) -> list[FormField]:
    """The teammate editor: every field of the desktop's except the photo."""
    m = member or default_member("developer")
    fields = [
        FormField("name", "Name", value=str(m.get("name") or ""), placeholder="Sofia"),
        FormField("title", "Job title", required=False, value=str(m.get("title") or ""),
                  placeholder="Backend Developer"),
        FormField("role", "Role", kind="select", options=ROLE_OPTIONS, value=str(m.get("role") or "custom"),
                  help="The role decides the default tools, skills and prompt."),
        FormField("specialty", "Specialty", required=False, value=str(m.get("specialty") or "")),
        FormField("email", "Email", required=False, value=str(m.get("email") or ""),
                  placeholder="sofia@team.navin.local", help="Signs this teammate's commits."),
        FormField("gender", "Gender", kind="select", options=GENDER_OPTIONS, value=str(m.get("gender") or ""),
                  help="Live gives the teammate a voice of the same gender."),
        FormField("voice", "Live voice", required=False, value=str(m.get("voice") or ""),
                  placeholder="auto (from the gender)"),
        FormField("model_preset", "Model", kind="select", options=preset_options(catalog),
                  value=str(m.get("model_preset") or "default"), help="Each teammate really runs on this model."),
        FormField("reasoning_effort", "Reasoning effort", kind="select", options=EFFORT_OPTIONS,
                  value=str(m.get("reasoning_effort") or "")),
        FormField("sandbox", "Sandbox", kind="select", options=SANDBOX_OPTIONS,
                  value=str(m.get("sandbox") or "workspace")),
        FormField("avatar", "Face", kind="select", options=AVATAR_OPTIONS, value=str(m.get("avatar") or "round")),
        FormField("color", "Color", required=False, value=str(m.get("color") or ""), placeholder="#2f7de1"),
        FormField("tools", "Tools", required=False,
                  value=", ".join(m.get("tools") or []) if member else "",
                  help="Comma separated. Empty: the role's tools."),
        FormField("skills", "Skills", required=False,
                  value=", ".join(m.get("skills") or []) if member else "",
                  help="Comma separated. Empty: the role's skills."),
        FormField("enabled", "Available", kind="toggle", value="true" if m.get("enabled", True) else "false"),
        FormField("edit_prompt", "Edit the prompt next", kind="toggle", value="false"),
    ]
    if member is None and teams:
        fields.insert(1, FormField(
            "team", "Add to team", kind="select",
            options=(("", "No team"), *((str(t["id"]), str(t["name"])) for t in teams)),
            value=default_team,
        ))
    return fields


def apply_member_answer(member: dict[str, Any] | None, answer: dict[str, str]) -> dict[str, Any]:
    """The teammate the form describes, ready for ``update_collab``.

    A changed role brings its own tools, skills, prompt and effort wherever the
    teammate still had the old role's defaults; anything customised stays.
    """
    role = answer.get("role") if answer.get("role") in ROLE_KEYS else "custom"
    old_role = str((member or {}).get("role") or role)
    base = dict(member) if member else default_member(role)
    tools = split_names(answer.get("tools", ""))
    skills = split_names(answer.get("skills", ""))
    if not tools or (role != old_role and tools == list(ROLE_TOOLS.get(old_role, ()))):
        tools = list(ROLE_TOOLS[role])
    if not skills or (role != old_role and skills == list(ROLE_SKILLS.get(old_role, ()))):
        skills = list(ROLE_SKILLS[role])
    prompt = str(base.get("prompt") or "")
    effort = answer.get("reasoning_effort", base.get("reasoning_effort") or "")
    if role != old_role:
        if prompt == ROLE_PROMPTS.get(old_role, ""):
            prompt = ROLE_PROMPTS[role]
        if effort == ROLE_DEFAULT_EFFORT.get(old_role, ""):
            effort = ROLE_DEFAULT_EFFORT[role]
    base.update(
        name=answer.get("name", "").strip(),
        title=answer.get("title", "").strip(),
        role=role,
        specialty=answer.get("specialty", "").strip(),
        email=answer.get("email", "").strip(),
        gender=answer.get("gender", ""),
        voice=answer.get("voice", "").strip(),
        model_preset=answer.get("model_preset") or "default",
        reasoning_effort=effort or None,
        sandbox=answer.get("sandbox") or "workspace",
        avatar=answer.get("avatar") or "round",
        color=answer.get("color", "").strip() or str(base.get("color") or "#6b7280"),
        tools=tools,
        skills=skills,
        prompt=prompt,
        enabled=answer.get("enabled", "true") == "true",
    )
    if not member:
        base["id"] = ""
    return base


def _member_field(member_id: str) -> str:
    return f"m_{member_id}"


def team_form_fields(team: dict[str, Any] | None, members: list[dict[str, Any]]) -> list[FormField]:
    """A team: its name, then one checkbox per teammate."""
    chosen = set((team or {}).get("members") or [])
    fields = [FormField("name", "Team name", value=str((team or {}).get("name") or ""), placeholder="Backend")]
    for member in members:
        job = str(member.get("title") or "").strip()
        label = f"{member.get('name')} - {job}" if job else str(member.get("name"))
        fields.append(FormField(
            _member_field(str(member["id"])), label, kind="toggle",
            value="true" if member["id"] in chosen else "false",
        ))
    return fields


def apply_team_answer(
    team: dict[str, Any] | None, answer: dict[str, str], members: list[dict[str, Any]]
) -> dict[str, Any]:
    ids = [str(m["id"]) for m in members if answer.get(_member_field(str(m["id"]))) == "true"]
    return {"id": str((team or {}).get("id") or ""), "name": answer.get("name", "").strip(), "members": ids}


__all__ = [
    "PATTERN_OPTIONS",
    "apply_member_answer",
    "apply_team_answer",
    "format_contacts",
    "member_form_fields",
    "member_line",
    "parse_contacts",
    "preset_options",
    "split_names",
    "team_form_fields",
]
