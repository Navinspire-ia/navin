# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pure parsers: one external scanner's JSON report -> normalized Findings.

Each parser takes the decoded JSON document and returns ``Finding`` objects.
They never run a process, so they are tested on recorded reports. A secret
value reported by a scanner is never copied into the evidence: only a
redacted match and its location are kept. A report that is in fact an error
(npm audit offline, for one) raises :class:`ReportError`, so a failed run can
never read as a clean one.
"""

from __future__ import annotations

import math
import re
from typing import Any

from navin.security.bridge import family_for_cwe
from navin.security.model import Evidence, Finding


class ReportError(ValueError):
    """The scanner answered with an error document instead of results."""


_SEVERITY_ALIASES = {
    "critical": "critical",
    "high": "high",
    "error": "high",
    "medium": "medium",
    "moderate": "medium",
    "warning": "medium",
    "unknown": "medium",
    "low": "low",
    "note": "low",
    "info": "info",
    "informational": "info",
    "negligible": "info",
}
_CWE_RE = re.compile(r"CWE-(\d+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[^a-z0-9]+")

# Rule ids and titles of SAST tools carry the weakness in words when no CWE is
# given. First family whose prefix starts a token wins, in this order.
_FAMILY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("secrets", ("secret", "password", "passwd", "token", "credential", "apikey", "hardcoded")),
    ("injection", ("sql", "nosql", "xss", "inject", "ldap", "xpath", "ssti", "template", "command", "shell", "subprocess")),
    ("code_execution", ("deserial", "pickle", "marshal", "eval", "exec", "unsafe")),
    ("files", ("traversal", "path", "upload", "zip", "tarfile")),
    ("cryptography", ("crypt", "md5", "sha1", "cipher", "ssl", "tls", "random", "hash", "cert")),
    ("authentication", ("jwt", "auth", "session", "csrf", "cookie", "redirect", "login")),
    ("network", ("ssrf", "request")),
    ("infrastructure", ("docker", "kubernetes", "k8s", "terraform", "helm")),
    ("availability", ("redos", "regex", "dos", "timeout")),
    ("frontend", ("cors", "prototype", "dom", "iframe", "clickjack")),
    ("audit_logging", ("logging", "logger")),
)

# CVSS v3.x base metrics (FIRST specification, section 7.4).
_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}
_PR = {"U": {"N": 0.85, "L": 0.62, "H": 0.27}, "C": {"N": 0.85, "L": 0.68, "H": 0.5}}


def normalize_severity(value: Any, default: str = "medium") -> str:
    """Map a scanner's severity word to the five Navin severities."""
    return _SEVERITY_ALIASES.get(str(value or "").strip().lower(), default)


def severity_from_cvss(score: float | None, default: str = "medium") -> str:
    """CVSS v3 qualitative rating of a base score."""
    if score is None:
        return default
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0.0:
        return "low"
    return "info"


def _roundup(value: float) -> float:
    whole = round(value * 100000)
    if whole % 10000 == 0:
        return whole / 100000.0
    return (math.floor(whole / 10000) + 1) / 10.0


def cvss3_base_score(vector: Any) -> float | None:
    """Base score of a ``CVSS:3.x/...`` vector, or None when it cannot be read."""
    text = str(vector or "").strip()
    if not text.upper().startswith("CVSS:3"):
        return None
    try:
        metrics = dict(part.split(":", 1) for part in text.split("/")[1:])
        scope = metrics["S"]
        iss = 1 - (1 - _CIA[metrics["C"]]) * (1 - _CIA[metrics["I"]]) * (1 - _CIA[metrics["A"]])
        exploitability = 8.22 * _AV[metrics["AV"]] * _AC[metrics["AC"]] * _PR[scope][metrics["PR"]] * _UI[metrics["UI"]]
    except (KeyError, ValueError):
        return None
    if scope == "U":
        impact = 6.42 * iss
    else:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    if impact <= 0:
        return 0.0
    total = impact + exploitability if scope == "U" else 1.08 * (impact + exploitability)
    return _roundup(min(total, 10.0))


def first_cwe(value: Any) -> str:
    """First ``CWE-n`` found in a string, an int, or a list of either."""
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, int) and not isinstance(item, bool):
            return f"CWE-{item}"
        match = _CWE_RE.search(str(item or ""))
        if match:
            return f"CWE-{match.group(1)}"
    return ""


