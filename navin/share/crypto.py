# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end encryption of a live share (same format as the guest page).

A share has one random 256-bit key. It is written into the link's
``#fragment`` (``https://relay.../s/<id>#<key>``), which browsers never send
to a server, so the relay only ever forwards ciphertext. Each frame is
``base64url(nonce[12] || AES-256-GCM(json))`` with the share id as associated
data: a frame cannot be replayed into another share.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key() -> str:
    """A fresh share key, as it appears after the ``#`` of the link."""
    return b64url(os.urandom(KEY_BYTES))


class ShareCipher:
    def __init__(self, key: str, share_id: str) -> None:
        raw = b64url_decode(key)
        if len(raw) != KEY_BYTES:
            raise ValueError("a share key is 32 bytes")
        self._aead = AESGCM(raw)
        self._aad = share_id.encode("ascii")

    def seal(self, payload: dict[str, Any]) -> str:
        nonce = os.urandom(NONCE_BYTES)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return b64url(nonce + self._aead.encrypt(nonce, data, self._aad))

    def open(self, frame: str) -> dict[str, Any]:
        """Decrypt a frame; raises ValueError on tampering or a wrong key."""
        try:
            raw = b64url_decode(frame)
            plain = self._aead.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], self._aad)
            payload = json.loads(plain)
        except Exception as exc:  # noqa: BLE001 - every failure means "not ours"
            raise ValueError("frame does not decrypt with this share key") from exc
        if not isinstance(payload, dict):
            raise ValueError("frame is not an object")
        return payload
