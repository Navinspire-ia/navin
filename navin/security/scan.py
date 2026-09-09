# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Local AppSec scanners for the security agent mode.

Combines fast heuristic checks (always available) with optional host CLIs
(gitleaks, bandit, npm audit, pip-audit, semgrep, osv-scanner) when installed.
Does not depend on AGPL third-party agent platforms.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from navin.security.findings import dedupe_findings, normalize_finding, severity_counts
from navin.security.fp_filter import apply_fp_filter
from navin.security.poc import enrich_findings_with_poc
from navin.utils.proc import no_window_kwargs

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "out",
        ".next",
        ".nuxt",
        ".output",
        "coverage",
        ".idea",
        ".vscode",
        "target",
        "vendor",
        ".tox",
        ".cache",
        "site-packages",
        ".terraform",
        ".gradle",
        "bower_components",
        ".turbo",
        ".parcel-cache",
        "htmlcov",
        ".eggs",
        "open-kritt",
    }
)

_CODE_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".cs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".swift",
        ".sh",
        ".bash",
        ".sql",
        ".vue",
        ".svelte",
        ".yml",
        ".yaml",
        ".json",
        ".env",
        ".toml",
        ".pem",
        ".key",
    }
)

_PLACEHOLDER = re.compile(
    r"(?i)(example|sample|placeholder|changeme|your[_-]?|xxx|todo|dummy|fake|test[_-]?(key|token|secret))"
)
_KEY_PLACEHOLDER = re.compile(r"(?i)(BEGIN.*EXAMPLE|REPLACE_ME)")

_HEURISTICS: list[dict[str, Any]] = [
    {
        "id": "private-key",
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "severity": "critical",
        "category": "Secrets",
        "summary": "Private key material in the repository",
        "recommendation": "Remove the key, rotate it, store it in a secrets manager.",
        "exclude": _KEY_PLACEHOLDER,
    },
    {
        "id": "aws-key",
        "pattern": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "severity": "critical",
        "category": "Secrets",
        "summary": "Hardcoded AWS access key id",
        "recommendation": "Revoke the key and use IAM roles or env vars.",
    },
    {
        "id": "hardcoded-secret",
        "pattern": re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"
        ),
        "severity": "high",
        "category": "Secrets",
        "summary": "Possible hardcoded secret",
        "recommendation": "Move the value to env vars or a secrets vault.",
        "exclude": _PLACEHOLDER,
    },
    {
        "id": "sql-injection",
        "pattern": re.compile(
            r"(f[\"'][^\"']*(SELECT\s[^\"']*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"']*\{"
            r"|[\"'][^\"']*(SELECT\s[^\"']*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"']*[\"']\s*(%|\+|\.format\())"
        ),
        "severity": "critical",
        "category": "Injection",
        "summary": "SQL built via string interpolation",
        "recommendation": "Use parameterized queries or an ORM.",
    },
    {
        "id": "eval-exec",
        "pattern": re.compile(r"(?<![\w.])(eval|exec)\s*\("),
        "severity": "high",
        "category": "Code execution",
        "summary": "eval/exec on potentially controlled input",
        "recommendation": "Prefer explicit parsers (ast.literal_eval, JSON).",
        "exts": {".py", ".js", ".ts", ".jsx", ".tsx"},
    },
    {
        "id": "pickle",
        "pattern": re.compile(r"\bpickle\.loads?\("),
        "severity": "high",
        "category": "Deserialization",
        "summary": "Unsafe pickle deserialization",
        "recommendation": "Never unpickle untrusted data; prefer JSON.",
        "exts": {".py"},
    },
    {
        "id": "shell-true",
        "pattern": re.compile(r"shell\s*=\s*True"),
        "severity": "high",
        "category": "Command injection",
        "summary": "subprocess with shell=True",
        "recommendation": "Pass an argv list without shell=True.",
        "exts": {".py"},
    },
    {
        "id": "tls-verify-off",
        "pattern": re.compile(
            r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true"
        ),
        "severity": "high",
        "category": "TLS",
        "summary": "TLS certificate verification disabled",
        "recommendation": "Re-enable verification; use a custom CA bundle if needed.",
    },
    {
        "id": "inner-html",
        "pattern": re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|document\.write\("),
        "severity": "medium",
        "category": "XSS",
        "summary": "Direct HTML injection sink",
        "recommendation": "Sanitize (DOMPurify) or use safe framework rendering.",
        "exts": {".ts", ".tsx", ".js", ".jsx"},
    },
    {
        "id": "jwt-none",
        "pattern": re.compile(
            r"(?i)(algorithms?\s*[:=]\s*\[?\s*[\"']none[\"']|verify_signature[\"']?\s*[:=]\s*False)"
        ),
        "severity": "high",
        "category": "Authentication",
        "summary": "Weakened JWT verification",
        "recommendation": "Always verify signatures with an explicit strong algorithm.",
    },
    {
        "id": "cors-wildcard",
        "pattern": re.compile(
            r"(Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*|allow_origins\s*=\s*\[?\s*[\"']\*)"
        ),
        "severity": "medium",
        "category": "CORS",
        "summary": "CORS allows any origin (*)",
        "recommendation": "Restrict allowed origins to an explicit allow-list.",
    },
]

