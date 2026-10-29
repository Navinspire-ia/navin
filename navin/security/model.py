# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Normalized security finding shared by every Navin Security engine.

Every scanner, analyzer and verification step produces or enriches the same
record, so the orchestrator can dedupe, correlate, chain and report without
knowing which engine spoke. Three result statuses are kept apart on purpose:

- ``potential``: a weakness a rule or a model suspects (static evidence only);
- ``confirmed``: the weakness was observed on the running target or traced
  from a user-controlled source to the sink (dynamic or dataflow evidence);
- ``exploitable``: a non-destructive reproduction demonstrated the impact in
  an authorized environment.

A scanner finishing clean never means the application is secure; the record
therefore also carries what was covered (level, controls) so the report can
say what was not.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info")
STATUSES: tuple[str, ...] = ("potential", "confirmed", "exploitable")
LEVELS: tuple[str, ...] = ("L1", "L2", "L3", "L4", "L5")
EVIDENCE_KINDS: tuple[str, ...] = ("static", "dataflow", "dynamic", "reproduction", "regression")
VALIDATION_STATES: tuple[str, ...] = ("open", "fixed", "accepted", "false_positive")
RESCAN_STATES: tuple[str, ...] = ("pending", "clean", "still_present")
IMPACTS: tuple[str, ...] = ("exfiltration", "escalation", "fraud", "compromise", "outage", "disclosure")
EXPOSURES: tuple[str, ...] = ("internet", "authenticated", "internal", "local", "unknown")

_FAMILY_IDS = frozenset(
    "injection authentication authorization multitenancy code_execution files api secrets "
    "cryptography supply_chain infrastructure frontend business_logic availability network "
    "mobile_desktop ai_agents audit_logging".split()
)
_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}
_STATUS_RANK = {name: index for index, name in enumerate(STATUSES)}


@dataclass
class Evidence:
    """One observation backing a finding. ``request`` is always non destructive."""

    kind: str
    description: str
    tool: str = ""
    request: str = ""
    observed: str = ""
    location: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind {self.kind!r}; expected one of {EVIDENCE_KINDS}")
        self.description = str(self.description or "").strip()[:2000]
        self.request = str(self.request or "")[:4000]
        self.observed = str(self.observed or "")[:4000]


@dataclass
class Validation:
    """Where the remediation of a finding stands."""

    state: str = "open"
    regression_tests: list[str] = field(default_factory=list)
    rescan: str = "pending"
    note: str = ""

    def __post_init__(self) -> None:
        if self.state not in VALIDATION_STATES:
            raise ValueError(f"unknown validation state {self.state!r}")
        if self.rescan not in RESCAN_STATES:
            raise ValueError(f"unknown rescan state {self.rescan!r}")


