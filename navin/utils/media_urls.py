# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Fetch the image and video links a user pastes, so the model can actually see them.

A pasted link used to be inert text. `https://.../shot.png` reached the model as
a string it could only guess about, and a YouTube link was worse: the video
extension check recognised it, then frame extraction failed with "file not
found" because nothing had downloaded it.

Only links that are unambiguously media are fetched. A URL is downloaded when
its path ends in a known image or video extension, or when its host is a video
site yt-dlp handles. Ordinary links stay text, because turning every link in a
message into a download would break normal browsing and citation behaviour.

Everything is bounded on purpose: how many links per message, how many bytes per
file, how long a download may take, and what resolution is pulled from a video
site. A chat turn must not become an unbounded transfer.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from navin.utils.video_frames import VIDEO_EXTENSIONS

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
)

# Hosts whose pages are a video rather than a document. The path carries no
# extension, so only the host can tell us this link is worth downloading.
VIDEO_SITES: frozenset[str] = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "music.youtube.com",
        "vimeo.com",
        "player.vimeo.com",
        "dailymotion.com",
        "dai.ly",
        "twitch.tv",
        "clips.twitch.tv",
        "streamable.com",
        "loom.com",
        "ted.com",
        "bilibili.com",
        "rumble.com",
    }
)

_URL_RE = re.compile(r"https?://[^\s\"'`<>\\]+", re.IGNORECASE)
# Markdown and prose routinely end a URL with punctuation that is not part of it.
_TRAILING_PUNCTUATION = ").,;:!?'\"]}>"

# Per message, not per conversation: enough for "compare these two screenshots",
# small enough that a link-heavy message cannot turn into a download queue.
MAX_URLS_PER_MESSAGE = 3
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60.0
# Frames are downscaled to 1280px anyway, so a bigger source buys nothing and
# costs bandwidth and disk.
VIDEO_SITE_MAX_HEIGHT = 480
VIDEO_SITE_MAX_DURATION_S = 3600


@dataclass(slots=True)
class MediaLink:
    """One URL worth downloading, and what we expect to find behind it."""

    url: str
    kind: str  # "image" | "video" | "video_site"


@dataclass(slots=True)
class FetchedMedia:
    """Outcome for one link, in a form safe to show the model."""

    url: str
    kind: str
    path: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.path)


def cache_root() -> Path:
    """Where downloaded media lives.

    Outside the workspace on purpose: fetching a link a user pasted must not
    drop files into the repository they are working on.
    """
    return Path.home() / ".navin" / "cache" / "media-urls"


def _strip_trailing_punctuation(url: str) -> str:
    while url and url[-1] in _TRAILING_PUNCTUATION:
        # A closing paren can legitimately belong to the URL (Wikipedia), but
        # only when the URL also opened one.
        if url[-1] == ")" and url.count("(") > url.count(")"):
            break
        url = url[:-1]
    return url


