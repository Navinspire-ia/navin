# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Native social publishing protocols with explicit media and processing states.

Sources: Reddit /dev/api, Meta's official Postman collections, Microsoft Learn
LinkedIn Images/Videos/Posts APIs and TikTok's Content Posting API reference.
HTTP transports are injected. No adapter retries a publication request.
"""

from __future__ import annotations

import ipaddress
import json
import math
import mimetypes
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore

HttpFn = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]
SOCIAL_CHANNELS = frozenset({"reddit", "instagram", "tiktok", "linkedin", "facebook"})
GRAPH_VERSION = "v23.0"
LINKEDIN_VERSION = "202607"
LOCAL_VIDEO_LIMIT = 128 * 1024 * 1024
LOCAL_IMAGE_LIMIT = 20 * 1024 * 1024
TOKEN_KEYS = {
    "reddit": "reddit_access_token", "instagram": "instagram_access_token",
    "tiktok": "tiktok_access_token", "linkedin": "linkedin_token", "facebook": "facebook_page_token",
}


class SocialPublishError(MarketingError):
    def __init__(
        self, message: str, *, status: int = 400, code: str = "",
        retry_after_s: float | None = None, outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message, status=status)
        self.code = code
        self.retry_after_s = retry_after_s
        self.outcome_unknown = outcome_unknown


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def public_url(value: Any) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise SocialPublishError("media URL is invalid") from exc
    if (
        parsed.scheme != "https" or not host or parsed.username or parsed.password
        or port not in {None, 443} or parsed.fragment
        or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
        or any(ord(char) < 32 for char in url)
    ):
        raise SocialPublishError("media URL must be a public HTTPS URL without credentials")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise SocialPublishError("media URL must use a public hostname") from None
    else:
        if not address.is_global:
            raise SocialPublishError("private or local media URLs cannot be published")
    return url


def _upload_url(value: Any, domains: tuple[str, ...]) -> str:
    url = public_url(value)
    host = urlsplit(url).hostname or ""
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        raise SocialPublishError("provider returned an upload URL outside its supported hosts", status=502)
    return url


def _token(store: MarketingStore, channel: str) -> str:
    value = store.get_secret(TOKEN_KEYS[channel])
    if not value:
        raise SocialPublishError(f"connect {channel} before publishing", status=409, code="not_connected")
    return value


def _request(
    http: HttpFn, method: str, url: str, headers: dict[str, str], data: bytes | None,
    *, mutation: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    try:
        status, response_headers, body = http(method, url, headers, data)
    except (MarketingError, OSError, TimeoutError) as exc:
        raise SocialPublishError(
            "social provider transport failed; its response was not received", status=getattr(exc, "status", 502),
            code="transport_error", outcome_unknown=mutation,
        ) from exc
    response_headers = {str(key).lower(): str(value) for key, value in response_headers.items()}
    if not 200 <= status < 300:
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        details = error if isinstance(error, dict) else payload if isinstance(payload, dict) else {}
        message = str(details.get("message") or details.get("error_description") or (error if isinstance(error, str) else "provider rejected the request"))
        code = str(details.get("code") or error or status)
        retry = _number(response_headers.get("retry-after"))
        raise SocialPublishError(
            f"social provider HTTP {status}: {message[:240]}", status=status if status < 500 else 502,
            code=code, retry_after_s=retry, outcome_unknown=mutation and status >= 500,
        )
    return status, response_headers, body


def _json_request(
    http: HttpFn, method: str, url: str, token: str, payload: dict[str, Any] | None = None,
    *, form: bool = False, mutation: bool = False, headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
    data = None
    if payload is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json; charset=UTF-8"
        data = urlencode(payload).encode() if form else json.dumps(payload, ensure_ascii=False).encode()
    _, response_headers, body = _request(http, method, url, request_headers, data, mutation=mutation)
    try:
        result = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise SocialPublishError("social provider returned invalid JSON", status=502, outcome_unknown=mutation) from exc
    if not isinstance(result, dict):
        raise SocialPublishError("social provider returned an invalid response", status=502, outcome_unknown=mutation)
    error = result.get("error")
    if isinstance(error, dict) and str(error.get("code") or "") not in {"", "ok", "0"}:
        code = str(error["code"])
        status = 429 if code in {"4", "17", "32", "613", "rate_limit_exceeded", "spam_risk_too_many_posts", "reached_active_user_cap"} else 401 if code in {"190", "access_token_invalid", "scope_not_authorized"} else 400
        raise SocialPublishError(
            f"{code}: {str(error.get('message') or 'provider rejected the request')[:240]}",
            status=status, code=code, retry_after_s=_number(response_headers.get("retry-after")),
        )
    return result, response_headers


def _id(data: dict[str, Any], field: str = "id") -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        raise SocialPublishError(f"provider returned no {field}; publication is not confirmed", status=502, outcome_unknown=True)
    return value


def _graph(cfg: dict[str, Any], *, instagram: bool = False) -> str:
    version = str(cfg.get("api_version") or GRAPH_VERSION)
    if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
        raise SocialPublishError("invalid Graph API version")
    host = "graph.instagram.com" if instagram and cfg.get("auth_mode", "instagram") == "instagram" else "graph.facebook.com"
    return f"https://{host}/{version}"


def _graph_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise SocialPublishError(f"a valid {label} is required", status=409)
    return text


def _pending(channel: str, operation_id: str, phase: str, **extra: Any) -> dict[str, Any]:
    return {
        "channel": channel, "via": channel, "mode": "api", "id": "", "url": "",
        "operation_id": operation_id, "phase": phase, "pending": True, "published": False,
        "poll_after_s": 15, **extra,
    }


def _published(channel: str, post_id: str, url: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "channel": channel, "via": channel, "mode": "api", "id": post_id, "url": url,
        "phase": "published", "pending": False, "published": True, **extra,
    }


def _checkpoint(store: MarketingStore, row: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    """Persist operation IDs before the next request; never expose upload tokens."""
    if row.get("id"):
        previous = store.get_content(str(row["id"]))
        context = {key: value for key, value in (previous.get("receipt") or {}).items() if key in {"origin", "account_binding", "started_at"}}
        store.upsert_content({
            "id": row["id"], "status": "publishing", "receipt": {**context, **receipt},
            "publish_checked_at": time.time(),
        })
    return receipt


def content_media(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any]:
    """Resolve an explicitly attached media or the generated creative for this post."""
    selected: dict[str, Any] = {}
    creative_id = str(row.get("creative_id") or "")
    explicit = any(row.get(key) for key in ("media_type", "media_url", "media_path", "photo_images", "image_url", "video_url"))
    if not explicit:
        candidates = [
            item for item in store.load_creatives()
            if (item.get("id") == creative_id if creative_id else item.get("content_id") == row.get("id"))
            and item.get("kind") in {"image", "banner", "video"}
        ]
        if candidates:
            selected = candidates[0]
        elif creative_id:
            raise SocialPublishError("the attached creative is missing or is not an image/video")
    kind = str(row.get("media_type") or selected.get("kind") or "").lower()
    if kind in {"banner", "photo"}:
        kind = "image"
    photos = row.get("photo_images")
    if photos is not None and (not isinstance(photos, list) or not photos or not all(isinstance(item, str) for item in photos)):
        raise SocialPublishError("photo_images must be a nonempty list of public URLs")
    url = str(row.get("media_url") or row.get("image_url") or row.get("video_url") or selected.get("public_url") or "").strip()
    path_text = str(row.get("media_path") or selected.get("path") or "").strip()
    if not kind and (photos or row.get("image_url")):
        kind = "image"
    if not kind and row.get("video_url"):
        kind = "video"
    if not kind and (url or path_text):
        suffix = Path(urlsplit(url).path if url else path_text).suffix.lower()
        kind = "image" if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else "video" if suffix in {".mp4", ".mov", ".webm"} else ""
    if (url or path_text or selected) and kind not in {"image", "video"}:
        raise SocialPublishError("select an explicit image or video media_type")
    if kind and kind not in {"image", "video"}:
        raise SocialPublishError(f"unsupported publication media type: {kind}")
    path: Path | None = None
    if path_text:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = store.root / path
        if not path.is_file() or not 0 < path.stat().st_size <= (LOCAL_IMAGE_LIMIT if kind == "image" else LOCAL_VIDEO_LIMIT):
            raise SocialPublishError("media file is missing, empty or exceeds the local upload limit (20 MiB image, 128 MiB video)")
    return {
        "kind": kind, "url": url, "path": path, "photo_images": photos or [],
        "duration_s": _number(row.get("duration_s") or selected.get("duration_s")),
        "mime_type": str(row.get("mime_type") or selected.get("mime_type") or ""),
    }


def probe_video(path: Path) -> dict[str, Any]:
    binary = shutil.which("ffprobe")
    if not binary:
        raise SocialPublishError("ffprobe is required to validate a local video before publishing", status=409)
    try:
        result = subprocess.run(
            [binary, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(path)],
            capture_output=True, timeout=10, check=True,
        )
        data = json.loads(result.stdout)
        stream = next(item for item in data.get("streams", []) if item.get("codec_type") == "video")
        duration = _number((data.get("format") or {}).get("duration"))
        if not duration or duration <= 0:
            raise ValueError("no duration")
        return {"duration_s": duration, "width": stream.get("width"), "height": stream.get("height")}
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as exc:
        raise SocialPublishError("could not validate the local video duration and stream") from exc


def _video_duration(media: dict[str, Any]) -> float:
    if media["path"] and not media.get("probed"):
        media.update(probe_video(media["path"]))
        media["probed"] = True
    value = media.get("duration_s")
    if not value or value <= 0:
        raise SocialPublishError("duration_s is required for a hosted video; verify the source duration before publishing")
    return float(value)


def _check_image(path: Path, channel: str) -> None:
    from PIL import Image

    try:
        with Image.open(path) as image:
            allowed = {"JPEG", "PNG", "GIF"} if channel == "linkedin" else {"JPEG", "PNG"}
            if image.format not in allowed or image.width * image.height >= 36152320:
                raise SocialPublishError(f"unsupported {channel} image format or dimensions")
            image.verify()
    except (OSError, ValueError) as exc:
        raise SocialPublishError("the attached file is not a valid publishable image") from exc


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def validate_content(
    channel: str, store: MarketingStore, cfg: dict[str, Any], row: dict[str, Any], text: str,
    *, require_consent: bool = True,
) -> dict[str, Any]:
    media = content_media(store, row)
    kind = media["kind"]
    if channel == "reddit":
        if kind:
            raise SocialPublishError("Reddit supports text or link posts here; attach a media URL as a link instead")
        subreddit = str(row.get("reddit_subreddit") or cfg.get("subreddit") or "").removeprefix("r/")
        if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", subreddit):
            raise SocialPublishError("choose a valid Reddit subreddit")
        title = str(row.get("title") or row.get("hook") or "").strip()
        if not title or len(title) > 300:
            raise SocialPublishError("Reddit needs a title of 1 to 300 characters")
        if len(text) > 40000:
            raise SocialPublishError("Reddit text exceeds 40000 characters")
        reddit_kind = row.get("reddit_kind") or "self"
        if reddit_kind not in {"self", "link"}:
            raise SocialPublishError("reddit_kind must be self or link")
        if reddit_kind == "link":
            public_url(row.get("reddit_url") or row.get("url") or row.get("link"))
    elif channel == "instagram":
        if kind not in {"image", "video"}:
            raise SocialPublishError("Instagram requires an image or reel; text-only publication is unavailable")
        url = public_url(media["url"])
        if kind == "image" and Path(urlsplit(url).path).suffix.lower() not in {".jpg", ".jpeg"} and media["mime_type"] != "image/jpeg":
            raise SocialPublishError("Instagram image publishing requires a publicly hosted JPEG")
        if kind == "video" and not 3 <= _video_duration(media) <= 900:
            raise SocialPublishError("Instagram reels must be between 3 and 900 seconds")
        if _utf16_length(text) > 2200:
            raise SocialPublishError("Instagram caption exceeds 2200 characters")
    elif channel == "tiktok":
        if kind not in {"image", "video"}:
            raise SocialPublishError("TikTok requires a video or photos; text-only publication is unavailable")
        if kind == "image":
            images = media["photo_images"] or [media["url"]]
            if not 1 <= len(images) <= 35:
                raise SocialPublishError("TikTok photo posts require 1 to 35 URLs")
            for url in images:
                public_url(url)
            cover = row.get("photo_cover_index", 0)
            if isinstance(cover, bool) or not isinstance(cover, int) or not 0 <= cover < len(images):
                raise SocialPublishError("photo_cover_index must identify one of the attached photos")
            if _utf16_length(str(row.get("title") or "")) > 90 or _utf16_length(text) > 4000:
                raise SocialPublishError("TikTok photo title/description exceeds 90/4000 UTF-16 characters")
        else:
            if media["url"] or not media["path"]:
                public_url(media["url"])
            if _video_duration(media) < 3:
                raise SocialPublishError("TikTok video must be at least 3 seconds")
            if _utf16_length(text) > 2200:
                raise SocialPublishError("TikTok video caption exceeds 2200 UTF-16 characters")
        if require_consent:
            if row.get("publish_consent") is not True or row.get("music_usage_confirmed") is not True:
                raise SocialPublishError("confirm the TikTok preview and Music Usage Confirmation before publishing", code="consent_required")
            if not row.get("privacy_level"):
                raise SocialPublishError("select a TikTok privacy level; there is no automatic default", code="privacy_required")
            for key in ("brand_content_toggle", "brand_organic_toggle"):
                if not isinstance(row.get(key), bool):
                    raise SocialPublishError(f"TikTok requires an explicit {key} disclosure")
            if row["brand_content_toggle"]:
                if row.get("privacy_level") == "SELF_ONLY":
                    raise SocialPublishError("TikTok branded content cannot use SELF_ONLY visibility")
                if row.get("branded_content_policy_confirmed") is not True:
                    raise SocialPublishError("confirm TikTok's Branded Content Policy for this post", code="consent_required")
        for key in ("disable_comment", "disable_duet", "disable_stitch", "auto_add_music", "is_aigc"):
            if key in row and not isinstance(row[key], bool):
                raise SocialPublishError(f"{key} must be a boolean")
    elif channel in {"linkedin", "facebook"}:
        if kind and not media["path"] and (channel == "linkedin" or not media["url"]):
            raise SocialPublishError(f"{channel} requires a local file for this media upload")
        if media["url"]:
            public_url(media["url"])
        if kind == "image" and media["path"]:
            _check_image(media["path"], channel)
        if kind == "video":
            duration = _video_duration(media)
            if channel == "facebook" and not 4 <= duration <= 60:
                raise SocialPublishError("this Facebook adapter publishes reels from 4 to 60 seconds")
        if channel == "linkedin" and len(text) > 3000:
            raise SocialPublishError("LinkedIn commentary exceeds 3000 characters")
    return media


def content_capabilities(store: MarketingStore, row: dict[str, Any], text: str) -> dict[str, Any]:
    channel = str(row.get("channel") or "")
    cfg = (store.load_settings().get("publish") or {}).get(channel) or {}
    supported = {
        "reddit": ["text", "link"], "instagram": ["image", "reel"], "tiktok": ["video", "photos"],
        "linkedin": ["text", "link", "image", "video"], "facebook": ["text", "link", "image", "reel"],
    }.get(channel, ["text"])
    errors = []
    kind = "text"
    try:
        kind = content_media(store, row)["kind"] or "text"
        if channel in SOCIAL_CHANNELS:
            validate_content(channel, store, cfg, row, text)
    except MarketingError as exc:
        errors.append(exc.message)
    return {
        "channel": channel, "supported_types": supported, "media_type": kind,
        "can_publish": not errors, "errors": errors,
        "requires_public_media_url": channel == "instagram" or channel == "tiktok" and kind == "image",
        "requires_creator_info": channel == "tiktok", "asynchronous": channel in {"instagram", "tiktok"} or kind == "video",
        "local_image_limit_bytes": LOCAL_IMAGE_LIMIT, "local_video_limit_bytes": LOCAL_VIDEO_LIMIT,
    }


def post_reddit(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    row = row or {}
    validate_content("reddit", store, cfg, row, text)
    kind = str(row.get("reddit_kind") or "self")
    subreddit = str(row.get("reddit_subreddit") or cfg.get("subreddit") or "").removeprefix("r/")
    fields: dict[str, Any] = {
        "api_type": "json", "raw_json": 1, "sr": subreddit, "kind": kind,
        "title": str(row.get("title") or row.get("hook") or ""), "resubmit": "false",
        "sendreplies": "true", "nsfw": "true" if row.get("nsfw") is True else "false",
        "spoiler": "true" if row.get("spoiler") is True else "false",
    }
    if kind == "link":
        fields["url"] = public_url(row.get("reddit_url") or row.get("url") or row.get("link"))
    else:
        fields["text"] = text
    flair = str(row.get("reddit_flair_id") or cfg.get("flair_id") or "")
    if flair:
        fields["flair_id"] = flair
    data, headers = _json_request(
        http, "POST", "https://oauth.reddit.com/api/submit", _token(store, "reddit"), fields,
        form=True, mutation=True, headers={"User-Agent": str(cfg.get("user_agent") or "NavinMarketing/1.0")},
    )
    response = data.get("json") if isinstance(data.get("json"), dict) else {}
    errors = response.get("errors") or []
    if errors:
        code = str(errors[0][0]) if isinstance(errors[0], list) and errors[0] else "reddit_error"
        message = "; ".join(" ".join(map(str, error)) if isinstance(error, list) else str(error) for error in errors)
        raise SocialPublishError(message[:400], status=429 if code == "RATELIMIT" else 400, code=code, retry_after_s=_number(headers.get("retry-after")))
    post = response.get("data") if isinstance(response.get("data"), dict) else {}
    post_id = _id(post, "name") if post.get("name") else _id(post)
    url = str(post.get("url") or "")
    return _published("reddit", post_id, url, subreddit=subreddit, quota={key: headers[key] for key in ("x-ratelimit-remaining", "x-ratelimit-reset") if key in headers})


def creator_info(store: MarketingStore, channel: str, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    if channel == "tiktok":
        data, _ = _json_request(http, "POST", "https://open.tiktokapis.com/v2/post/publish/creator_info/query/", _token(store, channel), {})
        creator = data.get("data")
        if not isinstance(creator, dict) or not isinstance(creator.get("privacy_level_options"), list):
            raise SocialPublishError("TikTok returned no creator privacy options", status=502)
        return {"channel": channel, **creator}
    if channel == "reddit":
        data, _ = _json_request(http, "GET", "https://oauth.reddit.com/api/v1/me", _token(store, channel), headers={"User-Agent": str(cfg.get("user_agent") or "NavinMarketing/1.0")})
        return {"channel": channel, "account": str(data.get("name") or ""), "id": _id(data)}
    if channel == "instagram":
        user_id = _graph_id(cfg.get("instagram_user_id"), "Instagram professional account id")
        data, _ = _json_request(http, "GET", f"{_graph(cfg, instagram=True)}/{user_id}?fields=id,username", _token(store, channel))
        return {"channel": channel, "account": str(data.get("username") or ""), "id": _id(data)}
    raise SocialPublishError(f"creator information is unavailable for {channel}")


def _instagram_quota(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    user_id = _graph_id(cfg.get("instagram_user_id"), "Instagram professional account id")
    data, _ = _json_request(http, "GET", f"{_graph(cfg, instagram=True)}/{user_id}/content_publishing_limit?fields=config,quota_usage", _token(store, "instagram"))
    rows = data.get("data")
    quota = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if quota is None or _number(quota.get("quota_usage")) is None:
        raise SocialPublishError("Instagram did not return its publication quota", status=502)
    limit = _number((quota.get("config") or {}).get("quota_total")) or 100
    if float(quota["quota_usage"]) >= limit:
        raise SocialPublishError("Instagram's rolling publication quota is exhausted; retry after it resets", status=429, code="publishing_quota_exhausted")
    return {"used": quota["quota_usage"], "limit": limit, "duration_s": (quota.get("config") or {}).get("quota_duration", 86400)}


def post_instagram(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    row = row or {}
    media = validate_content("instagram", store, cfg, row, text)
    user_id = _graph_id(cfg.get("instagram_user_id"), "Instagram professional account id")
    quota = _instagram_quota(store, cfg, http)
    fields = {"caption": text, "image_url" if media["kind"] == "image" else "video_url": media["url"]}
    if media["kind"] == "video":
        fields.update({"media_type": "REELS", "share_to_feed": "true" if row.get("share_to_feed") is True else "false"})
    data, _ = _json_request(http, "POST", f"{_graph(cfg, instagram=True)}/{user_id}/media", _token(store, "instagram"), fields, form=True, mutation=True)
    receipt = _checkpoint(store, row, _pending("instagram", _id(data), "container", media_type=media["kind"], quota=quota, account_id=user_id))
    return receipt


def _instagram_status(store: MarketingStore, cfg: dict[str, Any], http: HttpFn, row: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    operation = _graph_id(receipt.get("operation_id"), "Instagram container id")
    base, token = _graph(cfg, instagram=True), _token(store, "instagram")
    data, _ = _json_request(http, "GET", f"{base}/{operation}?fields=status_code,status", token)
    state = str(data.get("status_code") or "")
    if state in {"ERROR", "EXPIRED"}:
        return {**receipt, "pending": False, "published": False, "phase": "failed", "status": state, "error": str(data.get("status") or state)}
    if state == "PUBLISHED":
        return {**receipt, **_published("instagram", str(receipt.get("id") or ""), str(receipt.get("url") or "")), "status": state}
    if state != "FINISHED" or receipt.get("phase") in {"publishing", "unknown"}:
        return {**receipt, "status": state or "UNKNOWN", "pending": True}
    _instagram_quota(store, cfg, http)
    user_id = _graph_id(receipt.get("account_id") or cfg.get("instagram_user_id"), "Instagram professional account id")
    _checkpoint(store, row, {**receipt, "phase": "publishing"})
    posted, _ = _json_request(http, "POST", f"{base}/{user_id}/media_publish", token, {"creation_id": operation}, form=True, mutation=True)
    post_id = _id(posted)
    result = {**receipt, **_published("instagram", post_id), "status": "PUBLISHED"}
    _checkpoint(store, row, result)
    try:
        media, _ = _json_request(http, "GET", f"{base}/{post_id}?fields=permalink", token)
        result["url"] = str(media.get("permalink") or "")
    except MarketingError:
        # The publication ID is confirmed even when optional permalink lookup fails.
        pass
    return result


def post_tiktok(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    row = row or {}
    media = validate_content("tiktok", store, cfg, row, text)
    creator = creator_info(store, "tiktok", cfg, http)
    if row["privacy_level"] not in creator["privacy_level_options"]:
        raise SocialPublishError("selected TikTok privacy level is not offered by this creator account", code="privacy_level_option_mismatch")
    if media["kind"] == "video":
        maximum = _number(creator.get("max_video_post_duration_sec"))
        if not maximum or _video_duration(media) > maximum:
            raise SocialPublishError("video exceeds this TikTok creator's permitted duration", code="duration_check_failed")
    post_info = {
        "privacy_level": row["privacy_level"],
        "disable_comment": bool(creator.get("comment_disabled")) or row.get("disable_comment", True),
        "brand_content_toggle": row["brand_content_toggle"], "brand_organic_toggle": row["brand_organic_toggle"],
    }
    token = _token(store, "tiktok")
    if media["kind"] == "image":
        post_info.update({"title": str(row.get("title") or ""), "description": text, "auto_add_music": row.get("auto_add_music", False)})
        payload = {
            "post_info": post_info, "post_mode": "DIRECT_POST", "media_type": "PHOTO",
            "source_info": {"source": "PULL_FROM_URL", "photo_images": media["photo_images"] or [media["url"]], "photo_cover_index": row.get("photo_cover_index", 0)},
        }
        endpoint = "content"
    else:
        post_info.update({
            "title": text, "disable_duet": bool(creator.get("duet_disabled")) or row.get("disable_duet", True),
            "disable_stitch": bool(creator.get("stitch_disabled")) or row.get("disable_stitch", True),
            "is_aigc": row.get("is_aigc", False),
        })
        if media["path"] and not media["url"]:
            size = media["path"].stat().st_size
            chunk_size = min(8 * 1024 * 1024, size)
            source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": max(1, size // chunk_size)}
        else:
            source = {"source": "PULL_FROM_URL", "video_url": media["url"]}
        payload = {"post_info": post_info, "source_info": source}
        endpoint = "video"
    data, _ = _json_request(http, "POST", f"https://open.tiktokapis.com/v2/post/publish/{endpoint}/init/", token, payload, mutation=True)
    details = data.get("data") if isinstance(data.get("data"), dict) else {}
    receipt = _checkpoint(store, row, _pending("tiktok", _id(details, "publish_id"), "processing", media_type=media["kind"], privacy_level=row["privacy_level"], creator_username=creator.get("creator_username", "")))
    if media["kind"] == "video" and source["source"] == "FILE_UPLOAD":
        upload = _upload_url(details.get("upload_url"), ("tiktokapis.com",))
        size = payload["source_info"]["video_size"]
        chunk_size = payload["source_info"]["chunk_size"]
        count = payload["source_info"]["total_chunk_count"]
        mime = mimetypes.guess_type(media["path"].name)[0] or "video/mp4"
        with media["path"].open("rb") as file:
            offset = 0
            for index in range(count):
                length = size - offset if index == count - 1 else chunk_size
                body = file.read(length)
                if len(body) != length:
                    raise SocialPublishError("video changed during upload; publication is not confirmed", status=409)
                status, _, _ = _request(http, "PUT", upload, {
                    "Content-Type": mime, "Content-Length": str(length), "Content-Range": f"bytes {offset}-{offset + length - 1}/{size}",
                }, body, mutation=True)
                expected = 201 if index == count - 1 else 206
                if status != expected:
                    raise SocialPublishError("TikTok did not confirm the expected upload progress", status=502, outcome_unknown=True)
                offset += length
    return receipt


def _tiktok_status(store: MarketingStore, cfg: dict[str, Any], http: HttpFn, row: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    data, _ = _json_request(http, "POST", "https://open.tiktokapis.com/v2/post/publish/status/fetch/", _token(store, "tiktok"), {"publish_id": receipt["operation_id"]})
    details = data.get("data") if isinstance(data.get("data"), dict) else {}
    status = str(details.get("status") or "UNKNOWN")
    if status == "FAILED":
        return {**receipt, "pending": False, "published": False, "phase": "failed", "status": status, "error": str(details.get("fail_reason") or "TikTok processing failed")}
    if status == "PUBLISH_COMPLETE":
        ids = details.get("publicaly_available_post_id") or details.get("publicly_available_post_id") or []
        ids = [str(value) for value in ids] if isinstance(ids, list) else []
        username = str(receipt.get("creator_username") or "")
        # Private posts legitimately have no public ID or permalink.
        post_id = ids[0] if ids else ""
        url = f"https://www.tiktok.com/@{quote(username, safe='')}/{'photo' if receipt.get('media_type') == 'image' else 'video'}/{post_id}" if username and post_id else ""
        return {**receipt, **_published("tiktok", post_id, url), "status": status, "public_post_ids": ids}
    return {**receipt, "status": status, "pending": True, "published": False, "requires_user_action": status == "SEND_TO_USER_INBOX"}


def _linkedin_headers(cfg: dict[str, Any]) -> dict[str, str]:
    version = str(cfg.get("linkedin_version") or LINKEDIN_VERSION)
    if not re.fullmatch(r"[0-9]{6}", version):
        raise SocialPublishError("invalid LinkedIn API version")
    return {"LinkedIn-Version": version, "X-Restli-Protocol-Version": "2.0.0"}


def post_linkedin_media(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any], author: str) -> dict[str, Any]:
    media = validate_content("linkedin", store, cfg, row, text)
    path = media["path"]
    token, headers = _token(store, "linkedin"), _linkedin_headers(cfg)
    kind = media["kind"]
    request = {"owner": author}
    if kind == "video":
        request.update({"fileSizeBytes": path.stat().st_size, "uploadCaptions": False, "uploadThumbnail": False})
    endpoint = "images" if kind == "image" else "videos"
    data, _ = _json_request(
        http, "POST", f"https://api.linkedin.com/rest/{endpoint}?action=initializeUpload", token,
        {"initializeUploadRequest": request}, mutation=True, headers=headers,
    )
    details = data.get("value") if isinstance(data.get("value"), dict) else {}
    urn = _id(details, kind)
    if not urn.startswith(f"urn:li:{kind}:"):
        raise SocialPublishError("LinkedIn returned an invalid media URN", status=502)
    receipt = _checkpoint(store, row, _pending("linkedin", urn, "uploading", media_type=kind, author=author))
    if kind == "image":
        url = _upload_url(details.get("uploadUrl"), ("linkedin.com",))
        _request(http, "PUT", url, {"Authorization": f"Bearer {token}", "Content-Type": mimetypes.guess_type(path.name)[0] or "image/jpeg"}, path.read_bytes(), mutation=True)
    else:
        instructions = details.get("uploadInstructions")
        if not isinstance(instructions, list) or not instructions:
            raise SocialPublishError("LinkedIn returned no video upload instructions", status=502)
        parts = []
        expected_offset = 0
        size = path.stat().st_size
        with path.open("rb") as video:
            for item in instructions:
                first, last = item.get("firstByte"), item.get("lastByte")
                if (
                    isinstance(first, bool) or isinstance(last, bool) or not isinstance(first, int) or not isinstance(last, int)
                    or first != expected_offset or last < first or last >= size or last - first + 1 > 64 * 1024 * 1024
                ):
                    raise SocialPublishError("LinkedIn returned invalid video upload byte ranges", status=502)
                url = _upload_url(item.get("uploadUrl"), ("linkedin.com",))
                body = video.read(last - first + 1)
                if len(body) != last - first + 1:
                    raise SocialPublishError("video changed during upload", status=409)
                _, response_headers, _ = _request(http, "PUT", url, {"Content-Type": "application/octet-stream", "Authorization": f"Bearer {token}"}, body, mutation=True)
                etag = str(response_headers.get("etag") or "").strip('"')
                if not etag:
                    raise SocialPublishError("LinkedIn returned no uploaded part receipt", status=502)
                parts.append(etag)
                expected_offset = last + 1
        if expected_offset != size:
            raise SocialPublishError("LinkedIn upload instructions do not cover the complete video", status=502)
        payload = {"finalizeUploadRequest": {"video": urn, "uploadToken": str(details.get("uploadToken") or ""), "uploadedPartIds": parts}}
        _request(
            http, "POST", "https://api.linkedin.com/rest/videos?action=finalizeUpload",
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **headers}, json.dumps(payload).encode(), mutation=True,
        )
    return _checkpoint(store, row, {**receipt, "phase": "media_processing"})


def _linkedin_status(store: MarketingStore, cfg: dict[str, Any], http: HttpFn, row: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("phase") == "posting":
        return {**receipt, "pending": True, "error": "LinkedIn post outcome is unknown; check the account before attempting another post"}
    kind, urn = str(receipt.get("media_type") or ""), str(receipt.get("operation_id") or "")
    if kind not in {"image", "video"} or not urn.startswith(f"urn:li:{kind}:"):
        raise SocialPublishError("LinkedIn media receipt is invalid", status=409)
    token, headers = _token(store, "linkedin"), _linkedin_headers(cfg)
    endpoint = "images" if kind == "image" else "videos"
    url = f"https://api.linkedin.com/rest/{endpoint}/{quote(urn, safe='')}"
    try:
        data, _ = _json_request(http, "GET", url, token, headers=headers)
    except SocialPublishError as exc:
        if kind != "image" or exc.status != 403:
            raise
        # LinkedIn documents legacy image GET for write-only w_member_social.
        data, _ = _json_request(http, "GET", url, token, headers={"X-Restli-Protocol-Version": "2.0.0"})
    status = str(data.get("status") or "UNKNOWN")
    if status in {"PROCESSING_FAILED", "FAILED"}:
        return {**receipt, "pending": False, "published": False, "phase": "failed", "status": status, "error": "LinkedIn media processing failed"}
    if status != "AVAILABLE":
        return {**receipt, "status": status, "pending": True}
    payload = {
        "author": receipt.get("author"), "commentary": str(row.get("publish_text") or row.get("body") or ""),
        "visibility": "PUBLIC", "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"id": urn, "title": str(row.get("title") or row.get("hook") or "")}},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }
    _checkpoint(store, row, {**receipt, "phase": "posting"})
    _, response_headers, body = _request(
        http, "POST", "https://api.linkedin.com/rest/posts",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json", **headers},
        json.dumps(payload, ensure_ascii=False).encode(), mutation=True,
    )
    try:
        response = json.loads(body) if body else {}
    except ValueError:
        response = {}
    post_id = str(response_headers.get("x-restli-id") or response.get("id") or "")
    if not post_id:
        raise SocialPublishError("LinkedIn returned no post ID; publication is not confirmed", status=502, outcome_unknown=True)
    return {**receipt, **_published("linkedin", post_id, f"https://www.linkedin.com/feed/update/{post_id}")}


def post_facebook_media(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any]) -> dict[str, Any]:
    media = validate_content("facebook", store, cfg, row, text)
    base, token = _graph(cfg), _token(store, "facebook")
    page = _graph_id(cfg.get("page_id"), "Facebook page id")
    if media["kind"] == "image":
        fields = {"caption": text, "published": "true"}
        if media["path"]:
            from navin.marketing.publish import multipart_body

            body, content_type = multipart_body(fields, "source", media["path"])
            _, _, response = _request(http, "POST", f"{base}/{page}/photos", {"Authorization": f"Bearer {token}", "Content-Type": content_type}, body, mutation=True)
            try:
                data = json.loads(response)
            except ValueError as exc:
                raise SocialPublishError("Facebook returned no valid photo receipt", status=502, outcome_unknown=True) from exc
        else:
            data, _ = _json_request(http, "POST", f"{base}/{page}/photos", token, {**fields, "url": public_url(media["url"])}, form=True, mutation=True)
        photo_id = _id(data)
        post_id = str(data.get("post_id") or photo_id)
        return _published("facebook", post_id, f"https://www.facebook.com/{post_id}", photo_id=photo_id)
    data, _ = _json_request(http, "POST", f"{base}/{page}/video_reels", token, {"upload_phase": "start"}, form=True, mutation=True)
    video_id = _id(data, "video_id")
    upload = _upload_url(data.get("upload_url"), ("facebook.com",))
    receipt = _checkpoint(store, row, _pending("facebook", video_id, "uploading", media_type="video", account_id=page))
    headers = {"Authorization": f"OAuth {token}"}
    body = None
    if media["url"]:
        headers["file_url"] = public_url(media["url"])
    else:
        body = media["path"].read_bytes()
        headers.update({"offset": "0", "file_size": str(len(body)), "Content-Type": "application/octet-stream"})
    _, _, response = _request(http, "POST", upload, headers, body, mutation=True)
    try:
        success = json.loads(response).get("success") is True
    except ValueError:
        success = False
    if not success:
        raise SocialPublishError("Facebook did not confirm the reel upload", status=502)
    _checkpoint(store, row, {**receipt, "phase": "finishing"})
    finished, _ = _json_request(http, "POST", f"{base}/{page}/video_reels", token, {
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": text, "title": str(row.get("title") or ""),
    }, form=True, mutation=True)
    if finished.get("success") is not True:
        raise SocialPublishError("Facebook did not confirm reel submission", status=502, outcome_unknown=True)
    return _checkpoint(store, row, {**receipt, "phase": "processing"})


def _facebook_status(store: MarketingStore, cfg: dict[str, Any], http: HttpFn, row: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    video_id = _graph_id(receipt.get("operation_id"), "Facebook video id")
    data, _ = _json_request(http, "GET", f"{_graph(cfg)}/{video_id}?fields=status,permalink_url", _token(store, "facebook"))
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    phases = [status.get(name) or {} for name in ("uploading_phase", "processing_phase", "publishing_phase")]
    if status.get("video_status") == "error" or any(phase.get("status") == "error" for phase in phases):
        return {**receipt, "pending": False, "published": False, "phase": "failed", "status": status, "error": "Facebook reel upload or processing failed"}
    if (status.get("publishing_phase") or {}).get("status") == "complete":
        return {**receipt, **_published("facebook", video_id, str(data.get("permalink_url") or "")), "status": status}
    return {**receipt, "pending": True, "published": False, "status": status}


def poll_status(store: MarketingStore, row: dict[str, Any], cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    receipt = row.get("receipt") if isinstance(row.get("receipt"), dict) else {}
    channel = str(receipt.get("channel") or row.get("channel") or "")
    if receipt.get("published") is True:
        return receipt
    receipt = {key: value for key, value in receipt.items() if key not in {"error", "error_code", "retry_after_s", "failed"}}
    if not receipt.get("operation_id"):
        return {**receipt, "pending": True, "published": False, "phase": "unknown", "error": "No provider operation ID is available; check the account before retrying publication"}
    poller = {
        "instagram": _instagram_status, "tiktok": _tiktok_status,
        "linkedin": _linkedin_status, "facebook": _facebook_status,
    }.get(channel)
    if not poller:
        raise SocialPublishError(f"no asynchronous publication status for {channel}", status=409)
    return poller(store, cfg, http, row, receipt)
