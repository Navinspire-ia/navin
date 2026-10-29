# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage report: what was checked, what was found, what was not assessed.

A control reads ``clean`` only when an engine that really checks it ran and
reported nothing; with no such engine it is ``not_assessed`` and the report
says which engines would cover it. Open findings set the status of their
controls whatever ran. Frameworks (ASVS, WSTG, CWE, OWASP Top 10, API Top 10,
MASVS, LLM Top 10, CIS, NIST SSDF) are rolled up from the control references.
A clean coverage is never a statement that the application is secure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from navin.security import catalog
from navin.security.model import Finding, risk_score, severity_counts, status_counts

_RANK = {"clean": 0, "potential": 1, "confirmed": 2, "exploitable": 3}
_NO_ENGINE = "no engine that checks it ran"
_BY_HAND = "no automated check covers it yet: check it by hand, then tick it"
_FRAMEWORK_OF_REF: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^CWE-\d+$"), "mitre_cwe"),
    (re.compile(r"^ASVS\b"), "owasp_asvs_5"),
    (re.compile(r"^WSTG-"), "owasp_wstg"),
    (re.compile(r"^API\d+:2023$"), "owasp_api_top10"),
    (re.compile(r"^A\d\d:2025\b"), "owasp_top10_2025"),
    (re.compile(r"^MASVS"), "owasp_masvs"),
    (re.compile(r"^LLM\d\d:2025"), "owasp_llm_top10"),
    (re.compile(r"^OWASP Agentic"), "owasp_agentic_ai"),
    (re.compile(r"^CIS\b"), "cis_benchmarks"),
    (re.compile(r"^NIST SSDF"), "nist_ssdf"),
)
_TEXT = {
    "title": {"fr": "Couverture de l'audit Navin Security", "en": "Navin Security audit coverage", "ar": "تغطية تدقيق Navin Security"},
    "assessed": {"fr": "controles evalues", "en": "controls assessed", "ar": "ضوابط تم تقييمها"},
    "findings": {"fr": "Constats ouverts", "en": "Open findings", "ar": "النتائج المفتوحة"},
    "frameworks": {"fr": "Referentiels", "en": "Frameworks", "ar": "الأطر المرجعية"},
    "not_assessed": {"fr": "Non evalues", "en": "Not assessed", "ar": "لم يتم تقييمها"},
    "partial": {"fr": "Verifies en statique seulement", "en": "Checked statically only", "ar": "تم فحصها بشكل ثابت فقط"},
    "warning": {
        "fr": "Un controle propre signifie qu'aucun moteur lance n'a trouve de faille; ce n'est pas une preuve de securite.",
        "en": "A clean control means no engine that ran found a weakness; it is not proof that the application is secure.",
        "ar": "الضابط السليم يعني أن المحركات التي تم تشغيلها لم تجد ثغرة؛ وهذا ليس دليلا على أن التطبيق آمن.",
    },
    "needs": {"fr": "necessite", "en": "needs", "ar": "يتطلب"},
}


@dataclass
class ControlCoverage:
    id: str
    status: str  # clean | potential | confirmed | exploitable | not_assessed
    findings: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    reason: str = ""
    # Checked by an engine that ran, but the control's level was not reached
    # (a static pass over an L3 tenant-isolation control): never a full clean.
    partial: bool = False


@dataclass
class Coverage:
    controls: dict[str, ControlCoverage]
    frameworks: dict[str, dict[str, Any]]
    totals: dict[str, Any]
    accepted: list[str] = field(default_factory=list)

    def status_of(self, control_id: str) -> str:
        return self.controls[control_id].status

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Coverage:
        """Rebuild a saved coverage (``to_dict`` output); unknown keys are ignored."""
        fields = {"id", "status", "findings", "engines", "reason", "partial"}
        controls: dict[str, ControlCoverage] = {}
        for cid, raw in (data.get("controls") or {}).items():
            if isinstance(raw, dict):
                controls[str(cid)] = ControlCoverage(**{k: v for k, v in raw.items() if k in fields})
        for item in catalog.CONTROLS:  # an older audit may predate a control
            controls.setdefault(item.id, ControlCoverage(item.id, "not_assessed", engines=list(item.scanners),
                                                         reason="not in this audit"))
        return cls(controls=controls, frameworks=dict(data.get("frameworks") or {}),
                   totals=dict(data.get("totals") or {}), accepted=list(data.get("accepted") or []))

    def to_dict(self) -> dict[str, Any]:
        return {
            "controls": {cid: vars(item) for cid, item in self.controls.items()},
            "frameworks": self.frameworks,
            "totals": self.totals,
            "accepted": self.accepted,
        }


def frameworks_of(control: catalog.Control) -> set[str]:
    out: set[str] = set()
    for ref in control.references:
        for pattern, framework_id in _FRAMEWORK_OF_REF:
            if pattern.search(ref):
                out.add(framework_id)
    for family_id in control.families:
        if catalog.family(family_id).owasp_2025:
            out.add("owasp_top10_2025")
    return out


