# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: a Navin Security audit (plan, run, report, patch, checklist).

Every action runs in a worker thread: an audit walks the tree and may spawn
scanners for minutes, which must never hold the engine loop.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.security import catalog
from navin.security.model import Finding
from navin.security.orchestrator.audit import (
    AuditStateError,
    checklist_state,
    coverage_of,
    findings_of,
    load_last_audit,
    make_plan,
    run_audit,
    sarif_of,
    save_audit,
)
from navin.security.orchestrator.scan_planner import AuditInputs
from navin.security.remediation import propose_patches, validate_patch
from navin.security.reports.compliance import coverage_markdown

SARIF_FILE = "navin-security.sarif"
_ACTIONS = ("run", "plan", "report", "patch", "checklist")
_REMINDER = (
    "A clean control means no engine that ran found a weakness; it is not proof of security. "
    "Report potential findings as weaknesses to confirm, confirmed ones as vulnerabilities."
)


def _where(item: Finding) -> str:
    if item.file_path and item.line:
        return f"{item.file_path}:{item.line}"
    return item.file_path or item.endpoint


def _slim(item: Finding) -> dict[str, Any]:
    first = item.evidence[0] if item.evidence else None
    return {
        "fingerprint": item.fingerprint(),
        "id": item.id,
        "title": item.title,
        "severity": item.severity,
        "status": item.status,
        "family": item.family,
        "cwe": item.cwe,
        "where": _where(item),
        "endpoint": item.endpoint,
        "controls": list(item.controls),
        "confidence": round(item.confidence, 2),
        "source": item.source,
        "evidence_kind": first.kind if first else "",
        "evidence": first.observed[:600] if first else "",
        "remediation": item.remediation[:400],
    }


def _write_sarif(root: Path, log: dict[str, Any]) -> str:
    path = root / SARIF_FILE
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return SARIF_FILE


def _need_audit(root: Path) -> dict[str, Any]:
    audit = load_last_audit(root)
    if audit is None:
        raise AuditStateError("no audit saved for this project yet: run security_audit action=run first")
    return audit


def _run(root: Path, *, base_url: str, scanners: bool, offline: bool, timeout_s: int, sarif: bool, max_findings: int) -> str:
    result = run_audit(root, inputs=AuditInputs(base_url=base_url), scanners=scanners, offline=offline,
                       scanner_timeout_s=timeout_s)
    saved = save_audit(root, result)
    totals = result.coverage.totals
    by_status = totals["by_status"]
    header = (
        f"security_audit id={result.id} findings={len(result.findings)} exploitable={by_status['exploitable']} "
        f"confirmed={by_status['confirmed']} potential={by_status['potential']} "
        f"controls={totals['assessed']}/{totals['controls']} partial={totals['partial']} "
        f"score={totals['score']}/100 grade={totals['grade']} duration={result.duration_s}s\n"
    )
    payload = {
        "saved": saved.relative_to(root).as_posix(),
        "sarif": _write_sarif(root, result.sarif()) if sarif else None,
        "levels_reached": result.levels_reached,
        "next_levels": result.plan.get("missing_inputs"),
        "engines": [{"id": e.id, "kind": e.kind, "status": e.status, "findings": e.findings, "detail": e.detail}
                    for e in result.engines],
        "findings_total": len(result.findings),
        "findings": [_slim(f) for f in result.findings[:max_findings]],
        "attack_paths": result.attack_paths[:5],
        "follow_up_skills": result.follow_up_skills,
        "files_not_analyzed": len(result.skipped_files),
        "notes": result.notes,
        "reminder": _REMINDER,
    }
    return header + json.dumps(payload, ensure_ascii=False, indent=2)


def _plan(root: Path, *, base_url: str) -> str:
    found, plan, missing = make_plan(root, AuditInputs(base_url=base_url))
    header = f"security_audit plan levels={','.join(plan.levels)} controls={len(plan.controls)} skills={len(plan.skills)}\n"
    payload = {
        "surfaces": found.surfaces,
        "languages": found.languages,
        "frameworks": found.frameworks,
        "levels_reachable": plan.levels,
        "missing_inputs": plan.missing_inputs,
        "skills": plan.skills,
        "scanners_usable": plan.scanners,
        "scanners_not_installed": missing,
        "steps": [{"id": s.id, "level": s.level, "engine": s.engine, "controls": len(s.controls)} for s in plan.steps][:30],
        "controls_not_planned": len(plan.not_covered),
    }
    return header + json.dumps(payload, ensure_ascii=False, indent=2)


def _report(root: Path, *, lang: str, sarif: bool) -> str:
    audit = _need_audit(root)
    text = coverage_markdown(coverage_of(audit), lang=lang)
    paths = audit.get("attack_paths") or []
    if paths:
        text += "\n## Attack paths (hypotheses to verify)\n\n" + "\n".join(
            f"- {p.get('title')} ({p.get('severity')}, {p.get('impact')})" for p in paths[:10]) + "\n"
    header = f"security_audit report id={audit.get('id')} lang={lang}"
    if sarif:
        header += f" sarif={_write_sarif(root, sarif_of(audit))}"
    return header + "\n" + text


