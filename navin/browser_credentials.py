# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-site credentials and saved sessions for browser capture.

The user stores the login of the app to film (username, password, optional
TOTP seed) through Settings, never through the chat. The agent only ever sees
a reference such as ``site:app.example.com/password``; the browser tool
resolves it at fill time and the value never reaches the model, the session
history, the live action feed or the logs.

Secrets live in the OS keyring through :class:`navin.accounts.store.TokenVault`.
When no OS vault is available (headless Linux, WSL) they fall back to the
same at-rest scheme as ``config.json`` secrets: Fernet with the machine-local
``.config-key`` (``0600``). Playwright ``storage_state`` (cookies and
localStorage of a logged-in session) is too large for some keyrings, so it is
always a Fernet-encrypted file; its key sits in the keyring when there is
one, else it is the same machine-local config key.

The index file holds site names and secret *names* only, so Settings can list
what is stored without reading a single value.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from filelock import FileLock

SECRET_REF_PREFIX = "site:"
MAX_SECRET_LENGTH = 4096
MAX_STORAGE_STATE_BYTES = 8 * 1024 * 1024

_SITE_RE = re.compile(r"^[a-z0-9.\-]{1,253}(:[0-9]{1,5})?$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_REF_RE = re.compile(r"^site:(?P<site>[^/\s]+)/(?P<name>[A-Za-z0-9_.\-]{1,64})$")
_SESSION_KEY_ACCOUNT = "__storage_state_key__"


class BrowserCredentialError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def normalize_site(value: str) -> str:
    """``https://App.Example.com:443/login`` -> ``app.example.com``.

    Accepts a URL or a bare ``host[:port]``. Default ports are dropped so a
    credential saved for ``https://x.com`` matches ``x.com``; other ports are
    kept because two local dev servers on one host are two different apps.
    """
    text = (value or "").strip()
    if not text:
        raise BrowserCredentialError("A site is required.")
    if "://" not in text:
        text = f"http://{text}"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise BrowserCredentialError("The site is not a valid host or URL.") from None
    if not host:
        raise BrowserCredentialError("The site is not a valid host or URL.")
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    site = host if port is None or port == default else f"{host}:{port}"
    if not _SITE_RE.match(site):
        raise BrowserCredentialError("The site is not a valid host or URL.")
    return site


def parse_secret_ref(ref: str) -> tuple[str, str]:
    """``site:app.example.com/password`` -> ``("app.example.com", "password")``."""
    match = _REF_RE.match((ref or "").strip())
    if not match:
        raise BrowserCredentialError(
            "secret_ref must look like site:<host[:port]>/<name>, e.g. site:app.example.com/password."
        )
    return normalize_site(match.group("site")), match.group("name")


def secret_ref(site: str, name: str) -> str:
    return f"{SECRET_REF_PREFIX}{normalize_site(site)}/{name}"


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise BrowserCredentialError("Secret names use letters, digits, dot, dash or underscore (max 64).")
    return name


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-cred-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def totp_code(seed: str, *, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    """RFC 6238 TOTP from a base32 seed or an ``otpauth://`` URI."""
    text = (seed or "").strip()
    algorithm = "sha1"
    if text.lower().startswith("otpauth://"):
        query = parse_qs(urlsplit(text).query)
        text = unquote((query.get("secret") or [""])[0])
        digits = int((query.get("digits") or [digits])[0])
        period = int((query.get("period") or [period])[0])
        algorithm = str((query.get("algorithm") or ["sha1"])[0]).lower()
    if algorithm not in {"sha1", "sha256", "sha512"}:
        raise BrowserCredentialError("Unsupported TOTP algorithm.")
    cleaned = re.sub(r"[\s\-]", "", text).upper()
    if not cleaned:
        raise BrowserCredentialError("The TOTP seed is empty.")
    try:
        key = base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (ValueError, TypeError):
        raise BrowserCredentialError("The TOTP seed is not valid base32.") from None
    counter = int((time.time() if at is None else at) // max(1, period))
    digest = hmac.new(key, struct.pack(">Q", counter), algorithm).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


class BrowserCredentialStore:
    """Per-site secrets (keyring or encrypted file) and encrypted storage_state."""

    def __init__(self, root: Path | None = None, *, vault: Any = None, config_path: Path | None = None):
        from navin.config.loader import get_config_path

        self.config_path = Path(config_path) if config_path else Path(get_config_path())
        self.root = Path(root) if root else self.config_path.parent / "browser-credentials"
        self.root.mkdir(parents=True, exist_ok=True)
        if vault is None:
            from navin.accounts.store import TokenVault

            vault = TokenVault("browser-credentials:" + str(self.root.resolve()))
        self.vault = vault

    # -- plumbing ---------------------------------------------------------

    def lock(self) -> FileLock:
        return FileLock(str(self.root / "credentials.lock"), timeout=10)

    def keyring_ready(self) -> bool:
        try:
            return bool(self.vault.ready())
        except Exception:
            return False

    def backend_name(self) -> str:
        return "keyring" if self.keyring_ready() else "encrypted-file"

    def _fernet(self):
        from navin.config.secrets import load_or_create_fernet

        return load_or_create_fernet(self.config_path)

    def _session_fernet(self):
        from cryptography.fernet import Fernet

        if self.keyring_ready():
            stored = self.vault.get(_SESSION_KEY_ACCOUNT) or {}
            key = str(stored.get("key") or "")
            if not key:
                key = Fernet.generate_key().decode("ascii")
                self.vault.put(_SESSION_KEY_ACCOUNT, {"key": key})
            return Fernet(key.encode("ascii"))
        return self._fernet()

    def _index(self) -> dict[str, Any]:
        path = self.root / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict) or not isinstance(data.get("sites"), dict):
            data = {"sites": {}}
        return data

    def _save_index(self, data: dict[str, Any]) -> None:
        _write_private(
            self.root / "index.json",
            json.dumps(data, indent=2, sort_keys=True).encode("utf-8"),
        )

    def _file_secrets(self) -> dict[str, dict[str, str]]:
        path = self.root / "secrets.enc"
        if not path.is_file():
            return {}
        from cryptography.fernet import InvalidToken

        try:
            raw = self._fernet().decrypt(path.read_bytes().strip())
            data = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, OSError):
            raise BrowserCredentialError("The local credential file could not be unlocked.", 503) from None
        return data if isinstance(data, dict) else {}

    def _save_file_secrets(self, data: dict[str, dict[str, str]]) -> None:
        token = self._fernet().encrypt(json.dumps(data).encode("utf-8"))
        _write_private(self.root / "secrets.enc", token + b"\n")

    def _site_secrets(self, site: str) -> dict[str, str]:
        if self.keyring_ready():
            values = self.vault.get(site) or {}
        else:
            values = self._file_secrets().get(site) or {}
        return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}

    def _put_site_secrets(self, site: str, values: dict[str, str]) -> None:
        if self.keyring_ready():
            if values:
                self.vault.put(site, values)
            else:
                self.vault.delete(site)
            return
        data = self._file_secrets()
        if values:
            data[site] = values
        else:
            data.pop(site, None)
        self._save_file_secrets(data)

    def _session_path(self, site: str) -> Path:
        digest = hashlib.sha256(site.encode("utf-8")).hexdigest()[:24]
        return self.root / "sessions" / f"{digest}.enc"

    # -- secrets ------------------------------------------------------------

    def set_secrets(self, site: str, values: dict[str, str]) -> None:
        site = normalize_site(site)
        clean: dict[str, str] = {}
        for name, value in values.items():
            name = _check_name(name)
            text = "" if value is None else str(value)
            if len(text) > MAX_SECRET_LENGTH:
                raise BrowserCredentialError("A secret value is too long.")
            clean[name] = text
        if not clean:
            raise BrowserCredentialError("Nothing to save.")
        with self.lock():
            current = self._site_secrets(site)
            for name, text in clean.items():
                if text:
                    current[name] = text
                else:
                    current.pop(name, None)
            self._put_site_secrets(site, current)
            index = self._index()
            row = index["sites"].setdefault(site, {})
            row["names"] = sorted(current)
            row["updated"] = int(time.time())
            if not row["names"] and not row.get("session"):
                index["sites"].pop(site, None)
            self._save_index(index)

    def delete(self, site: str, name: str | None = None) -> None:
        site = normalize_site(site)
        with self.lock():
            current = self._site_secrets(site)
            if name:
                current.pop(_check_name(name), None)
            else:
                current = {}
            self._put_site_secrets(site, current)
            index = self._index()
            row = index["sites"].get(site)
            if row is not None:
                row["names"] = sorted(current)
                if not name:
                    self._session_path(site).unlink(missing_ok=True)
                    row["session"] = False
                if not row["names"] and not row.get("session"):
                    index["sites"].pop(site, None)
            self._save_index(index)

    def has(self, ref: str) -> bool:
        try:
            site, name = parse_secret_ref(ref)
        except BrowserCredentialError:
            return False
        return name in (self._index()["sites"].get(site) or {}).get("names", [])

    def resolve(self, ref: str) -> str:
        """The clear value behind a ref. Callers must never echo or log it."""
        site, name = parse_secret_ref(ref)
        value = self._site_secrets(site).get(name)
        if not value:
            raise BrowserCredentialError(
                f"No saved secret for {SECRET_REF_PREFIX}{site}/{name}. Ask the user to add it in "
                "Settings > Browser credentials; never ask them to paste it in the chat.",
                404,
            )
        return value

    def list_sites(self) -> list[dict[str, Any]]:
        sites = self._index()["sites"]
        return [
            {
                "site": site,
                "names": list(row.get("names") or []),
                "refs": [f"{SECRET_REF_PREFIX}{site}/{n}" for n in row.get("names") or []],
                "session": bool(row.get("session")),
                "updated": row.get("updated"),
            }
            for site, row in sorted(sites.items())
        ]

    # -- storage_state ------------------------------------------------------

    def save_storage_state(self, site: str, state: dict[str, Any]) -> None:
        site = normalize_site(site)
        payload = json.dumps(state or {}).encode("utf-8")
        if len(payload) > MAX_STORAGE_STATE_BYTES:
            raise BrowserCredentialError("The browser session is too large to save.", 413)
        with self.lock():
            token = self._session_fernet().encrypt(payload)
            _write_private(self._session_path(site), token + b"\n")
            index = self._index()
            row = index["sites"].setdefault(site, {"names": []})
            row["session"] = True
            row["session_saved"] = int(time.time())
            self._save_index(index)

    def load_storage_state(self, site: str) -> dict[str, Any] | None:
        site = normalize_site(site)
        path = self._session_path(site)
        if not path.is_file():
            return None
        from cryptography.fernet import InvalidToken

        try:
            raw = self._session_fernet().decrypt(path.read_bytes().strip())
            data = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, OSError):
            return None
        return data if isinstance(data, dict) else None

    def forget_storage_state(self, site: str) -> None:
        site = normalize_site(site)
        with self.lock():
            self._session_path(site).unlink(missing_ok=True)
            index = self._index()
            row = index["sites"].get(site)
            if row is not None:
                row["session"] = False
                if not row.get("names"):
                    index["sites"].pop(site, None)
            self._save_index(index)