def coverage(
    findings: list[Finding],
    *,
    checked: set[str],
    planned: set[str] | None = None,
    levels: list[str] | None = None,
    ran: set[str] | None = None,
) -> Coverage:
    """Coverage of the catalog by this audit.

    ``checked`` is the set of controls the engines that ran really check (see
    ``navin.security.analyzers.checked_controls``); ``planned`` limits the
    audit to some controls (others read ``not_assessed`` unless a finding hits
    them); ``levels`` are the levels the audit reached: a checked control above
    them is clean only ``partial``-ly, with what a full check needs. ``ran``
    (engine ids) narrows what a not-assessed control still needs to the
    engines that did not run.
    """
    open_items = [f for f in findings if f.validation.state in {"open", "accepted"}]
    reached = set(levels) if levels is not None else None
    controls: dict[str, ControlCoverage] = {}
    for item in catalog.CONTROLS:
        if item.id in checked and (planned is None or item.id in planned):
            entry = ControlCoverage(item.id, "clean", engines=sorted(set(item.scanners)))
            if reached is not None and item.level not in reached:
                needed = catalog.level(item.level)
                entry.partial = True
                entry.reason = f"checked statically only; a full check needs {item.level} ({', '.join(needed.needs)})"
            controls[item.id] = entry
        else:
            reason = "not planned in this audit" if planned is not None and item.id not in planned else _NO_ENGINE
            engines = [e for e in item.scanners if ran is None or e not in ran]
            if not engines and reason == _NO_ENGINE:
                reason = _BY_HAND
            controls[item.id] = ControlCoverage(item.id, "not_assessed", engines=engines, reason=reason)
    for finding in open_items:
        for cid in finding.controls:
            entry = controls.get(cid)
            if entry is None:
                continue
            entry.findings.append(finding.fingerprint())
            if entry.status == "not_assessed" or _RANK[finding.status] > _RANK[entry.status]:
                entry.status = finding.status
                entry.reason = ""
                entry.partial = False
    frameworks: dict[str, dict[str, Any]] = {}
    for fw in catalog.FRAMEWORKS:
        members = [c for c in catalog.CONTROLS if fw.id in frameworks_of(c)]
        if not members:
            continue
        statuses = [controls[c.id].status for c in members]
        frameworks[fw.id] = {
            "name": fw.name,
            "controls": len(members),
            "assessed": sum(1 for s in statuses if s != "not_assessed"),
            "with_findings": sum(1 for s in statuses if s in {"potential", "confirmed", "exploitable"}),
            "not_assessed": sum(1 for s in statuses if s == "not_assessed"),
        }
    score, grade = risk_score([f for f in open_items if f.validation.state == "open"])
    assessed = sum(1 for c in controls.values() if c.status != "not_assessed")
    totals = {
        "controls": len(controls),
        "assessed": assessed,
        "not_assessed": len(controls) - assessed,
        "partial": sum(1 for c in controls.values() if c.partial),
        "by_status": status_counts([f for f in open_items if f.validation.state == "open"]),
        "by_severity": severity_counts([f for f in open_items if f.validation.state == "open"]),
        "score": score,
        "grade": grade,
    }
    accepted = [f.fingerprint() for f in open_items if f.validation.state == "accepted"]
    return Coverage(controls=controls, frameworks=frameworks, totals=totals, accepted=accepted)


def coverage_markdown(report: Coverage, *, lang: str = "en") -> str:
    key = lang if lang in {"fr", "en", "ar"} else "en"
    t = {name: values[key] for name, values in _TEXT.items()}
    totals = report.totals
    lines = [
        f"# {t['title']}",
        "",
        f"{totals['assessed']}/{totals['controls']} {t['assessed']} - {totals['score']}/100 ({totals['grade']})",
        "",
        f"> {t['warning']}",
        "",
        f"## {t['findings']}",
        "",
    ]
    by_status = totals["by_status"]
    lines.append(" - ".join(f"{catalog.STATUS_LABELS[s][key]}: {n}" for s, n in by_status.items()))
    lines += ["", f"## {t['frameworks']}", ""]
    for fw in report.frameworks.values():
        lines.append(f"- {fw['name']}: {fw['assessed']}/{fw['controls']}, {fw['with_findings']} with findings")
    partial = [item for item in catalog.CONTROLS if report.controls[item.id].partial]
    if partial:
        lines += ["", f"## {t['partial']}", ""]
        for item in partial:
            needs = ", ".join(catalog.level(item.level).needs)
            lines.append(f"- {item.id} {item.label[key]} ({item.level}, {t['needs']}: {needs})")
    lines += ["", f"## {t['not_assessed']}", ""]
    for item in catalog.CONTROLS:
        entry = report.controls[item.id]
        if entry.status == "not_assessed":
            if entry.engines and entry.reason in {"", _NO_ENGINE}:
                lines.append(f"- {item.id} {item.label[key]} ({t['needs']}: {', '.join(entry.engines)})")
            else:
                lines.append(f"- {item.id} {item.label[key]} ({entry.reason})")
    return "\n".join(lines).rstrip() + "\n"
