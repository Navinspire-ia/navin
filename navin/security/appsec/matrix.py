# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage matrix of one audit against the 302-control referential.

Statuses follow the referential: ``passed`` needs an automated capability and
an engine of its list that really ran (its version, the scope and the date are
in the evidence); ``failed`` means an open finding bears on the control;
``inconclusive`` means an engine ran but covers only part of the control;
``blocked`` means the engine could not run (not installed, timed out, an input
such as an authorized base URL is missing); ``not_tested`` means no engine
exists yet or a human has to review it; ``not_applicable`` always carries the
discovery evidence that justifies it. Verified coverage counts applicable
controls tested with evidence (passed or failed). The audit is never marked
complete while an applicable control is not verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from navin.security.appsec import capabilities as caps
from navin.security.appsec import referential as ref
from navin.security.model import finding_from_dict

VERIFIED = frozenset({"passed", "failed"})
_OPEN = frozenset({"open", "accepted"})
_RISK = {"critical": "critical", "high": "high", "medium": "medium", "low": "low", "info": "low"}
_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_NOT_RUN = {"not_installed", "not_applicable", "timeout", "failed", "invalid_output", "skipped", "blocked", "not_run"}
_SURFACE_OF_TAG = {
    "source": "source", "auth": "auth", "api": "api", "web": "web", "db": "database", "data": "data",
    "cicd": "cicd", "iac": "iac", "runtime": "runtime", "mobile": "mobile", "desktop": "desktop", "ai": "ai",
    "serverless": "serverless", "native": "native", "integrations": "integrations",
}
_APPLICABILITY_OVERRIDE = {
    "NAV-CYBER-SAST-014": "native",  # "si langage concerne": memory-unsafe languages only
    # Row-level security only exists in some datastores (PostgreSQL, SQL Server, Oracle, Firebase rules...).
    **{f"NAV-CYBER-DB-{n:03d}": "rls" for n in (7, 8, 9, 10, 11, 12, 13, 15)},
}
# Datastores with a row-level (or per-document) access mechanism; "sql" is an ORM whose engine is unknown.
_RLS_DATASTORES = frozenset({"postgresql", "supabase", "mssql", "oracle", "cockroachdb", "firebase", "sql"})
_EXTRA_TAGS = ("rls",)
_INPUT_REASON = {
    "base_url": "needs the base URL of an authorized target (preferably staging) to run the passive web checks",
    "git": "needs a git repository to read the history",
    "accounts": "needs two synthetic test accounts on an isolated environment",
}
_CI = ("github_actions", "gitlab_ci", "jenkins", "azure_pipelines", "circleci", "bitbucket_pipelines")
_IAC = ("docker", "compose", "kubernetes", "helm", "terraform", "cloudformation", "pulumi", "ansible", "aws_cdk", "serverless")
WARNING = (
    "Un contrôle « réussi » signifie qu'un moteur de sa liste a réellement tourné sur ce périmètre sans trouver "
    "de faiblesse : ce n'est pas une preuve de sécurité. La couverture vérifiée ne compte que les contrôles "
    "applicables exécutés avec preuves (réussis ou en échec)."
)


