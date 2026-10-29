# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What Settings and the agent call: detail, connect, test, disconnect."""

from __future__ import annotations

from typing import Any

from navin.connectors import catalog, oauth, providers, runtime
from navin.connectors.catalog import ConnectorError
from navin.connectors.store import ConnectorStore

POINTER = "Settings > Channels > Connectors"
_FIELD_KEYS = ("key", "label", "inputType", "required", "secret", "placeholder", "defaultValue", "description")
MAX_VALUE = 32_000


def _public_method(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {key: item[key] for key in ("type", "label", "description") if item.get(key)}
    out["fields"] = [{key: field[key] for key in _FIELD_KEYS if key in field} for field in item.get("fields") or []]
    if item["type"] == "oauth2":
        out.update({
            "family": item.get("family") or "",
            "scopes": list(item.get("scopes") or []),
            "pkce": bool(item.get("pkce")),
            "app_url": item.get("appUrl") or "",
        })
    return out


def detail(store: ConnectorStore, service: str) -> dict[str, Any]:
    row = catalog.provider(service)
    service = row["service"]
    spec = providers.spec_for(service)
    data = catalog.summary(row, store.status(service))
    data["methods"] = [_public_method(item) for item in row["methods"]]
    data["docs"] = spec.docs
    data["base_url"] = spec.base
    if catalog.has_oauth(row):
        data["oauth_app"] = oauth.app_status(store, service)
    data["actions_page"] = catalog.actions(service, page=1, page_size=10)
    data["vault"] = store.backend_name()
    return data


def clean_values(item: dict[str, Any], values: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Split the form into non-secret fields and vault secrets, checking required ones."""
    public: dict[str, str] = {}
    secret: dict[str, str] = {}
    raw = values if isinstance(values, dict) else {}
    for field in item.get("fields") or []:
        value = raw.get(field["key"])
        text = "" if value is None else str(value).strip()
        if not text and field.get("defaultValue") and not field.get("secret"):
            text = str(field["defaultValue"])
        if field.get("required") and not text:
            raise ConnectorError(f"{field['label']} is required.")
        if len(text) > MAX_VALUE:
            raise ConnectorError(f"{field['label']} is too long.")
        if ("\n" in text or "\r" in text) and field.get("inputType") != "textarea":
            raise ConnectorError(f"{field['label']} must fit on one line.")
        if text:
            (secret if field.get("secret") else public)[field["key"]] = text
    if not secret:
        raise ConnectorError("Fill in the key or token fields.")
    return public, secret


def connect(store: ConnectorStore, service: str, kind: str | None, values: Any) -> dict[str, Any]:
    """Verify an API key, token or service account with the app, then store it."""
    row = catalog.provider(service)
    service = row["service"]
    item = catalog.method(service, kind or next((m["type"] for m in row["methods"] if m["type"] != "oauth2"), None))
    if item["type"] == "oauth2":
        raise ConnectorError(f"{row['name']} signs in with OAuth: use oauth_start.")
    public, secret = clean_values(item, values)
    result = runtime.verify_credential(service, item["type"], {**public, **secret})
    prepared = result["values"]
    status = store.save_connection(
        service,
        method=item["type"],
        fields={key: str(prepared.get(key, value)) for key, value in public.items()},
        secret_values=secret,
        profile=result["profile"],
    )
    return {"service": service, "name": row["name"], "status": status}


def test(store: ConnectorStore, service: str) -> dict[str, Any]:
    """Re-run the verification call with the stored credential."""
    row = catalog.provider(service)
    service = row["service"]
    cred = runtime.load_credential(service, store)
    try:
        try:
            result = runtime.verify_credential(
                service, cred.kind, cred.values, access_token=cred.access_token, metadata=cred.metadata
            )
        except ConnectorError:
            fresh = oauth.refresh(store, service) if cred.kind == "oauth2" else ""
            if not fresh:
                raise
            result = runtime.verify_credential(service, cred.kind, cred.values, access_token=fresh, metadata=cred.metadata)
    except ConnectorError as exc:
        store.mark_error(service, exc.message)
        raise
    status = store.set_profile(service, result["profile"])
    return {"service": service, "name": row["name"], "ok": True, "status": status}


def disconnect(store: ConnectorStore, service: str) -> dict[str, Any]:
    row = catalog.provider(service)
    store.remove(row["service"])
    return {"service": row["service"], "name": row["name"], "status": {"connected": False}}


def set_enabled(store: ConnectorStore, service: str, enabled: Any) -> dict[str, Any]:
    row = catalog.provider(service)
    flag = enabled if isinstance(enabled, bool) else str(enabled).strip().lower() in {"1", "true", "yes", "on"}
    return {"service": row["service"], "name": row["name"], "status": store.set_enabled(row["service"], flag)}


def connected_summary(store: ConnectorStore | None = None) -> list[dict[str, str]]:
    """Apps the agent may use now: connected and turned on."""
    store = store or ConnectorStore()
    rows: list[dict[str, str]] = []
    for service, status in store.statuses().items():
        if not status.get("connected") or not status.get("enabled", True):
            continue
        try:
            row = catalog.provider(service)
        except ConnectorError:
            continue
        rows.append({
            "service": row["service"], "name": row["name"], "account": str(status.get("account") or ""),
            "method": str(status.get("method") or ""),
        })
    return sorted(rows, key=lambda item: item["name"].lower())


def is_connected(service: str, store: ConnectorStore | None = None) -> tuple[bool, str]:
    """``(connected and on, account name)``."""
    key = catalog.resolve_service(service)
    if not key:
        return False, ""
    status = (store or ConnectorStore()).status(key)
    return bool(status.get("connected") and status.get("enabled", True)), str(status.get("account") or "")


def terminal_form(service: str, store: ConnectorStore | None = None) -> dict[str, Any]:
    """What the terminal card needs: the first key method's fields and the OAuth state."""
    store = store or ConnectorStore()
    row = catalog.provider(service)
    key_method = next((item for item in row["methods"] if item["type"] != "oauth2"), None)
    form: dict[str, Any] = {
        "service": row["service"],
        "name": row["name"],
        "key_method": _public_method(key_method) if key_method else None,
        "oauth": None,
    }
    if catalog.has_oauth(row):
        app = oauth.app_status(store, row["service"])
        oauth_method = catalog.method(row["service"], "oauth2")
        form["oauth"] = {
            "configured": app["configured"],
            "label": app["label"],
            "needs_fields": any(field.get("required") and not field.get("defaultValue") for field in oauth_method.get("fields") or []),
        }
    return form