_MAX_FILES = 2500
_MAX_FILE_BYTES = 400_000
_CMD_TIMEOUT_S = 90


def _is_test_path(rel: str) -> bool:
    lower = rel.replace("\\", "/").lower()
    return (
        "/test/" in f"/{lower}/"
        or "/tests/" in f"/{lower}/"
        or "/__tests__/" in f"/{lower}/"
        or lower.endswith("_test.py")
        or lower.endswith(".test.ts")
        or lower.endswith(".test.tsx")
        or lower.endswith(".spec.ts")
        or lower.endswith(".spec.tsx")
    )


def _iter_files(root: Path, subpath: str | None) -> list[tuple[str, Path]]:
    base = root
    if subpath:
        candidate = (root / subpath).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return []
        if candidate.is_file():
            return [(subpath.replace("\\", "/"), candidate)]
        if candidate.is_dir():
            base = candidate
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIRS and (not d.startswith(".") or d == ".github")
        )
        for name in sorted(filenames):
            if ".min." in name.lower():
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            ext = full.suffix.lower()
            if ext not in _CODE_EXTS and full.name not in {".env", "Dockerfile"}:
                continue
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if _is_test_path(rel):
                continue
            out.append((rel, full))
            if len(out) >= _MAX_FILES:
                return out
    return out


def scan_heuristics(root: Path, *, subpath: str | None = None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rel, full in _iter_files(root, subpath):
        ext = full.suffix.lower()
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if len(line) > 500:
                continue
            for check in _HEURISTICS:
                exts = check.get("exts")
                if exts and ext not in exts:
                    continue
                match = check["pattern"].search(line)
                if not match:
                    continue
                exclude = check.get("exclude")
                if exclude and exclude.search(line):
                    continue
                findings.append(
                    normalize_finding(
                        {
                            "id": check["id"],
                            "severity": check["severity"],
                            "category": check["category"],
                            "summary": check["summary"],
                            "explanation": f"{check['summary']}: `{line.strip()[:160]}`",
                            "recommendation": check["recommendation"],
                            "file_path": rel,
                            "line": idx,
                            "source": "heuristic",
                        }
                    )
                )
    return findings


def _run_json_cmd(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int = _CMD_TIMEOUT_S,
) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **no_window_kwargs(),
        )
    except FileNotFoundError:
        return None, "not_installed"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    raw = (completed.stdout or "").strip()
    if not raw:
        return None, completed.stderr.strip()[:200] or f"exit_{completed.returncode}"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, "invalid_json"