def infer_family(cwe: str, *hints: str, default: str = "injection") -> str:
    """Family of a SAST finding: its CWE when mapped, else words of its rule id and title."""
    mapped = family_for_cwe(cwe)
    if mapped:
        return mapped
    tokens = [t for t in _TOKEN_RE.split(" ".join(hints).lower()) if t]
    for family, prefixes in _FAMILY_HINTS:
        if any(token.startswith(prefix) for token in tokens for prefix in prefixes):
            return family
    return default


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _where(path: str, line: int | None) -> str:
    return f"{path}:{line}" if line else path


def dependency_finding(
    tool: str,
    *,
    advisory: str,
    package: str,
    version: str = "",
    severity: str = "medium",
    title: str = "",
    manifest: str = "",
    fixed: str = "",
    affected: str = "",
    aliases: list[str] | None = None,
    cwe: str = "",
    cvss: float | None = None,
    url: str = "",
    remediation: str = "",
) -> Finding:
    """One known vulnerability in one package, whatever the scanner.

    The advisory id is the finding id, so the same advisory reported by two
    scanners on the same package and lockfile merges into one finding.
    """
    aliases = [str(a) for a in (aliases or []) if a]
    cve = next((a for a in [advisory, *aliases] if a.upper().startswith("CVE-")), "")
    if not remediation and fixed:
        remediation = f"Upgrade {package} to a fixed version ({fixed})."
    elif not remediation:
        remediation = f"No fixed version of {package} is published yet: remove or replace it, or mitigate and record the accepted risk."
    what = f"{package} {version or affected}".strip()
    return Finding(
        id=advisory,
        family="supply_chain",
        title=title or f"{advisory} in {what}",
        severity=severity,
        cwe=cwe,
        cve=cve,
        file_path=manifest,
        component=f"{package}@{version}" if version else package,
        cvss=cvss,
        remediation=remediation,
        confidence=0.9,
        source=tool,
        references=[url] if url else [],
        evidence=[Evidence(kind="static", description=f"{advisory} affects {what}", tool=tool, location=manifest)],
        tags=["dependency", *sorted({a for a in aliases if a != advisory})[:6]],
    )


