# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Audit plan: which levels are reachable, which skills, scanners and controls.

The plan is honest about what it cannot do: a dynamic level needs a running
target, an architecture level needs accounts per role and two tenants, the
adversarial level needs an explicit authorization. Missing inputs are listed
so the report can say what was not covered rather than imply it was.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from navin.security import catalog
from navin.security.orchestrator.discovery import Discovery
from navin.security.orchestrator.skill_router import route_skills
from navin.security.orchestrator.threat_model import ThreatModel


@dataclass
class AuditInputs:
    """What the user handed over for this audit."""

    base_url: str = ""
    accounts: dict[str, str] = field(default_factory=dict)  # role -> account label
    tenants: list[str] = field(default_factory=list)
    authorized_targets: list[str] = field(default_factory=list)
    isolated_environment: bool = False
    human_approval: bool = False
    cloud_read_access: bool = False
    ci_integration: bool = False


@dataclass
class PlannedStep:
    id: str
    level: str
    engine: str
    controls: list[str]
    reason: str


@dataclass
class AuditPlan:
    levels: list[str]
    skills: list[str]
    scanners: list[str]
    controls: list[str]
    steps: list[PlannedStep]
    missing_inputs: dict[str, list[str]]
    not_covered: list[str]
    skill_routes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reachable_levels(inputs: AuditInputs) -> tuple[list[str], dict[str, list[str]]]:
    """Levels the inputs allow, and what each unreachable one still needs."""
    levels = ["L1"]
    missing: dict[str, list[str]] = {}
    l2_missing = [] if inputs.base_url else ["base_url"]
    if l2_missing:
        missing["L2"] = l2_missing
    else:
        levels.append("L2")
    l3_missing = list(l2_missing)
    if len(inputs.accounts) < 2:
        l3_missing.append("accounts for at least two roles")
    if len(inputs.tenants) < 2:
        l3_missing.append("two test tenants")
    if l3_missing:
        missing["L3"] = l3_missing
    else:
        levels.append("L3")
    l4_missing = list(l3_missing)
    if not inputs.authorized_targets:
        l4_missing.append("explicit list of authorized targets")
    if not inputs.isolated_environment:
        l4_missing.append("isolated environment with synthetic data")
    if not inputs.human_approval:
        l4_missing.append("human approval before sensitive operations")
    if l4_missing:
        missing["L4"] = l4_missing
    else:
        levels.append("L4")
    if inputs.ci_integration:
        levels.append("L5")
    else:
        missing["L5"] = ["CI integration (SARIF upload on every commit / PR)"]
    return levels, missing


def select_skills(found: Discovery, model: ThreatModel) -> list[str]:
    """Skills whose triggers match the discovered surfaces, plus the always-on ones."""
    # Chaining and remediation follow findings; the router adds them from the
    # correlation when there is something to chain or fix, not up-front.
    return [route.id for route in route_skills(found.surfaces)]


def plan_audit(found: Discovery, model: ThreatModel, inputs: AuditInputs | None = None, *, available_scanners: set[str] | None = None) -> AuditPlan:
    inputs = inputs or AuditInputs(base_url=found.base_url)
    levels, missing = reachable_levels(inputs)
    routes = route_skills(found.surfaces)
    skills = [route.id for route in routes]
    top_families = set(model.top_families(limit=18))
    controls: list[catalog.Control] = []
    for item in catalog.CONTROLS:
        if item.level not in levels:
            continue
        if not set(item.families) & top_families:
            continue
        if item.skill not in skills and item.skill not in {"security-sast-review", "security-compliance"}:
            continue
        controls.append(item)
    # Native controls only matter when a native target exists.
    if not ({"mobile", "desktop"} & set(found.surfaces)):
        controls = [c for c in controls if c.group != "G5"]
    if "ai" not in found.surfaces:
        controls = [c for c in controls if c.group != "G6"]

    wanted_scanners: list[str] = []
    for item in controls:
        for scanner in item.scanners:
            if scanner not in wanted_scanners:
                wanted_scanners.append(scanner)
    if available_scanners is not None:
        usable = [s for s in wanted_scanners if s in available_scanners or s.startswith("navin_")]
    else:
        usable = wanted_scanners

    steps: list[PlannedStep] = []
    for scanner in usable:
        covered = [c.id for c in controls if scanner in c.scanners]
        level_of = min((c.level for c in controls if scanner in c.scanners), default="L1")
        steps.append(PlannedStep(id=f"{level_of.lower()}-{scanner}", level=level_of, engine=scanner, controls=covered, reason=f"covers {len(covered)} planned control(s)"))
    steps.sort(key=lambda s: (catalog.LEVEL_IDS.index(s.level), -len(s.controls), s.engine))

    covered_ids = {c.id for c in controls}
    not_covered = [c.id for c in catalog.CONTROLS if c.id not in covered_ids]
    return AuditPlan(
        levels=levels,
        skills=skills,
        scanners=usable,
        controls=[c.id for c in controls],
        steps=steps,
        missing_inputs=missing,
        not_covered=not_covered,
        skill_routes=[route.to_dict() for route in routes],
    )
