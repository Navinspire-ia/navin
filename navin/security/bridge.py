# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bridge from the legacy scanners (rules.py, deep_scan.py, scan.py) to Finding.

The older engines emit loose dicts with a free-text ``category`` and a CWE. This
module maps both to one of the 18 catalog families so their output joins the
same merge, scoring and report path as every new engine.
"""

from __future__ import annotations

from typing import Any

from navin.security.catalog import FAMILY_IDS
from navin.security.model import Evidence, Finding, merge_findings

# Free-text category used by the legacy rule packs -> family id.
_CATEGORY_TO_FAMILY: dict[str, str] = {
    "injection": "injection",
    "command injection": "injection",
    "xml": "injection",
    "sast": "injection",
    "deserialization": "code_execution",
    "path traversal": "files",
    "filesystem": "files",
    "permissions": "files",
    "authentication": "authentication",
    "session": "authentication",
    "csrf": "authentication",
    "redirect": "authentication",
    "secrets": "secrets",
    "cryptography": "cryptography",
    "transport": "cryptography",
    "supply chain": "supply_chain",
    "ci/cd": "supply_chain",
    "container": "infrastructure",
    "kubernetes": "infrastructure",
    "cloud": "infrastructure",
    "network exposure": "infrastructure",
    "misconfiguration": "infrastructure",
    "request forgery": "network",
    "information exposure": "api",
}

# CWE -> family, checked first because it is more precise than the category.
_CWE_TO_FAMILY: dict[str, str] = {
    "CWE-89": "injection", "CWE-79": "injection", "CWE-78": "injection",
    "CWE-90": "injection", "CWE-91": "injection", "CWE-611": "injection",
    "CWE-943": "injection", "CWE-643": "injection", "CWE-113": "injection",
    "CWE-1336": "injection", "CWE-94": "code_execution", "CWE-95": "code_execution",
    "CWE-502": "code_execution", "CWE-22": "files", "CWE-434": "files",
    "CWE-59": "files", "CWE-798": "secrets", "CWE-259": "secrets",
    "CWE-321": "secrets", "CWE-327": "cryptography", "CWE-328": "cryptography",
    "CWE-326": "cryptography", "CWE-295": "cryptography", "CWE-330": "cryptography",
    "CWE-319": "cryptography", "CWE-347": "authentication", "CWE-384": "authentication",
    "CWE-352": "authentication", "CWE-601": "authentication", "CWE-287": "authentication",
    "CWE-639": "authorization", "CWE-285": "authorization", "CWE-284": "authorization",
    "CWE-918": "network", "CWE-444": "network", "CWE-1321": "frontend",
    "CWE-1021": "frontend", "CWE-942": "frontend", "CWE-1333": "availability",
    "CWE-400": "availability", "CWE-770": "availability", "CWE-532": "audit_logging",
    "CWE-778": "audit_logging", "CWE-1104": "supply_chain", "CWE-829": "supply_chain",
    "CWE-250": "infrastructure", "CWE-915": "api", "CWE-200": "api",
}


def family_for_cwe(cwe: str) -> str | None:
    """Catalog family of a CWE, or None when the CWE is not mapped."""
    return _CWE_TO_FAMILY.get(str(cwe or "").strip().upper())


def family_for(category: str = "", cwe: str = "") -> str:
    """Pick the catalog family for a legacy finding; ``injection`` is never a silent default."""
    mapped_cwe = family_for_cwe(cwe)
    if mapped_cwe:
        return mapped_cwe
    mapped = _CATEGORY_TO_FAMILY.get(str(category or "").strip().lower())
    if mapped:
        return mapped
    return "infrastructure"


def finding_from_legacy(raw: dict[str, Any], *, source: str = "navin_rules") -> Finding:
    """Convert one loose scanner dict (rules.py / deep_scan.py shape) to a Finding."""
    cwe = str(raw.get("cwe") or "").strip()
    category = str(raw.get("category") or "")
    evidence = []
    snippet = str(raw.get("snippet") or raw.get("evidence") or raw.get("match") or "")
    if snippet:
        evidence.append(Evidence(kind="static", description="rule match", tool=source, observed=snippet))
    where = str(raw.get("file_path") or raw.get("file") or raw.get("path") or "")
    return Finding(
        id=str(raw.get("id") or raw.get("rule_id") or "finding")[:120],
        family=family_for(category, cwe),
        title=str(raw.get("title") or raw.get("summary") or raw.get("message") or ""),
        severity=str(raw.get("severity") or "medium"),
        cwe=cwe,
        owasp=str(raw.get("owasp") or ""),
        file_path=where,
        line=_line(raw.get("line")),
        remediation=str(raw.get("recommendation") or raw.get("remediation") or ""),
        confidence=_confidence(raw.get("confidence")),
        source=source,
        evidence=evidence,
    )


def merge_legacy(rows: list[dict[str, Any]], *, source: str = "navin_rules") -> list[Finding]:
    """Legacy dicts -> merged, sorted findings."""
    return merge_findings([finding_from_legacy(row, source=source) for row in rows])


def _line(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return {"high": 0.8, "medium": 0.55, "low": 0.3}.get(str(value or "").lower(), 0.5)


assert set(_CWE_TO_FAMILY.values()) | set(_CATEGORY_TO_FAMILY.values()) <= set(FAMILY_IDS)
