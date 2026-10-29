# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SARIF 2.1.0 export of normalized findings (GitHub code scanning, GitLab, IDEs).

One rule per finding id, one result per finding with a stable partial
fingerprint, the status and the evidence level in the properties, and the
dataflow trace as a code flow. Accepted risks and false positives are
emitted as suppressed results; fixed findings are left out. Scanners that did
not run are listed as tool notifications, so the file never reads as a clean
scan of something that was not scanned.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from navin.security.model import Finding, sort_findings

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
_SECURITY_SEVERITY = {"critical": 9.5, "high": 8.0, "medium": 5.5, "low": 3.0, "info": 0.0}
_TRACE_STEP = re.compile(r"^\d+\.\s+(?P<path>.+?):(?P<line>\d+)\s(?P<message>.*)$")


def _uri(path: str) -> tuple[str, str | None]:
    posix = path.replace("\\", "/")
    if posix.startswith("/") or re.match(r"^[A-Za-z]:/", posix):
        return Path(path).resolve().as_uri(), None
    return quote(posix, safe="/"), "%SRCROOT%"


def _location(path: str, line: int | None, message: str = "") -> dict[str, Any]:
    uri, base = _uri(path)
    artifact: dict[str, Any] = {"uri": uri}
    if base:
        artifact["uriBaseId"] = base
    out: dict[str, Any] = {"physicalLocation": {"artifactLocation": artifact, "region": {"startLine": max(1, int(line or 1))}}}
    if message:
        out["message"] = {"text": message}
    return out


def _code_flows(finding: Finding) -> list[dict[str, Any]]:
    flows = []
    for evidence in finding.evidence:
        if evidence.kind != "dataflow":
            continue
        steps = []
        for raw in evidence.observed.splitlines():
            match = _TRACE_STEP.match(raw.strip())
            if match:
                steps.append({"location": _location(match["path"], int(match["line"]), match["message"][:300])})
        if len(steps) >= 2:
            flows.append({"message": {"text": evidence.description[:300]}, "threadFlows": [{"locations": steps}]})
    return flows


def _rule(finding: Finding, severity_score: float) -> dict[str, Any]:
    tags = ["security", finding.family, *finding.controls]
    if finding.cwe:
        tags.append(finding.cwe)
    rule: dict[str, Any] = {
        "id": finding.id,
        "name": "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", finding.id) if part) or "Finding",
        "shortDescription": {"text": finding.title or finding.id},
        "defaultConfiguration": {"level": _LEVEL[finding.severity]},
        "properties": {"tags": list(dict.fromkeys(tags)), "security-severity": f"{severity_score:.1f}", "precision": _precision(finding)},
    }
    if finding.description:
        rule["fullDescription"] = {"text": finding.description[:1000]}
    if finding.remediation:
        rule["help"] = {"text": finding.remediation[:2000]}
    if finding.cwe.startswith("CWE-"):
        rule["helpUri"] = f"https://cwe.mitre.org/data/definitions/{finding.cwe[4:]}.html"
    return rule


def _precision(finding: Finding) -> str:
    if finding.status != "potential" or finding.confidence >= 0.8:
        return "high"
    return "medium" if finding.confidence >= 0.5 else "low"


def _score(finding: Finding) -> float:
    return float(finding.cvss) if finding.cvss is not None else _SECURITY_SEVERITY[finding.severity]


def to_sarif(
    findings: list[Finding],
    *,
    root: str | Path | None = None,
    tool_version: str = "",
    not_run: dict[str, str] | None = None,
    include_fixed: bool = False,
) -> dict[str, Any]:
    """The SARIF log for *findings*; ``not_run`` maps an engine id to why it did not run."""
    kept = [f for f in sort_findings(list(findings)) if include_fixed or f.validation.state != "fixed"]
    rules: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    scores: dict[str, float] = {}
    for item in kept:
        scores[item.id] = max(scores.get(item.id, 0.0), _score(item))
    for item in kept:
        if item.id not in index:
            index[item.id] = len(rules)
            rules.append(_rule(item, scores[item.id]))
    results = []
    for item in kept:
        message = item.title or item.id
        if item.endpoint:
            message += f" ({item.endpoint})"
        result: dict[str, Any] = {
            "ruleId": item.id,
            "ruleIndex": index[item.id],
            "level": _LEVEL[item.severity],
            "message": {"text": f"{message} - {item.status}"},
            "partialFingerprints": {"navinFingerprint/v1": item.fingerprint()},
            "properties": {
                "status": item.status,
                "severity": item.severity,
                "confidence": round(item.confidence, 2),
                "source": item.source,
                "controls": list(item.controls),
                "evidence": sorted({e.kind for e in item.evidence}),
                "validation": item.validation.state,
            },
        }
        if item.file_path:
            result["locations"] = [_location(item.file_path, item.line)]
        elif item.endpoint:
            result["locations"] = [{"logicalLocations": [{"name": item.endpoint, "kind": "resource"}]}]
        flows = _code_flows(item)
        if flows:
            result["codeFlows"] = flows
        if item.validation.state in {"accepted", "false_positive"}:
            result["suppressions"] = [{
                "kind": "external",
                "status": "accepted",
                "justification": item.validation.note or item.validation.state.replace("_", " "),
            }]
        results.append(result)
    run: dict[str, Any] = {
        "tool": {"driver": {"name": "Navin Security", "informationUri": "https://navinspire.ai", "rules": rules}},
        "results": results,
        "columnKind": "utf16CodeUnits",
        "invocations": [{
            "executionSuccessful": True,
            "toolExecutionNotifications": [
                {"level": "warning", "message": {"text": f"{engine} did not run: {why}"}}
                for engine, why in sorted((not_run or {}).items())
            ],
        }],
    }
    if tool_version:
        run["tool"]["driver"]["version"] = tool_version
    if root is not None:
        run["originalUriBaseIds"] = {"%SRCROOT%": {"uri": Path(root).resolve().as_uri().rstrip("/") + "/"}}
    return {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}
