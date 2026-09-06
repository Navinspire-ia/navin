"""Local files for harvested and generated marketing media."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from navin.marketing.store import MarketingStore

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
_VIDEO_EXT = {".mp4", ".webm", ".mov"}
_AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac"}
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
}


def assets_root(store: MarketingStore) -> Path:
    root = store.root / "assets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def file_url(name: str) -> str:
    return f"/api/marketing?action=file&name={name}"


def mime_for(name: str) -> str:
    return _MIME.get(Path(name).suffix.lower(), "application/octet-stream")


def resolve_asset(store: MarketingStore, name: str) -> Path | None:
    clean = Path(name or "").name
    if not _SAFE_NAME.match(clean):
        return None
    path = (assets_root(store) / clean).resolve()
    try:
        path.relative_to(assets_root(store).resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def ingest_bytes(store: MarketingStore, data: bytes, *, hint: str, suffix: str) -> str:
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    if ext.lower() not in _MIME:
        ext = ".bin"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-")[:40] or "asset"
    name = f"{stem}-{uuid.uuid4().hex[:8]}{ext.lower()}"
    path = assets_root(store) / name
    path.write_bytes(data)
    return name


def ingest_local_file(store: MarketingStore, src: str | Path, *, hint: str) -> str | None:
    path = Path(src)
    if not path.is_file():
        return None
    suffix = path.suffix.lower() or ".bin"
    return ingest_bytes(store, path.read_bytes(), hint=hint, suffix=suffix)


def download_image(store: MarketingStore, url: str, *, hint: str, timeout_s: float = 8.0) -> dict[str, Any] | None:
    raw_url = (url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    request = Request(
        raw_url,
        headers={"User-Agent": "NavinMarketing/1.0 (+local desk harvest)"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - user-bound product URL
            data = response.read(2_500_000)
            content_type = str(response.headers.get("Content-Type") or "")
    except OSError:
        return {"url": raw_url, "preview": raw_url, "name": "", "path": ""}
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in _IMAGE_EXT:
        if "png" in content_type:
            suffix = ".png"
        elif "webp" in content_type:
            suffix = ".webp"
        elif "gif" in content_type:
            suffix = ".gif"
        elif "svg" in content_type:
            suffix = ".svg"
        else:
            suffix = ".jpg"
    name = ingest_bytes(store, data, hint=hint, suffix=suffix)
    return {"url": raw_url, "preview": file_url(name), "name": name, "path": str(assets_root(store) / name)}


def copy_generated(store: MarketingStore, src: str | Path, *, hint: str) -> dict[str, Any] | None:
    name = ingest_local_file(store, src, hint=hint)
    if not name:
        return None
    path = assets_root(store) / name
    return {"url": "", "preview": file_url(name), "name": name, "path": str(path)}


def is_previewable(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return suffix in _IMAGE_EXT or suffix in _VIDEO_EXT or suffix in _AUDIO_EXT
