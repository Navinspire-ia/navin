# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool exposing best-of-N candidate generation with a judge."""

from __future__ import annotations

from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import ToolContext, current_request_context


class BestOfTool(Tool):
    """Fan out N independent proposals and let an independent judge pick."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "best_of"

    @property
    def description(self) -> str:
        return (
            "Generate 2-4 independent candidate answers for one hard decision "
            "and have an independent judge score them against explicit "
            "criteria, then return the winner with the scores. Use it where a "
            "single trajectory is the risk: architecture choices, risky or "
            "irreversible plans, security verdicts, review calls you are "
            "unsure about, or intermittent failures needing a second opinion. "
            "It costs n+1 model calls, so keep it for decisions that matter - "
            "not routine edits. Text-only: candidates cannot run tools, so "
            "put every needed fact (code excerpts, constraints, findings) in "
            "task/context."
        )

    @property
    def read_only(self) -> bool:
        return True  # n+1 model calls; nothing on disk is touched

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "The decision or plan to generate candidates for. Be "
                        "complete: candidates only see this text plus context."
                    ),
                },
                "n": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 4,
                    "description": "How many candidates to generate (default 3)",
                },
                "criteria": {
                    "type": "string",
                    "description": (
                        "Optional judging criteria; defaults to correctness, "
                        "completeness, risk awareness and simplicity"
                    ),
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional supporting material: code excerpts, error "
                        "logs, constraints, prior findings"
                    ),
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str | None = None,
        n: int = 3,
        criteria: str | None = None,
        context: str | None = None,
        **kwargs: Any,
    ) -> str:
        from navin.agent.best_of import BestOfError, best_of_n

        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: best_of requires an active model runtime")
        if not task or not task.strip():
            return ToolResult.error("Error: best_of needs a task to decide on")
        try:
            result = await best_of_n(
                request_ctx.runtime,
                task,
                n=n,
                criteria=criteria,
                context=context,
            )
        except BestOfError as exc:
            return ToolResult.error(f"Error: {exc}")
        return result.render()