def parse_semgrep(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for item in (doc or {}).get("results") or []:
        extra = item.get("extra") or {}
        meta = extra.get("metadata") or {}
        cwe = first_cwe(meta.get("cwe"))
        rule = str(item.get("check_id") or "semgrep")
        path = str(item.get("path") or "")
        line = _int((item.get("start") or {}).get("line"))
        message = str(extra.get("message") or rule).strip()
        owasp = meta.get("owasp")
        out.append(
            Finding(
                id=f"semgrep:{rule}",
                family=infer_family(cwe, rule, message),
                title=message.splitlines()[0] if message else rule,
                severity=normalize_severity(extra.get("severity")),
                cwe=cwe,
                owasp=str(owasp[0] if isinstance(owasp, list) and owasp else owasp or ""),
                file_path=path,
                line=line,
                remediation=str(extra.get("fix") or ""),
                confidence={"HIGH": 0.85, "MEDIUM": 0.7, "LOW": 0.5}.get(str(meta.get("confidence") or "").upper(), 0.7),
                source="semgrep",
                references=[str(r) for r in (meta.get("references") or [])][:5],
                evidence=[Evidence(kind="static", description=f"rule {rule}", tool="semgrep", observed=str(extra.get("lines") or "")[:500], location=_where(path, line))],
            )
        )
    return out


def _redact(match: Any, secret: Any) -> str:
    text = str(match or "")
    secret_text = str(secret or "")
    if secret_text and secret_text != "REDACTED":
        text = text.replace(secret_text, "****")
    return text[:300]


def parse_gitleaks(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for item in doc or []:
        rule = str(item.get("RuleID") or "gitleaks")
        path = str(item.get("File") or "")
        line = _int(item.get("StartLine"))
        commit = str(item.get("Commit") or "")
        out.append(
            Finding(
                id=f"gitleaks:{rule}",
                family="secrets",
                title=str(item.get("Description") or f"Secret detected ({rule})"),
                severity="high",
                cwe="CWE-798",
                file_path=path,
                line=line,
                remediation="Revoke and rotate this secret, remove it from the code and the git history, load it from a secret manager.",
                confidence=0.85,
                source="gitleaks",
                evidence=[Evidence(kind="static", description=f"rule {rule}", tool="gitleaks", observed=_redact(item.get("Match"), item.get("Secret")), location=_where(path, line))],
                tags=[f"commit:{commit[:12]}"] if commit else [],
            )
        )
    return out


def _trivy_cvss(vuln: dict[str, Any]) -> float | None:
    sources = vuln.get("CVSS") or {}
    for name in ("nvd", "ghsa", "redhat"):
        score = _float((sources.get(name) or {}).get("V3Score"))
        if score is not None:
            return score
    return None


def parse_trivy(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for result in (doc or {}).get("Results") or []:
        target = str(result.get("Target") or "")
        for vuln in result.get("Vulnerabilities") or []:
            out.append(
                dependency_finding(
                    "trivy",
                    advisory=str(vuln.get("VulnerabilityID") or "trivy"),
                    package=str(vuln.get("PkgName") or ""),
                    version=str(vuln.get("InstalledVersion") or ""),
                    severity=normalize_severity(vuln.get("Severity")),
                    title=str(vuln.get("Title") or ""),
                    manifest=target,
                    fixed=str(vuln.get("FixedVersion") or ""),
                    cwe=first_cwe(vuln.get("CweIDs")),
                    cvss=_trivy_cvss(vuln),
                    url=str(vuln.get("PrimaryURL") or ""),
                )
            )
        for mis in result.get("Misconfigurations") or []:
            if str(mis.get("Status") or "FAIL").upper() != "FAIL":
                continue
            rule = str(mis.get("ID") or mis.get("AVDID") or "trivy-config")
            line = _int((mis.get("CauseMetadata") or {}).get("StartLine"))
            out.append(
                Finding(
                    id=f"trivy:{rule}",
                    family="infrastructure",
                    title=str(mis.get("Title") or rule),
                    severity=normalize_severity(mis.get("Severity")),
                    file_path=target,
                    line=line,
                    description=str(mis.get("Message") or ""),
                    remediation=str(mis.get("Resolution") or ""),
                    confidence=0.8,
                    source="trivy",
                    references=[str(r) for r in (mis.get("References") or [])][:3],
                    evidence=[Evidence(kind="static", description=f"misconfiguration {rule}", tool="trivy", location=_where(target, line))],
                )
            )
        for secret in result.get("Secrets") or []:
            rule = str(secret.get("RuleID") or "trivy-secret")
            line = _int(secret.get("StartLine"))
            out.append(
                Finding(
                    id=f"trivy:{rule}",
                    family="secrets",
                    title=str(secret.get("Title") or "Secret detected"),
                    severity=normalize_severity(secret.get("Severity"), "high"),
                    cwe="CWE-798",
                    file_path=target,
                    line=line,
                    remediation="Revoke and rotate this secret, then load it from a secret manager.",
                    confidence=0.8,
                    source="trivy",
                    evidence=[Evidence(kind="static", description=f"rule {rule}", tool="trivy", location=_where(target, line))],
                )
            )
    return out


def parse_osv_scanner(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for result in (doc or {}).get("results") or []:
        manifest = str((result.get("source") or {}).get("path") or "")
        for pkg in result.get("packages") or []:
            info = pkg.get("package") or {}
            groups = pkg.get("groups") or []
            for vuln in pkg.get("vulnerabilities") or []:
                vid = str(vuln.get("id") or "osv")
                specific = vuln.get("database_specific") or {}
                score = next((_float(g.get("max_severity")) for g in groups if vid in (g.get("ids") or [])), None)
                word = specific.get("severity")
                out.append(
                    dependency_finding(
                        "osv_scanner",
                        advisory=vid,
                        package=str(info.get("name") or ""),
                        version=str(info.get("version") or ""),
                        severity=normalize_severity(word) if word else severity_from_cvss(score),
                        title=str(vuln.get("summary") or ""),
                        manifest=manifest,
                        aliases=[str(a) for a in (vuln.get("aliases") or [])],
                        cwe=first_cwe(specific.get("cwe_ids")),
                        cvss=score,
                    )
                )
    return out


def parse_bandit(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for item in (doc or {}).get("results") or []:
        cwe_raw = item.get("issue_cwe")
        cwe = first_cwe(cwe_raw.get("id")) if isinstance(cwe_raw, dict) else first_cwe(cwe_raw)
        rule = str(item.get("test_id") or "bandit")
        name = str(item.get("test_name") or "")
        text = str(item.get("issue_text") or rule)
        path = str(item.get("filename") or "")
        line = _int(item.get("line_number"))
        out.append(
            Finding(
                id=f"bandit:{rule}",
                family=infer_family(cwe, name, text),
                title=text,
                severity=normalize_severity(item.get("issue_severity")),
                cwe=cwe,
                file_path=path,
                line=line,
                confidence={"HIGH": 0.8, "MEDIUM": 0.6, "LOW": 0.4}.get(str(item.get("issue_confidence") or "").upper(), 0.6),
                source="bandit",
                references=[str(item["more_info"])] if item.get("more_info") else [],
                evidence=[Evidence(kind="static", description=f"{rule} {name}".strip(), tool="bandit", observed=str(item.get("code") or "")[:500], location=_where(path, line))],
            )
        )
    return out


def parse_pip_audit(doc: Any) -> list[Finding]:
    # pip-audit 2.x wraps the list in {"dependencies": [...]}; 1.x printed the list.
    deps = doc.get("dependencies") if isinstance(doc, dict) else doc
    out: list[Finding] = []
    for dep in deps or []:
        for vuln in dep.get("vulns") or []:
            fixes = [str(v) for v in (vuln.get("fix_versions") or [])]
            description = str(vuln.get("description") or "").strip()
            out.append(
                dependency_finding(
                    "pip_audit",
                    advisory=str(vuln.get("id") or "pip-audit"),
                    package=str(dep.get("name") or ""),
                    version=str(dep.get("version") or ""),
                    # pip-audit gives no severity: high until the CVE is scored.
                    severity="high",
                    title=description.splitlines()[0][:200] if description else "",
                    fixed=fixes[0] if fixes else "",
                    aliases=[str(a) for a in (vuln.get("aliases") or [])],
                )
            )
    return out


def _npm_remediation(package: str, fix: Any) -> str:
    """npm says which direct dependency to bump; that is the fix to give."""
    if isinstance(fix, dict):
        target = str(fix.get("name") or package)
        version = str(fix.get("version") or "").strip()
        text = f"Upgrade {target} to {version}" if version else f"Upgrade {target}"
        text += "." if target == package else f": it pulls a fixed {package}."
        if fix.get("isSemVerMajor"):
            text += " This is a major version: check its breaking changes first."
        return text
    if fix:
        return f"Run npm audit fix: it upgrades {package} within the ranges package.json allows."
    return ""


def parse_npm_audit(doc: Any) -> list[Finding]:
    error = (doc or {}).get("error") if isinstance(doc, dict) else None
    if error:
        detail = (error.get("summary") or error.get("code")) if isinstance(error, dict) else error
        raise ReportError(f"npm audit failed: {str(detail).strip()[:300]}")
    out: list[Finding] = []
    for name, item in ((doc or {}).get("vulnerabilities") or {}).items():
        advisories = [v for v in (item.get("via") or []) if isinstance(v, dict)]
        # A package only pulled in through another vulnerable one lists names
        # in "via": the advisory is reported on that other package.
        for via in advisories:
            package = str(via.get("name") or name)
            url = str(via.get("url") or "")
            advisory = url.rstrip("/").rsplit("/", 1)[-1] if url else f"npm-{via.get('source') or 'advisory'}"
            score = _float((via.get("cvss") or {}).get("score"))
            out.append(
                dependency_finding(
                    "npm_audit",
                    advisory=advisory,
                    package=package,
                    severity=normalize_severity(via.get("severity") or item.get("severity")),
                    title=str(via.get("title") or ""),
                    manifest="package-lock.json",
                    affected=str(via.get("range") or item.get("range") or ""),
                    cwe=first_cwe(via.get("cwe")),
                    cvss=score if score else None,
                    url=url,
                    remediation=_npm_remediation(package, item.get("fixAvailable")),
                )
            )
    return out


def parse_cargo_audit(doc: Any) -> list[Finding]:
    out: list[Finding] = []
    for item in ((doc or {}).get("vulnerabilities") or {}).get("list") or []:
        adv = item.get("advisory") or {}
        pkg = item.get("package") or {}
        patched = [str(v) for v in ((item.get("versions") or {}).get("patched") or [])]
        score = cvss3_base_score(adv.get("cvss"))
        out.append(
            dependency_finding(
                "cargo_audit",
                advisory=str(adv.get("id") or "RUSTSEC"),
                package=str(pkg.get("name") or adv.get("package") or ""),
                version=str(pkg.get("version") or ""),
                # RustSec scores with a CVSS vector when it has one.
                severity=severity_from_cvss(score, "high"),
                title=str(adv.get("title") or ""),
                manifest="Cargo.lock",
                fixed=" or ".join(patched),
                aliases=[str(a) for a in (adv.get("aliases") or [])],
                cvss=score,
                url=str(adv.get("url") or ""),
            )
        )
    return out
