# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One Navin Security audit, end to end: discover, plan, analyze, scan, correlate, cover.

Every engine's outcome is recorded (ran, not installed, not applicable, timed
out, failed, skipped) and coverage is computed only from the engines that
ran: a missing scanner leaves its controls ``not_assessed``, never clean. The
audit itself is static (level L1); the plan says which inputs would let the
verification engine reach the dynamic and architecture levels. Results are
saved under the project's ``.navin/security/`` so the agent, the Cyber panel
and the CI read the same audit, and evidence is masked before it is saved.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from navin.security import catalog
from navin.security.analyzers import analyze_project, checked_controls
from navin.security.bridge import finding_from_legacy
from navin.security.deep_scan import run_deep_security_scan
from navin.security.model import Finding, finding_from_dict, merge_findings
from navin.security.orchestrator.discovery import Discovery, discover
from navin.security.orchestrator.finding_correlator import correlate
from navin.security.orchestrator.scan_planner import AuditInputs, AuditPlan, plan_audit
from navin.security.orchestrator.threat_model import build_threat_model
from navin.security.reports.compliance import Coverage, coverage
from navin.security.reports.sarif import to_sarif
from navin.security.scanners.registry import REGISTRY, detect_scanners, run_scanner
from navin.security.verification.probe import redact

STATE_DIR = Path(".navin") / "security"
REACHED_LEVELS: tuple[str, ...] = ("L1",)  # static engines; dynamic proofs go through the verification engine
_KEEP_AUDITS = 10
_LEGACY_MAX_FINDINGS = 1000
_QUOTED_SECRET = re.compile(r"""(["'`])([^"'`\s]{8,})\1""")
_ASSIGNED_SECRET = re.compile(r"(?m)^(\s*[A-Za-z_][A-Za-z0-9_.-]*\s*[=:]\s*)([^\s\"'`#]{8,})\s*$")
_GITIGNORE = "# Navin Security audit results: they quote code and paths, keep them local.\naudits/\nlast-audit.json\n*.sarif\n*.tmp\n"


class AuditStateError(RuntimeError):
    """A saved audit or checklist under .navin/security/ cannot be read."""


@dataclass
class EngineRun:
    id: str
    kind: str  # analyzer | rules | scanner
    status: str  # ok | not_installed | not_applicable | timeout | failed | invalid_output | skipped
    findings: int = 0
    duration_s: float = 0.0
    detail: str = ""
    network: bool = False


@dataclass
class AuditResult:
    id: str
    root: str
    started_at: str
    duration_s: float
    discovery: dict[str, Any]
    threats: list[str]
    plan: dict[str, Any]
    engines: list[EngineRun]
    findings: list[Finding]
    attack_paths: list[dict[str, Any]]
    coverage: Coverage
    follow_up_skills: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    levels_reached: list[str] = field(default_factory=lambda: list(REACHED_LEVELS))

    def not_run(self) -> dict[str, str]:
        """Engine id -> why it did not run, for the report and the SARIF notifications."""
        return {e.id: (f"{e.status}: {e.detail}" if e.detail else e.status) for e in self.engines if e.status != "ok"}

    def sarif(self, *, tool_version: str = "") -> dict[str, Any]:
        return to_sarif(self.findings, root=self.root, tool_version=tool_version, not_run=self.not_run())

    def to_dict(self, *, max_findings: int | None = None) -> dict[str, Any]:
        shown = self.findings if max_findings is None else self.findings[:max_findings]
        return {
            "id": self.id,
            "root": self.root,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "levels_reached": list(self.levels_reached),
            "discovery": self.discovery,
            "threats": list(self.threats),
            "plan": self.plan,
            "engines": [asdict(e) for e in self.engines],
            "findings": [f.to_dict() for f in shown],
            "findings_total": len(self.findings),
            "attack_paths": self.attack_paths,
            "coverage": self.coverage.to_dict(),
            "follow_up_skills": list(self.follow_up_skills),
            "skipped_files": list(self.skipped_files),
            "notes": list(self.notes),
        }


