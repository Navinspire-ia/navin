# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persistent weighted search allocation, independent of provider execution."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


def role_shares(roles: list[str], priorities: dict[str, float]) -> dict[str, float]:
    specified = [role for role in roles if role in priorities]
    total = sum(priorities[role] for role in specified)
    automatic = max(0, 100 - total) / (len(roles) - len(specified)) if len(roles) > len(specified) else 0
    weights = {role: priorities.get(role, automatic) for role in roles}
    total = sum(weights.values())
    return {role: weight / total if total else 0 for role, weight in weights.items()}


def role_scope(criteria: dict[str, Any], role: str) -> dict[str, Any]:
    if not role:
        return criteria
    # Matrix defaults for another role must not narrow this role's query. Manual
    # skills remain shared, and removed defaults are never restored here.
    matrix = criteria.get("role_skills", {})
    known = {skill.casefold() for skills in matrix.values() for skill in skills}
    relevant = {skill.casefold() for skill in matrix.get(role, [])}
    generated = {skill.casefold() for skill in criteria.get("autofill", {}).get("generated", {}).get("skills", [])}
    skills = [skill for skill in criteria["skills"] if skill.casefold() in relevant or skill.casefold() not in (known | generated)]
    return {**criteria, "roles": [role], "domain": "", "skills": skills}


def plan_search(tasks: list[dict[str, Any]], schedule: dict[str, Any], budget: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Smooth weighted rotation with no repeated task within a search cycle.

    Exhausted groups release their remaining budget to other roles. Credits
    and source/country offsets survive daily runs so small weights get a turn.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if task["weight"] > 0:
            groups[task["group"]].append(task)
    signature = hashlib.sha256(json.dumps([(task["id"], task["weight"]) for task in tasks], ensure_ascii=False).encode()).hexdigest()
    previous = schedule if schedule.get("signature") == signature else {}
    credits = {group: previous.get("credits", {}).get(group, 0.0) for group in groups}
    offsets = {group: previous.get("offsets", {}).get(group, 0) % len(rows) for group, rows in groups.items()}
    pending = {group: rows[offsets[group]:] + rows[:offsets[group]] for group, rows in groups.items()}
    planned = []
    while len(planned) < budget and pending:
        total = sum(rows[0]["weight"] for rows in pending.values())
        for group, rows in pending.items():
            credits[group] += rows[0]["weight"]
        chosen = max(pending, key=lambda group: credits[group])
        credits[chosen] -= total
        planned.append(pending[chosen].pop(0))
        offsets[chosen] = (offsets[chosen] + 1) % len(groups[chosen])
        if not pending[chosen]:
            del pending[chosen]
    return planned, {"signature": signature, "credits": credits, "offsets": offsets}
