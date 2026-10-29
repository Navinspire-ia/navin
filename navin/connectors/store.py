# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Connections index and secret vault for connectors.

``index.json`` holds what Settings and the agent may read: which app is
connected, by which method, as which account, and its non-secret fields
(a subdomain, a site). Keys, tokens and OAuth client secrets live in the OS
keyring (:class:`navin.accounts.store.TokenVault`) or, when there is none
(headless Linux, WSL), in ``secrets.enc``, Fernet-encrypted with the
machine-local config key, the same at-rest scheme as ``config.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from filelock import FileLock
from loguru import logger

from navin.accounts.store import AccountError
from navin.connectors.catalog import ConnectorError

MAX_PENDING = 20


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, readable by this user only.

    mkstemp creates the file with mode 0600 and os.replace keeps it, so no
    reader ever sees a partial or world-readable file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".navin-conn-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        # Already gone after a successful replace; removed when writing or replacing failed.
        tmp.unlink(missing_ok=True)


def _damaged(path: Path) -> str:
    return f"{path} is damaged or unreadable: fix it or move it aside, then try again."


def _read_json(path: Path) -> dict[str, Any]:
    """A JSON object file; {} when it does not exist yet.

    A damaged or unreadable file raises instead of reading as empty: saving
    over it would drop every connection it holds.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConnectorError(_damaged(path), 503) from None
    if not isinstance(data, dict):
        raise ConnectorError(_damaged(path), 503)
    return data


def public_status(row: Any) -> dict[str, Any]:
    """What the UI and the agent may see of a connection: never a secret."""
    if not isinstance(row, dict) or not row.get("connected"):
        return {"connected": False}
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    return {
        "connected": True,
        "enabled": bool(row.get("enabled", True)),
        "method": str(row.get("method") or ""),
        "account": str(profile.get("name") or ""),
        "account_id": str(profile.get("id") or ""),
        "connected_at": int(row.get("connected_at") or 0),
        "expires_at": int(row.get("expires_at") or 0),
        "scopes": [str(item) for item in (row.get("scopes") or [])][:60],
        "fields": {str(k): str(v) for k, v in (row.get("fields") or {}).items()},
        "error": str(row.get("error") or ""),
    }


class ConnectorStore:
    """Connections (index + vault), OAuth apps per provider family, pending sign-ins."""

    def __init__(self, root: Path | None = None, *, vault: Any = None, config_path: Path | None = None) -> None:
        from navin.config.loader import get_config_path

        self.config_path = Path(config_path) if config_path else Path(get_config_path())
        self.root = Path(root) if root else self.config_path.parent / "connectors"
        self.root.mkdir(parents=True, exist_ok=True)
        if vault is None:
            from navin.accounts.store import TokenVault

            vault = TokenVault("connectors:" + str(self.root.resolve()))
        self.vault = vault
        self._keyring: bool | None = None

    # -- plumbing -------------------------------------------------------------

    def lock(self) -> FileLock:
        return FileLock(str(self.root / "connectors.lock"), timeout=10)

    def keyring_ready(self) -> bool:
        if self._keyring is None:
            # TokenVault.ready() answers False instead of raising when no OS keyring is usable.
            self._keyring = bool(self.vault.ready())
        return self._keyring

    def backend_name(self) -> str:
        return "keyring" if self.keyring_ready() else "encrypted-file"

    def _fernet(self):
        from navin.config.secrets import load_or_create_fernet

        return load_or_create_fernet(self.config_path)

    def load(self) -> dict[str, Any]:
        path = self.root / "index.json"
        data = _read_json(path)
        for key in ("connections", "apps"):
            if not isinstance(data.setdefault(key, {}), dict):
                raise ConnectorError(_damaged(path), 503)
        return data

    def save(self, data: dict[str, Any]) -> None:
        _write_private(self.root / "index.json", json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))

    # -- secrets ---------------------------------------------------------------

    def _file_secrets(self) -> dict[str, Any]:
        path = self.root / "secrets.enc"
        if not path.is_file():
            return {}
        from cryptography.fernet import InvalidToken

        try:
            raw = self._fernet().decrypt(path.read_bytes().strip())
            data = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, OSError):
            raise ConnectorError("The local connector vault could not be unlocked.", 503) from None
        return data if isinstance(data, dict) else {}

    def _save_file_secrets(self, data: dict[str, Any]) -> None:
        token = self._fernet().encrypt(json.dumps(data).encode("utf-8"))
        _write_private(self.root / "secrets.enc", token + b"\n")

    def secrets(self, account: str) -> dict[str, Any]:
        """The secret bag of ``account``; {} when none is stored.

        The encrypted file holds an entry only when there is no keyring or the
        keyring refused the write, so that entry is the newest and wins.
        """
        value = self._file_secrets().get(account)
        if isinstance(value, dict) and value:
            return value
        if not self.keyring_ready():
            return {}
        try:
            stored = self.vault.get(account)
        except AccountError as exc:
            raise ConnectorError(f"The OS keyring could not be read: {exc.message}", 503) from None
        return stored if isinstance(stored, dict) else {}

    def put_secrets(self, account: str, data: dict[str, Any]) -> None:
        if self.keyring_ready():
            try:
                self.vault.put(account, data)
                self._drop_file_secret(account)
                return
            except AccountError as exc:
                # Keep the secret in the encrypted file rather than lose it.
                logger.warning("connector keyring write failed, using the encrypted file: {}", exc.message)
        current = self._file_secrets()
        current[account] = data
        self._save_file_secrets(current)

    def _drop_file_secret(self, account: str) -> None:
        if not (self.root / "secrets.enc").is_file():
            return
        current = self._file_secrets()
        if account in current:
            current.pop(account, None)
            self._save_file_secrets(current)

    def delete_secrets(self, account: str) -> None:
        self._drop_file_secret(account)
        if not self.keyring_ready():
            return
        try:
            self.vault.delete(account)
        except AccountError as exc:
            raise ConnectorError(f"The secret could not be removed from the OS keyring: {exc.message}", 503) from None

    # -- connections -----------------------------------------------------------

    def connection(self, service: str) -> dict[str, Any] | None:
        row = self.load()["connections"].get(service)
        return row if isinstance(row, dict) else None

    def statuses(self) -> dict[str, dict[str, Any]]:
        return {
            service: public_status(row)
            for service, row in self.load()["connections"].items()
            if isinstance(row, dict)
        }

    def status(self, service: str) -> dict[str, Any]:
        return public_status(self.connection(service))

    def save_connection(
        self,
        service: str,
        *,
        method: str,
        fields: dict[str, str],
        secret_values: dict[str, str],
        profile: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        scopes: list[str] | tuple[str, ...] = (),
        access_token: str = "",
        refresh_token: str = "",
        expires_at: int = 0,
        token_type: str = "",
    ) -> dict[str, Any]:
        bag: dict[str, Any] = {"values": dict(secret_values)}
        if access_token:
            bag.update({"access_token": access_token, "refresh_token": refresh_token, "token_type": token_type})
        now = int(time.time())
        with self.lock():
            # A damaged index stops here, before any secret is written.
            data = self.load()
            self.put_secrets(f"conn:{service}", bag)
            previous = data["connections"].get(service) or {}
            data["connections"][service] = {
                "connected": True,
                "enabled": bool(previous.get("enabled", True)),
                "method": method,
                "fields": dict(fields),
                "profile": {"id": str(profile.get("id") or ""), "name": str(profile.get("name") or "")},
                "metadata": dict(metadata or {}),
                "scopes": list(scopes),
                "expires_at": int(expires_at or 0),
                "connected_at": now,
                "updated_at": now,
                "error": "",
            }
            self.save(data)
            return public_status(data["connections"][service])

    def update_tokens(self, service: str, *, access_token: str, refresh_token: str = "", expires_at: int = 0) -> bool:
        """Store refreshed tokens; False when the connection was removed meanwhile."""
        with self.lock():
            data = self.load()
            row = data["connections"].get(service)
            if not isinstance(row, dict) or not row.get("connected"):
                # Disconnected while the refresh ran: keep no token for it.
                return False
            bag = self.secrets(f"conn:{service}")
            bag["access_token"] = access_token
            if refresh_token:
                bag["refresh_token"] = refresh_token
            self.put_secrets(f"conn:{service}", bag)
            row.update({"expires_at": int(expires_at or 0), "updated_at": int(time.time()), "error": ""})
            self.save(data)
            return True

    def _patch(self, service: str, **fields: Any) -> dict[str, Any]:
        with self.lock():
            data = self.load()
            row = data["connections"].get(service)
            if not isinstance(row, dict) or not row.get("connected"):
                raise ConnectorError("This connector is not connected.", 409)
            row.update(fields, updated_at=int(time.time()))
            self.save(data)
            return public_status(row)

    def mark_error(self, service: str, message: str) -> bool:
        """Record the last failure on a connection; False when it no longer exists."""
        with self.lock():
            data = self.load()
            row = data["connections"].get(service)
            if not isinstance(row, dict) or not row.get("connected"):
                return False
            row.update(error=str(message)[:400], updated_at=int(time.time()))
            self.save(data)
            return True

    def set_profile(self, service: str, profile: dict[str, Any]) -> dict[str, Any]:
        clean = {"id": str(profile.get("id") or ""), "name": str(profile.get("name") or "")}
        row = self.connection(service) or {}
        if not clean["id"] and not clean["name"]:
            clean = row.get("profile") or clean
        return self._patch(service, profile=clean, error="")

    def set_enabled(self, service: str, enabled: bool) -> dict[str, Any]:
        return self._patch(service, enabled=bool(enabled))

    def remove(self, service: str) -> None:
        with self.lock():
            data = self.load()
            self.delete_secrets(f"conn:{service}")
            if data["connections"].pop(service, None) is not None:
                self.save(data)

    # -- OAuth apps (one per provider family) ---------------------------------

    def app(self, family: str) -> dict[str, Any]:
        row = self.load()["apps"].get(family)
        return dict(row) if isinstance(row, dict) else {}

    def app_secret(self, family: str) -> str:
        return str(self.secrets(f"app:{family}").get("client_secret") or "")

    def save_app(self, family: str, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
        with self.lock():
            data = self.load()
            if client_secret:
                self.put_secrets(f"app:{family}", {"client_secret": client_secret})
            data["apps"][family] = {"client_id": client_id, "redirect_uri": redirect_uri, "updated_at": int(time.time())}
            self.save(data)

    # -- pending sign-ins --------------------------------------------------------

    def add_pending(self, key: str, entry: dict[str, Any]) -> None:
        path = self.root / "oauth-pending.json"
        with self.lock():
            now = time.time()
            pending = {
                k: v for k, v in _read_json(path).items()
                if isinstance(v, dict) and float(v.get("expires_at") or 0) > now
            }
            if len(pending) >= MAX_PENDING:
                raise ConnectorError("Too many sign-ins are waiting; finish one or wait ten minutes.", 429)
            pending[key] = entry
            _write_private(path, json.dumps(pending).encode("utf-8"))

    def take_pending(self, key: str) -> dict[str, Any] | None:
        """Pop a pending sign-in once; an expired one reads as missing."""
        path = self.root / "oauth-pending.json"
        with self.lock():
            pending = _read_json(path)
            entry = pending.pop(key, None)
            _write_private(path, json.dumps(pending).encode("utf-8"))
        if not isinstance(entry, dict) or float(entry.get("expires_at") or 0) <= time.time():
            return None
        return entry
