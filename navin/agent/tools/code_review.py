# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tools: code_review (scope + filter + HTML report)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.review.gates import filter_findings_precision
from navin.review.report_html import write_review_report
from navin.review.rules import load_review_rules
from navin.review.schema import dedupe_review_findings, normalize_review_finding
from navin.review.scope import collect_review_scope


class CodeReviewTool(_QualityTool):
    """Scope a change-set, filter findings, and/or write a review HTML report."""

    @property
    def name(self) -> str:
        return "code_review"

    @property
    def description(self) -> str:
        return (
            "Review mode helper. action=scope: gated change-set (OCR 5-gates + "
            "optional .navin/review-rules.json) + diff + path rules. "
            "action=filter: precision filter on findings_json (confidence, "
            "path/line, theoretical noise). action=report: write "
            "review-report-*.html (auto-filters first) and auto-open File Preview "
            "in the WebUI. For PR comments use pr_comments (optional; needs an "
            "open PR + gh). Precision over recall."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["scope", "filter", "report"],
                    "description": (
                        "scope: change-set; filter: drop FP findings; "
                        "report: write HTML from findings_json"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Optional project-relative path to scope.",
                },
                "findings_json": {
                    "type": "string",
                    "description": "JSON array of review findings (filter/report).",
                },
                "verdict": {
                    "type": "string",
                    "enum": ["approve", "request_changes", "comment"],
                    "description": "Overall review verdict for the HTML report.",
                },
                "effort": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Estimated review effort 1-5 (pr-agent style).",
                },
                "summary": {
                    "type": "string",
                    "description": "Optional executive summary (newline-separated bullets).",
                },
                "min_confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Confidence floor for filter/report (default 0.75).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str = "scope",
        path: str | None = None,
        findings_json: str | None = None,
        verdict: str = "request_changes",
        effort: int | None = None,
        summary: str | None = None,
        min_confidence: float = 0.75,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        act = (action or "scope").strip().lower()
        if act == "scope":
            sub = None
            raw = (path or "").strip()
            if raw:
                rel, path_error = self._project_relative(raw, root)
                if path_error:
                    return path_error
                sub = rel or None
            scope = await asyncio.to_thread(collect_review_scope, root, path=sub)
            return json.dumps(scope, ensure_ascii=False, indent=2)

        if act in {"filter", "report"}:
            # Models frequently pass the findings array natively instead of
            # as a JSON-encoded string; both shapes are unambiguous.
            if isinstance(findings_json, list):
                raw_findings: Any = findings_json
            elif isinstance(findings_json, dict):
                raw_findings = [findings_json]
            else:
                try:
                    raw_findings = json.loads(findings_json or "[]")
                except json.JSONDecodeError as exc:
                    return self.error(f"findings_json is not valid JSON: {exc}")
            if not isinstance(raw_findings, list):
                return self.error("findings_json must be a JSON array")
            normalized = [
                normalize_review_finding(f) for f in raw_findings if isinstance(f, dict)
            ]
            kept, dropped = filter_findings_precision(
                dedupe_review_findings(normalized),
                min_confidence=float(min_confidence or 0.75),
                require_evidence=True,
            )
            if act == "filter":
                rules = await asyncio.to_thread(load_review_rules, root)
                return json.dumps(
                    {
                        "ok": True,
                        "kept": kept,
                        "dropped": dropped,
                        "kept_count": len(kept),
                        "dropped_count": len(dropped),
                        "rules_source": rules.get("source"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

            bullets = [b.strip() for b in (summary or "").splitlines() if b.strip()]
            scope = await asyncio.to_thread(
                collect_review_scope, root, path=(path or "").strip() or None
            )
            report = await asyncio.to_thread(
                write_review_report,
                root,
                findings=kept,
                files=list(scope.get("files") or []),
                verdict=verdict or "request_changes",
                effort=effort,
                summary_bullets=bullets or None,
            )
            opened = self._open_file_preview(report)
            return json.dumps(
                {
                    "ok": True,
                    "report_path": str(report),
                    "finding_count": len(kept),
                    "dropped_count": len(dropped),
                    "files": scope.get("files") or [],
                    "preview_opened": opened,
                },
                ensure_ascii=False,
                indent=2,
            )

        return self.error("action must be scope, filter, or report")
