"""Open a file or folder in the editor UI for the user.

The counterpart of :mod:`open_terminal` for the explorer: "montre-moi ce
fichier" is a request to *see* the code in the editor, not to have its content
pasted into the chat. This tool tells the editor UI to open the file as a tab
in the Dev workbench (optionally scrolled to a line) or to expand a folder in
the file tree, exactly as if the user had clicked it themselves.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.bus.outbound_events import (
    EditorOpenRequestedEvent,
    outbound_message_for_event,
)


class OpenInEditorTool(Tool):
    """Ask the editor UI to open a file tab or reveal a folder."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None, working_dir: str | None = None) -> None:
        self._bus = bus
        self._working_dir = working_dir

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus, working_dir=ctx.workspace)

    @property
    def name(self) -> str:
        return "open_in_editor"

    @property
    def description(self) -> str:
        return (
            "Open a file or folder in the Navin editor for the user, like "
            "Cursor does. A file opens as an editor tab (optionally scrolled "
            "to a line), a folder expands in the explorer tree. Use this when "
            "the user asks to see, show or open a file or folder - it puts "
            "the real editor in front of them instead of pasting content "
            "into the chat. To read a file yourself, keep using read_file."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or folder to open. Relative paths resolve "
                        "against the project root."
                    ),
                },
                "line": {
                    "type": "integer",
                    "description": (
                        "Optional 1-based line to scroll to (files only)."
                    ),
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> Any:
        raw = str(kwargs.get("path") or "").strip()
        if not raw:
            return ToolResult.error("Error: Missing path.")
        line = kwargs.get("line")
        line = int(line) if isinstance(line, (int, float)) and int(line) >= 1 else None

        ctx = current_request_context()
        if ctx is None or ctx.channel != "websocket":
            return ToolResult.error(
                "Error: the editor panel only exists in the Navin editor "
                "(WebUI/desktop). On this channel, use read_file or "
                "find_files instead."
            )
        if self._bus is None:
            return ToolResult.error(
                "Error: no message bus available to reach the editor UI."
            )

        # The per-turn workspace is the session's project folder; the
        # constructor argument is the gateway default workspace, captured once
        # at startup. Resolving against the latter would send a relative path
        # to the wrong project whenever the chat works elsewhere.
        root = Path(getattr(ctx, "workspace", None) or self._working_dir or os.getcwd())
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.exists():
            return ToolResult.error(f"Error: path not found: {raw}")
        kind = "folder" if target.is_dir() else "file"

        try:
            self._bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=ctx.chat_id,
                    event=EditorOpenRequestedEvent(
                        path=str(target),
                        kind=kind,
                        line=line if kind == "file" else None,
                    ),
                )
            )
        except Exception as exc:
            return ToolResult.error(f"Error: could not reach the editor UI: {exc}")

        at = f" at line {line}" if line and kind == "file" else ""
        opened = "expanded in the explorer" if kind == "folder" else "opened in the editor"
        return f"{target} {opened} for the user{at}."
