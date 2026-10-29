# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skill router: which ``security-*`` playbooks an audit loads, and why.

The stack decides first: each skill of the catalog lists the surfaces that
call for it (a database calls for injection, a Dockerfile for containers) and
a few are always on. Findings decide next: a finding in a family routes the
skill that owns that family even when discovery missed the surface, and the
correlation adds attack chaining and remediation when there is something to
chain or fix. Every route names its reason, the controls the skill covers,
the registered scanners those controls use and the SKILL.md to read, so the
plan says what each playbook is for instead of listing names.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from navin.security import catalog
from navin.security.scanners.registry import REGISTRY

if TYPE_CHECKING:
    from navin.security.orchestrator.finding_correlator import CorrelationResult

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"

_FOLLOW_UP_REASONS = {
    "security-attack-chaining": "attack paths link several findings",
    "security-remediation": "findings to fix and validate",
}


@dataclass
class SkillRoute:
    id: str
    reasons: list[str]
    triggers: list[str]
    controls: list[str]
    scanners: list[str]
    path: str
    teaches: dict[str, str] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reason"] = self.reason
        return data


def skill_path(skill_id: str, root: Path = SKILLS_ROOT) -> Path | None:
    """SKILL.md of a catalog skill, or None when the playbook is not installed."""
    catalog.skill(skill_id)
    path = root / skill_id / "SKILL.md"
    return path if path.is_file() else None


def skill_controls(skill_id: str) -> list[str]:
    """Catalog controls this skill is the playbook for."""
    return [c.id for c in catalog.CONTROLS if c.skill == skill_id]


def skill_scanners(skill_id: str) -> list[str]:
    """Registered external scanners the skill's controls rely on, registry order."""
    used = {s for c in catalog.CONTROLS if c.skill == skill_id for s in c.scanners}
    return [sid for sid in REGISTRY if sid in used]


def _family_owners() -> dict[str, str]:
    return {f.id: f.skill for f in catalog.FAMILIES}


def route_skills(
    surfaces: Iterable[str],
    correlation: CorrelationResult | None = None,
    *,
    root: Path = SKILLS_ROOT,
) -> list[SkillRoute]:
    """Skills for this stack (and these findings), in catalog order."""
    seen = set(surfaces)
    owners = _family_owners()
    families_hit = dict(correlation.families_hit) if correlation is not None else {}
    follow_up = set(correlation.follow_up_skills) if correlation is not None else set()
    routes: list[SkillRoute] = []
    for spec in catalog.SKILLS:
        reasons: list[str] = []
        triggers: list[str] = []
        if "always" in spec.triggers:
            reasons.append("always on")
            triggers.append("always")
        matched = [t for t in spec.triggers if t in seen]
        if matched:
            reasons.append("stack: " + ", ".join(matched))
            triggers.extend(matched)
        owned_hits = [f"{fam} ({count})" for fam, count in families_hit.items() if count and owners.get(fam) == spec.id]
        if owned_hits:
            reasons.append("findings: " + ", ".join(owned_hits))
            triggers.append("findings")
        if spec.id in follow_up:
            reasons.append(_FOLLOW_UP_REASONS.get(spec.id, "correlation follow-up"))
            if "findings" not in triggers:
                triggers.append("findings")
        if not reasons:
            continue
        path = skill_path(spec.id, root)
        routes.append(
            SkillRoute(
                id=spec.id,
                reasons=reasons,
                triggers=triggers,
                controls=skill_controls(spec.id),
                scanners=skill_scanners(spec.id),
                path=str(path) if path else "",
                teaches=dict(spec.teaches),
            )
        )
    return routes
