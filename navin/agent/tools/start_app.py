"""Start any local workspace application (web, API, docker, …).

Companion to ``open_preview``: this tool focuses on bringing the process up
and reporting the URL/port. The agent runs apps - never the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.agent.tools.preview_server import (
    discover_project_dev_servers,
    ensure_project_preview_url,
    pick_server_for_url,
)


class StartAppTool(Tool):
    """Start the workspace app in the background and return its URL/port."""

    _scopes = {"core", "subagent"}

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        workspace = None
        raw = getattr(ctx, "workspace", None)
        if raw:
            workspace = Path(str(raw))
        return cls(workspace=workspace)

    @property
    def name(self) -> str:
        return "start_app"

    @property
    def description(self) -> str:
        return (
            "Start the project's application in the workspace (Vite/Next/npm, "
            "Python/Django/FastAPI, Docker Compose, Rails, Laravel, Go, Cargo, "
            "Deno, Makefile, start.sh, …). YOU start apps - never ask the user "
            "to run npm/docker/uvicorn. Returns the local URL and port. For a "
            "UI the user should see, call open_preview afterwards (or just "
            "open_preview alone - it also starts the app)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "Optional preferred local URL (e.g. http://127.0.0.1:3000). "
                        "Omit to discover or start the workspace app and use its real port. "
                        "If already up, returns it; otherwise starts the matching app."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> Any:
        raw_url = str(kwargs.get("url") or "").strip()
        preferred = raw_url or None
        if preferred and not preferred.startswith(("http://", "https://")):
            preferred = f"http://{preferred}"

        ctx = current_request_context()
        workspace = self._workspace
        if workspace is None and ctx is not None and ctx.workspace is not None:
            workspace = ctx.workspace
        owner_key = ctx.session_key if ctx else None

        url, err = await ensure_project_preview_url(
            workspace=workspace,
            preferred_url=preferred,
            owner_session_key=owner_key,
            start_if_needed=True,
        )
        if not url:
            servers = discover_project_dev_servers(workspace)
            hints = []
            for server in servers[:5]:
                hints.append(f"- {server.label}: `{server.command}` in {server.cwd}")
            extra = ("\nDetected entrypoints:\n" + "\n".join(hints)) if hints else ""
            return ToolResult.error(
                "Error: could not start an application. "
                + (err or "no runnable entrypoint found.")
                + extra
            )

        port = None
        try:
            port = urlparse(url).port
        except Exception:
            port = None

        picked = pick_server_for_url(discover_project_dev_servers(workspace), url)
        detail = ""
        if picked:
            detail = f" command=`{picked.command}` cwd={picked.cwd}"

        return (
            f"App is running at {url}"
            + (f" (port {port})" if port else "")
            + f".{detail} "
            "Call open_preview with this URL so the user can test in Preview. "
            "Do not ask the user to start anything."
        )
