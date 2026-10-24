# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn template props that name local files into loopback URLs.

Any string prop that resolves to an existing image, video or font file is
served by the job's :class:`~navin.montage.motion.server.MotionServer`.
Video layers go through a *seek proxy* first: an H.264 copy at the render frame
rate with a short GOP and no audio. The proxy decodes in every Chromium build,
lands each seek exactly on a frame (timestamps are ``k / fps``) and keeps a
per-frame seek cheap, which is what makes video textures deterministic.
Proxies are cached under ``~/.navin/montage/motion-cache/proxies``.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.montage import MONTAGE_HOME
from navin.montage.motion.brand import FONT_SUFFIXES, IMAGE_SUFFIXES, BrandKit

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"})
PROXY_DIR = MONTAGE_HOME / "motion-cache" / "proxies"
_PROXY_MAX_SECONDS = 600
_PROXY_TIMEOUT_S = 900.0

PathResolver = Callable[[str], "Path | None"]


class MotionMediaError(ValueError):
    """A media prop could not be prepared."""


def default_resolver(base_dir: Path | None) -> PathResolver:
    """Resolve absolute paths, or paths relative to *base_dir*; files only."""

    def resolve(value: str) -> Path | None:
        path = Path(value).expanduser()
        if not path.is_absolute():
            if base_dir is None:
                return None
            path = Path(base_dir) / path
        try:
            path = path.resolve()
        except OSError:
            return None
        return path if path.is_file() else None

    return resolve


def _looks_like_path(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 4096 or "\n" in text:
        return False
    if text.startswith(("http://", "https://", "data:", "blob:")):
        return False
    suffix = Path(text).suffix.lower()
    return suffix in IMAGE_SUFFIXES | VIDEO_SUFFIXES | FONT_SUFFIXES


async def make_video_proxy(
    source: Path,
    *,
    fps: int,
    max_dim: int,
    ffmpeg: str,
    cache_dir: Path = PROXY_DIR,
) -> Path:
    """Seek-friendly H.264 copy of *source* (cached by content identity)."""
    stat = source.stat()
    key = hashlib.sha256(
        f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{fps}|{max_dim}|v2".encode()
    ).hexdigest()[:24]
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{source.stem[:40]}-{key}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return target
    staging = target.with_name(f".{target.stem}.part.mp4")
    scale = (
        f"scale='if(gt(iw,ih),min({max_dim},iw),-2)':'if(gt(iw,ih),-2,min({max_dim},ih))'"
        ":flags=lanczos,pad=ceil(iw/2)*2:ceil(ih/2)*2"
    )
    from navin.montage.ffmpeg_runner import run_process

    result = await run_process(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            str(_PROXY_MAX_SECONDS),
            "-an",
            "-sn",
            "-vf",
            f"{scale},fps={fps},format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "14",
            "-g",
            "6",
            "-bf",
            "0",
            "-movflags",
            "+faststart",
            str(staging),
        ],
        timeout_s=_PROXY_TIMEOUT_S,
    )
    if not result.ok or not staging.is_file():
        try:
            staging.unlink()
        except OSError:
            pass
        raise MotionMediaError(
            f"could not prepare video layer {source.name}: {result.stderr or 'ffmpeg failed'}"
        )
    staging.replace(target)
    return target


async def prepare_props(
    props: dict[str, Any],
    *,
    server: Any,
    resolve: PathResolver,
    fps: int,
    max_dim: int,
    ffmpeg: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Deep copy of *props* with local media replaced by loopback URLs.

    Returns ``(props, used_paths)``. A string that names a media file which
    does not exist raises :class:`MotionMediaError` so a typo never renders a
    silently empty layer.
    """
    used: list[str] = []
    proxies: dict[Path, str] = {}

    async def convert(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: await convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [await convert(v) for v in value]
        if not isinstance(value, str) or not _looks_like_path(value):
            return value
        found = resolve(value)
        if found is None:
            raise MotionMediaError(f"media file not found or not allowed: {value}")
        used.append(str(found))
        if found.suffix.lower() in VIDEO_SUFFIXES:
            if found not in proxies:
                if not ffmpeg:
                    raise MotionMediaError("ffmpeg is required for video layers")
                proxy = await make_video_proxy(found, fps=fps, max_dim=max_dim, ffmpeg=ffmpeg)
                proxies[found] = server.register_file(proxy)
            return proxies[found]
        return server.register_file(found)

    converted = await convert(copy.deepcopy(props or {}))
    return converted, used


def brand_payload(kit: BrandKit, server: Any) -> dict[str, Any]:
    """The brand kit as the page sees it (``window.__NAVIN_BRAND__``)."""
    payload: dict[str, Any] = {
        "name": kit.name,
        "colors": dict(kit.colors),
        "fonts": dict(kit.fonts),
        "tagline": kit.tagline,
        "url": kit.url,
        "logo_url": "",
        "logo_dark_url": "",
        "font_faces": [],
    }
    if kit.logo_path and Path(kit.logo_path).is_file():
        payload["logo_url"] = server.register_file(Path(kit.logo_path))
    if kit.logo_dark_path and Path(kit.logo_dark_path).is_file():
        payload["logo_dark_url"] = server.register_file(Path(kit.logo_dark_path))
    for face in kit.font_faces():
        payload["font_faces"].append(
            {
                "family": face["family"],
                "url": server.register_file(Path(face["path"])),
                "weight": face["weight"],
                "style": face["style"],
            }
        )
    return payload


async def probe_seconds(path: Path, ffmpeg: str | None) -> float | None:
    """Duration of a media file in seconds (ffprobe, else ffmpeg header)."""
    from navin.montage.assemble import (
        find_ffprobe,
        probe_duration,
        probe_duration_with_ffmpeg,
    )

    ffprobe = await asyncio.to_thread(find_ffprobe, ffmpeg)
    if ffprobe:
        return await probe_duration(str(path), ffprobe)
    if ffmpeg:
        return await probe_duration_with_ffmpeg(str(path), ffmpeg)
    return None


__all__ = [
    "PROXY_DIR",
    "VIDEO_SUFFIXES",
    "MotionMediaError",
    "brand_payload",
    "default_resolver",
    "make_video_proxy",
    "prepare_props",
    "probe_seconds",
]