# ---------------------------------------------------------------------------
# Context read from one saved audit
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self, audit: dict[str, Any]) -> None:
        self.audit = audit
        self.discovery: dict[str, Any] = dict(audit.get("discovery") or {})
        self.inputs: dict[str, Any] = dict(audit.get("inputs") or {})
        self.runtime: dict[str, Any] = dict(audit.get("runtime_reports") or {})
        self.started_at = str(audit.get("started_at") or "")
        self.engines: dict[str, dict[str, Any]] = {}
        for raw in audit.get("engines") or []:
            if isinstance(raw, dict) and raw.get("id"):
                self.engines[str(raw["id"])] = raw
        # Audits saved before discovery was recorded as an engine still ran it.
        if self.discovery and caps.DISC not in self.engines:
            self.engines[caps.DISC] = {"id": caps.DISC, "kind": "discovery", "status": "ok", "version": ""}
        if audit.get("threats") is not None and caps.TM not in self.engines:
            self.engines[caps.TM] = {"id": caps.TM, "kind": "threat_model", "status": "ok", "version": ""}
        self.engines.setdefault(caps.MX, {"id": caps.MX, "kind": "matrix", "status": "ok", "version": ref.VERSION})
        self.findings: list[dict[str, Any]] = []
        for raw in audit.get("findings") or []:
            if not isinstance(raw, dict):
                continue
            state = str((raw.get("validation") or {}).get("state") or "open")
            if state not in _OPEN:
                continue
            row = dict(raw)
            if not row.get("fingerprint"):
                try:
                    row["fingerprint"] = finding_from_dict(raw).fingerprint()
                except (TypeError, ValueError):
                    row["fingerprint"] = f"{raw.get('id')}:{raw.get('file_path')}:{raw.get('line')}"
            self.findings.append(row)
        self.surfaces = self._surfaces()

    def _surfaces(self) -> dict[str, str]:
        """Applicability tag -> the discovery evidence that makes it applicable (missing = not detected)."""
        d = self.discovery
        found = set(d.get("surfaces") or [])
        infra = set(d.get("infrastructure") or [])
        langs = dict(d.get("languages") or {})
        out: dict[str, str] = {"all": "always applicable"}
        for tag, surface in _SURFACE_OF_TAG.items():
            if surface in found:
                out[tag] = f"surface {surface} detected"
        if "source" not in out and langs:
            out["source"] = "code files: " + ", ".join(f"{k} {v}" for k, v in list(langs.items())[:5])
        if "data" not in out and (d.get("databases") or d.get("data_signals")):
            out["data"] = "databases or personal-data fields detected"
        if "cicd" not in out and infra & set(_CI):
            out["cicd"] = "pipelines: " + ", ".join(sorted(infra & set(_CI)))
        if "iac" not in out and infra & set(_IAC):
            out["iac"] = "infrastructure files: " + ", ".join(sorted(infra & set(_IAC)))
        if "runtime" not in out and found & {"api", "web", "mobile", "desktop"}:
            out["runtime"] = "deployable surface detected"
        if "native" not in out and set(langs) & {"c", "cpp", "rust"}:
            out["native"] = "memory-unsafe or FFI-capable language: " + ", ".join(sorted(set(langs) & {"c", "cpp", "rust"}))
        if "integrations" not in out and (d.get("integrations") or "webhooks" in (d.get("features") or [])):
            out["integrations"] = "third-party SDKs or webhooks detected"
        rls = sorted(set(d.get("databases") or []) & _RLS_DATASTORES)
        if rls or "rls" in (d.get("features") or []):
            out["rls"] = "datastore with row-level access control: " + (", ".join(rls) or "RLS statements in the code")
        return out

    def not_detected_reason(self, tag: str) -> str:
        if tag == "rls" and self.discovery.get("databases"):
            found = ", ".join(sorted(self.discovery.get("databases") or []))
            return (f"the datastores found ({found}) have no row-level security mechanism: tenant isolation is enforced "
                    "by the application and is covered by NAV-CYBER-AUTHZ-006 and NAV-CYBER-DATA-007")
        if tag == "rls":
            tag = "database"
        seen = self.discovery.get("files_seen")
        partial = " (the discovery stopped early: check by hand)" if self.discovery.get("truncated") else ""
        return f"no {tag} surface found by discovery in {seen if seen is not None else 'the'} files read{partial}"

    def input_given(self, name: str) -> bool:
        if name == "base_url":
            return bool(self.inputs.get("base_url"))
        if name == "git":
            return bool((self.discovery.get("git") or {}).get("is_repo"))
        return False

    def ran(self, engine_id: str, check_ids: tuple[str, ...] = ()) -> bool:
        run = self.engines.get(engine_id)
        if not run or run.get("status") != "ok":
            return False
        checks = run.get("checks_run")
        if checks and check_ids:
            plain = {c for c in check_ids if not c.endswith("*")}
            return not plain or bool(plain & set(checks))
        return True


# ---------------------------------------------------------------------------
# Evidence for the engines that only the audit itself can judge
# ---------------------------------------------------------------------------


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, dict)) else int(value or 0)