def _patch(root: Path, *, fingerprint: str) -> str:
    audit = _need_audit(root)
    items = findings_of(audit)
    if fingerprint:
        items = [f for f in items if f.fingerprint() == fingerprint]
        if not items:
            raise AuditStateError(f"no finding with fingerprint {fingerprint!r} in the last audit")
    proposals = propose_patches(root, items)
    rows: list[dict[str, Any]] = []
    manual: list[dict[str, str]] = []
    for item, proposal in zip(items, proposals):
        if not proposal.generated:
            manual.append({"fingerprint": proposal.finding, "id": item.id, "where": _where(item), "reason": proposal.reason})
            continue
        if len(rows) >= 10:
            continue
        check = validate_patch(root, proposal.diff)
        rows.append({"fingerprint": proposal.finding, "id": item.id, "where": _where(item), "summary": proposal.summary,
                     "validation": check.status, "reasons": check.reasons, "diff": proposal.diff})
    valid = sum(1 for r in rows if r["validation"] == "ok")
    header = f"security_audit patch proposed={len(rows)} valid={valid} manual={len(manual)}\n"
    payload = {
        "patches": rows,
        "manual": manual[:40] if fingerprint or not rows else manual[:10],
        "note": ("Nothing was applied. Review each diff, apply it with apply_patch, then run security_audit again: "
                 "the finding is fixed only when the rescan no longer reports it."),
    }
    return header + json.dumps(payload, ensure_ascii=False, indent=2)


def _checklist(root: Path, *, lang: str) -> str:
    state = checklist_state(root, load_last_audit(root))
    done = {item["id"] for item in state["items"] if item["done"]}
    header = f"security_audit checklist {state['done']}/{state['total']} audit={state['audit_id'] or 'none'}\n"
    return header + catalog.checklist_markdown(done, lang=lang)


class SecurityAuditTool(_QualityTool):
    """Navin Security audit of the project with evidence levels and control coverage."""

    @property
    def name(self) -> str:
        return "security_audit"

    @property
    def description(self) -> str:
        return (
            "Navin Security audit of the project. action=run (default): discovers the stack, runs the code "
            "analyzers (user input reaching SQL, shell, eval, file paths, deserialization, outbound URLs and "
            "templates; routes without auth, weakened JWT checks, unsigned webhooks, login without rate limit; "
            "tenant isolation; prices set by the client), the offline rule pack and the installed scanners "
            "(semgrep, gitleaks, trivy, osv-scanner, bandit, pip-audit, npm audit, cargo-audit; a missing one is "
            "reported, never read as clean), correlates findings into attack paths and maps them to the 70 "
            "controls of the checklist. Each finding has a status: potential (static), confirmed (traced dataflow), "
            "exploitable (reproduced). The audit is saved in .navin/security/; sarif=true also writes "
            "navin-security.sarif. plan: levels L1-L5 reachable and the inputs each still needs, without scanning. "
            "report: coverage report of the last audit (lang=fr|en|ar). patch: mechanical fixes for the last "
            "audit's findings, each checked with git apply on a copy, never applied (review, then apply_patch). "
            "checklist: the 70 controls with progress. scanners=false runs only the built-in engines (fast); "
            "offline=true skips the scanners that use the network."
        )

    @property
    def read_only(self) -> bool:
        # run saves the audit under .navin/security/; sarif writes a file at the root.
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS), "description": "run (default) | plan | report | patch | checklist"},
                "base_url": {"type": "string", "description": "URL of a running test instance; plan says what the dynamic level still needs."},
                "scanners": {"type": "boolean", "description": "run: default true; false keeps the built-in analyzers and rules only."},
                "offline": {"type": "boolean", "description": "run: skip the scanners that send data to the network (npm audit, advisory databases)."},
                "scanner_timeout_s": {"type": "integer", "minimum": 10, "maximum": 600, "description": "run: limit per scanner (default 120)."},
                "sarif": {"type": "boolean", "description": "run / report: also write navin-security.sarif at the project root."},
                "lang": {"type": "string", "enum": ["fr", "en", "ar"], "description": "report / checklist language (default en)."},
                "max_findings": {"type": "integer", "minimum": 1, "maximum": 200, "description": "run: findings listed in the answer (default 40); the saved audit keeps all."},
                "finding": {"type": "string", "description": "patch: fingerprint of one finding (default: every finding with a mechanical fix)."},
            },
        }

    async def execute(
        self,
        action: str = "run",
        base_url: str = "",
        scanners: bool = True,
        offline: bool = False,
        scanner_timeout_s: int = 120,
        sarif: bool = False,
        lang: str = "en",
        max_findings: int = 40,
        finding: str = "",
        **kwargs: Any,
    ) -> Any:
        root, error = self._root_or_error()
        if root is None:
            return error
        verb = (action or "run").strip().lower()
        if verb not in _ACTIONS:
            return self.error(f"unknown action {verb!r}; expected one of {', '.join(_ACTIONS)}")
        language = lang if lang in {"fr", "en", "ar"} else "en"
        root = Path(root)
        try:
            if verb == "run":
                timeout = max(10, min(600, int(scanner_timeout_s or 120)))
                cap = max(1, min(200, int(max_findings or 40)))
                return await asyncio.to_thread(_run, root, base_url=str(base_url or "").strip(), scanners=bool(scanners),
                                               offline=bool(offline), timeout_s=timeout, sarif=bool(sarif), max_findings=cap)
            if verb == "plan":
                return await asyncio.to_thread(_plan, root, base_url=str(base_url or "").strip())
            if verb == "report":
                return await asyncio.to_thread(_report, root, lang=language, sarif=bool(sarif))
            if verb == "patch":
                return await asyncio.to_thread(_patch, root, fingerprint=str(finding or "").strip())
            return await asyncio.to_thread(_checklist, root, lang=language)
        except (AuditStateError, NotADirectoryError, ValueError, OSError) as exc:
            return self.error(str(exc))
