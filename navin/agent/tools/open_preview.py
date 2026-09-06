"""Open the Dev workbench Preview (web iframe) or Mobile panel for the user.

After scaffolding, "show me the app" means putting the Preview panel in front
of the user - not pasting a localhost URL into chat.

This tool starts the project's own server when needed (npm/vite/next/start.sh),
discovers the real port, and opens Preview. NEVER use Navin's editor ports
(gateway :8765 or WebUI Vite :5173).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.agent.tools.preview_server import ensure_project_preview_url
from navin.bus.outbound_events import (
    PreviewOpenRequestedEvent,
    outbound_message_for_event,
)
from navin.ports import ListenerInfo, is_navin_listener, resolve_from_config, who_listens


def emit_preview_open_request(
    *,
    bus: Any,
    kind: str = "web",
    url: str | None = None,
) -> str | None:
    """Publish a PreviewOpenRequestedEvent. Returns an error string or None."""
    ctx = current_request_context()
    if ctx is None or ctx.channel != "websocket":
        return (
            "the Preview panel only exists in the Navin editor "
            "(WebUI/desktop). On this channel, report the URL in your reply."
        )
    if bus is None:
        return "no message bus available to reach the editor UI."
    normalized_kind = "mobile" if kind == "mobile" else "web"
    try:
        bus.outbound.put_nowait(
            outbound_message_for_event(
                channel="websocket",
                chat_id=ctx.chat_id,
                event=PreviewOpenRequestedEvent(
                    kind=normalized_kind,
                    url=url,
                ),
            )
        )
    except Exception as exc:
        return f"could not open the Preview panel: {exc}"
    return None


def _navin_service_ports() -> set[int]:
    """Ports owned by Navin services (gateway WebUI, health, API)."""
    ports: set[int] = set()
    try:
        from navin.config.loader import load_config

        config = load_config()
    except Exception:
        config = None
    for role in resolve_from_config(config):
        if role.spec.owner == "navin":
            ports.add(int(role.port))
    return ports


def _looks_like_navin_webui_process(listener: ListenerInfo | None) -> bool:
    if listener is None:
        return False
    if is_navin_listener(listener):
        return True
    text = (listener.cmdline or "").lower()
    if not text:
        return False
    # Vite / npm serving the repo's webui package.
    if "vite" in text and ("webui" in text or "navin-ai" in text or "/navin/" in text):
        return True
    if "navin/web/dist" in text or "navin\\web\\dist" in text:
        return True
    return False


def _normalize_web_url(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return value


def _reject_navin_editor_url(url: str) -> str | None:
    """Refuse URLs that point at Navin itself (gateway / Vite WebUI)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid preview URL."
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    if port in _navin_service_ports():
        return (
            f"port {port} belongs to Navin itself (editor/gateway), not the "
            "project you built. Start the project's own server "
            "(e.g. open_preview will start npm/vite and detect the real port)."
        )

    listener = who_listens(port)
    if _looks_like_navin_webui_process(listener):
        return (
            f"{url} is the Navin editor UI, not your project site. "
            "Call open_preview / start_app on the workspace app instead "
            "(never Navin's :8765 or editor Vite)."
        )
    return None


def _resolve_workspace(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    ctx = current_request_context()
    if ctx is not None and ctx.workspace is not None:
        return ctx.workspace
    return None


class OpenPreviewTool(Tool):
    """Ask the editor UI to open Preview (web) or Mobile for the user."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None, workspace: Path | None = None) -> None:
        self._bus = bus
        self._workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        workspace = None
        raw = getattr(ctx, "workspace", None)
        if raw:
            workspace = Path(str(raw))
        return cls(bus=ctx.bus, workspace=workspace)

    @property
    def name(self) -> str:
        return "open_preview"

    @property
    def description(self) -> str:
        return (
            "Open the Navin Code Preview tab on the user's PROJECT app (web iframe "
            "or Mobile). YOU start the app - never ask the user to run npm/vite. "
            "Call this after building/changing a UI, or when the user wants to see "
            "the site. Optional url=http://127.0.0.1:<port>; if omitted or the port "
            "is down, this tool finds package.json/vite/next/start.sh in the "
            "workspace, starts the server, waits until it answers, then opens "
            "Preview. NEVER use Navin ports (:8765 / editor :5173). "
            "kind=mobile for Android device/emulator. Do not only paste the URL "
            "in chat."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["web", "mobile"],
                    "description": (
                        "'web' opens the Preview iframe (starts the project "
                        "server if needed); 'mobile' opens the Mobile Android "
                        "preview tab."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": (
                        "Optional local http(s) URL of the PROJECT server "
                        "(127.0.0.1 / localhost). If omitted or unreachable, "
                        "the tool starts the workspace app and discovers the port."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> Any:
        kind_raw = str(kwargs.get("kind") or "web").strip().lower()
        kind = "mobile" if kind_raw == "mobile" else "web"
        url: str | None = None

        if kind == "web":
            raw_url = str(kwargs.get("url") or "").strip()
            preferred = _normalize_web_url(raw_url) if raw_url else None
            if preferred:
                blocked = _reject_navin_editor_url(preferred)
                if blocked:
                    # Still try to start the real project instead of hard-failing
                    # when the model passed Navin's port by mistake.
                    preferred = None

            ctx = current_request_context()
            owner_key = ctx.session_key if ctx else None
            workspace = _resolve_workspace(self._workspace)

            url, ensure_err = await ensure_project_preview_url(
                workspace=workspace,
                preferred_url=preferred,
                owner_session_key=owner_key,
                start_if_needed=True,
            )
            if not url:
                return ToolResult.error(
                    "Error: "
                    + (
                        ensure_err
                        or "could not start or find a project Preview URL."
                    )
                )

            blocked = _reject_navin_editor_url(url)
            if blocked:
                return ToolResult.error(f"Error: {blocked}")

        err = emit_preview_open_request(bus=self._bus, kind=kind, url=url)
        if err:
            return ToolResult.error(f"Error: {err}")

        if kind == "mobile":
            return (
                "Mobile Preview tab opened for the user. "
                "It starts when a device/emulator is ready."
            )
        return (
            f"Preview opened for the user at {url}. "
            "The project server was started or reused automatically - "
            "do not ask the user to run npm themselves."
        )
