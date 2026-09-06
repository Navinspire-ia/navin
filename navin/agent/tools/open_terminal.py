"""Open the editor's integrated terminal panel for the user.

"Ouvre un shell" is a request to *see* a terminal, not to run a command. The
exec tool cannot honour it: its sessions are pipes without a TTY, so an
interactive shell started there never prints a prompt. This tool does what
Cursor does instead - it tells the editor UI to open its terminal panel, and
the UI creates a real PTY session through its normal ``terminal_open`` flow.
"""

from __future__ import annotations

from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.bus.outbound_events import (
    TerminalOpenRequestedEvent,
    outbound_message_for_event,
)


class OpenTerminalTool(Tool):
    """Ask the editor UI to open its integrated terminal panel."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None) -> None:
        self._bus = bus

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus)

    @property
    def name(self) -> str:
        return "open_terminal"

    @property
    def description(self) -> str:
        return (
            "Open the integrated terminal panel in the Navin editor for the "
            "user, like Cursor does. Use this when the user asks to open a "
            "shell/terminal/console for themselves. The panel opens with a "
            "real interactive PTY (bash, zsh, PowerShell, cmd, WSL - whatever "
            "the host offers). Do NOT try to start an interactive shell "
            "('bash -i', 'powershell') through exec sessions: they are pipes "
            "without a TTY and will never show a prompt. This tool is for "
            "giving the user a terminal; to run commands yourself, keep using "
            "exec."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "shell": {
                    "type": "string",
                    "description": (
                        "Optional shell name as the host lists it (e.g. "
                        "'bash', 'zsh', 'PowerShell', 'cmd', 'WSL: Ubuntu'). "
                        "Omit to use the project's default shell."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Optional directory to start in. Omit to use the "
                        "project root."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> Any:
        shell = str(kwargs.get("shell") or "").strip() or None
        cwd = str(kwargs.get("cwd") or "").strip() or None

        ctx = current_request_context()
        # A relative cwd means "inside the project". Resolve it here, against
        # the per-turn workspace, so the terminal_open flow receives a path it
        # can honour instead of guessing against the gateway's own cwd.
        workspace = getattr(ctx, "workspace", None)
        if cwd is not None and workspace is not None:
            from pathlib import Path

            expanded = Path(cwd).expanduser()
            if not expanded.is_absolute():
                cwd = str(Path(workspace) / expanded)
        if ctx is None or ctx.channel != "websocket":
            return ToolResult.error(
                "Error: the integrated terminal panel only exists in the "
                "Navin editor (WebUI/desktop). On this channel, run commands "
                "with the exec tool instead."
            )
        if self._bus is None:
            return ToolResult.error(
                "Error: no message bus available to reach the editor UI."
            )
        try:
            self._bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=ctx.chat_id,
                    event=TerminalOpenRequestedEvent(shell=shell, cwd=cwd),
                )
            )
        except Exception as exc:
            return ToolResult.error(f"Error: could not open the terminal panel: {exc}")
        target = f" ({shell})" if shell else ""
        where = f" in {cwd}" if cwd else ""
        return (
            f"Integrated terminal panel opened for the user{target}{where}. "
            "The shell is interactive and owned by the user; you do not see "
            "its output. To run commands yourself, use exec."
        )
