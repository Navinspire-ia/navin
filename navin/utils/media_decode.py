# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared helpers for decoding ``data:...;base64,...`` URLs to disk.

Historically lived in ``navin.api.server``; now shared by the WebSocket
channel so the ``api`` + ``websocket`` ingress paths apply the same parsing,
size guard, and filesystem layout.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import uuid
from pathlib import Path

from navin.utils.helpers import safe_filename

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_FILE_SIZE = DEFAULT_MAX_BYTES

_DATA_URL_RE = re.compile(r"^data:([^;,]+)(?:;[^,]*)*;base64,(.+)$", re.DOTALL)
_MIME_EXTENSION_OVERRIDES = {
    # Python's ``mimetypes`` maps browser-recorded audio/webm to ``.weba`` and
    # audio/ogg to ``.oga`` on macOS. Some transcription APIs validate by the
    # file extension and accept the canonical container extensions instead.
    "application/ogg": ".ogg",
    "audio/ogg": ".ogg",
    "audio/mpga": ".mpga",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    "audio/vnd.wave": ".wav",
    # Chat audio attachments are recognized by extension downstream, so every
    # accepted MIME needs a canonical one (``audio/mp3`` and ``audio/wave``
    # have none in the stdlib table).
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wave": ".wav",
    "audio/x-flac": ".flac",
    # Video extensions are load-bearing: frame extraction recognizes a video
    # attachment by extension, and ``mimetypes.guess_extension`` answers from
    # the host mime database (or the Windows registry), so it cannot be
    # trusted to return the canonical container extension everywhere.
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "application/toml": ".toml",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/x-yaml": ".yaml",
    "application/xhtml+xml": ".html",
    "application/xml": ".xml",
    "application/yaml": ".yaml",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/xml": ".xml",
    "text/yaml": ".yaml",
}


class FileSizeExceededError(Exception):
    """Raised when a decoded payload exceeds the caller's size limit."""


FileSizeExceeded = FileSizeExceededError


def save_base64_data_url(
    data_url: str,
    media_dir: Path,
    *,
    max_bytes: int | None = None,
    filename: str | None = None,
) -> str | None:
    """Decode a ``data:<mime>;base64,<payload>`` URL and persist it.

    Returns the absolute path on success, ``None`` when the URL shape or the
    base64 payload itself is malformed. Raises :class:`FileSizeExceeded`
    when the decoded payload is larger than ``max_bytes`` (default 10 MB).
    """
    m = _DATA_URL_RE.match(data_url)
    if not m:
        return None
    mime_type, b64_payload = m.group(1).strip().lower(), m.group(2)
    try:
        raw = base64.b64decode(b64_payload, validate=True)
    except Exception:
        return None
    if not raw:
        return None
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise FileSizeExceeded(f"File exceeds {limit // (1024 * 1024)}MB limit")
    ext = _MIME_EXTENSION_OVERRIDES.get(mime_type) or mimetypes.guess_extension(mime_type) or ".bin"
    base = safe_filename(filename or "")
    stem = Path(base).stem[:80] if base else ""
    saved_name = f"{uuid.uuid4().hex[:12]}_{stem}{ext}" if stem else f"{uuid.uuid4().hex[:12]}{ext}"
    dest = media_dir / safe_filename(saved_name)
    dest.write_bytes(raw)
    return str(dest)
