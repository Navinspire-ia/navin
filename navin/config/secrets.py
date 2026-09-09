# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""At-rest encryption for secrets written into ``config.json``.

The file stays a normal JSON document so operators can still edit host, port,
and model choices. Tokens and API keys are stored as ``enc:v1:...`` and
unlocked with a machine-local key next to the config (``0600``). Opening the
file in Code therefore no longer dumps the live license key.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

ENC_PREFIX = "enc:v1:"
KEY_FILENAME = ".config-key"
RUNTIME_CONFIG_DENIED = "Navin runtime config is hidden from Code and tools"

# Exact leaf names. ``token`` / ``tokens`` stay exact so ``maxTokens`` and
# ``usageRemainingTokens`` are never treated as credentials.
_SECRET_EXACT = frozenset(
    {
        "token",
        "tokens",
        "secret",
        "password",
        "authorization",
        "bearer",
        "credential",
        "credentials",
    }
)

# Suffixes: ``psiApiKey``, ``unsplashAccessKey``, ``imapPassword``,
# ``githubToken``, ``semrushApiKey``, ``x-api-key``, LiveKit ``apiSecret``.
_SECRET_SUFFIXES = (
    "apikey",
    "apisecret",
    "accesskey",
    "accesstoken",
    "activationtoken",
    "refreshtoken",
    "bottoken",
    "apptoken",
    "githubtoken",
    "ghtoken",
    "licensekey",
    "clientsecret",
    "clientkey",
    "privatekey",
    "webhooksecret",
    "password",
    "secret",
)


def key_path_for(config_path: Path) -> Path:
    return config_path.parent / KEY_FILENAME


def _normalize_leaf(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch not in "_-")


def is_secret_leaf(key: str) -> bool:
    leaf = _normalize_leaf(key)
    if not leaf or leaf in {"maxtokens", "maxoutputtokens", "maxtooltokens"}:
        return False
    if leaf in _SECRET_EXACT:
        return True
    return any(leaf.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def is_locked_secret(value: str | None) -> bool:
    """True when a value is still ciphertext and must not leave this machine."""
    return bool(value) and str(value).startswith(ENC_PREFIX)


def unlocked_secret(value: str | None) -> str:
    """Secret safe to send to navin.live or a provider. Empty if still locked."""
    text = (value or "").strip()
    if not text or is_locked_secret(text):
        return ""
    return text


def unlock_stored_secret(value: str | None, config_path: Path) -> str:
    """Decrypt a secret read from ``config.json`` with the adjacent key file.

    Desktop shells and the packaged entry read the file as JSON. They must
    unlock ``tokenIssueSecret`` the same way ``load_config`` does, or the
    bootstrap header would be ``enc:v1:...`` and the WebUI would fail.
    """
    text = (value or "").strip()
    if not text or not is_locked_secret(text):
        return text
    path = key_path_for(config_path)
    if not path.exists():
        logger.warning("config secret is encrypted but the local key file is missing")
        return ""
    raw = path.read_bytes().strip()
    if not raw:
        return ""
    try:
        fernet = Fernet(raw)
    except ValueError:
        logger.warning("config key file is unreadable; leaving the secret locked")
        return ""
    unlocked = decrypt_secret(text, fernet)
    return "" if is_locked_secret(unlocked) else unlocked


def is_runtime_secret_path(path: Path | str) -> bool:
    """True for the live ``config.json`` and its machine-local key.

    Host, port, and ``allowFrom`` are operator settings, not credentials.
    ``tokenIssueSecret`` and other secret leaves are encrypted at rest. The
    live file still must not be opened from Code, preview, or agent tools:
    that is how a user ends up staring at ``channels.websocket``.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    try:
        from navin.config.loader import get_config_path
        config = Path(get_config_path()).expanduser().resolve()
    except Exception:
        config = (Path.home() / ".navin" / "config.json").resolve()
    return resolved in {config, key_path_for(config)}


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_or_create_fernet(config_path: Path) -> Fernet:
    path = key_path_for(config_path)
    if path.exists():
        raw = path.read_bytes().strip()
        if raw:
            return Fernet(raw)
    key = Fernet.generate_key()
    _write_private(path, key + b"\n")
    return Fernet(key)


def encrypt_secret(value: str, fernet: Fernet) -> str:
    if not value or value.startswith(ENC_PREFIX) or value.startswith("${"):
        return value
    token = fernet.encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENC_PREFIX}{token}"


def decrypt_secret(value: str, fernet: Fernet | None) -> str:
    if not value.startswith(ENC_PREFIX):
        return value
    if fernet is None:
        logger.warning("config secret is encrypted but the local key file is missing")
        return value
    blob = value[len(ENC_PREFIX) :]
    try:
        return fernet.decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logger.warning("could not decrypt a config secret: {}", exc)
        return value


def _map_secret_value(value: Any, transform) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {key: _map_secret_value(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_secret_value(item, transform) for item in value]
    return value


def transform_secrets(data: Any, transform) -> Any:
    """Walk a JSON-like tree and transform string values on secret leaves."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if is_secret_leaf(key):
                out[key] = _map_secret_value(value, transform)
            else:
                out[key] = transform_secrets(value, transform)
        return out
    if isinstance(data, list):
        return [transform_secrets(item, transform) for item in data]
    return data


def has_plaintext_secrets(data: Any) -> bool:
    """True when at least one secret leaf is still stored in the clear."""
    found = False

    def mark(value: str) -> str:
        nonlocal found
        if value and not value.startswith(ENC_PREFIX) and not value.startswith("${"):
            found = True
        return value

    transform_secrets(data, mark)
    return found


def encrypt_config_data(data: dict[str, Any], config_path: Path) -> dict[str, Any]:
    fernet = load_or_create_fernet(config_path)
    return transform_secrets(data, lambda value: encrypt_secret(value, fernet))


def decrypt_config_data(data: dict[str, Any], config_path: Path) -> dict[str, Any]:
    path = key_path_for(config_path)
    fernet: Fernet | None = None
    if path.exists():
        raw = path.read_bytes().strip()
        if raw:
            try:
                fernet = Fernet(raw)
            except ValueError:
                logger.warning("config key file is unreadable; leaving secrets as stored")
    return transform_secrets(data, lambda value: decrypt_secret(value, fernet))
