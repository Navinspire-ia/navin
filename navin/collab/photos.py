# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Teammate photos: small square pictures kept beside the config, never in it.

Settings > Team shrinks a picked photo in the browser (256 px) and sends it as
a data URL. It is kept as one file per teammate; the config only holds the
marker ``custom:<hash>``, so the identity every team exchange carries stays a
few bytes and a changed photo is told apart from the old one.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from navin.config.paths import get_runtime_subdir

PHOTO_MAX_BYTES = 256 * 1024
CUSTOM_PREFIX = "custom:"
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
_DATA_URL = re.compile(r"data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)")


class PhotoError(ValueError):
    """Not a photo Navin keeps: a small PNG, JPEG or WebP picture."""


def _kind(raw: bytes) -> str | None:
    """The picture's real type, read from its bytes (never from its label)."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def photos_dir() -> Path:
    return get_runtime_subdir("collab") / "photos"


def _file(member_id: str) -> Path:
    if not _ID.fullmatch(member_id or ""):
        raise PhotoError("invalid teammate id")
    return photos_dir() / f"{member_id}.img"


def _marker(raw: bytes) -> str:
    return CUSTOM_PREFIX + hashlib.sha256(raw).hexdigest()[:12]


def save_photo(member_id: str, data_url: str) -> str:
    """Keep *data_url* as *member_id*'s photo and return its marker."""
    match = _DATA_URL.fullmatch((data_url or "").strip())
    if match is None:
        raise PhotoError("a photo must be a PNG, JPEG or WebP picture")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group(1)), validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError
        raise PhotoError("this photo cannot be read") from exc
    if not raw or len(raw) > PHOTO_MAX_BYTES:
        raise PhotoError(f"a photo is {PHOTO_MAX_BYTES // 1024} KB at most")
    if _kind(raw) is None:
        raise PhotoError("a photo must be a PNG, JPEG or WebP picture")
    target = _file(member_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(raw)
    partial.replace(target)
    return _marker(raw)


def _read(member_id: str) -> bytes | None:
    try:
        return _file(member_id).read_bytes()
    except FileNotFoundError:
        return None


def photo_marker(member_id: str) -> str:
    """The marker of the photo kept for *member_id*, "" when there is none."""
    raw = _read(member_id)
    return _marker(raw) if raw and _kind(raw) else ""


def photo_data_url(member_id: str) -> str:
    """The photo kept for *member_id* as a data URL, "" when there is none."""
    raw = _read(member_id)
    kind = _kind(raw) if raw else None
    if kind is None or raw is None:
        return ""
    return f"data:{kind};base64,{base64.b64encode(raw).decode('ascii')}"


def prune_photos(keep: set[str]) -> None:
    """Drop the photos of teammates that are gone or went back to a face."""
    folder = photos_dir()
    if not folder.is_dir():
        return
    for path in folder.glob("*.img"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)


__all__ = [
    "CUSTOM_PREFIX",
    "PHOTO_MAX_BYTES",
    "PhotoError",
    "photo_data_url",
    "photo_marker",
    "photos_dir",
    "prune_photos",
    "save_photo",
]