@dataclass
class Finding:
    """A normalized security finding."""

    id: str
    family: str
    title: str
    severity: str = "medium"
    status: str = "potential"
    cwe: str = ""
    cve: str = ""
    owasp: str = ""
    file_path: str = ""
    line: int | None = None
    endpoint: str = ""
    component: str = ""
    cvss: float | None = None
    epss: float | None = None
    exposure: str = "unknown"
    privileges_required: str = ""
    data_at_risk: str = ""
    impact: str = ""
    description: str = ""
    remediation: str = ""
    patch: str = ""
    confidence: float = 0.5
    source: str = "navin"
    level: str = "L1"
    controls: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    validation: Validation = field(default_factory=Validation)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.severity = str(self.severity or "medium").strip().lower()
        if self.severity not in _SEVERITY_RANK:
            self.severity = "medium"
        if self.status not in _STATUS_RANK:
            raise ValueError(f"unknown finding status {self.status!r}; expected one of {STATUSES}")
        if self.family not in _FAMILY_IDS:
            raise ValueError(f"unknown family {self.family!r}; expected one of {sorted(_FAMILY_IDS)}")
        if self.level not in LEVELS:
            raise ValueError(f"unknown level {self.level!r}; expected one of {LEVELS}")
        if self.exposure not in EXPOSURES:
            self.exposure = "unknown"
        if self.impact and self.impact not in IMPACTS:
            raise ValueError(f"unknown impact {self.impact!r}; expected one of {IMPACTS}")
        self.confidence = min(1.0, max(0.0, float(self.confidence)))
        self.title = str(self.title or "").strip()[:240]
        self.description = str(self.description or "").strip()[:4000]
        self.remediation = str(self.remediation or "").strip()[:4000]
        self.file_path = str(self.file_path or "").strip()[:500]
        if self.line is not None:
            self.line = int(self.line)
        # Evidence decides the status: a dataflow trace or a dynamic observation
        # confirms, a reproduction demonstrates; the status never goes back down.
        for item in self.evidence:
            self._promote_for(item)

    @property
    def key(self) -> str:
        """Dedupe key: same weakness class at the same place is one finding.

        A dependency advisory is keyed on the advisory and the package instead:
        two different CVEs in one lockfile share the file and often the CWE,
        never the key, while the same CVE reported by two scanners merges.
        """
        if self.family == "supply_chain" and self.component:
            return "|".join([self.family, self.cve or self.id, self.file_path, self.component])
        bucket = (self.line // 5) if isinstance(self.line, int) else -1
        return "|".join(
            [self.family, self.cwe or self.id, self.file_path, self.endpoint, str(bucket)]
        )

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]

    @property
    def status_rank(self) -> int:
        return _STATUS_RANK[self.status]

    def promote(self, status: str) -> None:
        """Raise the status; a finding never silently goes back down."""
        if status not in _STATUS_RANK:
            raise ValueError(f"unknown finding status {status!r}")
        if _STATUS_RANK[status] > self.status_rank:
            self.status = status

    def add_evidence(self, item: Evidence) -> None:
        self.evidence.append(item)
        self._promote_for(item)

    def _promote_for(self, item: Evidence) -> None:
        if item.kind == "reproduction":
            self.promote("exploitable")
        elif item.kind in {"dynamic", "dataflow"}:
            self.promote("confirmed")

    def fingerprint(self) -> str:
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fingerprint"] = self.fingerprint()
        return data


def finding_from_dict(raw: dict[str, Any]) -> Finding:
    """Build a Finding from a loose dict (scanner JSON, legacy deep_scan rows)."""
    evidence = [
        item if isinstance(item, Evidence) else Evidence(**_evidence_fields(item))
        for item in (raw.get("evidence") or [])
    ]
    validation_raw = raw.get("validation")
    if isinstance(validation_raw, Validation):
        validation = validation_raw
    elif isinstance(validation_raw, dict):
        validation = Validation(
            state=str(validation_raw.get("state") or "open"),
            regression_tests=list(validation_raw.get("regression_tests") or []),
            rescan=str(validation_raw.get("rescan") or "pending"),
            note=str(validation_raw.get("note") or ""),
        )
    else:
        validation = Validation()
    line = raw.get("line")
    try:
        line_n = int(line) if line not in (None, "") else None
    except (TypeError, ValueError):
        line_n = None
    return Finding(
        id=str(raw.get("id") or raw.get("rule_id") or "finding").strip()[:120],
        family=str(raw.get("family") or "injection").strip(),
        title=str(raw.get("title") or raw.get("summary") or raw.get("message") or "").strip(),
        severity=str(raw.get("severity") or "medium"),
        status=str(raw.get("status") or "potential"),
        cwe=str(raw.get("cwe") or "").strip()[:40],
        cve=str(raw.get("cve") or "").strip()[:40],
        owasp=str(raw.get("owasp") or "").strip()[:80],
        file_path=str(raw.get("file_path") or raw.get("file") or raw.get("path") or ""),
        line=line_n,
        endpoint=str(raw.get("endpoint") or "").strip()[:300],
        component=str(raw.get("component") or "").strip()[:200],
        cvss=_optional_float(raw.get("cvss")),
        epss=_optional_float(raw.get("epss")),
        exposure=str(raw.get("exposure") or "unknown"),
        privileges_required=str(raw.get("privileges_required") or "")[:120],
        data_at_risk=str(raw.get("data_at_risk") or "")[:300],
        impact=str(raw.get("impact") or ""),
        description=str(raw.get("description") or raw.get("explanation") or ""),
        remediation=str(raw.get("remediation") or raw.get("recommendation") or ""),
        patch=str(raw.get("patch") or "")[:8000],
        confidence=float(raw.get("confidence") if raw.get("confidence") is not None else 0.5),
        source=str(raw.get("source") or "navin")[:60],
        level=str(raw.get("level") or "L1"),
        controls=[str(c) for c in (raw.get("controls") or [])],
        references=[str(r) for r in (raw.get("references") or [])],
        evidence=evidence,
        validation=validation,
        tags=[str(t) for t in (raw.get("tags") or [])],
    )


