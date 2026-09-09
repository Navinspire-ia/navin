# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fixture tools for the browser and desk suites of the policy battery.

The eval runner executes the real agent loop with real filesystem tools in
a throwaway folder. Two suites need a web and a desk, and an eval must be
deterministic and offline, so these two tools serve the *fixture* instead:

* ``browser``: ``fetch`` reads ``_web/<host>/<path>`` from the sandbox and
  answers its text; a missing page is an HTTP 404 error, exactly the class
  the world model would see on a dead link;
* ``desk``: ``list`` / ``get`` read ``_desk/<kind>.json``; ``add`` appends a
  record; ``send`` drops a mail in ``_desk/outbox.json``. Reads are
  read-only, writes are not, so the policy learns "look before you send".

They are registered only by ``navin.policy.episodes`` for a sandbox run.
The gateway never sees them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema

WEB_DIR = "_web"
DESK_DIR = "_desk"
DESK_KINDS = ("contacts", "calendar", "invoices", "mail")
_MAX_PAGE_CHARS = 20_000


def _safe_relative(root: Path, *parts: str) -> Path | None:
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return target


_BROWSER_PARAMETERS = tool_parameters_schema(
    url=StringSchema("Page URL, e.g. https://example.test/pricing", min_length=8, max_length=2000),
    action=StringSchema("fetch (default) reads the page", enum=("fetch",), nullable=True),
    required=["url"],
)


@tool_parameters(_BROWSER_PARAMETERS)
class FixtureBrowserTool(Tool):
    """Read a page of the fixture web (``_web/<host>/<path>``)."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, *, workspace: Path | str) -> None:
        self._root = Path(workspace)

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return "Fetch a web page and return its text. Answers HTTP 404 when the page does not exist."

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, url: str, action: str | None = None, **kwargs: Any) -> Any:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
        if not host:
            return ToolResult.error(f"Error: invalid URL {url!r}")
        path = parts.path.strip("/") or "index"
        candidates = [path, f"{path}.html", f"{path}/index.html"]
        for candidate in candidates:
            target = _safe_relative(self._root / WEB_DIR / host, *candidate.split("/"))
            if target is not None and target.is_file():
                text = target.read_text(encoding="utf-8", errors="replace")
                return f"HTTP 200 OK {url}\n\n{text[:_MAX_PAGE_CHARS]}"
        return ToolResult.error(f"HTTP 404 Not Found: {url}")


_DESK_PARAMETERS = tool_parameters_schema(
    action=StringSchema("list | get | add | send", enum=("list", "get", "add", "send")),
    kind=StringSchema("contacts | calendar | invoices | mail", enum=DESK_KINDS),
    id=StringSchema("Record id for get", max_length=64, nullable=True),
    data=StringSchema("JSON object for add / send", max_length=4000, nullable=True),
    required=["action", "kind"],
)


@tool_parameters(_DESK_PARAMETERS)
class FixtureDeskTool(Tool):
    """The fixture desk: contacts, calendar, invoices and an outbox."""

    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, *, workspace: Path | str) -> None:
        self._root = Path(workspace)

    @property
    def name(self) -> str:
        return "desk"

    @property
    def description(self) -> str:
        return (
            "Desk records: list or get contacts, calendar entries and invoices; add a record; "
            "send a mail (kind=mail, data={to, subject, body})."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = arguments.get("action") if isinstance(arguments, dict) else None
        return action in ("list", "get")

    def _file(self, kind: str) -> Path:
        name = "outbox.json" if kind == "mail" else f"{kind}.json"
        return self._root / DESK_DIR / name

    def _load(self, kind: str) -> list[dict[str, Any]]:
        path = self._file(kind)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _save(self, kind: str, rows: list[dict[str, Any]]) -> None:
        path = self._file(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def execute(
        self,
        action: str,
        kind: str,
        id: str | None = None,  # noqa: A002 - the tool parameter is named id
        data: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if kind not in DESK_KINDS:
            return self.unknown_action(kind, parameter="kind")
        if action == "list":
            rows = self._load(kind)
            if not rows:
                return f"No {kind} records."
            return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        if action == "get":
            for row in self._load(kind):
                if str(row.get("id")) == (id or ""):
                    return json.dumps(row, ensure_ascii=False)
            return ToolResult.error(f"Error: {kind} record {id!r} not found")
        try:
            payload = json.loads(data or "")
        except json.JSONDecodeError:
            return ToolResult.error("Error: data must be a JSON object")
        if not isinstance(payload, dict):
            return ToolResult.error("Error: data must be a JSON object")
        if action == "send":
            if kind != "mail":
                return ToolResult.error("Error: send needs kind=mail")
            if not str(payload.get("to") or "").strip():
                return ToolResult.error("Error: mail needs a recipient (to)")
            rows = self._load("mail")
            rows.append(payload)
            self._save("mail", rows)
            return f"Sent mail to {payload['to']} ({len(rows)} in outbox)."
        if action == "add":
            rows = self._load(kind)
            payload.setdefault("id", f"{kind[0]}{len(rows) + 1}")
            rows.append(payload)
            self._save(kind, rows)
            return f"Added {kind} record {payload['id']}."
        return self.unknown_action(action)
