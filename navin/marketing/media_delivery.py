"""Expose selected studio media through the user's configured public Navin URL."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from navin.marketing.assets import mime_for
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore


@lru_cache(maxsize=128)
def _source_metadata(path: str, size: int, modified_ns: int, changed_ns: int, video: bool) -> tuple[str, float]:
    """Reuse read-only inspection until the source's filesystem identity changes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    duration = 0.0
    probe = shutil.which("ffprobe") if video else None
    if probe:
        try:
            result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path], capture_output=True, timeout=10, check=True)
            duration = float((json.loads(result.stdout).get("format") or {}).get("duration") or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return digest.hexdigest(), duration


def validate_media_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise MarketingError("Media hosting requires the public HTTPS origin of this Navin installation, without path or credentials", status=400)
    if parsed.hostname in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".localhost"):
        raise MarketingError("Social networks cannot fetch media from localhost", status=400)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise MarketingError("Social networks require a public media host", status=400)
    return value


def _local_source(store: MarketingStore, row: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    media = dict(row)
    creative = next((item for item in store.load_creatives() if
                     (row.get("creative_id") and item.get("id") == row["creative_id"])
                     or (not row.get("creative_id") and item.get("content_id") == row.get("id") and item.get("kind") in {"image", "banner", "video"})), {})
    kind = str(row.get("media_type") or creative.get("kind") or "")
    if kind == "banner":
        kind = "image"
    if kind in {"image", "video"}:
        media["media_type"] = kind
    raw = str(row.get("media_path") or creative.get("path") or "").strip()
    if not media.get("media_url") and str(creative.get("url") or "").startswith("https://"):
        media["media_url"] = creative["url"]
    if not raw:
        return media, None
    from navin.agent.tools.context import current_request_context
    from navin.config.loader import load_config

    context = current_request_context()
    workspace = (context.workspace if context else None) or load_config().workspace_path
    allowed = [store.root / "assets", store.root / "montage" / "exports", Path(workspace) / "marketing" / "montage" / "exports"]
    source = Path(raw).expanduser().resolve()
    if not any(source.is_relative_to(root.resolve()) for root in allowed) or not source.is_file():
        raise MarketingError("Import the media into the studio before publishing it", status=400)
    mime = mime_for(source.name)
    if not mime.startswith(("image/", "video/")) or mime == "image/svg+xml":
        raise MarketingError("Publishing requires a raster image or a supported video file", status=400)
    media.update({"media_path": str(source), "mime_type": mime, "media_type": "video" if mime.startswith("video/") else "image"})
    return media, source


def resolve_publish_media(store: MarketingStore, row: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
    """Plan the URL without writes, or stage the selected media for a real post."""
    media, source = _local_source(store, row)
    if not source:
        return media
    stamp = source.stat()
    digest, duration = _source_metadata(str(source), stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns, media.get("media_type") == "video")
    if duration > 0:
        media["duration_s"] = duration
    if media.get("media_url"):
        return media
    base = validate_media_base_url(str(store.load_settings().get("media_base_url") or ""))
    if not base:
        return media
    tiktok_photo = media.get("media_type") == "image" and row.get("channel") == "tiktok"
    jpeg = media.get("media_type") == "image" and (tiktok_photo or (row.get("channel") == "instagram" and source.suffix.lower() not in {".jpg", ".jpeg"}))
    name = f"post-{digest[:24]}{'-tt' if tiktok_photo else ''}{'.jpg' if jpeg else source.suffix.lower()}"
    target = store.root / "assets" / name
    media.update({"media_url": base + "/api/marketing?" + urlencode({"action": "file", "name": name}),
                  "mime_type": "image/jpeg" if jpeg else media["mime_type"]})
    if not create:
        return media
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".publish-media-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                if jpeg:
                    from PIL import Image, ImageOps

                    with Image.open(source) as image:
                        oriented = ImageOps.exif_transpose(image).convert("RGBA")
                        if tiktok_photo:
                            oriented.thumbnail((1080, 1080), Image.Resampling.LANCZOS)
                        background = Image.new("RGBA", oriented.size, "white")
                        background.alpha_composite(oriented)
                        background.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
                else:
                    with source.open("rb") as original:
                        shutil.copyfileobj(original, output)
            os.replace(temporary, target)
        except Exception as exc:
            raise MarketingError("The selected media could not be prepared for publishing", status=400) from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    media["media_path"] = str(target)
    return media
