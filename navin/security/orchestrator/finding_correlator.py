# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Correlate findings: dedupe across engines, map to controls, chain attack paths.

A chain is a sequence of weaknesses where each step widens what the previous
one gave: an IDOR exposes a response, the response carries a secret, the
secret grants a privileged service. Chains are hypotheses to verify in an
authorized sandbox, never claims; they are reported as such.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from navin.security import catalog
from navin.security.model import Finding, merge_findings

# family A -> family B means: a finding of A can be amplified by a finding of B.
_CHAIN_LINKS: dict[str, dict[str, str]] = {
    "authorization": {
        "secrets": "an unauthorized read returns a response that carries a secret",
        "api": "an unauthorized read exposes more fields than the UI shows",
        "multitenancy": "an id swap crosses the tenant boundary",
    },
    "multitenancy": {
        "secrets": "cross-tenant data includes another tenant's tokens or keys",
        "business_logic": "cross-tenant write alters another tenant's prices or credits",
    },
    "secrets": {
        "infrastructure": "the secret grants access to a cloud or cluster resource",
        "supply_chain": "the secret can publish or alter artifacts in CI",
        "api": "the key calls a privileged API as the service",
    },
    "injection": {
        "secrets": "the injection reads configuration or credential tables",
        "code_execution": "the injection escalates to code execution",
        "authentication": "the injection bypasses the login check",
    },
    "files": {
        "secrets": "traversal reads .env or key files",
        "code_execution": "an uploaded file is executed",
    },
    "network": {
        "infrastructure": "SSRF reaches the metadata service or internal admin ports",
        "secrets": "SSRF returns cloud credentials",
    },
    "authentication": {
        "authorization": "a forged token selects a privileged role",
    },
    "code_execution": {
        "infrastructure": "code execution inside a privileged container escapes to the host",
    },
    "ai_agents": {
        "secrets": "injected instructions make the agent read and send secrets",
        "code_execution": "injected instructions run a shell tool",
    },
    "frontend": {
        "authentication": "DOM XSS steals the session token",
    },
    "supply_chain": {
        "code_execution": "a malicious dependency runs at install or at runtime",
    },
    "infrastructure": {
        "secrets": "a public bucket or exposed service reveals credentials",
    },
}

_IMPACT_RANK = {"disclosure": 1, "outage": 2, "fraud": 3, "exfiltration": 4, "escalation": 5, "compromise": 6}


@dataclass
class AttackPath:
    id: str
    title: str
    steps: list[dict[str, str]]
    families: list[str]
    severity: str
    impact: str
    status: str = "hypothesis"  # hypothesis | verified | refuted
    verification: str = "reproduce in an authorized sandbox before reporting as exploitable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CorrelationResult:
    findings: list[Finding]
    attack_paths: list[AttackPath]
    control_status: dict[str, str]  # control id -> clean | potential | confirmed | exploitable | not_assessed
    families_hit: dict[str, int]
    follow_up_skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "attack_paths": [p.to_dict() for p in self.attack_paths],
            "control_status": self.control_status,
            "families_hit": self.families_hit,
            "follow_up_skills": self.follow_up_skills,
        }


def map_controls(finding: Finding) -> list[str]:
    """Controls a finding bears on: declared ones first, else by family + CWE."""
    if finding.controls:
        return list(finding.controls)
    hits: list[str] = []
    for item in catalog.controls_for_family(finding.family):
        if finding.cwe and finding.cwe in item.references:
            hits.insert(0, item.id)
        elif not finding.cwe:
            hits.append(item.id)
    if not hits:
        hits = [c.id for c in catalog.controls_for_family(finding.family)[:1]]
    return hits[:3]


def correlate(findings: list[Finding], *, planned_controls: list[str] | None = None) -> CorrelationResult:
    merged = merge_findings(findings)
    for item in merged:
        item.controls = map_controls(item)

    families_hit: dict[str, int] = {}
    for item in merged:
        families_hit[item.family] = families_hit.get(item.family, 0) + 1

    control_status: dict[str, str] = {}
    planned = set(planned_controls or [])
    for control in catalog.CONTROLS:
        if planned and control.id not in planned:
            control_status[control.id] = "not_assessed"
        else:
            control_status[control.id] = "clean"
    rank = {"clean": 0, "potential": 1, "confirmed": 2, "exploitable": 3}
    for item in merged:
        for cid in item.controls:
            current = control_status.get(cid, "clean")
            if current == "not_assessed" or rank[item.status] > rank.get(current, 0):
                control_status[cid] = item.status

    paths = chain_attack_paths(merged)
    follow_up: list[str] = []
    if paths:
        follow_up.append("security-attack-chaining")
    if merged:
        follow_up.append("security-remediation")
    return CorrelationResult(merged, paths, control_status, families_hit, follow_up)


def chain_attack_paths(findings: list[Finding], *, max_paths: int = 12) -> list[AttackPath]:
    """Two- and three-step chains between findings of linked families."""
    by_family: dict[str, list[Finding]] = {}
    for item in findings:
        by_family.setdefault(item.family, []).append(item)
    paths: list[AttackPath] = []
    seen: set[tuple[str, ...]] = set()
    for fam_a, links in _CHAIN_LINKS.items():
        for first in by_family.get(fam_a, []):
            for fam_b, why in links.items():
                for second in by_family.get(fam_b, []):
                    if second is first:
                        continue
                    steps = [first, second]
                    key = tuple(f.fingerprint() for f in steps)
                    if key in seen:
                        continue
                    seen.add(key)
                    paths.append(_path(steps, [why]))
                    for fam_c, why_c in _CHAIN_LINKS.get(fam_b, {}).items():
                        for third in by_family.get(fam_c, []):
                            if third in steps:
                                continue
                            key3 = key + (third.fingerprint(),)
                            if key3 in seen:
                                continue
                            seen.add(key3)
                            paths.append(_path([first, second, third], [why, why_c]))
    paths.sort(key=lambda p: (-len(p.steps), -_IMPACT_RANK.get(p.impact, 0), p.severity))
    return paths[:max_paths]


def _path(steps: list[Finding], reasons: list[str]) -> AttackPath:
    worst = min(steps, key=lambda f: f.severity_rank)
    impact = max((f.impact for f in steps if f.impact), key=lambda i: _IMPACT_RANK.get(i, 0), default="")
    if not impact:
        impact = "compromise" if len(steps) >= 3 else "exfiltration"
    step_rows = []
    for index, item in enumerate(steps):
        step_rows.append({
            "finding": item.fingerprint(),
            "family": item.family,
            "title": item.title,
            "location": item.endpoint or item.file_path,
            "link": reasons[index - 1] if index > 0 else "entry point",
        })
    title = " -> ".join(catalog.family(f.family).name["en"] for f in steps)
    return AttackPath(
        id="-".join(f.fingerprint()[:6] for f in steps),
        title=title,
        steps=step_rows,
        families=[f.family for f in steps],
        severity=worst.severity,
        impact=impact,
    )
