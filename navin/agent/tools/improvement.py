# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read measured learning state. Models cannot submit rewards or force promotion."""

import asyncio
import json
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.improvement.control import control


@tool_parameters(tool_parameters_schema(module=StringSchema("Module to inspect", enum=["all", "code", "career", "tenders"])))
class ImprovementTool(Tool):
    _scopes = {"core"}

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(Path(ctx.workspace))

    @property
    def name(self) -> str:
        return "improvement"

    @property
    def description(self) -> str:
        return ("Read the measured strategy-improvement state for Code, Career or Tenders: accepted policy, candidate, "
                "comparison results, generations and rollback history. No observations means improvement is unproven. "
                "Read-only; cannot change rewards, force promotion or retrain a model.")

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, module: str = "all", **kwargs: Any) -> ToolResult:
        request = current_request_context()
        workspace = Path(request.workspace) if request and request.workspace else self.workspace
        try:
            state = await asyncio.to_thread(control, workspace, module)
            return ToolResult(json.dumps(state, ensure_ascii=False))
        except (ValueError, OSError) as exc:
            return ToolResult.error(str(exc))
