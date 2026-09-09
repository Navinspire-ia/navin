# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Short-lived HMAC collab tokens for org members joining a host gateway.

Format matches the site license token style: ``base64url(json).hmac_sha256``.
The signing secret is shared with the license server
(``NAVIN_COLLAB_SECRET`` or ``NAVIN_LICENSE_SIGNING_SECRET``), so any org
member who obtained a token from the host (or a future site mint endpoint)
can prove membership without holding the host's static WS token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from navin.collab.acl import normalize_org_role

DEFAULT_TTL_S = 15 * 60
MIN_TTL_S = 30
MAX_TTL_S = 24 * 60 * 60
MIN_SECRET_LEN = 32


class CollabTokenError(ValueError):
    """Raised when minting fails (missing secret, bad claims, …)."""


def resolve_collab_secret(explicit: str | None = None) -> str:
    """Pick the HMAC secret used to mint / verify collab tokens.

    Preference order: explicit argument, ``NAVIN_COLLAB_SECRET``,
    ``NAVIN_LICENSE_SIGNING_SECRET``. Empty / too-short secrets are rejected
    at mint/verify time so we never sign weakly.
    """
    for candidate in (
        (explicit or "").strip(),
        os.environ.get("NAVIN_COLLAB_SECRET", "").strip(),
        os.environ.get("NAVIN_LICENSE_SIGNING_SECRET", "").strip(),
    ):
        if len(candidate) >= MIN_SECRET_LEN:
            return candidate
    return ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(digest)


def mint_collab_token(
    *,
    member_id: str,
    org_id: str,
    role: str,
    display_name: str = "",
    chat_id: str | None = None,
    host_device: str | None = None,
    ttl_s: int = DEFAULT_TTL_S,
    secret: str | None = None,
    now: int | None = None,
) -> str:
    """Mint a short-lived collab join token for an org member."""
    secret_value = resolve_collab_secret(secret)
    if not secret_value:
        raise CollabTokenError(
            "collab signing secret missing or too short "
            f"(need {MIN_SECRET_LEN}+ chars via NAVIN_COLLAB_SECRET "
            "or NAVIN_LICENSE_SIGNING_SECRET)"
        )
    mid = (member_id or "").strip()
    oid = (org_id or "").strip()
    if not mid or not oid:
        raise CollabTokenError("member_id and org_id are required")
    normalized_role = normalize_org_role(role) or "member"
    ttl = max(MIN_TTL_S, min(int(ttl_s), MAX_TTL_S))
    issued = int(now if now is not None else time.time())
    claims: dict[str, Any] = {
        "sub": mid,
        "org_id": oid,
        "role": normalized_role,
        "display_name": (display_name or mid).strip()[:120],
        "iat": issued,
        "exp": issued + ttl,
        "typ": "collab",
    }
    if chat_id:
        claims["chat_id"] = str(chat_id).strip()
    if host_device:
        claims["host_device"] = str(host_device).strip()[:64]
    payload = _b64url(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{payload}.{_sign(payload, secret_value)}"


def verify_collab_token(
    token: str,
    *,
    secret: str | None = None,
    now: int | None = None,
    expected_org_id: str | None = None,
    expected_chat_id: str | None = None,
) -> dict[str, Any] | None:
    """Verify and decode a collab token. Returns claims or None if invalid."""
    if not isinstance(token, str) or "." not in token:
        return None
    secret_value = resolve_collab_secret(secret)
    if not secret_value:
        return None
    # Compact form payload.signature (not JWT header.payload.sig).
    parts = token.split(".")
    if len(parts) != 2:
        return None
    payload, signature = parts
    if not payload or not signature:
        return None
    expected = _sign(payload, secret_value)
    try:
        if not hmac.compare_digest(signature, expected):
            return None
    except (TypeError, ValueError):
        return None
    try:
        claims = json.loads(_b64url_decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    if claims.get("typ") not in (None, "collab"):
        return None
    try:
        exp = int(claims.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    clock = int(now if now is not None else time.time())
    if exp <= clock:
        return None
    role = normalize_org_role(claims.get("role"))
    if role is None:
        return None
    sub = str(claims.get("sub") or "").strip()
    org_id = str(claims.get("org_id") or "").strip()
    if not sub or not org_id:
        return None
    if expected_org_id and org_id != expected_org_id.strip():
        return None
    chat_id = claims.get("chat_id")
    if expected_chat_id and chat_id and str(chat_id) != str(expected_chat_id):
        return None
    return {
        "sub": sub,
        "org_id": org_id,
        "role": role,
        "display_name": str(claims.get("display_name") or sub).strip()[:120],
        "iat": int(claims.get("iat") or 0),
        "exp": exp,
        "chat_id": str(chat_id).strip() if isinstance(chat_id, str) and chat_id.strip() else None,
        "host_device": str(claims.get("host_device") or "").strip() or None,
    }


def collab_identity_from_claims(claims: dict[str, Any]) -> dict[str, str]:
    """Normalize verified claims into a presence / ACL identity dict."""
    return {
        "member_id": str(claims.get("sub") or ""),
        "display_name": str(claims.get("display_name") or claims.get("sub") or ""),
        "role": str(claims.get("role") or "member"),
        "org_id": str(claims.get("org_id") or ""),
    }