def _disc_evidence(ctx: _Context, control_id: str) -> list[str]:
    d = ctx.discovery
    pick: dict[str, Callable[[], list[str]]] = {
        "NAV-CYBER-DISC-001": lambda: [f"modules: {_count(d.get('modules'))}", f"monorepo: {bool(d.get('monorepo'))}",
                                       f"submodules: {_count(d.get('submodules'))}",
                                       f"git branches: {_count((d.get('git') or {}).get('branches'))}"],
        "NAV-CYBER-DISC-002": lambda: [f"languages: {', '.join(list(d.get('languages') or {})[:8]) or 'none'}",
                                       f"frameworks: {', '.join((d.get('frameworks') or [])[:8]) or 'none'}",
                                       f"manifests: {_count(d.get('manifests'))}"],
        "NAV-CYBER-DISC-003": lambda: [f"surfaces: {', '.join(d.get('surfaces') or []) or 'none'}"],
        "NAV-CYBER-DISC-004": lambda: [f"{kind}: {_count(paths)}" for kind, paths in (d.get("entrypoint_kinds") or {}).items()]
        or [f"entry points: {_count(d.get('entrypoints'))}"],
        "NAV-CYBER-DISC-005": lambda: [f"deploy targets: {_count(d.get('deploy_targets'))}"],
        "NAV-CYBER-DISC-006": lambda: [f"databases: {', '.join(d.get('databases') or []) or 'none'}",
                                       f"integrations: {', '.join((d.get('integrations') or [])[:8]) or 'none'}"],
        "NAV-CYBER-DISC-009": lambda: [f"config files: {_count(d.get('config_files'))}", f"env files: {_count(d.get('env_files'))}",
                                       f"migrations: {_count(d.get('migrations'))}", f"secret-like files: {_count(d.get('secret_like_files'))}"],
        "NAV-CYBER-DISC-011": lambda: [f"{cls}: {n}" for cls, n in (d.get("file_classes") or {}).items()],
    }
    fn = pick.get(control_id)
    base = [f"discovery: {d.get('files_seen', '?')} files read"]
    return base + (fn() if fn else [])


def _qa(ctx: _Context, control_id: str, rows: dict[str, dict[str, Any]]) -> tuple[str, str, list[str]]:
    """(status, reason, evidence) for the controls the audit itself is evidence of."""
    engines = [e for e in ctx.engines.values() if e.get("kind") not in {"matrix"}]
    ok = [e for e in engines if e.get("status") == "ok"]
    scanners_ok = [e for e in ok if e.get("kind") == "scanner"]
    listing = [f"{e['id']} {e.get('version') or ''} {e.get('status')}".replace("  ", " ") for e in engines]
    findings = ctx.findings
    if control_id == "NAV-CYBER-DISC-012":
        na = sum(1 for r in rows.values() if r["status"] == "not_applicable")
        return "passed", "", [f"302 controls with applicability, engines and evidence; {na} not applicable, each with its reason"]
    if control_id == "NAV-CYBER-QA-001":
        if scanners_ok:
            return "passed", "", listing
        return "inconclusive", "only Navin's built-in engines ran: no external scanner (semgrep, gitleaks, trivy, osv-scanner...) ran", listing
    if control_id == "NAV-CYBER-QA-002":
        bad = [e for e in engines if e.get("status") in {"failed", "invalid_output", "timeout"}]
        if not ok:
            return "blocked", "no engine ran", listing
        if bad:
            return "inconclusive", "engines without usable output: " + ", ".join(f"{e['id']} ({e.get('status')})" for e in bad), listing
        return "passed", "", listing
    if control_id == "NAV-CYBER-QA-003":
        if ctx.discovery.get("truncated"):
            return "inconclusive", f"discovery stopped at {ctx.discovery.get('files_seen')} files", [f"files read: {ctx.discovery.get('files_seen')}"]
        return "passed", "", [f"files read: {ctx.discovery.get('files_seen')}", f"surfaces: {', '.join(ctx.discovery.get('surfaces') or [])}"]
    if control_id == "NAV-CYBER-QA-004":
        return "passed", "", ["this matrix: every control has a status, a reason or evidence"]
    if control_id == "NAV-CYBER-QA-005":
        loose = [f for f in findings if not (f.get("file_path") or f.get("endpoint") or f.get("component"))]
        if loose:
            return "failed", f"{len(loose)} finding(s) without a file, endpoint or asset", [str(f.get("id")) for f in loose[:10]]
        return "passed", "", [f"{len(findings)} open finding(s), each with a file, endpoint or asset"]
    if control_id == "NAV-CYBER-QA-006":
        serious = [f for f in findings if f.get("severity") in {"critical", "high"}]
        proven = [f for f in serious if f.get("status") in {"confirmed", "exploitable"}]
        return "inconclusive", (f"{len(serious)} critical or high finding(s), {len(proven)} confirmed or reproduced; "
                                "reproduce the rest with the verification engine on an authorized sandbox"), []
    if control_id == "NAV-CYBER-QA-008":
        versions = [e for e in engines if e.get("version")]
        if ctx.audit.get("id") and ctx.started_at and versions:
            return "passed", "", [f"audit {ctx.audit.get('id')} at {ctx.started_at}", f"{len(versions)} engine version(s) recorded",
                                  f"inputs: {json.dumps(ctx.inputs, sort_keys=True)[:200]}"]
        return "inconclusive", "engine versions were not recorded by this audit", []
    if control_id == "NAV-CYBER-QA-009":
        tested = [f for f in findings if (f.get("validation") or {}).get("regression_tests")]
        return "inconclusive", f"{len(tested)} open finding(s) carry a regression test; fixes are rescanned on demand", []
    if control_id == "NAV-CYBER-QA-011":
        return "passed", "", ["audit_complete is computed from the 302 controls and stays false while one is not verified"]
    if control_id == "NAV-CYBER-QA-012":
        return "inconclusive", "the matrix and the gaps are exported as JSON; owners are not recorded", []
    return "not_tested", "", []