def _wanted_scanners(scanners: bool | Iterable[str]) -> set[str]:
    if scanners is True:
        return set(REGISTRY)
    if scanners is False:
        return set()
    wanted = {str(s).strip() for s in scanners if str(s).strip()}
    unknown = sorted(wanted - set(REGISTRY))
    if unknown:
        raise ValueError(f"unknown scanner {unknown[0]!r}; known: {', '.join(sorted(REGISTRY))}")
    return wanted


def _legacy_rules(base: Path, sink: list[Finding], notes: list[str]) -> EngineRun:
    clock = time.monotonic()
    result = run_deep_security_scan(base, kind="quick", max_findings=_LEGACY_MAX_FINDINGS)
    spent = round(time.monotonic() - clock, 2)
    if not result.get("ok"):
        return EngineRun("navin_rules", "rules", "failed", duration_s=spent, detail=str(result.get("error") or "")[:300])
    found: list[Finding] = []
    rejected = 0
    for row in result.get("findings") or []:
        if not isinstance(row, dict):
            continue
        try:
            found.append(finding_from_legacy(row, source="navin_rules"))
        except ValueError:
            rejected += 1
    if rejected:
        notes.append(f"{rejected} rule match(es) could not be normalized and were left out")
    merged = merge_findings(found)
    sink.extend(merged)
    return EngineRun("navin_rules", "rules", "ok", findings=len(merged), duration_s=spent)


def _scan(base: Path, wanted: set[str], scanners: bool | Iterable[str], *, offline: bool, timeout_s: int,
          deadline: float, budget_s: float, sink: list[Finding]) -> list[EngineRun]:
    statuses = detect_scanners(sorted(wanted)) if wanted else {}
    engines: list[EngineRun] = []
    for scanner_id, spec in REGISTRY.items():
        network = bool(spec.permissions.network)
        if scanner_id not in wanted:
            why = "scanners turned off for this audit" if scanners is False else "not requested"
            engines.append(EngineRun(scanner_id, "scanner", "skipped", detail=why, network=network))
            continue
        if offline and network:
            engines.append(EngineRun(scanner_id, "scanner", "skipped", detail="needs the network and the audit is offline", network=network))
            continue
        status = statuses.get(scanner_id)
        if status is None or not status.available:
            hint = spec.install_hint or (status.error if status else "")
            engines.append(EngineRun(scanner_id, "scanner", "not_installed", detail=hint, network=network))
            continue
        left = deadline - time.monotonic()
        if left < 5:
            engines.append(EngineRun(scanner_id, "scanner", "skipped", detail=f"the audit time budget ({budget_s:g} s) was used up", network=network))
            continue
        run = run_scanner(scanner_id, base, timeout_s=int(max(5, min(timeout_s, left))))
        engines.append(EngineRun(scanner_id, "scanner", run.status, findings=len(run.findings),
                                 duration_s=round(run.duration_s, 2), detail=run.detail[:300], network=network))
        if run.ok:
            sink.extend(run.findings)
    return engines


def mask_secret(text: str) -> str:
    """Keep the first four characters of a quoted or assigned secret value, mask the rest."""
    text = _QUOTED_SECRET.sub(lambda m: f"{m[1]}{m[2][:4]}****{m[1]}", text)
    return _ASSIGNED_SECRET.sub(lambda m: f"{m[1]}{m[2][:4]}****", text)


def _mask_evidence(findings: list[Finding]) -> None:
    for item in findings:
        for evidence in item.evidence:
            observed = redact(evidence.observed)
            evidence.observed = mask_secret(observed) if item.family == "secrets" else observed
            evidence.request = redact(evidence.request)


def _not_applicable(found: Discovery) -> dict[str, str]:
    surfaces = set(found.surfaces)
    reasons: dict[str, str] = {}
    if not ({"mobile", "desktop"} & surfaces):
        reasons["G5"] = "no mobile or desktop target found in the project"
    if "ai" not in surfaces:
        reasons["G6"] = "no AI, RAG or MCP component found in the project"
    return {c.id: reasons[c.group] for c in catalog.CONTROLS if c.group in reasons}


