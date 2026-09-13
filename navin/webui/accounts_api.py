"""User-controlled account connections and approvals, separate from agent tools."""

from __future__ import annotations

from typing import Any

from navin.accounts.providers import PROVIDERS, capabilities, registration
from navin.accounts.store import DEFAULT_PERMISSIONS, AccountError, AccountStore


def account_snapshot(store: AccountStore | None = None) -> dict[str, Any]:
    store = store or AccountStore()
    state = store.load()
    return {"accounts": [{**row, "capabilities": capabilities(row["provider"], row["scopes"])} for row in state["accounts"].values()],
            "providers": [{"id": key, "name": spec["name"], "ready": bool(registration(key)["client_id"])} for key, spec in PROVIDERS.items()],
            "vault_ready": store.vault.ready(), "local_tokens": True, "cloud_relay": False,
            "pending": [row for row in state["operations"].values() if row["status"] == "pending"][-100:]}


def handle_accounts_action(action: str, body: dict[str, Any] | None = None, *, store: AccountStore | None = None) -> dict[str, Any]:
    from navin.accounts.oauth import flow_status, start
    from navin.accounts.service import execute

    store, body = store or AccountStore(), body or {}
    if action == "snapshot":
        return account_snapshot(store)
    if action == "connect":
        return start(store, str(body.get("provider") or ""), body.get("permissions"), locale=str(body.get("locale") or "en"))
    if action in {"poll", "cancel"}:
        return flow_status(store, str(body.get("flow_id") or ""), cancel=action == "cancel")
    account_id = str(body.get("account_id") or "")
    if action == "permissions":
        with store.lock():
            state = store.load()
            row = store.account(account_id)
            granted = capabilities(row["provider"], row["scopes"])
            permissions = {key: body.get("permissions", {}).get(key) is True for key in DEFAULT_PERMISSIONS}
            if any(value and not granted[key] for key, value in permissions.items()):
                raise AccountError("Reconnect the account to grant these additional permissions.", 409)
            state["accounts"][account_id]["permissions"] = permissions
            store.save(state)
    elif action == "disconnect":
        with store.lock():
            state = store.load()
            store.vault.delete(account_id)
            state["accounts"].pop(account_id, None)
            for operation in state["operations"].values():
                if operation["account_id"] == account_id and operation["status"] == "pending":
                    operation["status"] = "rejected"
            store.save(state)
    elif action in {"approve", "reject"}:
        key = str(body.get("approval_id") or "")
        with store.lock():
            state = store.load()
            entry = state["operations"].get(key)
            if not entry or entry["status"] != "pending":
                raise AccountError("This request is no longer pending.", 409)
            if action == "reject":
                entry["status"] = "rejected"
                store.save(state)
        if action == "approve":
            result = execute(store, entry["account_id"], entry["action"], entry["body"], approved=True)
            return {**account_snapshot(store), "result": result}
    else:
        raise AccountError("Unknown account action.")
    return account_snapshot(store)
