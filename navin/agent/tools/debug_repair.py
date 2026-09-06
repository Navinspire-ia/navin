"""Agent tool: debug_repair (isolated branch + DebugMCP probe + report)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from navin.agent.tools.quality import _QualityTool
from navin.debug.mcp_client import probe_debugmcp
from navin.debug.repair import current_branch, start_repair_branch, write_debug_report


class DebugRepairTool(_QualityTool):
    """Orchestrate the debug repair workflow helpers."""

    @property
    def name(self) -> str:
        return "debug_repair"

    @property
    def description(self) -> str:
        return (
            "Debug mode helper. action=mcp_status: probe DebugMCP at "
            "http://127.0.0.1:3001/mcp (real breakpoints via MCP preset "
            "debugmcp; if down, reports clearly and falls back to logs/pdb). "
            "action=start_branch: create isolated navin/debug-* git branch. "
            "action=status: current branch. action=report: write "
            "debug-report-*.html from JSON payload (signal, repro_steps, "
            "root_cause, hypotheses, before, after, stack, variables, ask_log) "
            "and auto-open File Preview in the WebUI."
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
                    "enum": ["mcp_status", "start_branch", "status", "report"],
                },
                "slug": {
                    "type": "string",
                    "description": "Branch slug suffix (action=start_branch).",
                },
                "payload_json": {
                    "type": "string",
                    "description": "JSON object for action=report.",
                },
                "mcp_url": {
                    "type": "string",
                    "description": "Override DebugMCP URL (default http://127.0.0.1:3001/mcp).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str = "status",
        slug: str = "debug-repair",
        payload_json: str | None = None,
        mcp_url: str | None = None,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        act = (action or "status").strip().lower()
        if act == "mcp_status":
            result = await asyncio.to_thread(probe_debugmcp, mcp_url)
            return json.dumps(result, ensure_ascii=False, indent=2)

        if act == "status":
            branch = await asyncio.to_thread(current_branch, root)
            return json.dumps({"ok": True, "branch": branch}, ensure_ascii=False, indent=2)

        if act == "start_branch":
            result = await asyncio.to_thread(start_repair_branch, root, slug=slug or "debug-repair")
            return json.dumps(result, ensure_ascii=False, indent=2)

        if act == "report":
            try:
                payload = json.loads(payload_json or "{}")
            except json.JSONDecodeError as exc:
                return self.error(f"payload_json invalid: {exc}")
            if not isinstance(payload, dict):
                return self.error("payload_json must be a JSON object")
            if not payload.get("branch"):
                payload["branch"] = await asyncio.to_thread(current_branch, root)
            path = await asyncio.to_thread(write_debug_report, root, payload)
            opened = self._open_file_preview(path)
            return json.dumps(
                {
                    "ok": True,
                    "report_path": str(path),
                    "preview_opened": opened,
                },
                ensure_ascii=False,
                indent=2,
            )

        return self.error("action must be mcp_status, start_branch, status, or report")
