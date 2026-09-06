"""Measure media files (duration, dimensions, fps, streams) for the editor.

The timeline UI and the agent both need the real length of a clip before
they can trim it or place a crossfade: a guessed 4 s clip renders as a
frozen last frame. ffprobe's JSON output is the source of truth; hosts that
only ship the bundled ``ffmpeg`` fall back to the container header it
prints when asked to open a file with no output.

Results are cached on (path, size, mtime) so listing a gallery or reloading
a timeline does not re-probe untouched files.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from navin.montage.assemble import (
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    find_ffprobe,
    parse_ffmpeg_duration,
)
from navin.montage.ffmpeg_runner import run_process

AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"})

_CACHE_LIMIT = 512
_cache: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()


class ProbeError(ValueError):
    """The file could not be measured (missing, unreadable, or no toolchain)."""


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """What the editor needs to know about one media file."""

    path: str
    kind: str  # video | image | audio | file
    size_bytes: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_video: bool = False
    has_audio: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    #: Which tool produced the numbers: ffprobe, ffmpeg (header only), or none.
    source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def media_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return "file"


def _fraction(value: str | None) -> float | None:
    raw = (value or "").strip()
    if not raw or raw in {"0/0", "N/A"}:
        return None
    numerator, separator, denominator = raw.partition("/")
    try:
        if separator:
            bottom = float(denominator)
            if bottom == 0:
                return None
            result = float(numerator) / bottom
        else:
            result = float(raw)
    except ValueError:
        return None
    return round(result, 3) if result > 0 else None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def media_info_from_ffprobe(path: str | Path, payload: Any, *, size_bytes: int) -> MediaInfo:
    """Build :class:`MediaInfo` from ffprobe ``-print_format json`` output.

    Pure so the mapping can be tested with recorded ffprobe documents.
    """
    kind = media_kind(path)
    if not isinstance(payload, dict):
        raise ProbeError("ffprobe returned no JSON document")
    streams = [row for row in payload.get("streams") or [] if isinstance(row, dict)]
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    duration = _float(fmt.get("duration"))
    if duration is None and video is not None:
        duration = _float(video.get("duration"))
    if duration is None and audio is not None:
        duration = _float(audio.get("duration"))
    fps: float | None = None
    if video is not None:
        fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    if kind == "image":
        # A still is a one-frame "video" for ffprobe; its duration is meaningless.
        duration = None
        fps = None
    is_video_stream = video is not None and (
        kind != "image" and (video.get("codec_name") or "") not in {"png", "mjpeg", "webp", "bmp"}
        or kind == "video"
    )
    return MediaInfo(
        path=str(path),
        kind=kind,
        size_bytes=size_bytes,
        duration=round(duration, 3) if duration else None,
        width=_int(video.get("width")) if video else None,
        height=_int(video.get("height")) if video else None,
        fps=fps,
        has_video=bool(is_video_stream),
        has_audio=audio is not None,
        video_codec=str(video.get("codec_name")) if video and video.get("codec_name") else None,
        audio_codec=str(audio.get("codec_name")) if audio and audio.get("codec_name") else None,
        container=str(fmt.get("format_name")) if fmt.get("format_name") else None,
        source="ffprobe",
    )


_HEADER_VIDEO_RE = re.compile(
    r"Stream #\d+:\d+(?:\[[^\]]*\])?(?:\([^)]*\))?: Video: (?P<codec>[a-z0-9_]+)"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)
_HEADER_AUDIO_RE = re.compile(
    r"Stream #\d+:\d+(?:\[[^\]]*\])?(?:\([^)]*\))?: Audio: (?P<codec>[a-z0-9_]+)",
    re.IGNORECASE,
)
_HEADER_SIZE_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
_HEADER_FPS_RE = re.compile(r"\b(\d+(?:\.\d+)?) fps\b")


def media_info_from_ffmpeg_header(
    path: str | Path, stderr: str, *, size_bytes: int
) -> MediaInfo:
    """Read what ``ffmpeg -i <file>`` prints when no ffprobe is installed.

    The bundled toolchain the montage installer places in ``~/.navin/montage``
    ships only ``ffmpeg``; its header still names the codecs, the frame size,
    the frame rate and the duration, which is everything the editor needs.
    """
    kind = media_kind(path)
    duration = parse_ffmpeg_duration(stderr)
    width = height = None
    fps: float | None = None
    video_codec = audio_codec = None
    for line in (stderr or "").splitlines():
        video = _HEADER_VIDEO_RE.search(line)
        if video and video_codec is None:
            video_codec = video.group("codec").lower()
            rest = video.group("rest")
            size = _HEADER_SIZE_RE.search(rest)
            if size:
                width, height = int(size.group(1)), int(size.group(2))
            rate = _HEADER_FPS_RE.search(rest)
            if rate:
                fps = _float(rate.group(1))
            continue
        audio = _HEADER_AUDIO_RE.search(line)
        if audio and audio_codec is None:
            audio_codec = audio.group("codec").lower()
    if kind == "image":
        duration = None
        fps = None
    return MediaInfo(
        path=str(path),
        kind=kind,
        size_bytes=size_bytes,
        duration=round(duration, 3) if duration else None,
        width=width,
        height=height,
        fps=fps,
        has_video=video_codec is not None and kind != "image" or kind == "video",
        has_audio=audio_codec is not None,
        video_codec=video_codec,
        audio_codec=audio_codec,
        source="ffmpeg",
    )


def _cache_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns)


def cached_media_info(path: str | Path) -> dict[str, Any] | None:
    """Return the cached probe for *path* if the file has not changed."""
    try:
        key = _cache_key(Path(path))
    except OSError:
        return None
    hit = _cache.get(key)
    if hit is not None:
        _cache.move_to_end(key)
    return dict(hit) if hit is not None else None


def _remember(key: tuple[str, int, int], info: dict[str, Any]) -> None:
    _cache[key] = dict(info)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)


def clear_probe_cache() -> None:
    _cache.clear()


async def probe_media(
    path: str | Path,
    *,
    ffmpeg: str | None = None,
    timeout_s: float = 20.0,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Measure *path* with ffprobe (or the ffmpeg header) and return a dict.

    Raises :class:`ProbeError` when the file does not exist. A missing
    toolchain does not raise: the result carries ``source="none"`` so the
    caller can still show the file and explain why there is no duration.
    """
    target = Path(path).expanduser()
    if not target.is_file():
        raise ProbeError(f"media file not found: {path}")
    key = _cache_key(target)
    if use_cache:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return dict(hit)
    size_bytes = key[1]
    kind = media_kind(target)
    from navin.montage import detect

    # PATH lookups can take seconds on WSL / network mounts; keep them off the
    # gateway loop so other requests are not stalled by a probe.
    binary = ffmpeg or await asyncio.to_thread(detect.find_ffmpeg)
    ffprobe = await asyncio.to_thread(find_ffprobe, binary)
    info: MediaInfo | None = None
    if ffprobe:
        result = await run_process(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(target),
            ],
            timeout_s=timeout_s,
        )
        if result.ok:
            try:
                info = media_info_from_ffprobe(
                    target, json.loads(result.stdout or "{}"), size_bytes=size_bytes
                )
            except (json.JSONDecodeError, ProbeError):
                info = None
    if info is None and binary:
        result = await run_process(
            [binary, "-hide_banner", "-nostdin", "-i", str(target)],
            timeout_s=timeout_s,
            capture_stdout=False,
        )
        info = media_info_from_ffmpeg_header(target, result.raw_stderr, size_bytes=size_bytes)
    if info is None:
        info = MediaInfo(path=str(target), kind=kind, size_bytes=size_bytes, source="none")
    payload = info.to_dict()
    _remember(key, payload)
    return payload