def credentials_snapshot(store: BrowserCredentialStore | None = None) -> dict[str, Any]:
    store = store or BrowserCredentialStore()
    return {"sites": store.list_sites(), "vault": store.backend_name()}


def handle_browser_credentials_action(
    action: str, body: dict[str, Any] | None = None, *, store: BrowserCredentialStore | None = None
) -> dict[str, Any]:
    """Settings API. Values go in, only names ever come back out.

    Actions: ``list``; ``set`` with ``site`` plus ``values`` ({name: value})
    or ``name``/``value`` (an empty value removes that name); ``delete`` with
    ``site`` and optional ``name``; ``forget_session`` with ``site``.
    """
    store, body = store or BrowserCredentialStore(), body or {}
    action = (action or "list").strip()
    if action in {"list", "snapshot"}:
        return credentials_snapshot(store)
    site = str(body.get("site") or "")
    if action == "set":
        values = body.get("values")
        if values is None and body.get("name") is not None:
            values = {str(body.get("name")): body.get("value")}
        if not isinstance(values, dict):
            raise BrowserCredentialError("Provide values as {name: value}.")
        store.set_secrets(site, {str(k): ("" if v is None else str(v)) for k, v in values.items()})
    elif action == "delete":
        store.delete(site, str(body.get("name") or "") or None)
    elif action == "forget_session":
        store.forget_storage_state(site)
    else:
        raise BrowserCredentialError("Unknown browser credential action.")
    return credentials_snapshot(store)