def _host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def classify_url(url: str) -> str | None:
    """``"image"``, ``"video"``, ``"video_site"``, or ``None`` for a plain link."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    suffix = Path(parsed.path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    host = _host_of(url)
    if host in VIDEO_SITES or any(host.endswith(f".{site}") for site in VIDEO_SITES):
        return "video_site"
    return None


def find_media_links(text: str, *, limit: int = MAX_URLS_PER_MESSAGE) -> list[MediaLink]:
    """Media URLs mentioned in *text*, de-duplicated, in order of appearance."""
    if not text:
        return []
    found: list[MediaLink] = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(text):
        url = _strip_trailing_punctuation(raw)
        if not url or url in seen:
            continue
        kind = classify_url(url)
        if kind is None:
            continue
        seen.add(url)
        found.append(MediaLink(url=url, kind=kind))
        if len(found) >= limit:
            break
    return found


def _destination(url: str, suffix: str) -> Path:
    """Cache path for *url*, keeping the original file name where there is one.

    The name survives into the note the model reads, so a hash would leave it
    reasoning about "2bccd861.mp4" instead of "demo.mp4".
    """
    from navin.utils.video_frames import safe_stem

    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    stem = safe_stem(Path(urlparse(url).path).stem) if urlparse(url).path else ""
    name = f"{stem}{suffix}" if stem and stem != "video" else f"{digest}{suffix}"
    # The digest stays in the directory, so two URLs with the same file name
    # cannot overwrite each other.
    return cache_root() / digest / name


def _suffix_for(url: str, content_type: str | None, kind: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    known = IMAGE_EXTENSIONS if kind == "image" else VIDEO_EXTENSIONS
    if suffix in known:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return ".jpg" if guessed == ".jpe" else guessed
    return ".jpg" if kind == "image" else ".mp4"


async def _download_direct(link: MediaLink) -> FetchedMedia:
    """Fetch a URL that points straight at an image or video file."""
    import httpx

    from navin.security.network import (
        PinnedDNSAsyncTransport,
        UnsafeURLRequestError,
        resolve_url_target,
    )

    ok, error, _ips = resolve_url_target(link.url)
    if not ok:
        return FetchedMedia(link.url, link.kind, reason=f"blocked: {error}")

    limit = MAX_IMAGE_BYTES if link.kind == "image" else MAX_VIDEO_BYTES
    expected = "image/" if link.kind == "image" else "video/"
    try:
        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT_S,
            follow_redirects=True,
            transport=PinnedDNSAsyncTransport(),
        ) as client:
            async with client.stream(
                "GET", link.url, headers={"User-Agent": "navin/1.0"}
            ) as response:
                if response.status_code >= 400:
                    return FetchedMedia(
                        link.url, link.kind, reason=f"HTTP {response.status_code}"
                    )
                content_type = response.headers.get("content-type", "")
                head = content_type.split(";", 1)[0].strip().lower()
                if head and not head.startswith(expected):
                    return FetchedMedia(
                        link.url, link.kind, reason=f"not {link.kind} content ({head})"
                    )
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    return FetchedMedia(
                        link.url,
                        link.kind,
                        reason=f"too large ({int(declared) // (1024 * 1024)} MB)",
                    )
                dest = _destination(link.url, _suffix_for(link.url, content_type, link.kind))
                dest.parent.mkdir(parents=True, exist_ok=True)
                total = 0
                with dest.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            handle.close()
                            dest.unlink(missing_ok=True)
                            return FetchedMedia(
                                link.url,
                                link.kind,
                                reason=f"too large (over {limit // (1024 * 1024)} MB)",
                            )
                        handle.write(chunk)
    except UnsafeURLRequestError as exc:
        return FetchedMedia(link.url, link.kind, reason=f"blocked: {exc}")
    except (httpx.HTTPError, OSError) as exc:
        return FetchedMedia(link.url, link.kind, reason=f"download failed: {exc}")

    if total == 0:
        dest.unlink(missing_ok=True)
        return FetchedMedia(link.url, link.kind, reason="empty response")
    return FetchedMedia(link.url, link.kind, path=str(dest))


def _download_video_site_sync(url: str) -> FetchedMedia:
    """Pull a bounded copy of a video-site page with yt-dlp (blocking)."""
    try:
        import yt_dlp
    except ImportError:
        return FetchedMedia(url, "video_site", reason="yt-dlp is not installed")

    from navin.montage.detect import find_ffmpeg

    target = cache_root() / hashlib.sha256(url.encode()).hexdigest()[:16]
    target.mkdir(parents=True, exist_ok=True)

    options: dict[str, object] = {
        # Single-file formats first: merging streams would need a second pass.
        "format": (
            f"b[height<={VIDEO_SITE_MAX_HEIGHT}]/"
            f"bv*[height<={VIDEO_SITE_MAX_HEIGHT}]+ba/b"
        ),
        "outtmpl": str(target / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": MAX_VIDEO_BYTES,
        "socket_timeout": 30,
        "retries": 1,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration < {VIDEO_SITE_MAX_DURATION_S}"
        ),
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # yt-dlp raises a wide family of its own errors
        reason = str(exc).strip().splitlines()[-1] if str(exc).strip() else "download failed"
        return FetchedMedia(url, "video_site", reason=reason[:200])

    if not isinstance(info, dict):
        return FetchedMedia(url, "video_site", reason="nothing downloadable at this link")
    # A filtered-out video (too long) returns info without a file on disk.
    downloaded = sorted(p for p in target.iterdir() if p.is_file() and p.stat().st_size > 0)
    if not downloaded:
        return FetchedMedia(
            url,
            "video_site",
            reason=f"skipped (longer than {VIDEO_SITE_MAX_DURATION_S // 60} min, or no usable format)",
        )
    return FetchedMedia(url, "video_site", path=str(downloaded[0]))


async def fetch_media_link(link: MediaLink) -> FetchedMedia:
    """Download one link, whichever kind it is."""
    if link.kind == "video_site":
        return await asyncio.to_thread(_download_video_site_sync, link.url)
    return await _download_direct(link)


def describe_fetch(result: FetchedMedia) -> str:
    """One line telling the model what was retrieved, or why nothing was."""
    if result.ok:
        return f"[fetched from {result.url} -> attached below]"
    return (
        f"[link not fetched: {result.url} - {result.reason}. "
        "Do not describe or guess its content.]"
    )


@dataclass(slots=True)
class MediaURLExpansion:
    text: str
    media: list[str] = field(default_factory=list)
    results: list[FetchedMedia] = field(default_factory=list)


async def expand_media_urls(
    text: str,
    media: list[str] | None,
    *,
    limit: int = MAX_URLS_PER_MESSAGE,
) -> MediaURLExpansion:
    """Download the media links in *text* and append them to *media*.

    Videos are appended as files; turning them into frames is the caller's next
    step (``expand_video_attachments``), which already knows how.
    """
    current = list(media or [])
    links = find_media_links(text, limit=limit)
    if not links:
        return MediaURLExpansion(text=text, media=current)

    results = await asyncio.gather(*(fetch_media_link(link) for link in links))
    notes: list[str] = []
    for result in results:
        if result.ok and result.path:
            current.append(result.path)
        else:
            logger.info("Media link not fetched: {} ({})", result.url, result.reason)
        notes.append(describe_fetch(result))

    annotated = f"{text}\n\n" + "\n".join(notes) if notes else text
    return MediaURLExpansion(text=annotated, media=current, results=list(results))
