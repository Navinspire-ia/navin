# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: structured AppSec scan (heuristics + optional host CLIs)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.security.report_html import write_security_report
from navin.security.scan import run_security_scan


class SecurityScanTool(_QualityTool):
    """Run a bounded security scan and return structured findings."""

    @property
    def name(self) -> str:
        return "security_scan"

    @property
    def description(self) -> str:
        return (
            "Run a structured AppSec scan on the project: always-on heuristics "
            "(secrets, injection sinks, XSS, weak JWT, CORS, shell=True, pickle…) "
            "plus optional host CLIs when installed (gitleaks, bandit, semgrep, "
            "npm audit, pip-audit). Each finding includes a PoC sketch. "
            "Set write_report=true to write security-report-*.html and auto-open "
            "File Preview in the WebUI. kind=secrets|sast|sca|quick|full."
        )

    @property
    def read_only(self) -> bool:
        # write_report creates an HTML file in the workspace.
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
                        "secrets|sast|sca: focused passes"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Optional project-relative file or directory to scope the scan.",
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
            },
        }

    async def execute(
        self,
        kind: str = "full",
        path: str | None = None,
        max_findings: int = 80,
        write_report: bool = False,
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

        result = await asyncio.to_thread(
            run_security_scan,
            root,
            kind=kind or "full",
            subpath=sub,
            max_findings=int(max_findings or 80),
        )
        if not result.get("ok"):
            return self.error(str(result.get("error") or "scan failed"))

        report_path = None
        preview_opened = False
        if write_report:
            report_file = await asyncio.to_thread(
                write_security_report,
                root,
                findings=list(result.get("findings") or []),
                kind=str(result.get("kind") or kind or "full"),
                tools=list(result.get("tools") or []),
                title="Security review",
            )
            report_path = str(report_file)
            preview_opened = self._open_file_preview(report_file)

        counts = result.get("counts") or {}
        tools = result.get("tools") or []
        available = [
            t.get("tool")
            for t in tools
            if isinstance(t, dict) and t.get("available")
        ]
        header = (
            f"security_scan kind={result.get('kind')} "
            f"findings={result.get('finding_count')} "
            f"counts={counts} tools={available}"
        )
        if report_path:
            header += f" report={report_path}"
            if preview_opened:
                header += " preview=opened"
        header += "\n"
        body = json.dumps(
            {
                "counts": counts,
                "tools": tools,
                "findings": result.get("findings") or [],
                "report_path": report_path,
                "preview_opened": preview_opened,
                "note": result.get("note"),
            },
            ensure_ascii=False,
            indent=2,
        )
        return header + body
