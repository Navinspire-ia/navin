"""Public account metadata and OS-protected tokens. Never store tokens in JSON."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from filelock import FileLock

from navin.career.store import _atomic_write, _read_json
from navin.config.loader import get_config_path


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


DEFAULT_PERMISSIONS = {"read_mail": True, "draft_mail": True, "send_mail": False,
                       "archive_mail": False, "delete_mail": False, "read_calendar": True,
                       "write_calendar": False, "cancel_events": False, "read_contacts": False}


class TokenVault:
    def __init__(self, namespace: str):
        self.namespace = "Navin.Accounts." + hashlib.sha256(namespace.encode()).hexdigest()[:20]

    def _backend(self):
        try:
            import keyring
            backend = keyring.get_keyring()
            if type(backend).__module__ == "keyring.backends.chainer":
                backend = next((b for b in backend.backends if type(b).__module__ in {
                    "keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService"}), None)
            if backend is None or type(backend).__module__ not in {
                "keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService"
            }:
                raise AccountError("The secure OS credential vault is unavailable. Unlock your system session.", 503)
            return backend
        except ImportError:
            raise AccountError("The secure credential vault component must be installed with Navin.", 503) from None

    def ready(self) -> bool:
        try:
            self._backend()
            return True
        except Exception:
            return False

    def get(self, account_id: str) -> dict[str, Any]:
        try:
            value = self._backend().get_password(self.namespace, account_id)
            return json.loads(value) if value else {}
        except AccountError:
            raise
        except Exception:
            raise AccountError("The local secure credential vault could not be read.", 503) from None

    def put(self, account_id: str, tokens: dict[str, Any]) -> None:
        try:
            self._backend().set_password(self.namespace, account_id, json.dumps(tokens))
        except Exception:
            raise AccountError("Tokens could not be saved in the local secure credential vault.", 503) from None

    def delete(self, account_id: str) -> None:
        if not self.get(account_id):
            return
        try:
            self._backend().delete_password(self.namespace, account_id)
        except Exception:
            raise AccountError("The account could not be removed from the secure credential vault.", 503) from None


class AccountStore:
    def __init__(self, root: Path | None = None):
        self.root = root or get_config_path().parent / "accounts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.vault = TokenVault(str(self.root.resolve()))

    def lock(self):
        return FileLock(str(self.root / "accounts.lock"), timeout=10)

    def load(self) -> dict[str, Any]:
        return _read_json(self.root / "accounts.json", {"accounts": {}, "operations": {}})

    def save(self, data: dict[str, Any]) -> None:
        _atomic_write(self.root / "accounts.json", data)

    def account(self, account_id: str) -> dict[str, Any]:
        row = self.load()["accounts"].get(account_id)
        if not row:
            raise AccountError("Connect a Google or Microsoft account in Navin.", 409)
        return row

    def require(self, account_id: str, permission: str) -> dict[str, Any]:
        row = self.account(account_id)
        if not row.get("permissions", {}).get(permission):
            raise AccountError("This action is not allowed by the account permissions: " + permission, 403)
        return row
