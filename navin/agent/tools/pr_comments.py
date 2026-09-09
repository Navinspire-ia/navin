# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: post review/security findings as inline PR/MR comments."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.github.pr_comments import post_pr_review, preview_pr_comments
from navin.review.schema import normalize_review_finding
from navin.security.findings import normalize_finding


class PrCommentsTool(_QualityTool):
    """Preview or post inline PR review comments from structured findings."""

    @property
    def name(self) -> str:
        return "pr_comments"

    @property
    def description(self) -> str:
        return (
            "Inline PR/MR comments on GitHub, GitLab or Forgejo. "
            "action=preview: resolve the request + list path:line payloads (no "
            "network write). action=post: create a real review with inline "
            "comments (asks approval). Findings need file_path + "
            "start_line/line on the diff. Needs a forge token (Settings > Git) "
            "or a signed-in gh on github.com."
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
                    "enum": ["preview", "post"],
                    "description": "preview: plan only; post: create the review on the forge.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["review", "security"],
                    "description": "Comment framing (review vs security).",
                },
                "findings_json": {
                    "type": "string",
                    "description": "JSON array of findings with file_path + line.",
                },
                "pr": {
                    "type": "integer",
                    "description": "PR number (optional; defaults to current branch PR).",
                },
                "event": {
                    "type": "string",
                    "enum": ["COMMENT", "REQUEST_CHANGES", "APPROVE"],
                    "description": (
                        "Review event (default COMMENT); mapped per forge "
                        "(APPROVED on Forgejo, an approval call on GitLab)."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "Optional top-level review body markdown.",
                },
            },
            "required": ["action", "findings_json"],
        }

    async def execute(
        self,
        action: str = "preview",
        kind: str = "review",
        findings_json: str | None = None,
        pr: int | None = None,
        event: str = "COMMENT",
        summary: str | None = None,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        try:
            raw = json.loads(findings_json or "[]")
        except json.JSONDecodeError as exc:
            return self.error(f"findings_json is not valid JSON: {exc}")
        if not isinstance(raw, list):
            return self.error("findings_json must be a JSON array")

        kind_n = (kind or "review").strip().lower()
        if kind_n not in {"review", "security"}:
            kind_n = "review"
        if kind_n == "security":
            findings = [normalize_finding(f) for f in raw if isinstance(f, dict)]
        else:
            findings = [normalize_review_finding(f) for f in raw if isinstance(f, dict)]

        act = (action or "preview").strip().lower()
        if act == "preview":
            result = await asyncio.to_thread(
                preview_pr_comments,
                root,
                findings,
                kind=kind_n,
                pr=int(pr) if pr else None,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        if act != "post":
            return self.error("action must be preview or post")

        from navin.agent.approval import ApprovalRequest, request_approval

        decision = await request_approval(
            ApprovalRequest(
                tool="pr_comments",
                action="post",
                reason="Post structured findings as a pull request review",
                detail=f"kind={kind_n} findings={len(findings)} pr={pr or 'current'}",
                consequence=(
                    "Creates a visible PR/MR review with inline comments on the forge"
                ),
                scope=str(root),
                allow_when_unattended=False,
            )
        )
        if not decision.allowed:
            return self.error(f"refused: {decision.reason}")

        result = await asyncio.to_thread(
            post_pr_review,
            root,
            findings,
            kind=kind_n,
            pr=int(pr) if pr else None,
            event=event or "COMMENT",
            summary=summary,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