def _evidence_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(item.get("kind") or "static"),
        "description": str(item.get("description") or ""),
        "tool": str(item.get("tool") or ""),
        "request": str(item.get("request") or ""),
        "observed": str(item.get("observed") or ""),
        "location": str(item.get("location") or ""),
    }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def merge_findings(findings: list[Finding]) -> list[Finding]:
    """Dedupe by key, keeping the strongest severity and status, merging evidence.

    Two independent sources agreeing on the same key raise the confidence: a
    weakness seen by a rule engine and by a dataflow trace is less likely a
    false positive than either alone.
    """
    merged: dict[str, Finding] = {}
    sources: dict[str, set[str]] = {}
    for item in findings:
        key = item.key
        previous = merged.get(key)
        if previous is None:
            merged[key] = item
            sources[key] = {item.source}
            continue
        sources[key].add(item.source)
        if item is previous:
            # The same object listed twice: appending its evidence to itself
            # while iterating it never ends and fills the machine's memory.
            continue
        if item.severity_rank < previous.severity_rank:
            previous.severity = item.severity
        previous.promote(item.status)
        for evidence in list(item.evidence):
            if evidence not in previous.evidence:
                previous.evidence.append(evidence)
        for ref in item.references:
            if ref not in previous.references:
                previous.references.append(ref)
        for control in item.controls:
            if control not in previous.controls:
                previous.controls.append(control)
        if not previous.cwe and item.cwe:
            previous.cwe = item.cwe
        if not previous.remediation and item.remediation:
            previous.remediation = item.remediation
        previous.confidence = max(previous.confidence, item.confidence)
    for key, item in merged.items():
        if len(sources[key]) > 1:
            item.confidence = min(1.0, item.confidence + 0.15 * (len(sources[key]) - 1))
            item.tags = sorted(set(item.tags) | {"multi-source"})
    return sort_findings(list(merged.values()))


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Exploitable before confirmed before potential, then by severity."""
    return sorted(
        findings,
        key=lambda f: (-f.status_rank, f.severity_rank, -f.confidence, f.file_path, f.line or 0),
    )


def severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {name: 0 for name in SEVERITIES}
    for item in findings:
        counts[item.severity] += 1
    return counts


def status_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {name: 0 for name in STATUSES}
    for item in findings:
        counts[item.status] += 1
    return counts


def risk_score(findings: list[Finding]) -> tuple[int, str]:
    """0-100 score and A-F grade; confirmed and exploitable weigh more.

    A potential critical costs 10, a confirmed one 20 and an exploitable one
    30; lower severities scale down. Capped per severity so a thousand infos
    cannot mask one exploitable critical.
    """
    weight = {"critical": 10.0, "high": 6.0, "medium": 3.0, "low": 1.0, "info": 0.0}
    multiplier = {"potential": 1.0, "confirmed": 2.0, "exploitable": 3.0}
    cap = {"critical": 60.0, "high": 40.0, "medium": 25.0, "low": 10.0, "info": 0.0}
    penalty = 0.0
    per_severity: dict[str, float] = {name: 0.0 for name in SEVERITIES}
    for item in findings:
        per_severity[item.severity] += weight[item.severity] * multiplier[item.status]
    for name, value in per_severity.items():
        penalty += min(value, cap[name])
    score = max(0, min(100, round(100 - penalty)))
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    elif score >= 20:
        grade = "E"
    else:
        grade = "F"
    return score, grade
