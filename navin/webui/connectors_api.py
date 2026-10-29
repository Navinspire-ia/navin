# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP actions behind Settings > Channels > Connectors.

``GET|POST /api/connectors?action=...`` (token required). Bodies ride the
``X-Navin-File-Body-*`` headers like every other Settings route. Values go
in; only statuses, account names and non-secret fields come back.

``GET /api/connectors/oauth/callback`` is the provider's return address. It
needs no API token: the one-use ``state`` created by ``oauth_start`` is the
proof, and it answers a small HTML page the user can close.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from loguru import logger

from navin.connectors import catalog, manager, oauth
from navin.connectors.catalog import ConnectorError
from navin.connectors.store import ConnectorStore

ACTIONS = (
    "catalog", "detail", "actions", "connect", "test", "disconnect", "toggle",
    "oauth_app", "oauth_start", "status",
)


def _service(query: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    value = str(body.get("service") or query.get("service") or "").strip()
    if not value:
        raise ConnectorError("service is required.")
    return catalog.provider(value)["service"]


def handle_connectors_action(
    action: str,
    query: Mapping[str, Any],
    body: Mapping[str, Any],
    *,
    store: ConnectorStore | None = None,
) -> Any:
    store = store or ConnectorStore()
    action = (action or "catalog").strip()
    if action == "catalog":
        return catalog.search(
            query=query.get("q") or body.get("q") or "",
            category=query.get("category") or body.get("category") or "",
            auth=query.get("auth") or body.get("auth") or "",
            status=query.get("status") or body.get("status") or "",
            page=query.get("page") or body.get("page") or 1,
            page_size=query.get("page_size") or body.get("page_size") or catalog.DEFAULT_PAGE_SIZE,
            statuses=store.statuses(),
        )
    if action == "detail":
        return manager.detail(store, _service(query, body))
    if action == "actions":
        return catalog.actions(
            _service(query, body),
            query=query.get("q") or body.get("q") or "",
            op=query.get("op") or body.get("op") or "",
            page=query.get("page") or body.get("page") or 1,
            page_size=query.get("page_size") or body.get("page_size") or 10,
        )
    if action == "connect":
        return manager.connect(store, _service(query, body), str(body.get("method") or ""), body.get("values"))
    if action == "test":
        return manager.test(store, _service(query, body))
    if action == "disconnect":
        return manager.disconnect(store, _service(query, body))
    if action == "toggle":
        return manager.set_enabled(store, _service(query, body), body.get("enabled"))
    if action == "oauth_app":
        return oauth.save_app(
            store,
            _service(query, body),
            client_id=body.get("client_id"),
            client_secret=body.get("client_secret"),
            redirect_uri=body.get("redirect_uri"),
        )
    if action == "oauth_start":
        return oauth.start(store, _service(query, body), body.get("fields"), session_key=str(body.get("session_key") or ""))
    if action == "status":
        service = _service(query, body)
        return {"service": service, "status": store.status(service)}
    raise ConnectorError(f"Unknown connectors action. Use one of: {', '.join(ACTIONS)}.")


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Navin - {title}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f6f3;color:#111;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
main{{max-width:28rem;margin:1.5rem;padding:1.75rem 2rem;border-radius:20px;background:#fff;
box-shadow:0 1px 2px rgba(0,0,0,.06),0 8px 28px rgba(0,0,0,.08)}}
h1{{margin:0 0 .5rem;font-size:1.15rem;text-wrap:balance}}p{{margin:0;color:#555;text-wrap:pretty}}
.dot{{display:inline-block;width:.6rem;height:.6rem;margin-inline-end:.5rem;border-radius:50%;background:{color}}}
@media (prefers-color-scheme:dark){{body{{background:#111;color:#eee}}main{{background:#1b1b1b}}p{{color:#aaa}}}}
</style></head>
<body><main><h1><span class="dot"></span>{title}</h1><p>{detail}</p></main>
<script>setTimeout(function(){{try{{window.close()}}catch(e){{}}}},{close_ms});</script>
</body></html>
"""


def oauth_callback_page(query: Mapping[str, Any], *, store: ConnectorStore | None = None) -> tuple[int, str]:
    """``(status, html)`` for the provider's redirect back to Navin."""
    try:
        result = oauth.finish(store or ConnectorStore(), dict(query))
        title = f"{result['name']} is connected"
        detail = (
            f"Connected as {result['account']}. You can close this tab and go back to Navin."
            if result.get("account")
            else "You can close this tab and go back to Navin."
        )
        status, color, close_ms = 200, "#16a34a", 2500
    except ConnectorError as exc:
        title, detail, status, color, close_ms = "The connection did not finish", exc.message, 400, "#dc2626", 600000
    except Exception:
        # Request boundary: the browser still gets a page and the log keeps the full trace.
        logger.exception("connector OAuth callback failed")
        title = "The connection did not finish"
        detail = "Navin could not finish this sign-in. Start again from Settings > Channels > Connectors."
        status, color, close_ms = 500, "#dc2626", 600000
    page = _PAGE.format(title=html.escape(title), detail=html.escape(detail), color=color, close_ms=close_ms)
    return status, page
