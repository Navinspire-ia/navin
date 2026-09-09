"""Publish desk content to real channels, with a scheduled queue and UTM links.

API channels include LinkedIn, Facebook Pages, Reddit, Instagram and TikTok.
Media processing keeps a content row in ``publishing`` until the provider
confirms publication. YouTube and Product Hunt retain a manual handoff or an
explicitly configured webhook bridge.

Every publish attempt leaves a receipt on the content row (url, remote id,
error) so the loop and the UI never guess what happened.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from navin.marketing import social_publish
from navin.marketing.errors import MarketingError
from navin.marketing.media_delivery import resolve_publish_media
from navin.marketing.store import CHANNELS, PUBLISH_CHANNELS, MarketingStore
from navin.utils.atomic_io import InterProcessLock, LockTimeoutError

HttpResult = tuple[int, dict[str, str], bytes]
HttpFn = Callable[[str, str, dict[str, str], bytes | None], HttpResult]

MANUAL_CHANNELS = tuple(channel for channel in CHANNELS if channel not in PUBLISH_CHANNELS)
X_LIMIT = 280
LINKEDIN_VERSION = social_publish.LINKEDIN_VERSION
GRAPH_VERSION = social_publish.GRAPH_VERSION
_TIMEOUT_S = 25

HINTS = {
    "linkedin": "Token OAuth avec le scope w_member_social (ou w_organization_social) ; author = urn:li:person:... ou urn:li:organization:...",
    "x": "App X avec acces Read and Write : API key, API secret, Access token, Access secret (OAuth 1.0a).",
    "facebook": "Page access token avec pages_manage_posts + l'identifiant de la Page.",
    "instagram": "Compte Instagram professionnel, permission content_publish et image JPEG ou reel disponible sur une URL HTTPS publique.",
    "tiktok": "Compte TikTok avec video.publish, choix de confidentialite et accord explicite pour chaque contenu. Les medias heberges exigent un domaine verifie dans l'app TikTok.",
    "reddit": "OAuth avec identity + submit, subreddit choisi et respect des regles de la communaute.",
    "telegram": "Bot token (ou le bot Navin) et le chat id / @canal ou le bot est admin.",
    "email": "Canal Email Navin configure (SMTP) et une liste de destinataires.",
    "webhook": "URL HTTPS qui recoit un JSON signe HMAC-SHA256 (en-tete X-Navin-Signature).",
    "blog": "Dossier de sortie des articles Markdown (front matter) et l'URL publique du blog.",
}


# -- HTTP -----------------------------------------------------------------------


def http_request(method: str, url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> HttpResult:
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return int(resp.status), {key.lower(): value for key, value in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), {key.lower(): value for key, value in (exc.headers or {}).items()}, body or b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MarketingError(f"{urllib.parse.urlparse(url).netloc}: {exc}", status=502) from exc


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def _error_text(status: int, body: bytes) -> str:
    data = _json(body)
    detail = data.get("message") or data.get("detail") or data.get("description") or data.get("title")
    if isinstance(data.get("error"), dict):
        detail = data["error"].get("message") or detail
    elif data.get("error") and not detail:
        detail = data.get("error")
    if not detail and data.get("errors"):
        first = data["errors"][0] if isinstance(data["errors"], list) and data["errors"] else data["errors"]
        detail = first.get("message") if isinstance(first, dict) else str(first)
    return f"HTTP {status}: {str(detail or body[:160].decode('utf-8', errors='replace')).strip()}"


# -- OAuth 1.0a (X) ----------------------------------------------------------------


def _pct(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~")


def oauth1_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    extra_params: dict[str, str] | None = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Authorization header per RFC 5849 (HMAC-SHA1). JSON bodies are not signed."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    base = signature_base_string(method, url, {**oauth, **(extra_params or {})})
    key = f"{_pct(consumer_secret)}&{_pct(token_secret)}".encode()
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


def signature_base_string(method: str, url: str, params: dict[str, str]) -> str:
    """RFC 5849 base string: query and body params join the oauth_* ones, encoded then sorted."""
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    pairs = [*params.items(), *urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)]
    normalized = "&".join(f"{key}={value}" for key, value in sorted((_pct(k), _pct(v)) for k, v in pairs))
    return "&".join([method.upper(), _pct(base_url), _pct(normalized)])


# -- links and text ---------------------------------------------------------------


def utm_link(base: str, *, source: str, campaign: str, content: str, medium: str = "social") -> str:
    """Site link tagged so Plausible / Matomo can attribute visits to one post."""
    url = str(base or "").strip()
    if not url:
        return ""
    if "//" not in url:
        url = f"https://{url}"
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign or "navin",
            "utm_content": content,
        }
    )
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:60] or "post"


_URL_RE = re.compile(r"https?://\S+")
X_LINK_WEIGHT = 23


def x_length(text: str) -> int:
    """Characters as X counts them: every link weighs 23 whatever its length."""
    return len(_URL_RE.sub("x" * X_LINK_WEIGHT, text))


def render_for_channel(row: dict[str, Any], link: str) -> str:
    """Body plus tracked link, trimmed to the channel limit (X)."""
    channel = str(row.get("channel") or "")
    body = str(row.get("body") or "").strip()
    if link and link not in body:
        body = f"{body}\n\n{link}".strip() if body else link
    if channel == "x" and x_length(body) > X_LIMIT:
        # Keep the link (23 weighted chars plus its newline) and the ellipsis; cut the prose.
        room = X_LIMIT - (X_LINK_WEIGHT + 1 if link else 0) - 3
        prose = _URL_RE.sub("", str(row.get("body") or "")).strip()
        prose = prose[: max(0, room)].rsplit(" ", 1)[0].rstrip(" ,.;:") + "..."
        body = f"{prose}\n{link}".strip() if link else prose
    return body


def _channel_config(settings: dict[str, Any], channel: str) -> dict[str, Any]:
    publish = settings.get("publish") if isinstance(settings.get("publish"), dict) else {}
    cfg = publish.get(channel) if isinstance(publish.get(channel), dict) else {}
    return dict(cfg)


def _navin_telegram_token() -> str:
    try:
        from navin.config.loader import load_config

        cfg = load_config()
        raw = getattr(getattr(cfg, "channels", None), "telegram", None)
        if raw is None:
            extra = getattr(getattr(cfg, "channels", None), "__pydantic_extra__", None) or {}
            raw = extra.get("telegram")
        data = raw.model_dump() if raw is not None and hasattr(raw, "model_dump") else (raw or {})
        if isinstance(data, dict):
            return str(data.get("token") or data.get("bot_token") or "").strip()
    except Exception:
        return ""
    return ""


def _email_ready() -> bool:
    try:
        from navin.crm.outreach import channel_status

        return bool(channel_status("email").get("ready"))
    except Exception:
        return False


def channel_state(store: MarketingStore, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What each channel can do right now, for the settings pane and the loop."""
    settings = settings or store.load_settings()
    secrets = store.load_secrets()
    rows: list[dict[str, Any]] = []
    # The webhook is a transport, not a content channel: it gets a connector row all the same.
    for channel in (*CHANNELS, *(item for item in PUBLISH_CHANNELS if item not in CHANNELS)):
        cfg = _channel_config(settings, channel)
        mode = "api" if channel in PUBLISH_CHANNELS else "manual"
        if channel == "blog":
            mode = "file"
        missing: list[str] = []
        if channel == "linkedin":
            if not secrets.get("linkedin_token"):
                missing.append("linkedin_token")
        elif channel == "x":
            missing.extend(key for key in ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret") if not secrets.get(key))
        elif channel == "facebook":
            if not secrets.get("facebook_page_token"):
                missing.append("facebook_page_token")
            if not cfg.get("page_id"):
                missing.append("page_id")
        elif channel in {"instagram", "tiktok", "reddit"}:
            token_key = f"{channel}_access_token"
            if not secrets.get(token_key):
                missing.append(token_key)
            if channel == "instagram" and not cfg.get("instagram_user_id"):
                missing.append("instagram_user_id")
            if channel == "reddit" and not cfg.get("subreddit"):
                missing.append("subreddit")
        elif channel == "telegram":
            if not (secrets.get("telegram_bot_token") or _navin_telegram_token()):
                missing.append("telegram_bot_token")
            if not cfg.get("chat_id"):
                missing.append("chat_id")
        elif channel == "email":
            if not _email_ready():
                missing.append("smtp")
            if not cfg.get("to"):
                missing.append("to")
        elif channel == "webhook":
            if not str(cfg.get("url") or "").startswith(("http://", "https://")):
                missing.append("url")
        elif channel == "blog":
            pass
        configured = mode != "manual" and not missing
        rows.append(
            {
                "channel": channel,
                "mode": mode,
                "enabled": bool(cfg.get("enabled")) if mode != "manual" else False,
                "configured": configured,
                "ready": configured and bool(cfg.get("enabled")),
                "missing": missing,
                "hint": HINTS.get(channel, "Copier-coller depuis la fiche contenu."),
                "fields": {key: value for key, value in cfg.items() if key != "enabled"},
                "secrets": {key: bool(secrets.get(key)) for key in _secret_keys(channel)},
                "tested_at": float(cfg.get("tested_at") or 0) if isinstance(cfg.get("tested_at"), (int, float)) else 0.0,
                "account": str(cfg.get("account") or ""),
                "last_error": str(cfg.get("last_error") or ""),
                "bridge": "",
            }
        )
    webhook_ready = any(row["channel"] == "webhook" and row["ready"] for row in rows)
    for row in rows:
        legacy_bridge = row["channel"] in {"instagram", "tiktok", "reddit"} and not any(row["secrets"].values())
        if (row["mode"] == "manual" or legacy_bridge) and webhook_ready:
            row["bridge"] = "webhook"
            row["ready"] = True
    return rows


def _secret_keys(channel: str) -> tuple[str, ...]:
    from navin.marketing.store import SECRET_NAMES

    return SECRET_NAMES.get(channel, ())


def ready_channels(store: MarketingStore, settings: dict[str, Any] | None = None) -> set[str]:
    return {row["channel"] for row in channel_state(store, settings) if row.get("ready")}


# -- connectors --------------------------------------------------------------------


def _linkedin_author(token: str, cfg: dict[str, Any], http: HttpFn) -> str:
    author = str(cfg.get("author") or "").strip()
    if author.startswith("urn:li:"):
        return author
    status, _headers, body = http("GET", "https://api.linkedin.com/v2/userinfo", {"Authorization": f"Bearer {token}"}, None)
    if status != 200:
        raise MarketingError(f"linkedin userinfo failed - {_error_text(status, body)}", status=502)
    sub = str(_json(body).get("sub") or "").strip()
    if not sub:
        raise MarketingError("linkedin userinfo returned no member id", status=502)
    return f"urn:li:person:{sub}"


def _social_text_post(http: HttpFn, url: str, headers: dict[str, str], data: bytes) -> HttpResult:
    try:
        return http("POST", url, headers, data)
    except (MarketingError, OSError, TimeoutError) as exc:
        raise social_publish.SocialPublishError("The provider did not confirm its response; check the account before retrying publication", status=502, outcome_unknown=True) from exc


def post_linkedin(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    media = social_publish.validate_content("linkedin", store, cfg, row or {}, text)
    token = store.get_secret("linkedin_token")
    author = _linkedin_author(token, cfg, http)
    if media["kind"]:
        return social_publish.post_linkedin_media(text, store, cfg, http, row=row or {}, author=author)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": str(cfg.get("linkedin_version") or LINKEDIN_VERSION),
    }
    title = str((row or {}).get("title") or (row or {}).get("hook") or "").strip()[:200]
    payload: dict[str, Any] = {
        "author": author,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if link:
        # The tracked link becomes the article card, so the click lands with its UTM tags.
        payload["content"] = {"article": {"source": link, "title": title or link}}
    status, resp_headers, body = _social_text_post(http, "https://api.linkedin.com/rest/posts", headers, json.dumps(payload).encode())
    if status in (400, 404, 426):
        # Older apps only have the v2 share endpoint.
        share: dict[str, Any] = {"shareCommentary": {"text": text}, "shareMediaCategory": "NONE"}
        if link:
            share["shareMediaCategory"] = "ARTICLE"
            share["media"] = [{"status": "READY", "originalUrl": link, "title": {"text": title or link}}]
        legacy = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        legacy_headers = {key: value for key, value in headers.items() if key != "LinkedIn-Version"}
        status, resp_headers, body = _social_text_post(http, "https://api.linkedin.com/v2/ugcPosts", legacy_headers, json.dumps(legacy).encode())
    if status not in (200, 201):
        raise social_publish.SocialPublishError(f"linkedin post failed - {_error_text(status, body)}", status=status if 400 <= status < 500 else 502, outcome_unknown=status >= 500)
    urn = str({key.lower(): value for key, value in resp_headers.items()}.get("x-restli-id") or _json(body).get("id") or "").strip()
    if not urn:
        raise social_publish.SocialPublishError("LinkedIn returned no post ID; publication is not confirmed", status=502, outcome_unknown=True)
    return {"id": urn, "url": f"https://www.linkedin.com/feed/update/{urn}", "author": author, "published": True, "pending": False}


def test_linkedin(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    token = store.get_secret("linkedin_token")
    if not token:
        raise MarketingError("linkedin_token missing", status=400)
    status, _headers, body = http("GET", "https://api.linkedin.com/v2/userinfo", {"Authorization": f"Bearer {token}"}, None)
    if status != 200:
        raise MarketingError(f"linkedin token rejected - {_error_text(status, body)}", status=502)
    data = _json(body)
    return {"account": str(data.get("name") or data.get("email") or data.get("sub") or "linkedin member"), "author": f"urn:li:person:{data.get('sub')}" if data.get("sub") else ""}


def _x_headers(store: MarketingStore, method: str, url: str) -> dict[str, str]:
    secrets = store.load_secrets()
    return {
        "Authorization": oauth1_header(
            method,
            url,
            consumer_key=secrets.get("x_api_key", ""),
            consumer_secret=secrets.get("x_api_secret", ""),
            token=secrets.get("x_access_token", ""),
            token_secret=secrets.get("x_access_secret", ""),
        ),
        "Content-Type": "application/json",
    }


def post_x(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    url = "https://api.x.com/2/tweets"
    status, _headers, body = http("POST", url, _x_headers(store, "POST", url), json.dumps({"text": text}).encode())
    if status not in (200, 201):
        raise MarketingError(f"x post failed - {_error_text(status, body)}", status=502)
    data = _json(body).get("data") or {}
    tweet_id = str(data.get("id") or "").strip()
    return {"id": tweet_id, "url": f"https://x.com/i/web/status/{tweet_id}" if tweet_id else ""}


def test_x(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    url = "https://api.x.com/2/users/me"
    status, _headers, body = http("GET", url, _x_headers(store, "GET", url), None)
    if status != 200:
        raise MarketingError(f"x credentials rejected - {_error_text(status, body)}", status=502)
    data = _json(body).get("data") or {}
    return {"account": f"@{data.get('username')}" if data.get("username") else str(data.get("name") or "x account")}


def post_facebook(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    media = social_publish.validate_content("facebook", store, cfg, row or {}, text)
    if media["kind"]:
        return social_publish.post_facebook_media(text, store, cfg, http, row=row or {})
    token = store.get_secret("facebook_page_token")
    page = str(cfg.get("page_id") or "").strip()
    url = f"{social_publish._graph(cfg)}/{page}/feed"
    fields = {"message": text, "access_token": token}
    if link:
        fields["link"] = link
    form = urllib.parse.urlencode(fields).encode()
    status, _headers, body = _social_text_post(http, url, {"Content-Type": "application/x-www-form-urlencoded"}, form)
    if status != 200:
        raise social_publish.SocialPublishError(f"facebook post failed - {_error_text(status, body)}", status=status if 400 <= status < 500 else 502, outcome_unknown=status >= 500)
    post_id = str(_json(body).get("id") or "").strip()
    if not post_id:
        raise social_publish.SocialPublishError("Facebook returned no post ID; publication is not confirmed", status=502, outcome_unknown=True)
    return {"id": post_id, "url": f"https://www.facebook.com/{post_id}", "published": True, "pending": False}


def test_facebook(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    token = store.get_secret("facebook_page_token")
    page = str(cfg.get("page_id") or "").strip()
    if not token or not page:
        raise MarketingError("facebook_page_token and page_id are required", status=400)
    url = f"{social_publish._graph(cfg)}/{page}?fields=name"
    status, _headers, body = http("GET", url, {"Authorization": f"Bearer {token}"}, None)
    if status != 200:
        raise MarketingError(f"facebook token rejected - {_error_text(status, body)}", status=502)
    return {"account": str(_json(body).get("name") or page)}


def _telegram_token(store: MarketingStore) -> str:
    return store.get_secret("telegram_bot_token") or _navin_telegram_token()


def content_still(store: MarketingStore, row: dict[str, Any] | None) -> Path | None:
    """The produced image attached to a post, when the file still exists."""
    if not row:
        return None
    creative_id = str(row.get("creative_id") or "").strip()
    candidates = []
    for creative in store.load_creatives():
        if creative.get("kind") not in {"image", "banner"}:
            continue
        if creative_id and creative.get("id") == creative_id:
            candidates.insert(0, creative)
        elif not creative_id and creative.get("content_id") == row.get("id"):
            candidates.append(creative)
    for creative in candidates:
        raw = str(creative.get("path") or "").strip()
        if raw and Path(raw).is_file():
            return Path(raw)
    return None


def multipart_body(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"navin{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    chunks.append(path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_telegram(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    token = _telegram_token(store)
    chat = str(cfg.get("chat_id") or "").strip()
    still = content_still(store, row)
    if still is not None and len(text) <= 1024:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        data, content_type = multipart_body({"chat_id": chat, "caption": text}, "photo", still)
        status, _headers, body = http("POST", url, {"Content-Type": content_type}, data)
    else:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat, "text": text[:4096], "disable_web_page_preview": False}
        status, _headers, body = http("POST", url, {"Content-Type": "application/json"}, json.dumps(payload).encode())
    data_out = _json(body)
    if status != 200 or not data_out.get("ok"):
        raise MarketingError(f"telegram post failed - {_error_text(status, body)}", status=502)
    result = data_out.get("result") or {}
    message_id = str(result.get("message_id") or "")
    handle = str((result.get("chat") or {}).get("username") or (chat.lstrip("@") if chat.startswith("@") else "")).strip()
    return {"id": message_id, "url": f"https://t.me/{handle}/{message_id}" if handle and message_id else "", "photo": still is not None}


def test_telegram(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    token = _telegram_token(store)
    if not token:
        raise MarketingError("telegram bot token missing (desk secret or Navin Telegram channel)", status=400)
    status, _headers, body = http("GET", f"https://api.telegram.org/bot{token}/getMe", {}, None)
    data = _json(body)
    if status != 200 or not data.get("ok"):
        raise MarketingError(f"telegram token rejected - {_error_text(status, body)}", status=502)
    bot = data.get("result") or {}
    return {"account": f"@{bot.get('username')}" if bot.get("username") else "telegram bot"}


def post_email(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    from navin.crm.outreach import _send_email as smtp_send

    recipients = [item.strip() for item in re.split(r"[,;\s]+", str(cfg.get("to") or "")) if "@" in item]
    if not recipients:
        raise MarketingError("email recipients missing", status=400)
    subject = str((row or {}).get("title") or (row or {}).get("hook") or "Update").strip()[:160]
    for dest in recipients:
        smtp_send(dest, subject, text)
    return {"id": f"email-{int(time.time())}", "url": "", "recipients": len(recipients)}


def test_email(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    if not _email_ready():
        raise MarketingError("Navin email channel (SMTP) is not configured", status=409)
    recipients = [item.strip() for item in re.split(r"[,;\s]+", str(cfg.get("to") or "")) if "@" in item]
    if not recipients:
        raise MarketingError("email recipients missing", status=400)
    return {"account": f"{len(recipients)} recipient(s)"}


def webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _webhook_send(event: str, payload: dict[str, Any], store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    url = str(cfg.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise MarketingError("webhook url missing", status=400)
    body = json.dumps({"event": event, "sent_at": time.time(), **payload}, ensure_ascii=False).encode()
    stamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Navin-Event": event, "X-Navin-Timestamp": stamp}
    secret = store.get_secret("webhook_secret")
    if secret:
        headers["X-Navin-Signature"] = webhook_signature(secret, stamp, body)
    status, _headers, resp = http("POST", url, headers, body)
    if status >= 300:
        raise MarketingError(f"webhook rejected - {_error_text(status, resp)}", status=502)
    data = _json(resp)
    return {"id": str(data.get("id") or stamp), "url": str(data.get("url") or "")}


def post_webhook(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    row = row or {}
    still = content_still(store, row)
    payload = {
        "channel": str(row.get("channel") or ""),
        "text": text,
        "link": link,
        "image": str(still) if still else "",
        "content": {key: value for key, value in row.items() if key not in {"receipt", "publish_text"}},
    }
    return _webhook_send("marketing.publish", payload, store, cfg, http)


def test_webhook(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    _webhook_send("marketing.ping", {"text": "navin marketing desk"}, store, cfg, http)
    return {"account": urllib.parse.urlparse(str(cfg.get("url") or "")).netloc}


def _blog_dir(store: MarketingStore, cfg: dict[str, Any]) -> Path:
    raw = str(cfg.get("dir") or "").strip()
    return Path(raw).expanduser() if raw else store.root / "blog"


def post_blog(text: str, store: MarketingStore, cfg: dict[str, Any], http: HttpFn, *, row: dict[str, Any] | None = None, link: str = "") -> dict[str, Any]:
    row = row or {}
    folder = _blog_dir(store, cfg)
    folder.mkdir(parents=True, exist_ok=True)
    title = str(row.get("title") or row.get("hook") or "Post").strip()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slug(title)
    path = folder / f"{day}-{slug}.md"
    if path.exists():
        slug = f"{slug}-{str(row.get('id') or uuid.uuid4().hex)[-6:]}"
        path = folder / f"{day}-{slug}.md"
    front = {
        "title": title,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": "blog",
        "content_id": str(row.get("id") or ""),
        "campaign_id": str(row.get("campaign_id") or ""),
        "tags": [str(tag).lstrip("#") for tag in (row.get("hashtags") or [])],
        "link": link,
    }
    lines = ["---"]
    for key, value in front.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", "", f"# {title}", "", text.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    return {"id": slug, "url": f"{base}/{slug}/" if base else "", "path": str(path)}


def test_blog(store: MarketingStore, cfg: dict[str, Any], http: HttpFn) -> dict[str, Any]:
    folder = _blog_dir(store, cfg)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".navin-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise MarketingError(f"blog folder not writable: {exc}", status=400) from exc
    return {"account": str(folder)}


_POSTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "linkedin": post_linkedin,
    "x": post_x,
    "facebook": post_facebook,
    "instagram": social_publish.post_instagram,
    "tiktok": social_publish.post_tiktok,
    "reddit": social_publish.post_reddit,
    "telegram": post_telegram,
    "email": post_email,
    "webhook": post_webhook,
    "blog": post_blog,
}


def _refresh_social_token(store: MarketingStore, channel: str, http: HttpFn) -> None:
    """Use the same injectable transport for token renewal and publication."""
    if channel not in social_publish.SOCIAL_CHANNELS:
        return
    from navin.marketing.oauth import ensure_access_token

    def oauth_http(method: str, url: str, *, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request_headers = {"Accept": "application/json", **(headers or {})}
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        if encoded is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            status, _, body = http(method, url, request_headers, encoded)
        except (MarketingError, OSError, TimeoutError):
            raise MarketingError("Social token renewal could not reach the provider; reconnect if this persists", status=502) from None
        result = _json(body)
        if not 200 <= status < 300 or not result:
            raise MarketingError(f"Social token renewal refused (HTTP {status}); reconnect the account", status=401)
        return result

    ensure_access_token(store, channel, http=oauth_http)


def creator_info(store: MarketingStore, channel: str, *, http: HttpFn | None = None) -> dict[str, Any]:
    channel = str(channel or "").strip().lower()
    client = http or http_request
    _refresh_social_token(store, channel, client)
    with InterProcessLock(store.path(".oauth.lock"), timeout=5):
        return social_publish.creator_info(store, channel, _channel_config(store.load_settings(), channel), client)
_TESTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "linkedin": test_linkedin,
    "x": test_x,
    "facebook": test_facebook,
    "telegram": test_telegram,
    "email": test_email,
    "webhook": test_webhook,
    "blog": test_blog,
}


# -- desk operations -------------------------------------------------------------------


def test_connection(store: MarketingStore, channel: str, *, http: HttpFn | None = None) -> dict[str, Any]:
    channel = str(channel or "").lower().strip()
    tester = _TESTERS.get(channel)
    if tester is None and channel not in {"instagram", "tiktok", "reddit"}:
        raise MarketingError(f"{channel or 'channel'} has no API connector", status=400)
    settings = store.load_settings()
    cfg = _channel_config(settings, channel)
    try:
        client = http or http_request
        _refresh_social_token(store, channel, client)
        result = tester(store, cfg, client) if tester else social_publish.creator_info(store, channel, cfg, client)
        if channel == "tiktok":
            result["account"] = str(result.get("creator_nickname") or result.get("creator_username") or "TikTok creator")
    except MarketingError as exc:
        store.save_settings({"publish": {channel: {"tested_at": time.time(), "last_error": exc.message}}})
        store.append_journal({"kind": "publish", "text": f"{channel} connection failed - {exc.message}"})
        return {"channel": channel, "ok": False, "error": exc.message}
    patch: dict[str, Any] = {"tested_at": time.time(), "last_error": "", "account": str(result.get("account") or "")}
    if channel == "linkedin" and result.get("author") and not cfg.get("author"):
        patch["author"] = result["author"]
    store.save_settings({"publish": {channel: patch}})
    store.append_journal({"kind": "publish", "text": f"{channel} connected as {result.get('account') or 'ok'}"})
    return {"channel": channel, "ok": True, **result}


def _site(store: MarketingStore) -> str:
    product = store.load_product()
    brand = store.load_brand()
    return str(product.get("site") or brand.get("site") or "").strip()


def tracked_link(store: MarketingStore, row: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    settings = settings or store.load_settings()
    site = _site(store)
    if not site:
        return ""
    campaign = str(settings.get("utm_campaign") or row.get("campaign_id") or "navin").strip()
    medium = "email" if row.get("channel") == "email" else "referral" if row.get("channel") in {"blog", "webhook"} else "social"
    return utm_link(site, source=str(row.get("channel") or "navin"), campaign=campaign, content=str(row.get("id") or ""), medium=medium)


def approve(store: MarketingStore, content_id: str) -> dict[str, Any]:
    row = store.get_content(content_id)
    if row.get("status") in {"published", "publishing"}:
        raise MarketingError("content is already published or publishing", status=409)
    saved = store.upsert_content({"id": content_id, "status": "approved", "approved_at": time.time(), "error": ""})
    store.append_journal({"kind": "publish", "text": f"approved {content_id} ({row.get('channel')})"})
    return saved


def parse_when(value: Any, *, now: float | None = None) -> float:
    """Epoch seconds from an epoch, an ISO date or a relative '+2h' / '+30m' / '+1d'."""
    clock = now if now is not None else time.time()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = float(value)
        return stamp / 1000.0 if stamp > 1e12 else stamp
    text = str(value or "").strip()
    if not text:
        raise MarketingError("scheduled_at is required", status=400)
    match = re.fullmatch(r"\+\s*(\d+)\s*([mhd])", text.lower())
    if match:
        amount = int(match.group(1))
        unit = {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
        return clock + amount * unit
    try:
        return parse_when(float(text), now=clock)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketingError("scheduled_at must be an ISO date, epoch seconds or +2h", status=400) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def schedule(store: MarketingStore, content_id: str, when: Any, *, now: float | None = None) -> dict[str, Any]:
    row = store.get_content(content_id)
    if row.get("status") in {"published", "publishing"}:
        raise MarketingError("content is already published or publishing", status=409)
    stamp = parse_when(when, now=now)
    saved = store.upsert_content({"id": content_id, "status": "scheduled", "scheduled_at": stamp, "error": ""})
    store.append_journal(
        {"kind": "publish", "text": f"scheduled {content_id} ({row.get('channel')}) for {datetime.fromtimestamp(stamp).isoformat(timespec='minutes')}"}
    )
    return saved


def unschedule(store: MarketingStore, content_id: str) -> dict[str, Any]:
    row = store.get_content(content_id)
    if row.get("status") not in {"scheduled", "approved", "failed"}:
        raise MarketingError("only scheduled, approved or failed content can go back to ready", status=409)
    saved = store.upsert_content({"id": content_id, "status": "ready", "scheduled_at": 0, "error": ""})
    store.append_journal({"kind": "publish", "text": f"back to ready {content_id}"})
    return saved


def publish_content(
    store: MarketingStore,
    content_id: str,
    *,
    http: HttpFn | None = None,
    force: bool = False,
    origin: str = "manual",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Serialize posting and its receipt so concurrent requests cannot post twice."""
    arguments = {"http": http, "force": force, "origin": origin, "dry_run": dry_run}
    if dry_run:
        return _publish_content(store, content_id, **arguments)
    try:
        with InterProcessLock(store.root / ".publish.lock", timeout=0):
            return _publish_content(store, content_id, **arguments)
    except LockTimeoutError as exc:
        raise MarketingError("a publication is already in progress; refresh before retrying", status=409) from exc


def _publish_content(
    store: MarketingStore,
    content_id: str,
    *,
    http: HttpFn | None = None,
    force: bool = False,
    origin: str = "manual",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Post one content, preserving an asynchronous or uncertain receipt."""
    row = store.get_content(content_id)
    channel = str(row.get("channel") or "")
    if row.get("status") == "published" and not force:
        raise MarketingError("content is already published", status=409)
    if row.get("status") == "publishing":
        raise MarketingError("content is already publishing; check its status before starting another post", status=409)
    settings = store.load_settings()
    link = tracked_link(store, row, settings)
    text = render_for_channel(row, link)
    states = {item["channel"]: item for item in channel_state(store, settings)}
    via = channel
    if states.get(channel, {}).get("bridge") == "webhook":
        via = "webhook"
    elif channel in MANUAL_CHANNELS:
        if dry_run:
            return {"ok": True, "dry_run": True, "manual": True, "via": "manual", "text": text, "link": link, "content": row, "confirmed": False}
        saved = store.upsert_content({
            "id": content_id, "utm_url": link, "publish_text": text, "status": "published",
            "published_at": time.time(), "receipt": {"channel": channel, "mode": "manual", "origin": origin}, "error": "",
        })
        store.append_journal({"kind": "publish", "text": f"{channel} {content_id} marked published (manual channel)"})
        return {"ok": True, "manual": True, "via": "manual", "content": saved, "text": text, "link": link, "confirmed": False}
    state = states.get(via)
    if not state or not state.get("configured"):
        missing = ", ".join((state or {}).get("missing") or []) or "connector"
        raise MarketingError(f"{via} is not configured ({missing})", status=409)
    if via in social_publish.SOCIAL_CHANNELS and not state.get("enabled"):
        raise MarketingError(f"activate the {via} connector before publishing", status=409)
    prepared = resolve_publish_media(store, row, create=False) if via in social_publish.SOCIAL_CHANNELS else row
    if via == "reddit" and prepared.get("reddit_kind") == "link" and not prepared.get("reddit_url"):
        prepared = {**prepared, "reddit_url": link}
    if via in social_publish.SOCIAL_CHANNELS:
        social_publish.validate_content(via, store, _channel_config(settings, via), prepared, text)
    if dry_run:
        return {"ok": True, "dry_run": True, "manual": False, "via": via, "text": text, "link": link, "content": row, "confirmed": False}
    cfg = _channel_config(settings, via)
    poster = _POSTERS[via]
    try:
        client = http or http_request
        if via in social_publish.SOCIAL_CHANNELS:
            _refresh_social_token(store, via, client)
            with InterProcessLock(store.path(".oauth.lock"), timeout=5):
                cfg = _channel_config(store.load_settings(), via)
                if not cfg.get("enabled"):
                    raise MarketingError(f"activate the {via} connector before publishing", status=409)
                prepared = resolve_publish_media(store, row, create=True)
                if via == "reddit" and prepared.get("reddit_kind") == "link" and not prepared.get("reddit_url"):
                    prepared = {**prepared, "reddit_url": link}
                social_publish.validate_content(via, store, cfg, prepared, text)
                intent = {
                    "channel": channel, "via": via, "mode": "api", "origin": origin,
                    "phase": "starting", "pending": True, "published": False,
                    "started_at": time.time(), "account_binding": _account_binding(store, via, cfg),
                }
                store.upsert_content({
                    "id": content_id, "status": "publishing", "receipt": intent,
                    "utm_url": link, "publish_text": text, "error": "", "published_url": "", "remote_id": "", "published_at": 0,
                })
                receipt = poster(text, store, cfg, client, row=prepared, link=link)
        else:
            receipt = poster(text, store, cfg, client, row=row, link=link)
    except MarketingError as exc:
        return _publication_error(store, content_id, exc, via=via, text=text, link=link)
    except Exception as exc:  # noqa: BLE001 - connector bug must not kill the loop
        logger.exception("marketing publish failed")
        message = f"{type(exc).__name__}: {exc}"[:240]
        error = social_publish.SocialPublishError(message, status=502, outcome_unknown=via in social_publish.SOCIAL_CHANNELS)
        return _publication_error(store, content_id, error, via=via, text=text, link=link)
    return _publication_result(store, content_id, receipt, via=via, text=text, link=link, origin=origin)


def _account_binding(store: MarketingStore, channel: str, cfg: dict[str, Any]) -> dict[str, str]:
    from navin.marketing.oauth import connection_status

    identity = str(connection_status(store, channel).get("account_id") or "")
    configured = str(cfg.get({"instagram": "instagram_user_id", "facebook": "page_id", "linkedin": "author"}.get(channel, "")) or "")
    binding = {"account_id": identity, "configured_account": configured, "auth_mode": str(cfg.get("auth_mode") or "")}
    if not identity:
        # Legacy tokens have no independently verified identity. Renewal of a
        # managed OAuth token does not change the stable binding above.
        token = store.get_secret(social_publish.TOKEN_KEYS[channel])
        binding["manual_token_fingerprint"] = hmac.new(token.encode(), b"Navin publication account binding", hashlib.sha256).hexdigest()
    return binding


def _publication_result(
    store: MarketingStore, content_id: str, receipt: dict[str, Any], *, via: str,
    text: str, link: str, origin: str = "manual",
) -> dict[str, Any]:
    previous = store.get_content(content_id)
    old_receipt = previous.get("receipt") if isinstance(previous.get("receipt"), dict) else {}
    receipt = {"channel": previous.get("channel"), "via": via, "mode": "api", "origin": origin, **old_receipt,
               "error": "", "failed": False, "pending": False, "published": via not in social_publish.SOCIAL_CHANNELS, **receipt}
    pending = bool(receipt.get("pending"))
    failed = bool(receipt.get("failed")) or receipt.get("phase") == "failed"
    confirmed = not pending and not failed and (receipt.get("published") is True or via not in social_publish.SOCIAL_CHANNELS)
    if not (pending or failed or confirmed):
        pending = True
        receipt.update({"phase": "unknown", "pending": True, "published": False, "error": "The provider did not confirm publication; check the remote account before retrying"})
    status = "publishing" if pending else "failed" if failed else "published"
    message = str(receipt.get("error") or "")
    patch: dict[str, Any] = {
        "id": content_id, "status": status, "publish_checked_at": time.time(), "utm_url": link,
        "publish_text": text, "receipt": receipt, "error": message,
    }
    if confirmed:
        patch.update({"published_at": time.time(), "published_url": str(receipt.get("url") or ""), "remote_id": str(receipt.get("id") or "")})
    elif failed:
        patch["failed_at"] = time.time()
    saved = store.upsert_content(patch)
    if previous.get("status") != status or old_receipt.get("phase") != receipt.get("phase"):
        store.append_journal({"kind": "publish", "text": f"{previous.get('channel')} {content_id} {status} via {via} {message or receipt.get('url') or receipt.get('operation_id') or ''}".strip()})
    return {"ok": not failed and not message, "pending": pending, "confirmed": confirmed, "manual": False, "via": via, "content": saved, "text": text, "link": link, "error": message}


def _publication_error(
    store: MarketingStore, content_id: str, error: MarketingError, *, via: str,
    text: str, link: str, polling: bool = False,
) -> dict[str, Any]:
    previous = store.get_content(content_id)
    receipt = dict(previous.get("receipt") or {})
    unknown = bool(getattr(error, "outcome_unknown", False))
    pending = polling or unknown
    receipt.update({"pending": pending, "published": False, "failed": not pending, "error": error.message})
    if unknown and receipt.get("phase") == "starting":
        receipt["phase"] = "unknown"
    elif not pending:
        receipt["phase"] = "failed"
    if getattr(error, "code", ""):
        receipt["error_code"] = error.code
    if getattr(error, "retry_after_s", None) is not None:
        receipt["retry_after_s"] = error.retry_after_s
    if pending:
        receipt["poll_after_s"] = max(30, float(getattr(error, "retry_after_s", None) or 0))
    result = _publication_result(store, content_id, receipt, via=via, text=text, link=link)
    result.update({"ok": False, "error": error.message, "status": error.status})
    return result


def content_capabilities(store: MarketingStore, row: dict[str, Any]) -> dict[str, Any]:
    """Read-only validation for the content editor; never refresh an OAuth token."""
    link = tracked_link(store, row)
    try:
        prepared = resolve_publish_media(store, row, create=False)
        if prepared.get("channel") == "reddit" and prepared.get("reddit_kind") == "link" and not prepared.get("reddit_url"):
            prepared = {**prepared, "reddit_url": link}
        return social_publish.content_capabilities(store, prepared, render_for_channel(row, link))
    except MarketingError as exc:
        result = social_publish.content_capabilities(store, row, render_for_channel(row, link))
        result.update({"can_publish": False, "errors": list(dict.fromkeys([exc.message, *result["errors"]]))})
        return result


def publish_status(store: MarketingStore, content_id: str, *, http: HttpFn | None = None) -> dict[str, Any]:
    """Poll a known operation, publishing its processed media when it is ready."""
    try:
        with InterProcessLock(store.path(".publish.lock"), timeout=0):
            row = store.get_content(content_id)
            via = str((row.get("receipt") or {}).get("via") or row.get("channel") or "")
            text, link = str(row.get("publish_text") or ""), str(row.get("utm_url") or "")
            if row.get("status") != "publishing":
                return {"ok": row.get("status") == "published", "pending": False, "confirmed": row.get("status") == "published" and (row.get("receipt") or {}).get("mode") != "manual", "content": row, "via": via, "text": text, "link": link}
            client = http or http_request
            try:
                cfg = _channel_config(store.load_settings(), via)
                if not cfg.get("enabled"):
                    raise MarketingError(f"The {via} connector is paused; activate it to continue this publication", status=409)
                _refresh_social_token(store, via, client)
                with InterProcessLock(store.path(".oauth.lock"), timeout=5):
                    cfg = _channel_config(store.load_settings(), via)
                    if not cfg.get("enabled"):
                        raise MarketingError(f"The {via} connector is paused", status=409)
                    expected = (row.get("receipt") or {}).get("account_binding")
                    if not expected or expected != _account_binding(store, via, cfg):
                        raise MarketingError("This publication belongs to a different account or token; reconnect its original account before continuing", status=409)
                    receipt = social_publish.poll_status(store, row, cfg, client)
            except MarketingError as exc:
                return _publication_error(store, content_id, exc, via=via, text=text, link=link, polling=True)
            except Exception as exc:  # noqa: BLE001 - keep an uncertain operation out of the new-post queue
                logger.exception("marketing publication status failed")
                error = social_publish.SocialPublishError(f"Could not check publication ({type(exc).__name__}); refresh its status", status=502)
                return _publication_error(store, content_id, error, via=via, text=text, link=link, polling=True)
            return _publication_result(store, content_id, receipt, via=via, text=text, link=link)
    except LockTimeoutError as exc:
        raise MarketingError("a publication is already in progress; refresh its status shortly", status=409) from exc


def poll_pending_publications(
    store: MarketingStore, *, http: HttpFn | None = None, limit: int = 10, now: float | None = None,
) -> dict[str, Any]:
    """Bounded continuation of accepted operations; never start a new post."""
    clock = now if now is not None else time.time()
    report: dict[str, Any] = {"sent": [], "failed": [], "pending": [], "skipped": []}
    enabled = {item["channel"] for item in channel_state(store) if item.get("enabled") and item.get("configured")}
    pending = sorted((row for row in store.load_content() if row.get("status") == "publishing"), key=lambda row: float(row.get("publish_checked_at") or 0))
    checked = 0
    for row in pending:
        receipt = row.get("receipt") or {}
        if row.get("channel") not in enabled or clock < float(row.get("publish_checked_at") or 0) + max(5, float(receipt.get("poll_after_s") or 15)) or checked >= max(0, min(10, limit)):
            report["skipped"].append(row["id"])
            continue
        try:
            result = publish_status(store, row["id"], http=http)
        except MarketingError as exc:
            if exc.status == 409:
                report["skipped"].append(row["id"])
                break
            result = {"ok": False, "pending": True, "content": row, "error": exc.message}
        target = "pending" if result.get("pending") else "sent" if result.get("confirmed") else "failed"
        report[target].append({"id": row["id"], "channel": row.get("channel"), **result})
        checked += 1
    report["checked"] = checked
    return report


def publish_queue(store: MarketingStore, *, now: float | None = None) -> dict[str, Any]:
    """What the next loop pass would send, for the UI."""
    clock = now if now is not None else time.time()
    settings = store.load_settings()
    ready = sendable_channels(store, settings)
    rows = store.load_content()
    due = [row for row in rows if row.get("status") == "scheduled" and float(row.get("scheduled_at") or 0) <= clock]
    waiting = [row for row in rows if row.get("status") == "scheduled" and float(row.get("scheduled_at") or 0) > clock]
    approved = [row for row in rows if row.get("status") == "approved"]
    auto = [row for row in rows if row.get("status") == "ready" and row.get("channel") in ready] if (
        settings.get("execution_mode") == "autonomous" and settings.get("auto_publish")
    ) else []
    blocked = [row for row in [*due, *approved] if row.get("channel") not in ready]
    return {
        "due": [row["id"] for row in due],
        "waiting": [row["id"] for row in sorted(waiting, key=lambda item: float(item.get("scheduled_at") or 0))],
        "approved": [row["id"] for row in approved],
        "auto": [row["id"] for row in auto],
        "blocked": [row["id"] for row in blocked],
        "pending": [row["id"] for row in rows if row.get("status") == "publishing"],
        "ready_channels": sorted(ready),
        "per_cycle": int((settings.get("publish") or {}).get("per_cycle") or 1),
        "auto_publish": bool(settings.get("execution_mode") == "autonomous" and settings.get("auto_publish")),
    }


def sendable_channels(store: MarketingStore, settings: dict[str, Any] | None = None) -> set[str]:
    """API channels that are ready, plus every manual channel when the webhook bridge is ready."""
    ready = ready_channels(store, settings)
    if "webhook" in ready:
        ready |= set(MANUAL_CHANNELS)
    return ready


def publish_due(
    store: MarketingStore,
    *,
    now: float | None = None,
    http: HttpFn | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Loop pass: scheduled rows that are due, then approved rows, then auto-publish."""
    clock = now if now is not None else time.time()
    settings = store.load_settings()
    ready = sendable_channels(store, settings)
    per_cycle = int((settings.get("publish") or {}).get("per_cycle") or 1) if limit is None else limit
    queue = publish_queue(store, now=clock)
    continued = {"sent": [], "failed": [], "pending": []} if dry_run else poll_pending_publications(store, now=clock, http=http)
    sent: list[dict[str, Any]] = continued["sent"]
    failed: list[dict[str, Any]] = continued["failed"]
    pending: list[dict[str, Any]] = continued["pending"]
    preview: list[dict[str, Any]] = []
    skipped: list[str] = []
    budget = per_cycle
    ordered = [(cid, "scheduled") for cid in queue["due"]] + [(cid, "approved") for cid in queue["approved"]] + [(cid, "auto") for cid in queue["auto"]]
    for content_id, origin in ordered:
        row = store.get_content(content_id)
        channel = str(row.get("channel") or "")
        if channel not in ready:
            # Not configured, or a manual channel without the webhook bridge: stays in the queue.
            skipped.append(content_id)
            continue
        if origin != "scheduled" and budget <= 0:
            # Over the per-cycle budget: stays approved for the next pass.
            skipped.append(content_id)
            continue
        try:
            result = publish_content(store, content_id, http=http, origin=origin, dry_run=dry_run)
        except MarketingError as exc:
            if exc.status == 409:
                skipped.append(content_id)
                continue
            result = {"ok": False, "error": exc.message, "content": row}
        if result.get("dry_run"):
            preview.append({"id": content_id, "channel": channel, **result})
        else:
            (pending if result.get("pending") else sent if result.get("ok") else failed).append({
                "id": content_id,
                "channel": channel,
                "via": result.get("via") or channel,
                "url": (result.get("content") or {}).get("published_url", ""),
                "error": result.get("error", ""),
                **result,
            })
        if origin != "scheduled":
            budget -= 1
    if not dry_run and (sent or failed or pending):
        store.append_journal({"kind": "publish", "text": f"loop publish: {len(sent)} sent, {len(pending)} publishing, {len(failed)} failed, {len(skipped)} waiting"})
    return {
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "preview": preview,
        "skipped": skipped,
        "ready_channels": sorted(ready),
    }
