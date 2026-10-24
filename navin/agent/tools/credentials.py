# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ask the user for a site login or a git account through a secure card."""

from __future__ import annotations

from typing import Any

from navin.agent.credentials import (
    KINDS,
    SITE_NAMES,
    CredentialRequest,
    clean_names,
    normalize_host,
    request_credentials,
)
from navin.agent.tools.base import Tool, ToolResult


class RequestCredentialsTool(Tool):
    """Show a credential card; the values go to the vault, never to the model."""

    _scopes = {"core"}

    @property
    def read_only(self) -> bool:
        return True

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "request_credentials"

    @property
    def description(self) -> str:
        return (
            "Ask the user for a missing login through a secure card, only when a "
            "task really needs it and nothing is saved yet. kind=site_login: a "
            "website account (host, names among username/password/totp); the "
            "values are stored in the vault and you get refs like "
            "site:<host>/password to use with browser action=login. "
            "kind=git_host: connect a git forge account (GitHub sign-in or "
            "token) so git push/pull/clone and forge work. You never see a "
            "value. Call it once per host with a clear one-line reason; never "
            "ask the user to paste a password or token in the chat."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "host": {
                    "type": "string",
                    "description": "Site host or URL (site_login), or forge host such as github.com (git_host).",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(SITE_NAMES)},
                    "description": "site_login: which values are needed (default username and password).",
                },
                "reason": {
                    "type": "string",
                    "description": "Why, in one line in the user's language (shown on the card).",
                },
            },
            "required": ["kind", "host", "reason"],
        }

    async def execute(
        self,
        kind: str = "",
        host: str = "",
        names: list[str] | None = None,
        reason: str = "",
        **_: Any,
    ) -> ToolResult:
        kind = str(kind or "").strip()
        if kind not in KINDS:
            return ToolResult.error(f"Error: kind must be one of {', '.join(KINDS)}.")
        clean_host = normalize_host(kind, host)
        if not clean_host:
            return ToolResult.error("Error: host must be a host name or URL, e.g. app.example.com.")
        text = " ".join(str(reason or "").split())
        if not text:
            return ToolResult.error("Error: give a one-line reason the user will read on the card.")
        request = CredentialRequest(
            kind=kind,
            host=clean_host,
            names=clean_names(names) if kind == "site_login" else (),
            reason=text[:240],
        )
        answer = await request_credentials(request)
        return ToolResult(answer.as_text())
