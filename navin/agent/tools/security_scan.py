# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: structured AppSec scan (heuristics + optional host CLIs).

``deep=true`` (the default) adds the infrastructure and project layers of
:mod:`navin.security.deep_scan`: Dockerfile, Compose, Kubernetes, Terraform,
GitHub Actions, env files, tainted sinks, weak crypto, project hygiene, and
returns a 0-100 score with OWASP / CWE tags and a remediation plan. Reports:
``write_report`` writes the HTML, ``write_pdf`` the PDF next to it.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.security.deep_scan import run_deep_security_scan
from navin.security.report_deep import (
    pdf_backends_available,
    render_deep_report_html,
    render_deep_report_pdf,
)
from navin.security.report_html import write_security_report
from navin.security.scan import run_security_scan

_FOCUSED_KINDS = {"secrets", "sast", "sca"}


class SecurityScanTool(_QualityTool):
    """Run a bounded security scan and return structured findings."""

    @property
    def name(self) -> str:
        return "security_scan"

    @property
    def description(self) -> str:
        return (
            "Run a structured security scan on the project: 110+ always-on offline "
            "code rules with CWE + OWASP Top 10 tags (secrets and tokens, SQL/NoSQL/"
            "LDAP/XPath/command/template injection, XSS, deserialization, path "
            "traversal, SSRF, XXE, weak crypto, JWT/session/CSRF/CORS, debug and bind "
            "misconfig, known-vulnerable pinned dependencies) across Python, JS/TS, Go, "
            "Java, PHP, Ruby, C#, C, shell, SQL and YAML; a deep layer for "
            "infrastructure (Dockerfile, Compose, Kubernetes, Terraform, GitHub "
            "Actions, env files) and project hygiene (tracked .env, lockfiles, helmet, "
            "CI scanners); plus host CLIs when installed (gitleaks, bandit, semgrep, "
            "npm audit, pip-audit). Comments, docstrings, regex sources, placeholders "
            "and test paths are filtered out; `# nosec` silences a line. Returns a "
            "0-100 score, a lab payload + PoC sketch per finding and an ordered "
            "remediation plan. write_report=true writes security-report-*.html (and "
            "opens File Preview), write_pdf=true also writes the PDF. "
            "kind=full|quick|secrets|sast|sca; deep=false keeps the code-only pass."
        )

    @property
    def read_only(self) -> bool:
        # write_report / write_pdf create files in the workspace.
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["full", "quick", "secrets", "sast", "sca"],
                    "description": (
                        "full: heuristics + secrets/SAST/SCA CLIs; "
                        "quick: heuristics only; "
                        "secrets|sast|sca: focused code-only passes"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Optional project-relative file or directory to scope the scan.",
                },
                "deep": {
                    "type": "boolean",
                    "description": (
                        "Default true: add infrastructure, configuration and project "
                        "checks, score, OWASP/CWE tags and remediation plan. "
                        "Ignored for secrets|sast|sca."
                    ),
                },
                "max_findings": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 300,
                    "description": "Max findings to return after dedupe (default 80).",
                },
                "write_report": {
                    "type": "boolean",
                    "description": (
                        "If true, write security-report-<timestamp>.html under the "
                        "project root and return its path (then open_file_preview)."
                    ),
                },
                "write_pdf": {
                    "type": "boolean",
                    "description": (
                        "If true, also write security-report-<timestamp>.pdf next to "
                        "the HTML (WeasyPrint or ReportLab; reported when neither is installed)."
                    ),
                },
            },
        }

    async def execute(
        self,
        kind: str = "full",
        path: str | None = None,
        deep: bool = True,
        max_findings: int = 80,
        write_report: bool = False,
        write_pdf: bool = False,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        sub: str | None = None
        raw_path = (path or "").strip()
        if raw_path:
            rel, path_error = self._project_relative(raw_path, root)
            if path_error:
                return path_error
            sub = rel or None

        kind_n = (kind or "full").strip().lower() or "full"
        use_deep = bool(deep) and kind_n not in _FOCUSED_KINDS
        if use_deep:
            result = await asyncio.to_thread(
                run_deep_security_scan,
                root,
                kind=kind_n,
                subpath=sub,
                max_findings=int(max_findings or 80),
            )
        else:
            result = await asyncio.to_thread(
                run_security_scan,
                root,
                kind=kind_n,
                subpath=sub,
                max_findings=int(max_findings or 80),
            )
        if not result.get("ok"):
            return self.error(str(result.get("error") or "scan failed"))

        report_path: str | None = None
        pdf_path: str | None = None
        pdf_error: str | None = None
        preview_opened = False
        if write_report or write_pdf:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            title = f"Security assessment - {root.name}"
            if use_deep:
                html_file = root / f"security-report-{stamp}.html"
                html_doc = await asyncio.to_thread(render_deep_report_html, result, title=title)
                await asyncio.to_thread(html_file.write_text, html_doc, "utf-8")
            else:
                html_file = await asyncio.to_thread(
                    write_security_report,
                    root,
                    findings=list(result.get("findings") or []),
                    kind=str(result.get("kind") or kind_n),
                    tools=list(result.get("tools") or []),
                    title="Security review",
                )
            if write_report:
                report_path = str(html_file)
                preview_opened = self._open_file_preview(html_file)
            if write_pdf:
                if not pdf_backends_available():
                    pdf_error = "no PDF backend installed (pip install weasyprint or reportlab)"
                else:
                    try:
                        pdf_bytes = await asyncio.to_thread(
                            render_deep_report_pdf,
                            result if use_deep else _as_deep_shape(result, root),
                            title=title,
                        )
                        pdf_file = Path(html_file).with_suffix(".pdf")
                        await asyncio.to_thread(pdf_file.write_bytes, pdf_bytes)
                        pdf_path = str(pdf_file)
                    except RuntimeError as exc:
                        pdf_error = str(exc)
            if not write_report:
                # PDF only: the HTML was a rendering step, do not leave it behind.
                # A failed removal is reported next to the PDF path, not hidden.
                try:
                    Path(html_file).unlink()
                except OSError as exc:
                    pdf_error = (
                        f"{pdf_error}; " if pdf_error else ""
                    ) + f"temporary HTML left at {html_file}: {exc}"

        counts = result.get("counts") or {}
        tools = result.get("tools") or []
        available = [
            t.get("tool")
            for t in tools
            if isinstance(t, dict) and t.get("available")
        ]
        header = (
            f"security_scan kind={result.get('kind')} deep={str(use_deep).lower()} "
            f"findings={result.get('finding_count')} "
            f"counts={counts} tools={available}"
        )
        if use_deep:
            header += f" score={result.get('score')}/100 grade={result.get('grade')}"
        if report_path:
            header += f" report={report_path}"
            if preview_opened:
                header += " preview=opened"
        if pdf_path:
            header += f" pdf={pdf_path}"
        if pdf_error:
            header += f" pdf_error={pdf_error}"
        header += "\n"
        payload: dict[str, Any] = {
            "counts": counts,
            "tools": tools,
            "findings": result.get("findings") or [],
            "report_path": report_path,
            "pdf_path": pdf_path,
            "pdf_error": pdf_error,
            "preview_opened": preview_opened,
            "note": result.get("note"),
        }
        if use_deep:
            payload.update(
                {
                    "score": result.get("score"),
                    "grade": result.get("grade"),
                    "files_scanned": result.get("files_scanned"),
                    "by_owasp": result.get("by_owasp"),
                    "by_category": result.get("by_category"),
                    "project_checks": result.get("project_checks"),
                    "remediation_plan": result.get("remediation_plan"),
                }
            )
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return header + body


def _as_deep_shape(result: dict[str, Any], root: Path) -> dict[str, Any]:
    """Give a code-only result the keys the PDF renderer reads."""
    from navin.security.deep_scan import compute_score
    from navin.security.findings import severity_counts

    findings = list(result.get("findings") or [])
    counts = severity_counts(findings)
    score, grade = compute_score(counts)
    shaped = dict(result)
    shaped.setdefault("root", str(root))
    shaped.update({"counts": counts, "score": score, "grade": grade})
    shaped.setdefault("by_owasp", [])
    shaped.setdefault("project_checks", [])
    shaped.setdefault("remediation_plan", [])
    shaped.setdefault("files_scanned", 0)
    shaped.setdefault("duration_ms", 0)
    return shaped
