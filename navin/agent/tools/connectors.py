# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool: use the apps connected in Settings > Channels > Connectors.

``list`` and ``describe`` read the catalog (79 apps, their auth methods and
actions); ``request`` calls a connected app's API, the credential being
injected by :mod:`navin.connectors.runtime` and never shown to the model;
``connect`` opens the app's connection form (OAuth or API key) as a card in
the chat and waits for the user; ``test`` re-checks a connection.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.credentials import (
    CredentialRequest,
    already_asked,
    credentials_interactive,
    ensure_connector,
    request_credentials,
)
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import RequestContext
from navin.runtime_context import (
    RuntimeContextBlock,
    RuntimeContextProvider,
    wrap_runtime_context_lines,
)

ACTIONS = ("list", "describe", "request", "connect", "test")
MAX_OUTPUT = 14_000
_READ_VERBS = frozenset({"GET", "HEAD"})


def _connected_rows() -> list[dict[str, str]]:
    from navin.connectors.catalog import ConnectorError
    from navin.connectors.manager import connected_summary

    try:
        return connected_summary()
    except (ConnectorError, OSError) as exc:
        # A locked vault or an unreadable index only hides this hint; the tool still answers.
        logger.warning(
            "connected apps hint unavailable: {}", getattr(exc, "message", None) or type(exc).__name__
        )
        return []