async def probe_many(
    paths: list[str | Path],
    *,
    ffmpeg: str | None = None,
    concurrency: int = 4,
) -> dict[str, dict[str, Any]]:
    """Probe several files at once; unreadable ones are simply absent."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: dict[str, dict[str, Any]] = {}

    async def one(item: str | Path) -> None:
        async with semaphore:
            try:
                results[str(item)] = await probe_media(item, ffmpeg=ffmpeg)
            except ProbeError:
                return

    await asyncio.gather(*(one(item) for item in paths))
    return results


def probe_media_sync(path: str | Path, *, ffmpeg: str | None = None) -> dict[str, Any]:
    """Blocking helper for code paths that are not async (CLI, tests)."""
    return asyncio.run(probe_media(path, ffmpeg=ffmpeg))


def file_is_unchanged(path: str | Path, cached: dict[str, Any]) -> bool:
    """Cheap check used by callers that keep their own copy of a probe."""
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return int(cached.get("size_bytes") or -1) == stat.st_size


__all__ = [
    "AUDIO_SUFFIXES",
    "MediaInfo",
    "ProbeError",
    "cached_media_info",
    "clear_probe_cache",
    "file_is_unchanged",
    "media_info_from_ffmpeg_header",
    "media_info_from_ffprobe",
    "media_kind",
    "probe_many",
    "probe_media",
    "probe_media_sync",
]
