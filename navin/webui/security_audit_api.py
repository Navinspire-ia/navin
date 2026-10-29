# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cyber panel API for Navin Security audits.

One entry, ``/api/webui/security-audit?action=...&path=<project>``:

- ``catalog``: families, controls, levels, frameworks, statuses (fr/en/ar);
- ``run``: run an audit (``scanners``, ``offline``, ``base_url``), save it, return it;
- ``last``: the project's last saved audit, or ``null``;
- ``checklist``: the 70 controls with status, manual ticks and progress;
- ``check``: tick or untick a control by hand (``control``, ``checked``, ``note``);
- ``coverage``: the coverage report as markdown (``lang``);
- ``sarif``: the last audit as a SARIF download.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navin.security import catalog
from navin.security.orchestrator.audit import (
    AuditStateError,
    checklist_state,
    coverage_of,
    load_last_audit,
    run_audit,
    sarif_of,
    save_audit,
    set_check,
)
from navin.security.orchestrator.scan_planner import AuditInputs
from navin.security.reports.compliance import coverage_markdown

ACTIONS = ("catalog", "run", "last", "checklist", "check", "coverage", "sarif")
_MAX_FINDINGS = 500


class SecurityAuditError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _project(raw_path: str) -> Path:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise SecurityAuditError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise SecurityAuditError("path must be absolute")
    if not path.is_dir():
        raise SecurityAuditError("not a directory", status=404)
    return path.resolve()


def _flag(value: str | None, default: bool) -> bool:
    text = (value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    if text:
        raise SecurityAuditError(f"expected true or false, got {value!r}")
    return default


def _with_cap(audit: dict[str, Any]) -> dict[str, Any]:
    findings = audit.get("findings") or []
    if len(findings) <= _MAX_FINDINGS:
        return audit
    return {**audit, "findings": findings[:_MAX_FINDINGS], "findings_truncated": len(findings) - _MAX_FINDINGS}


def security_audit_payload(action: str, params: dict[str, str]) -> dict[str, Any]:
    """JSON payload for one panel action. Blocking (``run`` takes minutes): call it off the loop."""
    verb = (action or "").strip().lower()
    if verb == "catalog":
        return catalog.catalog_payload()
    if verb not in ACTIONS or verb == "sarif":
        raise SecurityAuditError(f"unknown action {verb!r}; expected one of {', '.join(a for a in ACTIONS if a != 'sarif')}")
    root = _project(params.get("path", ""))
    try:
        if verb == "run":
            result = run_audit(
                root,
                inputs=AuditInputs(base_url=(params.get("base_url") or "").strip()),
                scanners=_flag(params.get("scanners"), True),
                offline=_flag(params.get("offline"), False),
            )
            save_audit(root, result)
            audit = result.to_dict()
            return {"audit": _with_cap(audit), "checklist": checklist_state(root, audit)}
        audit = load_last_audit(root)
        if verb == "last":
            return {"audit": _with_cap(audit) if audit else None}
        if verb == "checklist":
            return checklist_state(root, audit)
        if verb == "check":
            control_id = (params.get("control") or "").strip()
            if control_id not in catalog.CONTROL_IDS:
                raise SecurityAuditError(f"unknown control {control_id!r}")
            set_check(root, control_id, _flag(params.get("checked"), True), params.get("note") or "")
            return checklist_state(root, audit)
        if audit is None:
            raise SecurityAuditError("no audit saved for this project yet: run one first", status=404)
        lang = (params.get("lang") or "en").strip().lower()
        return {"markdown": coverage_markdown(coverage_of(audit), lang=lang), "audit_id": audit.get("id")}
    except AuditStateError as exc:
        raise SecurityAuditError(str(exc), status=500) from exc


def security_audit_sarif(raw_path: str) -> tuple[bytes, str]:
    """(body, filename) of the last audit as SARIF 2.1.0."""
    root = _project(raw_path)
    try:
        audit = load_last_audit(root)
    except AuditStateError as exc:
        raise SecurityAuditError(str(exc), status=500) from exc
    if audit is None:
        raise SecurityAuditError("no audit saved for this project yet: run one first", status=404)
    body = json.dumps(sarif_of(audit), ensure_ascii=False, indent=1).encode("utf-8")
    return body, f"navin-security-{root.name or 'project'}-{audit.get('id', 'audit')}.sarif"