def _scan_gitleaks(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"tool": "gitleaks", "available": False}
    bin_path = shutil.which("gitleaks")
    if not bin_path:
        return [], meta
    meta["available"] = True
    report = root / ".navin-gitleaks-report.json"
    try:
        completed = subprocess.run(  # noqa: S603
            [
                bin_path,
                "detect",
                "--source",
                str(root),
                "--no-git",
                "--report-format",
                "json",
                "--report-path",
                str(report),
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CMD_TIMEOUT_S,
            **no_window_kwargs(),
        )
        meta["exit_code"] = completed.returncode
        if not report.is_file():
            return [], meta
        data = json.loads(report.read_text(encoding="utf-8", errors="replace") or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        meta["error"] = str(exc)
        return [], meta
    finally:
        try:
            report.unlink(missing_ok=True)
        except OSError:
            pass
    findings: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data[:200]:
            if not isinstance(item, dict):
                continue
            findings.append(
                normalize_finding(
                    {
                        "id": str(item.get("RuleID") or "gitleaks"),
                        "severity": "critical",
                        "category": "Secrets",
                        "summary": str(item.get("Description") or "Secret detected by gitleaks"),
                        "explanation": str(item.get("Match") or item.get("Secret") or "")[:200],
                        "recommendation": "Rotate the secret and remove it from the tree/history.",
                        "file_path": str(item.get("File") or ""),
                        "line": item.get("StartLine"),
                        "source": "gitleaks",
                    }
                )
            )
    meta["count"] = len(findings)
    return findings, meta


def _scan_bandit(root: Path, subpath: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"tool": "bandit", "available": False}
    bin_path = shutil.which("bandit")
    if not bin_path:
        return [], meta
    meta["available"] = True
    target = str((root / subpath).resolve()) if subpath else str(root)
    payload, err = _run_json_cmd([bin_path, "-r", target, "-f", "json", "-q"], cwd=root)
    if err:
        meta["error"] = err
        return [], meta
    findings: list[dict[str, Any]] = []
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, list):
        for item in results[:200]:
            if not isinstance(item, dict):
                continue
            sev = str(item.get("issue_severity") or "MEDIUM").lower()
            findings.append(
                normalize_finding(
                    {
                        "id": str(item.get("test_id") or "bandit"),
                        "severity": sev if sev in {"low", "medium", "high"} else "medium",
                        "category": "SAST",
                        "summary": str(item.get("issue_text") or "Bandit finding"),
                        "explanation": str(item.get("issue_text") or ""),
                        "recommendation": str(item.get("more_info") or "Review and harden this sink."),
                        "file_path": str(item.get("filename") or "").replace(str(root) + "/", ""),
                        "line": item.get("line_number"),
                        "source": "bandit",
                    }
                )
            )
    meta["count"] = len(findings)
    return findings, meta


def _scan_npm_audit(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"tool": "npm_audit", "available": False}
    if not (root / "package.json").is_file():
        return [], meta
    npm = shutil.which("npm")
    if not npm:
        return [], meta
    meta["available"] = True
    payload, err = _run_json_cmd([npm, "audit", "--json"], cwd=root, timeout=120)
    if err and payload is None:
        meta["error"] = err
        return [], meta
    findings: list[dict[str, Any]] = []
    vulns = payload.get("vulnerabilities") if isinstance(payload, dict) else None
    if isinstance(vulns, dict):
        for name, info in list(vulns.items())[:150]:
            if not isinstance(info, dict):
                continue
            sev = str(info.get("severity") or "moderate").lower()
            if sev == "moderate":
                sev = "medium"
            via = info.get("via")
            summary = name
            if isinstance(via, list) and via:
                first = via[0]
                if isinstance(first, dict):
                    summary = str(first.get("title") or name)
                elif isinstance(first, str):
                    summary = first
            findings.append(
                normalize_finding(
                    {
                        "id": f"npm:{name}",
                        "severity": sev if sev in {"critical", "high", "medium", "low", "info"} else "medium",
                        "category": "Supply chain",
                        "summary": f"npm vulnerability in {name}: {summary}"[:240],
                        "explanation": str(info.get("range") or ""),
                        "recommendation": "Upgrade to a non-vulnerable version (npm audit fix when safe).",
                        "file_path": "package-lock.json"
                        if (root / "package-lock.json").is_file()
                        else "package.json",
                        "source": "npm_audit",
                    }
                )
            )
    meta["count"] = len(findings)
    return findings, meta


def _scan_pip_audit(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"tool": "pip_audit", "available": False}
    req = None
    for name in ("requirements.txt", "requirements.lock", "pyproject.toml"):
        if (root / name).is_file():
            req = name
            break
    if req is None:
        return [], meta
    bin_path = shutil.which("pip-audit")
    if not bin_path:
        return [], meta
    meta["available"] = True
    argv = [bin_path, "-f", "json"]
    if req == "requirements.txt":
        argv.extend(["-r", "requirements.txt"])
    payload, err = _run_json_cmd(argv, cwd=root, timeout=120)
    if err and payload is None:
        meta["error"] = err
        return [], meta
    findings: list[dict[str, Any]] = []
    rows = payload if isinstance(payload, list) else []
    for item in rows[:150]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "package")
        vulns = item.get("vulns") if isinstance(item.get("vulns"), list) else []
        for vuln in vulns[:5]:
            if not isinstance(vuln, dict):
                continue
            findings.append(
                normalize_finding(
                    {
                        "id": str(vuln.get("id") or f"pip:{name}"),
                        "severity": "high",
                        "category": "Supply chain",
                        "summary": f"pip vulnerability in {name}: {vuln.get('id')}",
                        "explanation": str(vuln.get("description") or "")[:500],
                        "recommendation": "Upgrade the package to a fixed version.",
                        "file_path": req,
                        "source": "pip_audit",
                    }
                )
            )
    meta["count"] = len(findings)
    return findings, meta