# ---------------------------------------------------------------------------
# One control
# ---------------------------------------------------------------------------


def _tools(ctx: _Context, cap: caps.Capability) -> list[dict[str, str]]:
    out = []
    for engine_id in cap.engines:
        run = ctx.engines.get(engine_id) or {}
        out.append({"name": engine_id, "version": str(run.get("version") or ""), "status": str(run.get("status") or "not_run")})
    return out


def _where(f: dict[str, Any]) -> str:
    if f.get("file_path"):
        return f"{f['file_path']}:{f['line']}" if f.get("line") else str(f["file_path"])
    return str(f.get("endpoint") or f.get("component") or f.get("id"))


def _target(ctx: _Context, ran: list[str]) -> tuple[str, str]:
    if caps.WEB in ran:
        return str(ctx.inputs.get("base_url") or ""), "authorized target (passive GET/HEAD/OPTIONS)"
    if caps.DBE in ran:
        version = (ctx.runtime.get("database") or {}).get("postgres_version") or ""
        return "project SQL migrations", f"ephemeral local PostgreSQL {version}".strip()
    if ran:
        return str(ctx.audit.get("root") or ""), "source tree (read-only)"
    return "", ""


def _evaluate(ctx: _Context, control: ref.Control, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cap = caps.capability(control.id)
    tag = _APPLICABILITY_OVERRIDE.get(control.id, control.applicability)
    row: dict[str, Any] = {
        "id": control.id, "category": control.category, "task": control.task, "applicability": tag,
        "applicability_detected": tag in ctx.surfaces, "verification_mode": control.verification_mode,
        "status": "not_tested", "capability": cap.mode, "manual_review": cap.mode == "manual",
        "priority": caps.priority(control.id), "executed_at": "", "tools": _tools(ctx, cap), "tool_name": "", "tool_version": "",
        "target": "", "environment": "", "evidence_refs": [], "findings": [], "findings_count": 0, "confidence": "",
        "risk": "none", "remediation": "", "reason": "", "reason_if_not_applicable": "",
    }
    if tag not in ctx.surfaces:
        reason = ctx.not_detected_reason(tag)
        row.update(status="not_applicable", reason=reason, reason_if_not_applicable=reason, manual_review=False)
        return row
    if "git" in cap.requires and not ctx.input_given("git"):
        reason = "not a git repository: there is no history to scan"
        row.update(status="not_applicable", reason=reason, reason_if_not_applicable=reason, manual_review=False)
        return row
    row["applicability_reason"] = ctx.surfaces[tag]

    hits = [f for f in ctx.findings
            if caps.finding_matches(cap, str(f.get("id") or ""), list(f.get("controls") or []), str(f.get("source") or ""))]
    ran = [e for e in cap.engines if ctx.ran(e, cap.finding_ids)]
    if ran:
        first = ctx.engines[ran[0]]
        row.update(executed_at=ctx.started_at, tool_name=ran[0], tool_version=str(first.get("version") or ""))
        row["target"], row["environment"] = _target(ctx, ran)

    if hits:
        worst = max((_RISK.get(str(f.get("severity")), "medium") for f in hits), key=_RISK_RANK.__getitem__)
        proven = any(f.get("status") in {"confirmed", "exploitable"} for f in hits)
        row.update(
            status="failed", findings=[str(f["fingerprint"]) for f in hits], findings_count=len(hits),
            evidence_refs=[_where(f) for f in hits[:10]], risk=worst, confidence="high" if proven else "medium",
            remediation=next((str(f.get("remediation")) for f in hits if f.get("remediation")), ""),
            reason=f"{len(hits)} open finding(s): " + "; ".join(str(f.get("title") or f.get("id"))[:120] for f in hits[:3]),
            executed_at=row["executed_at"] or ctx.started_at,
        )
        if not ran and hits:
            src = str(hits[0].get("source") or "")
            row.update(tool_name=src, tool_version=str((ctx.engines.get(src) or {}).get("version") or ""))
        return row

    if caps.MX in cap.engines:
        status, reason, evidence = _qa(ctx, control.id, rows)
        row.update(status=status, reason=reason, evidence_refs=evidence, executed_at=ctx.started_at, tool_name=caps.MX,
                   tool_version=ref.VERSION, target=str(ctx.audit.get("root") or ""), environment="audit record",
                   confidence="high" if status == "passed" else "low")
        if cap.mode == "partial" and status == "passed":
            row["status"] = "inconclusive"
        return row

    if cap.mode in {"manual", "missing"}:
        missing = [r for r in cap.requires if not ctx.input_given(r)]
        why = cap.note + (f"; {_INPUT_REASON[missing[0]]}" if missing else "")
        row.update(reason=("manual review: " if cap.mode == "manual" else "no Navin engine covers it yet: ") + why,
                   remediation="check it by hand and record the result" if cap.mode == "manual" else "")
        return row

    missing_inputs = [r for r in cap.requires if not ctx.input_given(r)]
    if not ran:
        if missing_inputs:
            row.update(status="blocked", reason=_INPUT_REASON.get(missing_inputs[0], missing_inputs[0]))
            return row
        states = [f"{t['name']} {t['status']}" + (f" ({ctx.engines[t['name']].get('detail')})" if ctx.engines.get(t["name"], {}).get("detail") else "")
                  for t in row["tools"]]
        blocked = any(t["status"] in _NOT_RUN for t in row["tools"])
        row.update(status="blocked" if blocked else "not_tested",
                   reason="no engine of this control ran: " + "; ".join(states)[:400])
        return row

    evidence = []
    for engine_id in ran:
        run = ctx.engines[engine_id]
        evidence.append(f"{engine_id} {run.get('version') or ''} ok, {run.get('findings', 0)} finding(s) in the run, "
                        f"{run.get('duration_s', 0)} s".replace("  ", " "))
    evidence.append(f"audit {ctx.audit.get('id')}")
    if caps.DISC in ran:
        evidence += _disc_evidence(ctx, control.id)
    not_ran = [t["name"] for t in row["tools"] if t["name"] not in ran]
    row["evidence_refs"] = evidence
    if cap.mode == "automated" and not missing_inputs and not (caps.DISC in ran and ctx.discovery.get("truncated")):
        dynamic = bool({caps.WEB, caps.DBE} & set(ran))
        row.update(status="passed", confidence="high" if dynamic or not not_ran else "medium",
                   reason=(f"no finding from {', '.join(ran)}" + (f"; not run: {', '.join(not_ran)}" if not_ran else "")))
        return row
    parts = [cap.note] if cap.note else []
    if missing_inputs:
        parts.append(_INPUT_REASON.get(missing_inputs[0], missing_inputs[0]))
    if not_ran:
        parts.append(f"not run: {', '.join(not_ran)}")
    row.update(status="inconclusive", confidence="low", reason="partial check: " + "; ".join(p for p in parts if p))
    return row


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def build_matrix(audit: dict[str, Any]) -> dict[str, Any]:
    """The 302-control matrix of a saved audit (``AuditResult.to_dict`` shape). Pure: no I/O."""
    ctx = _Context(audit)
    rows: dict[str, dict[str, Any]] = {}
    deferred = {"NAV-CYBER-DISC-012"}  # judged once the other rows exist
    for control in ref.CONTROLS:
        if control.id not in deferred:
            rows[control.id] = _evaluate(ctx, control, rows)
    for control_id in deferred:
        rows[control_id] = _evaluate(ctx, ref.control(control_id), rows)
    controls = [rows[c.id] for c in ref.CONTROLS]

    applicable = [r for r in controls if r["status"] != "not_applicable"]
    by_status = {s: sum(1 for r in controls if r["status"] == s) for s in ref.STATUS_ENUM}
    verified = sum(1 for r in applicable if r["status"] in VERIFIED)
    blockers = [r["id"] for r in applicable if r["status"] not in VERIFIED]
    summary = {
        "total": len(controls), "applicable": len(applicable), "not_applicable": by_status["not_applicable"],
        "passed": by_status["passed"], "failed": by_status["failed"], "inconclusive": by_status["inconclusive"],
        "blocked": by_status["blocked"], "not_tested": by_status["not_tested"], "verified": verified,
        "verified_coverage": round(100.0 * verified / len(applicable), 1) if applicable else 0.0,
        "manual_review": sum(1 for r in applicable if r["manual_review"]),
        "audit_complete": bool(applicable) and not blockers, "complete_blockers": len(blockers),
        "capabilities": caps.counts(),
    }
    categories = []
    for cat in ref.CATEGORIES:
        mine = [r for r in controls if r["category"] == cat.id]
        entry = {"id": cat.id, "number": cat.number, "name": dict(cat.name), "applicability": cat.applicability,
                 "verification_mode": cat.verification_mode, "total": len(mine),
                 "applicable": sum(1 for r in mine if r["status"] != "not_applicable")}
        entry.update({s: sum(1 for r in mine if r["status"] == s) for s in ref.STATUS_ENUM})
        categories.append(entry)
    applicability = []
    for tag in (*ref.APPLICABILITY_TAGS, *_EXTRA_TAGS):
        detected = tag in ctx.surfaces
        applicability.append({"tag": tag, "detected": detected,
                              "reason": ctx.surfaces[tag] if detected else ctx.not_detected_reason(tag)})
    return {
        "referential": {"id": ref.REFERENTIAL_ID, "version": ref.VERSION, "title": ref.TITLE,
                        "controls": len(ref.CONTROLS), "categories": len(ref.CATEGORIES)},
        "audit_id": str(audit.get("id") or ""), "audited_at": ctx.started_at, "root": str(audit.get("root") or ""),
        "levels_reached": list(audit.get("levels_reached") or []),
        "summary": summary, "applicability": applicability, "categories": categories, "controls": controls,
        "complete_blockers": blockers[:50], "warning": WARNING,
    }


def matrix_of(audit: dict[str, Any]) -> dict[str, Any]:
    """The matrix saved with the audit, or one built now for an audit saved before matrices existed."""
    saved = audit.get("matrix")
    if isinstance(saved, dict) and (saved.get("referential") or {}).get("version") == ref.VERSION:
        return saved
    return build_matrix(audit)


_STATUS_FR = {"passed": "réussi", "failed": "échec", "inconclusive": "non concluant", "blocked": "bloqué",
              "not_tested": "non testé", "not_applicable": "non applicable"}


def matrix_markdown(matrix: dict[str, Any]) -> str:
    """The matrix as a readable report (French, like the referential)."""
    s = matrix["summary"]
    lines = [
        f"# Couverture Navin Cyber - {matrix['referential']['controls']} contrôles",
        "",
        f"Audit `{matrix['audit_id']}` du {matrix['audited_at']} sur `{matrix['root']}`.",
        "",
        f"**Couverture vérifiée : {s['verified_coverage']} %** ({s['verified']} contrôles applicables testés avec preuves "
        f"sur {s['applicable']} applicables).",
        "",
        "| Statut | Contrôles |",
        "|---|---|",
    ]
    lines += [f"| {_STATUS_FR[k]} | {s[k]} |" for k in ("passed", "failed", "inconclusive", "blocked", "not_tested", "not_applicable")]
    lines += ["", ("**Audit complet.**" if s["audit_complete"] else
                   f"**Audit non complet** : {s['complete_blockers']} contrôle(s) applicable(s) non vérifié(s)."), "",
              f"> {matrix['warning']}", ""]
    rows = {r["id"]: r for r in matrix["controls"]}
    for cat in matrix["categories"]:
        lines += [f"## {cat['number']:02d}. {cat['name']['fr']} ({cat['applicable']}/{cat['total']} applicables)", "",
                  "| Contrôle | Statut | Outils | Preuve ou raison |", "|---|---|---|---|"]
        for control in ref.controls_of(cat["id"]):
            r = rows[control.id]
            tools = ", ".join(f"{t['name']} {t['version']}".strip() for t in r["tools"] if t["status"] == "ok") or "-"
            detail = "; ".join(r["evidence_refs"][:3]) if r["status"] in VERIFIED else r["reason"]
            detail = (detail or "-").replace("|", "/").replace("\n", " ")[:220]
            lines.append(f"| {r['id']} {r['task'][:70]} | {_STATUS_FR[r['status']]} | {tools} | {detail} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def matrix_json(matrix: dict[str, Any]) -> str:
    return json.dumps(matrix, ensure_ascii=False, indent=1) + "\n"