def _discovery_summary(found: Discovery) -> dict[str, Any]:
    data = found.to_dict()
    keep = ("files_seen", "languages", "frameworks", "databases", "auth", "api_styles", "payments",
            "infrastructure", "native", "ai", "features", "hardening", "surfaces")
    out = {key: data[key] for key in keep if key in data}
    out["manifests"] = list(data.get("manifests") or [])[:30]
    out["entrypoints"] = len(data.get("entrypoints") or [])
    return out


def _plan_summary(plan: AuditPlan) -> dict[str, Any]:
    return {
        "levels_reachable": list(plan.levels),
        "missing_inputs": dict(plan.missing_inputs),
        "skills": list(plan.skills),
        "controls_planned": len(plan.controls),
        "scanners": list(plan.scanners),
        "steps": [asdict(step) for step in plan.steps][:40],
    }


def make_plan(root: str | Path, inputs: AuditInputs | None = None) -> tuple[Discovery, AuditPlan, dict[str, Any]]:
    """What an audit can reach on *root* with these inputs, without scanning. Blocking."""
    base = Path(root).expanduser().resolve()
    inputs = inputs or AuditInputs()
    found = discover(base, base_url=inputs.base_url)
    model = build_threat_model(found)
    statuses = detect_scanners()
    available = {sid for sid, status in statuses.items() if status.available}
    plan = plan_audit(found, model, inputs, available_scanners=available)
    missing = {sid: REGISTRY[sid].install_hint for sid, status in statuses.items() if not status.available}
    return found, plan, missing


def run_audit(
    root: str | Path,
    *,
    inputs: AuditInputs | None = None,
    scanners: bool | Iterable[str] = True,
    offline: bool = False,
    scanner_timeout_s: int = 120,
    budget_s: float = 900.0,
) -> AuditResult:
    """Audit *root*. Blocking (minutes when scanners run): call it off the loop."""
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise NotADirectoryError(f"not a directory: {base}")
    clock = time.monotonic()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    inputs = inputs or AuditInputs()
    wanted = _wanted_scanners(scanners)
    found = discover(base, base_url=inputs.base_url)
    model = build_threat_model(found)

    findings: list[Finding] = []
    engines: list[EngineRun] = []
    notes: list[str] = []
    analysis = analyze_project(base)
    for analyzer_id in analysis.ran:
        count = sum(1 for f in analysis.findings if f.source == analyzer_id)
        engines.append(EngineRun(analyzer_id, "analyzer", "ok", findings=count,
                                 duration_s=analysis.durations.get(analyzer_id, 0.0)))
    findings.extend(analysis.findings)
    notes.extend(analysis.notes)
    engines.append(_legacy_rules(base, findings, notes))
    engines.extend(_scan(base, wanted, scanners, offline=offline, timeout_s=scanner_timeout_s,
                         deadline=clock + budget_s, budget_s=budget_s, sink=findings))
    available = {e.id for e in engines if e.kind == "scanner" and e.status != "not_installed"}
    plan = plan_audit(found, model, inputs, available_scanners=available)

    correlation = correlate(findings)
    merged = correlation.findings
    _mask_evidence(merged)
    ran = [e.id for e in engines if e.status == "ok"]
    excluded = _not_applicable(found)
    report = coverage(merged, checked=checked_controls(ran), planned={c.id for c in catalog.CONTROLS} - set(excluded),
                      levels=list(REACHED_LEVELS), ran=set(ran))
    for control_id, why in excluded.items():
        if report.controls[control_id].status == "not_assessed":
            report.controls[control_id].reason = why
    return AuditResult(
        id=f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
        root=str(base),
        started_at=started_at,
        duration_s=round(time.monotonic() - clock, 2),
        discovery=_discovery_summary(found),
        threats=list(model.top_families(limit=8)),
        plan=_plan_summary(plan),
        engines=engines,
        findings=merged,
        attack_paths=[path.to_dict() for path in correlation.attack_paths],
        coverage=report,
        follow_up_skills=list(correlation.follow_up_skills),
        skipped_files=list(analysis.skipped)[:200],
        notes=list(dict.fromkeys(notes)),
    )


