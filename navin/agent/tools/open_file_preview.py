"""Open a workspace file in the WebUI File Preview panel for the user.

Counterpart of :mod:`open_in_editor` for studio / chat views that do not have
the Dev workbench editor. Used after RiskLens (and similar) writes an HTML
report so the user sees it immediately with Download HTML / Export PDF.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.bus.outbound_events import (
    FilePreviewOpenRequestedEvent,
    outbound_message_for_event,
)


def _within(target: Path, root: Path) -> bool:
    try:
        return target == root or root in target.parents
    except OSError:
        return False


def _workspace_restricted() -> bool:
    """Whether the preview panel will enforce the project boundary.

    Read rather than assumed, so turning the restriction off keeps working the
    way it does everywhere else instead of being contradicted here.
    """
    try:
        from navin.config.loader import load_config

        return bool(load_config().tools.restrict_to_workspace)
    except Exception:
        return True


class OpenFilePreviewTool(Tool):
    """Ask the WebUI to open a file in the File Preview side panel."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None, working_dir: str | None = None) -> None:
        self._bus = bus
        self._working_dir = working_dir

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus, working_dir=ctx.workspace)

    @property
    def name(self) -> str:
        return "open_file_preview"

    @property
    def description(self) -> str:
        return (
            "Open a workspace file in the Navin File Preview panel for the user "
            "(HTML reports, markdown, images, etc.). Use this right after saving "
            "a deliverable the user should see immediately - for example a "
            "risklens-report-*.html. Prefer this over pasting the whole file into "
            "chat. To read a file yourself, keep using read_file."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File to preview. Relative paths resolve against the "
                        "project root."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        raw = str(kwargs.get("path") or "").strip()
        if not raw:
            return ToolResult.error("Error: Missing path.")

        ctx = current_request_context()
        if ctx is None or ctx.channel != "websocket":
            return ToolResult.error(
                "Error: the File Preview panel only exists in the Navin WebUI "
                "/ desktop. On this channel, point the user to the file path "
                "instead."
            )
        if self._bus is None:
            return ToolResult.error(
                "Error: no message bus available to reach the preview UI."
            )

        # Prefer the per-turn workspace (the session's project folder) over the
        # gateway default workspace captured at startup: a relative path from a
        # chat working on another project must resolve inside that project.
        root = Path(
            getattr(ctx, "workspace", None) or self._working_dir or os.getcwd()
        ).resolve()
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.error(
                f"Error: file not found: {raw} (resolved to {target}). "
                f"Relative paths resolve against the project root, {root}."
            )
        # Under the workspace restriction the preview panel refuses anything
        # above the root, so sending it would be reported as opened and then
        # show nothing at all. Better to say so here, while there is still an
        # agent able to put the file where it can be seen.
        if _workspace_restricted() and not _within(target, root):
            return ToolResult.error(
                f"Error: {target} is outside the project ({root}), and the preview "
                "panel only shows files inside it. Write or copy the file into the "
                "project first, then preview that copy."
            )

        try:
            self._bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=ctx.chat_id,
                    event=FilePreviewOpenRequestedEvent(path=str(target)),
                )
            )
        except Exception as exc:
            return ToolResult.error(f"Error: could not reach the preview UI: {exc}")

        return f"{target} opened in File Preview for the user."