class ConnectorsTool(Tool):
    """Call connected third-party apps without ever handling their secrets."""

    _scopes = {"core"}

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> ConnectorsTool:
        return cls(workspace=getattr(ctx, "workspace", None))

    @property
    def name(self) -> str:
        return "connectors"

    @property
    def description(self) -> str:
        return (
            "Use the user's apps through Navin connectors (79 apps: Slack, Gmail, Outlook, Teams, Notion, "
            "Google Drive/Sheets/Calendar, HubSpot, Salesforce, Stripe, Shopify, Hunter, Apollo, Mailchimp, "
            "Brevo, Tavily, Supabase, Vercel, Sentry, Figma, ElevenLabs...). list: catalog and what is "
            "connected. describe: an app's API base, reference and actions with parameters. request: call "
            "the app's REST API (path relative to the base, method, params, body); Navin injects the "
            "credential, you never see it and must not add auth headers. connect: when an app is needed "
            "but not connected, open its connection card (OAuth sign-in or API key form) in the chat and "
            "wait for the user. test: re-check a connection. Never ask for a key or token in the chat."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "service": {
                    "type": "string",
                    "description": "Connector id or name, e.g. slack, gmail, hubspot, stripe, googledrive.",
                },
                "query": {"type": "string", "description": "list/describe: words to filter apps or actions."},
                "category": {"type": "string", "description": "list: category id, e.g. crm, emailing, cloud."},
                "page": {"type": "integer", "minimum": 1, "description": "list/describe: result page."},
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                    "description": "request: HTTP method (default GET).",
                },
                "path": {
                    "type": "string",
                    "description": "request: API path relative to the app's base URL (see describe), or a full URL on the app's own hosts.",
                },
                "params": {"type": "object", "description": "request: query string parameters."},
                "body": {"description": "request: JSON body for POST, PUT, PATCH or DELETE."},
                "headers": {"type": "object", "description": "request: extra non-auth headers."},
                "body_format": {
                    "type": "string",
                    "enum": ["json", "form", "multipart", "raw"],
                    "description": "request: how the body is sent. Default json (form for Stripe and Mailgun); "
                    "multipart goes with files, raw with body_file.",
                },
                "files": {
                    "type": "object",
                    "description": "request: multipart upload, form field name -> workspace file path.",
                },
                "body_file": {
                    "type": "string",
                    "description": "request: workspace file sent as the raw body (Drive, Dropbox, OneDrive uploads...).",
                },
                "content_type": {
                    "type": "string",
                    "description": "request: Content-Type of a raw body (guessed from the file name).",
                },
                "save_to": {
                    "type": "string",
                    "description": "request: workspace file to write the response to (downloads, binary files).",
                },
                "reason": {
                    "type": "string",
                    "description": "connect/request: one line in the user's language, shown on the connection card.",
                },
            },
            "required": ["action"],
        }

    def call_read_only(self, arguments: Any) -> bool:
        args = arguments if isinstance(arguments, dict) else {}
        action = str(args.get("action") or "").strip().lower()
        if action in {"list", "describe", "test"}:
            return True
        if action == "request":
            uploads = args.get("files") or args.get("body_file")
            return (
                str(args.get("method") or "GET").strip().upper() in _READ_VERBS
                and not args.get("save_to")
                and not uploads
            )
        return False

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return self.call_read_only(arguments)

    def runtime_context_provider(self) -> RuntimeContextProvider:
        return self._provide_runtime_context

    async def _provide_runtime_context(self, request: RequestContext) -> RuntimeContextBlock | None:
        rows = await asyncio.to_thread(_connected_rows)
        if not rows:
            return None
        listed = "; ".join(
            f"{row['service']} ({row['name']}{' as ' + row['account'] if row['account'] else ''})" for row in rows[:30]
        )
        content = wrap_runtime_context_lines([
            "Connected apps the agent may use when the task needs them (connectors tool: describe service=<id>, "
            f"then request; load it with tool_search select:connectors if it is not listed): {listed}"
        ])
        return RuntimeContextBlock(source="connectors", content=content) if content else None

    async def execute(
        self,
        action: str = "",
        service: str = "",
        query: str = "",
        category: str = "",
        page: int = 1,
        method: str = "GET",
        path: str = "",
        params: dict[str, Any] | None = None,
        body: Any = None,
        headers: dict[str, Any] | None = None,
        save_to: str = "",
        reason: str = "",
        body_format: str = "",
        files: dict[str, Any] | None = None,
        body_file: str = "",
        content_type: str = "",
        **_: Any,
    ) -> ToolResult:
        from navin.connectors.catalog import ConnectorError

        action = str(action or "").strip().lower()
        if action not in ACTIONS:
            return self.unknown_action(action)
        if action != "list" and not str(service or "").strip():
            return ToolResult.error(f"Error: service is required for action={action} (see connectors action=list).")
        try:
            if action == "list":
                return ToolResult(await asyncio.to_thread(self._list, query, category, page))
            if action == "describe":
                return ToolResult(await asyncio.to_thread(self._describe, service, query, page))
            if action == "connect":
                return await self._connect(service, reason)
            if action == "test":
                return ToolResult(await asyncio.to_thread(self._test, service))
            return await self._request(
                service, method, path, params, body, headers, save_to, reason,
                body_format=body_format, files=files, body_file=body_file, content_type=content_type,
            )
        except ConnectorError as exc:
            return ToolResult.error(f"Error: {exc.message}")

    # -- actions --------------------------------------------------------------

    @staticmethod
    def _state(status: dict[str, Any]) -> str:
        if not status.get("connected"):
            return "not connected"
        account = f" as {status['account']}" if status.get("account") else ""
        off = "" if status.get("enabled", True) else " (turned off)"
        return f"connected{account}{off}"

    def _list(self, query: str, category: str, page: int) -> str:
        from navin.connectors import catalog
        from navin.connectors.store import ConnectorStore

        statuses = ConnectorStore().statuses()
        data = catalog.search(query=query, category=category, page=page, page_size=40, statuses=statuses)
        connected = [
            f"{service}{' as ' + status['account'] if status.get('account') else ''}"
            for service, status in statuses.items() if status.get("connected")
        ]
        lines = [f"Connected ({len(connected)}): {', '.join(connected) or 'none yet'}"]
        lines.append(
            f"Catalog: {data['total']} app(s) match, page {data['page']}/{data['pages']}. id | name | category | auth | status"
        )
        for item in data["items"]:
            lines.append(
                f"- {item['service']} | {item['name']} | {item['category']} | {'/'.join(item['auth_types'])} | "
                f"{self._state(item['status'])}"
            )
        if not query and not category:
            lines.append("Categories: " + ", ".join(f"{row['id']} ({row['count']})" for row in data["categories"]))
        lines.append("Next: describe service=<id> for its API, request to call it, connect to open its connection card.")
        return "\n".join(lines)

    def _describe(self, service: str, query: str, page: int) -> str:
        from navin.connectors import catalog, runtime
        from navin.connectors.providers import spec_for
        from navin.connectors.store import ConnectorStore

        store = ConnectorStore()
        row = catalog.provider(service)
        status = store.status(row["service"])
        spec = spec_for(row["service"])
        base = runtime.api_base(row["service"], store)
        hosts = ", ".join(spec.hosts)
        lines = [
            f"{row['name']} ({row['service']}), category {catalog.category_label(row['category'])}, "
            f"auth: {', '.join(item['type'] for item in row['methods'])}.",
            f"Status: {self._state(status)}"
            + ("" if status.get("connected") else f". Call connectors action=connect service={row['service']} to open the card."),
            f"API base: {base}"
            + (" ({...} parts come from the connection)" if "{" in base else "")
            + (f"; full URLs also allowed on {hosts}" if hosts else ""),
            "Navin injects the credential: never add Authorization, API key or token headers yourself.",
            f"Bodies: {spec.body_format} by default"
            + (" (pass nested JSON, Navin form-encodes it)" if spec.body_format == "form" else "")
            + "; uploads with files (multipart) or body_file (raw), downloads with save_to.",
        ]
        if spec.notes:
            lines.append(f"Notes: {spec.notes}")
        if spec.docs:
            lines.append(f"API reference: {spec.docs}")
        found = catalog.actions(row["service"], query=query, page=page, page_size=25)
        lines.append(
            f"Actions: {found['total']}{' matching ' + repr(query) if query else ''}, page {found['page']}/{found['pages']} "
            "(name [read|write|destructive] endpoint when known, description | params, * = required):"
        )
        for item in found["items"]:
            params = ", ".join(f"{name}{'*' if required else ''}:{kind}" for name, kind, required, _ in item["params"][:12])
            endpoint = f" {item['endpoint']}" if item.get("endpoint") else ""
            lines.append(f"- {item['name']} [{item['op']}]{endpoint} {item['description']}" + (f" | {params}" if params else ""))
        lines.append("Call the matching REST endpoint of the API reference with action=request.")
        return "\n".join(lines)

    def _test(self, service: str) -> str:
        from navin.connectors import manager
        from navin.connectors.store import ConnectorStore

        result = manager.test(ConnectorStore(), service)
        account = result["status"].get("account") or ""
        return f"{result['name']} works{' (account ' + account + ')' if account else ''}."

    async def _connect(self, service: str, reason: str) -> ToolResult:
        from navin.connectors import catalog, manager

        row = catalog.provider(service)
        connected, account = await asyncio.to_thread(manager.is_connected, row["service"])
        if connected:
            who = f" as {account}" if account else ""
            return ToolResult(f"{row['name']} is already connected{who}. Use connectors action=request.")
        if not credentials_interactive():
            return ToolResult.error(
                f"Error: nobody can fill a connection card in this run. Tell the user to connect {row['name']} "
                "in Settings > Channels > Connectors, then continue with what does not need it."
            )
        text = " ".join(str(reason or "").split())[:240] or f"Connect {row['name']} so I can continue."
        answer = await request_credentials(CredentialRequest("connector", row["service"], (), text))
        return ToolResult(answer.as_text())

    def _workspace_path(self, raw: str, *, existing: bool = False) -> Path:
        from navin.connectors.catalog import ConnectorError
        from navin.security.workspace_access import current_tool_workspace

        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        root = Path(access.project_path or self.workspace or ".").resolve()
        candidate = Path(str(raw)).expanduser()
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not target.is_relative_to(root):
            raise ConnectorError("File paths must be inside the workspace.")
        if existing and not target.is_file():
            raise ConnectorError(f"{raw} is not a file in the workspace.")
        return target

    def _request_paths(
        self, save_to: str, files: Any, body_file: str
    ) -> tuple[Path | None, dict[str, Path] | None, Path | None]:
        """Resolve the download target and the uploads (disk checks: run off the loop)."""
        from navin.connectors.catalog import ConnectorError

        target = self._workspace_path(save_to) if save_to else None
        uploads: dict[str, Path] | None = None
        if files:
            if not isinstance(files, dict):
                raise ConnectorError("files must map each form field to a workspace file path.")
            uploads = {str(name): self._workspace_path(str(path), existing=True) for name, path in files.items()}
        raw_file = self._workspace_path(body_file, existing=True) if body_file else None
        return target, uploads, raw_file

    async def _request(
        self,
        service: str,
        method: str,
        path: str,
        params: Any,
        body: Any,
        headers: Any,
        save_to: str,
        reason: str,
        *,
        body_format: str = "",
        files: Any = None,
        body_file: str = "",
        content_type: str = "",
    ) -> ToolResult:
        from navin.connectors import catalog, manager, runtime

        row = catalog.provider(service)
        connected, _ = await asyncio.to_thread(manager.is_connected, row["service"])
        if not connected:
            text = " ".join(str(reason or "").split())[:240] or f"Connect {row['name']} so I can continue."
            answer = await ensure_connector(row["service"], text)
            if answer is None:
                # Asked already in this turn and refused (declined, timed out): say that
                # again instead of inviting a second card.
                previous = already_asked("connector", row["service"])
                if previous is not None and not previous.ok:
                    answer = dataclasses.replace(previous, repeated=True)
            if answer is None or not answer.ok:
                note = answer.as_text() if answer is not None else (
                    f"{row['name']} is not connected. Call connectors action=connect service={row['service']} once, "
                    "or tell the user to connect it in Settings > Channels > Connectors."
                )
                return ToolResult.error(f"Error: {note}")
        target, uploads, raw_file = await asyncio.to_thread(self._request_paths, save_to, files, body_file)
        result = await asyncio.to_thread(
            runtime.request, row["service"], method or "GET", path, params=params, body=body, headers=headers,
            save_to=target, body_format=body_format, files=uploads, body_file=raw_file, content_type=content_type,
        )
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_OUTPUT:
            text = (
                text[:MAX_OUTPUT]
                + f" ... [{len(text) - MAX_OUTPUT} more chars cut: narrow the request (fields, limit, page) or use save_to]"
            )
        return ToolResult(text) if result.get("ok") else ToolResult.error(text)