# ---------------------------------------------------------------------------
# Saved state: .navin/security/
# ---------------------------------------------------------------------------


def state_dir(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / STATE_DIR


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditStateError(f"{path} could not be read: {exc}") from exc


def save_audit(root: str | Path, result: AuditResult) -> Path:
    """Save *result* as the project's last audit and keep the ten most recent ones."""
    folder = state_dir(root)
    audits = folder / "audits"
    audits.mkdir(parents=True, exist_ok=True)
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text(_GITIGNORE, encoding="utf-8")
    data = result.to_dict()
    path = audits / f"{result.id}.json"
    _write_json(path, data)
    _write_json(folder / "last-audit.json", data)
    for stale in sorted(audits.glob("*.json"))[:-_KEEP_AUDITS]:
        stale.unlink(missing_ok=True)
    return path


def load_last_audit(root: str | Path) -> dict[str, Any] | None:
    data = _read_json(state_dir(root) / "last-audit.json")
    return data if isinstance(data, dict) else None


def findings_of(audit: dict[str, Any]) -> list[Finding]:
    return [finding_from_dict(raw) for raw in audit.get("findings") or [] if isinstance(raw, dict)]


def coverage_of(audit: dict[str, Any]) -> Coverage:
    return Coverage.from_dict(audit.get("coverage") or {})


def sarif_of(audit: dict[str, Any], *, tool_version: str = "") -> dict[str, Any]:
    not_run = {
        str(e.get("id")): (f"{e.get('status')}: {e.get('detail')}" if e.get("detail") else str(e.get("status")))
        for e in audit.get("engines") or [] if isinstance(e, dict) and e.get("status") != "ok"
    }
    return to_sarif(findings_of(audit), root=audit.get("root"), tool_version=tool_version, not_run=not_run)


def _checklist_path(root: str | Path) -> Path:
    return state_dir(root) / "checklist.json"


def _manual_ticks(root: str | Path) -> dict[str, dict[str, Any]]:
    data = _read_json(_checklist_path(root))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise AuditStateError(f"{_checklist_path(root)} is not a JSON object")
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and k in catalog.CONTROL_IDS}


def set_check(root: str | Path, control_id: str, checked: bool, note: str = "") -> None:
    """Tick or untick a control by hand (a check done outside Navin, an accepted risk)."""
    catalog.control(control_id)
    ticks = _manual_ticks(root)
    if checked:
        ticks[control_id] = {"checked": True, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": str(note or "")[:500]}
    else:
        ticks.pop(control_id, None)
    _write_json(_checklist_path(root), ticks)


def checklist_state(root: str | Path, audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """The 70 controls with the last audit's status and the manual ticks.

    A control is done when the audit fully checked it and found nothing (not
    a partial static pass), or when someone ticked it by hand.
    """
    ticks = _manual_ticks(root)
    controls = ((audit or {}).get("coverage") or {}).get("controls") or {}
    items: list[dict[str, Any]] = []
    for item in catalog.CONTROLS:
        cov = controls.get(item.id) or {}
        status = str(cov.get("status") or "not_assessed")
        partial = bool(cov.get("partial"))
        tick = ticks.get(item.id) or {}
        manual = bool(tick.get("checked"))
        verified = status == "clean" and not partial
        items.append({
            "id": item.id, "group": item.group, "level": item.level, "label": item.label, "skill": item.skill,
            "status": status, "partial": partial, "reason": str(cov.get("reason") or ""),
            "findings": len(cov.get("findings") or []), "verified": verified, "manual": manual,
            "note": str(tick.get("note") or ""), "done": verified or manual,
        })
    groups = [
        {"id": g.id, "number": g.number, "title": g.title,
         "done": sum(1 for i in items if i["group"] == g.id and i["done"]),
         "total": sum(1 for i in items if i["group"] == g.id)}
        for g in catalog.CONTROL_GROUPS
    ]
    return {
        "items": items,
        "groups": groups,
        "done": sum(1 for i in items if i["done"]),
        "total": len(items),
        "audit_id": (audit or {}).get("id"),
        "audited_at": (audit or {}).get("started_at"),
    }
