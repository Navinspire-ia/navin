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
from navin.security.rules import (
    LINE_RULES,
    file_checks,
    is_dependency_manifest,
    scan_dependencies,
)
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
        ".tf",
        ".tfvars",
        ".hcl",
        ".xml",
        ".properties",
        ".ini",
        ".cfg",
        ".conf",
        ".gradle",
        ".kts",
        ".html",
        ".erb",
        ".ejs",
        ".dockerfile",
        ".p12",
        ".pfx",
        ".ppk",
    }
)

_KEY_FILE_NAMES = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "Jenkinsfile"})


def _logical_ext(name: str) -> str:
    """Extension used for rule matching; Dockerfile* and .env* get a stable one."""
    lower = name.lower()
    if lower == "dockerfile" or lower.startswith("dockerfile.") or lower.endswith(".dockerfile"):
        return ".dockerfile"
    if lower == ".env" or lower.startswith(".env."):
        return ".env"
    return Path(name).suffix.lower()


def _wanted(name: str) -> bool:
    ext = _logical_ext(name)
    return (
        ext in _CODE_EXTS
        or name in _KEY_FILE_NAMES
        or is_dependency_manifest(name)
    )


_IGNORE_MARK = re.compile(r"(?i)(#|//|/\*|<!--)\s*(nosec|noqa:\s*S\d+|navin-security:\s*ignore|nosemgrep)")

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
            if ".min." in name.lower() or name.endswith((".lock", "-lock.json")):
                continue
            # Our own HTML deliverables quote every payload they report on.
            if name.startswith(("security-report-", "review-report-")) and name.endswith(".html"):
                continue
            full = Path(dirpath) / name
            try:
                if full.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            if not _wanted(full.name):
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


_BLOCK_COMMENT_EXTS = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".swift", ".rs", ".scala"}
)
_REGEX_LINE = re.compile(r"\\[sbwd][*+(?]|\(\?:|\(\?i\)|\(\?<!")
_COMMENT_LINE = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--|\"\"\"|''')|^\s*\*/")


def _redact_secret(excerpt: str, match: re.Match[str]) -> str:
    """Keep the shape of a leaked value (first 4 chars) so the report never re-leaks it."""
    value = match.group(0)
    quoted = re.findall(r"[\"']([^\"']{6,})[\"']", value)
    if quoted:
        secret = quoted[-1]
    elif re.search(r"[:=]\s*\S", value):
        secret = re.split(r"[:=]\s*", value, maxsplit=1)[-1]
    elif "://" in value:
        secret = value.split("://", 1)[1]
    else:
        secret = value
    secret = secret.strip()
    if len(secret) < 8:
        return excerpt
    masked = secret[:4] + "*" * min(len(secret) - 4, 12)
    return excerpt.replace(secret, masked)


def _inside_string(line: str, pos: int) -> bool:
    """True when ``pos`` sits inside a quoted literal (odd number of quotes before it)."""
    head = line[:pos]
    dq = len(re.findall(r'(?<!\\)"', head))
    sq = len(re.findall(r"(?<!\\)'", head))
    return dq % 2 == 1 or sq % 2 == 1 or head.count("`") % 2 == 1


def scan_heuristics(root: Path, *, subpath: str | None = None) -> list[dict[str, Any]]:
    """Line rules + whole-file checks + offline dependency pins."""
    findings: list[dict[str, Any]] = []
    for rel, full in _iter_files(root, subpath):
        ext = _logical_ext(full.name)
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if is_dependency_manifest(rel):
            findings.extend(normalize_finding(f) for f in scan_dependencies(rel, text))
        findings.extend(normalize_finding(f) for f in file_checks(rel, text))
        if ext not in _CODE_EXTS and full.name not in _KEY_FILE_NAMES:
            continue
        in_doc = False
        in_block = False
        for idx, line in enumerate(text.splitlines(), start=1):
            # Docstrings (""" / ''') and /* */ blocks: their lines count as
            # comments for the code-only rules, so prose about `shell=True`
            # or `eval(` does not become a finding.
            was_comment = in_doc or in_block
            if ext == ".py":
                if (line.count('"""') + line.count("'''")) % 2 == 1:
                    in_doc = not in_doc
                    was_comment = True
            elif ext in _BLOCK_COMMENT_EXTS:
                opens, closes = line.count("/*"), line.count("*/")
                if opens != closes:
                    in_block = opens > closes
                    was_comment = True
            if len(line) > 500 or _IGNORE_MARK.search(line):
                continue
            is_comment = was_comment or bool(_COMMENT_LINE.match(line))
            # Regex sources (a scanner's own rules, validators) mention sinks by name.
            is_regex_line = bool(_REGEX_LINE.search(line))
            for check in LINE_RULES:
                exts = check.get("exts")
                if exts and ext not in exts:
                    continue
                code_only = bool(check.get("code_only"))
                if code_only and is_comment:
                    continue
                if is_regex_line and check.get("category") != "Secrets":
                    continue
                match = check["pattern"].search(line)
                if not match:
                    continue
                if code_only and _inside_string(line, match.start()):
                    continue
                exclude = check.get("exclude")
                if exclude and exclude.search(line):
                    continue
                excerpt = line.strip()[:160]
                if check["category"] == "Secrets":
                    excerpt = _redact_secret(excerpt, match)
                findings.append(
                    normalize_finding(
                        {
                            "id": check["id"],
                            "severity": check["severity"],
                            "category": check["category"],
                            "summary": check["summary"],
                            "explanation": f"{check['summary']}: `{excerpt}`",
                            "recommendation": check["recommendation"],
                            "file_path": rel,
                            "line": idx,
                            "source": "heuristic",
                            "cwe": check.get("cwe"),
                            "owasp": check.get("owasp"),
                            "confidence": check.get("confidence"),
                            "malicious_input_example": check.get("malicious_input_example"),
                            "poc_sketch": check.get("poc_sketch"),
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


# Generic rules that step aside when a more specific one fired on the same line.
_GENERIC_RULES = {
    "hardcoded-secret": "Secrets",
    "hardcoded-secret-env": "Secrets",
    "sql-concat-call": "Injection",
    "path-traversal-var": "Path traversal",
    "weak-hash": None,
}


def _collapse_overlaps(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One finding per line and category: the specific rule wins over the generic one."""
    specific: set[tuple[str, Any, str]] = set()
    for item in findings:
        if item.get("id") not in _GENERIC_RULES:
            specific.add((str(item.get("file_path")), item.get("line"), str(item.get("category"))))
    out: list[dict[str, Any]] = []
    for item in findings:
        rid = str(item.get("id"))
        if rid in _GENERIC_RULES:
            cat = _GENERIC_RULES[rid] or str(item.get("category"))
            if (str(item.get("file_path")), item.get("line"), cat) in specific:
                continue
        out.append(item)
    return out


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
        dedupe_findings(_collapse_overlaps(findings))[: max(1, min(int(max_findings) * 2, 400))]
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