def _scan_semgrep(root: Path, subpath: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"tool": "semgrep", "available": False}
    bin_path = shutil.which("semgrep")
    if not bin_path:
        return [], meta
    meta["available"] = True
    target = str((root / subpath).resolve()) if subpath else str(root)
    payload, err = _run_json_cmd(
        [bin_path, "scan", "--config", "auto", "--json", "--quiet", target],
        cwd=root,
        timeout=180,
    )
    if err and payload is None:
        meta["error"] = err
        return [], meta
    findings: list[dict[str, Any]] = []
    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, list):
        for item in results[:200]:
            if not isinstance(item, dict):
                continue
            extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
            sev = str(extra.get("severity") or "WARNING").lower()
            sev_map = {"error": "high", "warning": "medium", "info": "info"}
            findings.append(
                normalize_finding(
                    {
                        "id": str(item.get("check_id") or "semgrep"),
                        "severity": sev_map.get(sev, "medium"),
                        "category": "SAST",
                        "summary": str(extra.get("message") or item.get("check_id") or "Semgrep finding"),
                        "explanation": str(extra.get("message") or ""),
                        "recommendation": "Follow the Semgrep rule guidance and patch the sink.",
                        "file_path": str(item.get("path") or ""),
                        "line": (item.get("start") or {}).get("line")
                        if isinstance(item.get("start"), dict)
                        else None,
                        "source": "semgrep",
                    }
                )
            )
    meta["count"] = len(findings)
    return findings, meta


_SCANNERS: dict[str, Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]] = {
    "gitleaks": lambda root, subpath: _scan_gitleaks(root),
    "bandit": _scan_bandit,
    "npm_audit": lambda root, subpath: _scan_npm_audit(root),
    "pip_audit": lambda root, subpath: _scan_pip_audit(root),
    "semgrep": _scan_semgrep,
}


def run_security_scan(
    root: Path,
    *,
    kind: str = "full",
    subpath: str | None = None,
    max_findings: int = 120,
) -> dict[str, Any]:
    """Run heuristic + optional CLI scanners. ``kind``: full|secrets|sast|sca|quick."""
    root = root.expanduser().resolve(strict=False)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {root}", "findings": []}

    kind_n = (kind or "full").strip().lower()
    findings: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    want_heur = kind_n in {"full", "quick", "secrets", "sast"}
    want_secrets_cli = kind_n in {"full", "secrets"}
    want_sast_cli = kind_n in {"full", "sast"}
    want_sca = kind_n in {"full", "sca"}

    if want_heur:
        findings.extend(scan_heuristics(root, subpath=subpath))
        tools.append({"tool": "heuristic", "available": True, "count": len(findings)})

    if want_secrets_cli:
        extra, meta = _scan_gitleaks(root)
        findings.extend(extra)
        tools.append(meta)

    if want_sast_cli:
        for name in ("bandit", "semgrep"):
            extra, meta = _SCANNERS[name](root, subpath)
            findings.extend(extra)
            tools.append(meta)

    if want_sca:
        for name in ("npm_audit", "pip_audit"):
            extra, meta = _SCANNERS[name](root, subpath)
            findings.extend(extra)
            tools.append(meta)

    merged = enrich_findings_with_poc(
        dedupe_findings(findings)[: max(1, min(int(max_findings) * 2, 400))]
    )
    # Default confidence for heuristic/CLI before FP filter.
    for item in merged:
        if item.get("confidence") is None:
            src = str(item.get("source") or "")
            item["confidence"] = 0.85 if src in {"gitleaks", "bandit", "semgrep"} else 0.72
    kept, dropped = apply_fp_filter(merged, min_confidence=0.65)
    kept = kept[: max(1, min(int(max_findings), 300))]
    return {
        "ok": True,
        "root": str(root),
        "kind": kind_n,
        "subpath": subpath,
        "counts": severity_counts(kept),
        "tools": tools,
        "findings": kept,
        "dropped_count": len(dropped),
        "finding_count": len(kept),
        "note": (
            "Heuristic findings always run. CLI scanners only contribute when "
            "installed on PATH (gitleaks, bandit, semgrep, npm, pip-audit). "
            "Each finding includes malicious_input_example + poc_sketch. "
            "FP filter drops low-confidence / theoretical / test-path noise "
            f"({len(dropped)} dropped)."
        ),
    }
