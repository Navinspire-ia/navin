"""Scrape tool: fetch, crawl, clean, enrich and export web data at scale.

Prefers the Rust ``navin_core`` scrape hot path when available; falls back to a
pure-Python pipeline (httpx + stdlib HTML parsing) so the agent still works
without ``make native``.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import html
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.agent.tools.scrape_ops import (
    OriginRateLimiter,
    RobotsCache,
    enrich_pages,
    extract_tables,
    parse_sitemap_urls,
    request_with_retries,
)
from navin.agent.tools.scrape_pagination import (
    compile_optional,
    content_hash,
    crawl_stats,
    discover_pagination_urls,
    link_allowed,
    normalize_url,
)
from navin.config_base import Base
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.utils.native import native

_DEFAULT_UA = (
    "Mozilla/5.0 (compatible; NavinScrape/0.1; +https://navin.ai) AppleWebKit/537.36"
)
_HARD_MAX_PAGES = 500
_HARD_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_TIMEOUT = 30
_EXPORT_FORMATS = ("csv", "json", "jsonl", "xml", "xlsx", "md", "report")


class ScrapeToolConfig(Base):
    """Scrape tool configuration."""

    enabled: bool = True
    max_pages: int = Field(default=50, ge=1, le=_HARD_MAX_PAGES)
    concurrency: int = Field(default=8, ge=1, le=32)
    timeout_seconds: float = Field(default=float(_DEFAULT_TIMEOUT), ge=1, le=300)
    max_bytes: int = Field(default=_HARD_MAX_BYTES, ge=1024, le=20 * 1024 * 1024)
    user_agent: str = _DEFAULT_UA
    # Present each request as a mainstream desktop browser (coherent UA +
    # Accept / Accept-Language / sec-ch-ua), rotated per host. The honest
    # NavinScrape UA is the single easiest bot signal to filter on; a real
    # browser identity is the baseline for getting data back. A custom
    # `user_agent` (non-default) always wins over rotation.
    rotate_user_agent: bool = True
    proxy: str | None = None
    respect_same_domain: bool = True
    # When a captcha / Cloudflare / paywall / login wall is detected: pause and
    # ask the user to solve it in a real browser session. Never auto-bypass.
    assisted: bool = True
    # Respect robots.txt (default on). Crawl-delay is honoured when present.
    respect_robots: bool = True
    # Transient HTTP retries (408/429/5xx) with Retry-After / backoff.
    max_retries: int = Field(default=2, ge=0, le=8)
    # Attach wordCount / fetchedAt / lang / OG enrichments on records.
    enrich: bool = True
    # Follow pagination links (rel=next, ?page=, /page/N) during crawl.
    follow_pagination: bool = True
    # Skip pages whose content hash was already seen (mass crawl dedupe).
    dedupe_content: bool = True
    # Soft delay between requests when no robots crawl-delay (seconds).
    delay_ms: int = Field(default=0, ge=0, le=30_000)
    # Scrape handles untrusted URLs, so private targets are blocked by default.
    # Set this only for explicit local/VPN scraping.
    allow_private_network: bool = False
    # --- Evasion (opt-in, off by default) -------------------------------
    # Master switch for active anti-bot measures beyond a realistic identity.
    # When off, human walls still pause for the user (assisted mode). When on,
    # the tool can rotate through a proxy pool and expose a captcha solver so
    # the agent can pass a challenge via the browser tool with the user's own
    # paid solver credits. This never "breaks" protections - it forwards
    # challenges to a solving service and injects the returned token.
    evasion: bool = False
    # Rotating proxy pool (residential/datacenter URLs). Used per host when set;
    # falls back to the single `proxy`. Requires allow_private_network=true for
    # the same DNS-pinning reason as `proxy`.
    proxy_pool: list[str] = Field(default_factory=list)
    # Captcha solving service: "capsolver" or "2captcha". The API key comes from
    # CAPSOLVER_API_KEY / TWOCAPTCHA_API_KEY / CAPTCHA_API_KEY (never stored here).
    captcha_provider: str | None = None


_CAPTCHA_RE = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|cf-challenge|challenge-platform|"
    r"g-recaptcha|h-captcha|data-sitekey",
    re.I,
)
_CLOUDFLARE_RE = re.compile(
    r"just a moment|attention required|cf-browser-verification|"
    r"cloudflare|cf-ray|checking your browser|enable javascript and cookies|"
    r"__cf_chl|cdn-cgi/challenge",
    re.I,
)
_PAYWALL_RE = re.compile(
    r"subscribe to (continue|read|unlock)|paywall|premium (content|article)|"
    r"members? only|already a subscriber|sign in to continue reading|"
    r"metered[_-]?paywall|piano\.io|tinypass",
    re.I,
)
_LOGIN_RE = re.compile(
    r"sign in to continue|log in to continue|please (log|sign) in|"
    r"create an account to|authentication required|sso/login|"
    r"accounts\.google\.com/signin|okta\.com|auth0\.com",
    re.I,
)


def detect_wall(
    *,
    status: int | None = None,
    title: str = "",
    text: str = "",
    html: str = "",
    error: str | None = None,
    content_type: str = "",
) -> dict[str, Any] | None:
    """Classify access obstacles. Returns None when the page looks usable.

    Human walls (captcha / cloudflare / paywall / login) must pause for the
    user - the agent must not attempt to bypass them.
    """
    if error:
        return {
            "kind": "error",
            "human": False,
            "message": f"Fetch failed: {error}",
            "hint": "Retry later, or open the URL with the browser tool if it is a transient block.",
        }

    blob = " ".join(
        part for part in (title, text, html[:8000] if html else "", content_type) if part
    )
    status_i = int(status or 0)

    if _CAPTCHA_RE.search(blob) or (status_i == 403 and _CAPTCHA_RE.search(blob)):
        return {
            "kind": "captcha",
            "human": True,
            "message": "Captcha / bot challenge detected.",
            "hint": (
                "Do not bypass. Ask the user to open the URL in the browser tool, "
                "solve the captcha, then retry with browser action=content."
            ),
        }
    if _CLOUDFLARE_RE.search(blob) or (
        status_i in (403, 503) and re.search(r"cloudflare|cf-ray|just a moment", blob, re.I)
    ):
        return {
            "kind": "cloudflare",
            "human": True,
            "message": "Cloudflare / bot-management challenge detected.",
            "hint": (
                "Do not bypass. Pause and ask the user to complete the challenge "
                "in a real browser session, then continue with the browser tool."
            ),
        }
    if _PAYWALL_RE.search(blob):
        return {
            "kind": "paywall",
            "human": True,
            "message": "Paywall / subscriber gate detected.",
            "hint": (
                "Do not bypass. If the user has a legitimate subscription, ask them "
                "to sign in via the browser tool, then resume extraction."
            ),
        }
    if status_i in (401, 407) or _LOGIN_RE.search(title) or (
        len((text or "").strip()) < 80 and _LOGIN_RE.search(blob)
    ):
        return {
            "kind": "login",
            "human": True,
            "message": "Login wall detected.",
            "hint": (
                "Do not bypass. Ask the user to authenticate in the browser tool "
                "with their own credentials, then retry content extraction."
            ),
        }
    if status_i == 403:
        return {
            "kind": "forbidden",
            "human": False,
            "message": "HTTP 403 Forbidden.",
            "hint": "Try the browser tool for a rendered session; do not attempt WAF evasion.",
        }
    if status_i >= 400:
        return {
            "kind": "error",
            "human": False,
            "message": f"HTTP {status_i}.",
            "hint": "Check the URL or retry later.",
        }

    text_len = len((text or "").strip())
    script_heavy = bool(html) and html.lower().count("<script") >= 8 and text_len < 120
    if text_len < 40 or script_heavy:
        return {
            "kind": "empty_shell",
            "human": False,
            "message": "Page looks empty or client-rendered.",
            "hint": (
                "Escalate to the browser tool (action=content, or network + "
                "response_body). Not a captcha bypass."
            ),
        }
    return None


def annotate_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a ``wall`` field on each page record when an obstacle is detected.

    ``_fetch_one_py`` already classifies walls while the full HTML is in hand,
    so an existing verdict is preserved. This re-runs only when none is present
    (Rust fetch, replayed checkpoints), using any retained raw HTML so a
    markup-only challenge is still caught.
    """
    out: list[dict[str, Any]] = []
    for page in pages:
        row = dict(page)
        if not isinstance(row.get("wall"), dict):
            wall = detect_wall(
                status=row.get("status") if isinstance(row.get("status"), int) else int(row.get("status") or 0),
                title=str(row.get("title") or ""),
                text=str(row.get("text") or row.get("markdown") or ""),
                html=str(row.get("_rawHtml") or ""),
                error=row.get("error"),
                content_type=str(row.get("contentType") or row.get("content_type") or ""),
            )
            if wall is not None:
                row["wall"] = wall
        out.append(row)
    return out


def wall_summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [p for p in pages if isinstance(p.get("wall"), dict)]
    human = [p for p in blocked if p["wall"].get("human")]
    soft = [p for p in blocked if not p["wall"].get("human")]
    by_kind: dict[str, int] = {}
    for p in blocked:
        kind = str(p["wall"].get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "blocked": len(blocked),
        "human_walls": len(human),
        "soft_walls": len(soft),
        "by_kind": by_kind,
        "urls_human": [
            str(p.get("url") or p.get("finalUrl") or "") for p in human if p.get("url") or p.get("finalUrl")
        ],
        "urls_soft": [
            str(p.get("url") or p.get("finalUrl") or "") for p in soft if p.get("url") or p.get("finalUrl")
        ],
    }


def _validate_url_safe(url: str, *, enforce_ssrf: bool = True) -> tuple[bool, str]:
    from navin.security.network import validate_url_target

    return validate_url_target(url, enforce_ssrf=enforce_ssrf)


def _validate_urls(
    urls: list[str], *, enforce_ssrf: bool = True
) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    errors: list[str] = []
    for raw in urls:
        url = (raw or "").strip()
        if not url:
            continue
        good, reason = _validate_url_safe(url, enforce_ssrf=enforce_ssrf)
        if good:
            ok.append(url)
        else:
            errors.append(f"{url}: {reason}")
    return ok, errors


def _parse_url_list(urls: str | None, url: str | None) -> list[str]:
    items: list[str] = []
    if url and url.strip():
        items.append(url.strip())
    if urls and urls.strip():
        text = urls.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"urls must be a JSON array or newline/comma list: {exc}") from exc
            if not isinstance(parsed, list):
                raise ValueError("urls must be a JSON array")
            items.extend(str(x).strip() for x in parsed if str(x).strip())
        else:
            for part in re.split(r"[\n,]+", text):
                if part.strip():
                    items.append(part.strip())
    # Preserve order, drop dupes.
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _options_payload(config: ScrapeToolConfig, **overrides: Any) -> dict[str, Any]:
    payload = {
        "timeoutSecs": int(overrides.get("timeout_secs", config.timeout_seconds)),
        "maxBytes": int(overrides.get("max_bytes", config.max_bytes)),
        "userAgent": overrides.get("user_agent", config.user_agent) or _DEFAULT_UA,
        "proxy": overrides.get("proxy", config.proxy),
        "proxyPool": list(overrides.get("proxy_pool", config.proxy_pool) or []),
        "evasion": bool(overrides.get("evasion", config.evasion)),
        "followRedirects": True,
        "concurrency": int(overrides.get("concurrency", config.concurrency)),
        "maxDepth": int(overrides.get("max_depth", 1)),
        "maxPages": int(overrides.get("max_pages", config.max_pages)),
        "sameDomain": bool(overrides.get("same_domain", config.respect_same_domain)),
        "respectRobots": bool(overrides.get("respect_robots", config.respect_robots)),
        "maxRetries": int(overrides.get("max_retries", config.max_retries)),
        "enrich": bool(overrides.get("enrich", config.enrich)),
        "method": str(overrides.get("method", "GET")).upper(),
        "followPagination": bool(
            overrides.get("follow_pagination", config.follow_pagination)
        ),
        "dedupeContent": bool(overrides.get("dedupe_content", config.dedupe_content)),
        "delayMs": int(overrides.get("delay_ms", config.delay_ms)),
        "rotateUserAgent": bool(
            overrides.get("rotate_user_agent", config.rotate_user_agent)
        ),
        "allow": overrides.get("allow"),
        "deny": overrides.get("deny"),
        "checkpointPath": overrides.get("checkpoint_path"),
        "body": overrides.get("body"),
        "jsonBody": overrides.get("json_body"),
        "formBody": overrides.get("form_body"),
        "headers": overrides.get("headers") or {},
        "cookies": overrides.get("cookies") or {},
        "enforceSsrf": not config.allow_private_network,
    }
    return payload


def _native_scrape() -> Any | None:
    mod = native()
    if mod is None:
        return None
    if not all(
        hasattr(mod, name)
        for name in ("scrape_fetch", "scrape_extract", "scrape_clean", "scrape_export")
    ):
        return None
    return mod


# ---------------------------------------------------------------------------
# Pure-Python fallback
# ---------------------------------------------------------------------------


def _strip_tag_blocks(html_text: str, tag: str) -> str:
    pattern = re.compile(rf"<{tag}\b[\s\S]*?</{tag}>", re.I)
    return pattern.sub("", html_text)


def _strip_tags(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    return html.unescape(text)


def _clean_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            lines.append(collapsed)
        elif lines and lines[-1]:
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def _readability_main(html_text: str) -> str | None:
    """Return the article-body HTML per readability-lxml, or None if unavailable.

    Mozilla-class main-content extraction beats the ``<article>``/``<main>``
    heuristic on real pages (news, blogs, docs) that bury the story in nested
    divs. Optional dependency: absence or any parse error is not fatal.
    """
    try:
        from readability import Document
    except Exception:
        return None
    try:
        summary = Document(html_text).summary(html_partial=True)
    except Exception:
        return None
    return summary or None


def _extract_html_py(html_text: str, base_url: str) -> dict[str, Any]:
    working = html_text
    for tag in ("script", "style", "noscript"):
        working = _strip_tag_blocks(working, tag)

    title = ""
    m = re.search(r"<title[^>]*>([\s\S]*?)</title>", working, re.I)
    if m:
        title = _clean_text(_strip_tags(m.group(1)))
    if not title:
        m = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", working, re.I)
        if m:
            title = _clean_text(_strip_tags(m.group(1)))

    meta: dict[str, str] = {}
    for tag in re.finditer(r"<meta\b[^>]*>", working, re.I):
        chunk = tag.group(0)
        name_m = re.search(r'(?:name|property)\s*=\s*["\']([^"\']+)["\']', chunk, re.I)
        content_m = re.search(r'content\s*=\s*["\']([^"\']*)["\']', chunk, re.I)
        if not name_m or not content_m:
            continue
        name = name_m.group(1).strip().lower()
        content = html.unescape(content_m.group(1).strip())
        if name in {
            "description",
            "og:title",
            "og:description",
            "og:url",
            "og:type",
            "twitter:title",
            "twitter:description",
            "keywords",
            "author",
            "robots",
        }:
            meta[name] = content

    links: list[str] = []
    seen: set[str] = set()
    for href_m in re.finditer(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["']""", working, re.I):
        href = href_m.group(1).strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)

    main = working
    for css_re in (
        r"<article\b[\s\S]*?</article>",
        r"<main\b[\s\S]*?</main>",
    ):
        found = re.search(css_re, working, re.I)
        if found:
            main = found.group(0)
            break
    else:
        body = re.search(r"<body\b[\s\S]*?</body>", working, re.I)
        if body:
            main = body.group(0)
            for noise in ("nav", "footer", "header", "aside", "form"):
                main = _strip_tag_blocks(main, noise)

    text = _clean_text(_strip_tags(main))
    # Refine the main-content block with readability-lxml when it recovers more
    # article text than the tag heuristic (which keeps whole <body> noise when a
    # page has no <article>/<main>). Title, meta and links stay from the parsing
    # above; readability only sharpens the body. Never let it fail the extract.
    readable_main = _readability_main(html_text)
    if readable_main:
        readable_text = _clean_text(_strip_tags(readable_main))
        if len(readable_text) > len(text):
            main = readable_main
            text = readable_text
    # Rough markdown: keep headings.
    md = main
    for level, tag in enumerate(("h1", "h2", "h3", "h4", "h5", "h6"), start=1):
        md = re.sub(
            rf"<{tag}\b[^>]*>([\s\S]*?)</{tag}>",
            lambda m, p="#" * level: f"\n\n{p} {_clean_text(_strip_tags(m.group(1)))}\n\n",
            md,
            flags=re.I,
        )
    md = re.sub(r"<p\b[^>]*>([\s\S]*?)</p>", lambda m: f"\n\n{_clean_text(_strip_tags(m.group(1)))}\n\n", md, flags=re.I)
    md = re.sub(r"<li\b[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_clean_text(_strip_tags(m.group(1)))}", md, flags=re.I)
    markdown = _clean_text(_strip_tags(md))
    if not title and meta.get("og:title"):
        title = meta["og:title"]
    return {
        "title": title,
        "text": text,
        "markdown": markdown or text,
        "links": links,
        "meta": meta,
    }


_ALLOWED_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "authorization",
    "content-type",
    "if-match",
    "if-none-match",
    "referer",
    "user-agent",
    "x-api-key",
    "x-requested-with",
}
_SECRET_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-api-key"}


def _safe_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if name.lower() not in _ALLOWED_REQUEST_HEADERS:
            raise ValueError(f"Request header is not allowed: {name}")
        clean[name] = str(value)
    return clean


def _redact_error(value: BaseException | str, secrets: list[str]) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(
        r"(?i)(authorization|cookie|x-api-key)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )
    return text


def _extract_pdf(body: bytes) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    extractor = "pypdf"
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(body))
        for index, page in enumerate(reader.pages):
            pages.append({"page": index + 1, "text": page.extract_text() or ""})
        metadata = {
            str(key).lstrip("/"): str(value)
            for key, value in (reader.metadata or {}).items()
            if value is not None
        }
    except Exception as first_error:
        try:
            import pdfplumber

            extractor = "pdfplumber"
            with pdfplumber.open(io.BytesIO(body)) as pdf:
                pages = [
                    {"page": index + 1, "text": page.extract_text() or ""}
                    for index, page in enumerate(pdf.pages)
                ]
                metadata = {
                    str(key): str(value)
                    for key, value in (pdf.metadata or {}).items()
                    if value is not None
                }
        except Exception as second_error:
            raise ValueError(
                f"PDF extraction failed: pypdf={first_error}; pdfplumber={second_error}"
            ) from second_error
    text = "\n\n".join(page["text"].strip() for page in pages if page["text"].strip())
    return {
        "document": {
            "type": "pdf",
            "extractor": extractor,
            "pageCount": len(pages),
            "metadata": metadata,
            "pages": pages,
        },
        "text": text,
        "markdown": text,
    }


def _fetch_one_py(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
    method: str = "GET",
    max_retries: int = 2,
    robots: RobotsCache | None = None,
    limiter: OriginRateLimiter | None = None,
    request_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "url": url,
        "finalUrl": url,
        "status": 0,
        "title": "",
        "text": "",
        "markdown": "",
        "links": [],
        "meta": {},
        "contentType": "",
        "bytes": 0,
        "error": None,
        "method": (method or "GET").upper(),
    }
    request_options = request_options or {}
    headers = _safe_headers(request_options.get("headers"))
    cookies = request_options.get("cookies")
    if isinstance(cookies, dict) and cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    secrets = [
        value
        for key, value in headers.items()
        if key.lower() in _SECRET_HEADERS
    ]
    try:
        if robots is not None and not robots.allowed(client, url):
            record["error"] = "Blocked by robots.txt"
            record["status"] = 0
            return record
        delay = robots.crawl_delay(url) if robots is not None else None
        if limiter is not None:
            limiter.wait(url, min(float(delay), 10.0) if delay else None)
        raw_body = request_options.get("body")
        body = base64.b64decode(raw_body, validate=True) if raw_body else None
        resp = request_with_retries(
            client,
            url,
            method=method,
            max_retries=max_retries,
            body=body,
            json_body=request_options.get("jsonBody"),
            form_body=request_options.get("formBody"),
            headers=headers,
            max_bytes=max_bytes,
            follow_redirects=bool(request_options.get("followRedirects", True)),
            enforce_ssrf=bool(request_options.get("enforceSsrf", False)),
        )
        record["status"] = resp.status_code
        record["finalUrl"] = str(resp.url)
        record["contentType"] = resp.headers.get("content-type", "")
        body = resp.content
        record["bytes"] = len(body)
        ct = record["contentType"].lower()
        if "application/pdf" in ct or body.startswith(b"%PDF-"):
            record.update(_extract_pdf(body))
        else:
            text = body.decode("utf-8", errors="replace")
        if "application/pdf" in ct or body.startswith(b"%PDF-"):
            pass
        elif "html" in ct or "xml" in ct or text.lstrip()[:15].lower().startswith(
            ("<!doctype", "<html")
        ):
            extracted = _extract_html_py(text, record["finalUrl"])
            record.update(extracted)
            record["tables"] = extract_tables(text)
            # Kept for pagination discovery during crawl; stripped before agent export.
            record["_rawHtml"] = text[:250_000]
        else:
            cleaned = _clean_text(text)
            record["text"] = cleaned
            record["markdown"] = cleaned
    except Exception as exc:  # noqa: BLE001 - surface to agent
        record["error"] = _redact_error(exc, secrets)
    # Classify walls here, at the single choke point every action shares, while
    # the full HTML is still in hand. Detecting later from stripped records let
    # markup-only challenges (a bare `g-recaptcha` div, a Cloudflare interstitial
    # whose body text was discarded) slip through unflagged.
    try:
        wall = detect_wall(
            status=int(record.get("status") or 0),
            title=str(record.get("title") or ""),
            text=str(record.get("text") or record.get("markdown") or ""),
            html=str(record.get("_rawHtml") or ""),
            error=record.get("error"),
            content_type=str(record.get("contentType") or ""),
        )
        if wall is not None:
            record["wall"] = wall
    except Exception:  # noqa: BLE001 - detection must never fail a fetch
        pass
    return record


def _strip_internal_fields(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for page in pages:
        page.pop("_rawHtml", None)
    return pages


def _select_proxy(opts: dict[str, Any], urls: list[str]) -> str | None:
    """Pick a proxy for this batch: rotate the pool per host, else the single proxy.

    Keying rotation on the target host keeps one origin behind one exit IP for a
    crawl (a request stream that hops IPs mid-session is itself a bot tell) while
    spreading different origins across the pool.
    """
    pool = [str(p).strip() for p in (opts.get("proxyPool") or []) if str(p).strip()]
    if pool:
        first = next((u for u in urls if u), "")
        host = ""
        try:
            host = (urlparse(first).hostname or "").lower()
        except Exception:
            host = ""
        if host:
            index = hashlib.sha256(host.encode("utf-8")).digest()[0] % len(pool)
            return pool[index]
        return pool[0]
    single = opts.get("proxy")
    return str(single) if single else None


def _fetch_many_py(urls: list[str], opts: dict[str, Any]) -> dict[str, Any]:
    timeout = float(opts.get("timeoutSecs", _DEFAULT_TIMEOUT))
    max_bytes = int(opts.get("maxBytes", _HARD_MAX_BYTES))
    method = str(opts.get("method") or "GET").upper()
    max_retries = int(opts.get("maxRetries", 2))
    delay_ms = int(opts.get("delayMs") or 0)
    concurrency = max(1, min(int(opts.get("concurrency") or 1), 32))
    request_headers = dict(opts.get("headers") or {})
    # Agent-supplied headers go through the allowlist; our own realistic browser
    # headers are trusted and set at the client level so they are not rejected
    # by _safe_headers (which does not know sec-ch-ua and friends).
    headers = _safe_headers(request_headers)
    explicit_ua = opts.get("userAgent")
    if bool(opts.get("rotateUserAgent", True)) and (
        not explicit_ua or explicit_ua == _DEFAULT_UA
    ):
        from navin.agent.tools.scrape_stealth import stealth_headers_for_urls

        profile_headers = stealth_headers_for_urls(urls)
        for key, value in profile_headers.items():
            headers.setdefault(key, value)
    else:
        headers.setdefault("User-Agent", explicit_ua or _DEFAULT_UA)
    # Public tool calls always set this explicitly. Keeping the low-level
    # helper permissive preserves MockTransport and explicit local callers.
    enforce_ssrf = bool(opts.get("enforceSsrf", False))
    from navin.security.network import PinnedDNSSyncTransport

    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "headers": headers,
        "follow_redirects": False,
        "transport": PinnedDNSSyncTransport(enforce_ssrf=enforce_ssrf),
    }
    proxy = _select_proxy(opts, urls)
    if proxy:
        if enforce_ssrf:
            raise ValueError(
                "Proxy scraping requires allow_private_network=true because DNS pinning "
                "cannot be guaranteed through an external proxy"
            )
        kwargs.pop("transport", None)
        kwargs["proxy"] = proxy
    robots = (
        RobotsCache(headers["User-Agent"], enforce_ssrf=enforce_ssrf)
        if bool(opts.get("respectRobots", True))
        else None
    )
    limiter = OriginRateLimiter(delay_ms / 1000.0)
    with httpx.Client(**kwargs) as client:
        def fetch(url: str) -> dict[str, Any]:
            return _fetch_one_py(
                client,
                url,
                max_bytes=max_bytes,
                method=method,
                max_retries=max_retries,
                robots=robots,
                limiter=limiter,
                request_options=opts,
            )

        with ThreadPoolExecutor(
            max_workers=concurrency, thread_name_prefix="navin-scrape"
        ) as pool:
            pages = list(pool.map(fetch, urls))
    if bool(opts.get("enrich", True)):
        pages = enrich_pages(pages)
    return {
        "pages": pages,
        "backend": "python",
        "observability": {
            "requested": len(urls),
            "completed": len(pages),
            "concurrency": concurrency,
            "errors": sum(1 for page in pages if page.get("error")),
        },
    }


def _load_checkpoint(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {"seen": [], "queue": [], "pages": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": [], "queue": [], "pages": []}


def _save_checkpoint(
    path: Path | None,
    *,
    seen: set[str],
    queue: deque[tuple[str, int]],
    pages: list[dict[str, Any]],
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen": list(seen),
        "queue": [[u, d] for u, d in queue],
        "pages": _strip_internal_fields([dict(p) for p in pages]),
        "savedAt": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _crawl_py(seeds: list[str], opts: dict[str, Any]) -> dict[str, Any]:
    max_pages = min(int(opts.get("maxPages", 25)), _HARD_MAX_PAGES)
    max_depth = min(int(opts.get("maxDepth", 1)), 8)
    same_domain = bool(opts.get("sameDomain", True))
    follow_pagination = bool(opts.get("followPagination", True))
    dedupe_content = bool(opts.get("dedupeContent", True))
    allow_re = compile_optional(opts.get("allow") if isinstance(opts.get("allow"), str) else None)
    deny_re = compile_optional(opts.get("deny") if isinstance(opts.get("deny"), str) else None)
    seed_hosts = {
        (urlparse(u).hostname or "").lower() for u in seeds if urlparse(u).hostname
    }
    checkpoint_raw = opts.get("checkpointPath")
    checkpoint_path = Path(checkpoint_raw) if checkpoint_raw else None
    ckpt = _load_checkpoint(checkpoint_path)

    seen: set[str] = {normalize_url(u) for u in (ckpt.get("seen") or [])}
    queue: deque[tuple[str, int]] = deque()
    for item in ckpt.get("queue") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            queue.append((str(item[0]), int(item[1])))
    pages: list[dict[str, Any]] = list(ckpt.get("pages") or [])
    hashes: set[str] = {
        content_hash(str(p.get("text") or p.get("markdown") or ""))
        for p in pages
        if p.get("text") or p.get("markdown")
    }

    for seed in seeds:
        norm = normalize_url(seed)
        if norm not in seen:
            seen.add(norm)
            queue.append((seed, 0))

    # Fetch one-by-one so we can expand the frontier (links + pagination).
    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()
        batch = _fetch_many_py([url], opts)
        page = batch["pages"][0]
        text_blob = str(page.get("text") or page.get("markdown") or "")
        digest = content_hash(text_blob)
        if dedupe_content and text_blob and digest in hashes and pages:
            page["deduped"] = True
            page["contentHash"] = digest
        else:
            if text_blob:
                hashes.add(digest)
            page["contentHash"] = digest
            pages.append(page)

        if depth < max_depth and not page.get("error"):
            candidates: list[str] = list(page.get("links") or [])
            if follow_pagination:
                raw_html = str(page.get("_rawHtml") or "")
                if raw_html:
                    candidates.extend(
                        discover_pagination_urls(raw_html, page.get("finalUrl") or url)
                    )
            for link in candidates:
                if len(seen) >= max_pages * 8:
                    break
                norm = normalize_url(link)
                if norm in seen:
                    continue
                if not link_allowed(
                    link,
                    seed_hosts=seed_hosts,
                    same_domain=same_domain,
                    allow_re=allow_re,
                    deny_re=deny_re,
                ):
                    continue
                seen.add(norm)
                # Pagination links stay at same depth so they don't burn depth budget.
                is_page = bool(
                    re.search(r"[?&](?:page|p|pg|paged|offset)=\d+", link, re.I)
                    or re.search(r"/(?:page|p|pg)/\d+", link, re.I)
                )
                next_depth = depth if is_page else depth + 1
                if next_depth <= max_depth:
                    queue.append((link, next_depth))

        page.pop("_rawHtml", None)
        if checkpoint_path and len(pages) % 10 == 0:
            _save_checkpoint(
                checkpoint_path, seen=seen, queue=queue, pages=pages,
            )

    if checkpoint_path:
        _save_checkpoint(checkpoint_path, seen=seen, queue=queue, pages=pages)

    pages = _strip_internal_fields(pages)
    if bool(opts.get("enrich", True)):
        pages = enrich_pages(pages)
    return {
        "pages": pages,
        "queued": len(queue),
        "seen": len(seen),
        "stats": crawl_stats(pages),
        "checkpoint": str(checkpoint_path) if checkpoint_path else None,
    }


def _paginate_py(seed: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Walk pagination from a single listing URL (rel=next / page params)."""
    max_pages = min(int(opts.get("maxPages", 25)), _HARD_MAX_PAGES)
    same_domain = bool(opts.get("sameDomain", True))
    host = (urlparse(seed).hostname or "").lower()
    seed_hosts = {host} if host else set()
    allow_re = compile_optional(opts.get("allow") if isinstance(opts.get("allow"), str) else None)
    deny_re = compile_optional(opts.get("deny") if isinstance(opts.get("deny"), str) else None)

    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = seed
    while current and len(pages) < max_pages:
        norm = normalize_url(current)
        if norm in seen:
            break
        seen.add(norm)
        batch = _fetch_many_py([current], {**opts, "enrich": False})
        page = batch["pages"][0]
        pages.append(page)
        if page.get("error"):
            break
        raw_html = str(page.get("_rawHtml") or "")
        page.pop("_rawHtml", None)
        nexts = discover_pagination_urls(raw_html, page.get("finalUrl") or current, limit=10)
        nxt = None
        for candidate in nexts:
            if normalize_url(candidate) in seen:
                continue
            if not link_allowed(
                candidate,
                seed_hosts=seed_hosts,
                same_domain=same_domain,
                allow_re=allow_re,
                deny_re=deny_re,
            ):
                continue
            nxt = candidate
            break
        current = nxt

    pages = _strip_internal_fields(pages)
    if bool(opts.get("enrich", True)):
        pages = enrich_pages(pages)
    return {
        "pages": pages,
        "seen": len(seen),
        "stats": crawl_stats(pages),
        "mode": "paginate",
    }


def _export_py(records: list[dict[str, Any]], fmt: str, path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lower()
    if fmt == "json":
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt == "jsonl":
        with path.open("w", encoding="utf-8") as fh:
            for row in records:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    elif fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["url", "title", "status", "text", "error"])
            writer.writeheader()
            for row in records:
                text = str(row.get("text") or row.get("markdown") or "")
                writer.writerow(
                    {
                        "url": row.get("url") or row.get("finalUrl") or "",
                        "title": row.get("title") or "",
                        "status": row.get("status") or "",
                        "text": text[:32000],
                        "error": row.get("error") or "",
                    }
                )
    elif fmt == "xml":
        root = ET.Element("pages")
        for row in records:
            page = ET.SubElement(root, "page")
            for key in ("url", "title", "status", "text", "error"):
                val = row.get(key)
                if key == "url":
                    val = row.get("url") or row.get("finalUrl") or ""
                if key == "text":
                    val = row.get("text") or row.get("markdown") or ""
                if val is None or val == "":
                    if key == "error":
                        continue
                    val = ""
                el = ET.SubElement(page, key)
                el.text = str(val)
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    elif fmt in ("md", "markdown"):
        parts = ["# Scrape export\n"]
        for i, row in enumerate(records, start=1):
            title = row.get("title") or f"Page {i}"
            parts.append(f"## {title}\n")
            parts.append(f"- URL: {row.get('url') or row.get('finalUrl') or ''}")
            parts.append(f"- Status: {row.get('status') or ''}\n")
            parts.append(str(row.get("markdown") or row.get("text") or ""))
            parts.append("\n---\n")
        path.write_text("\n".join(parts), encoding="utf-8")
    elif fmt in ("report", "html"):
        ok = sum(1 for r in records if not r.get("error"))
        rows_html = []
        for i, row in enumerate(records, start=1):
            err = row.get("error") or ""
            preview = str(row.get("text") or "")[:160]
            note = html.escape(str(err)) if err else html.escape(preview)
            rows_html.append(
                "<tr>"
                f"<td>{i}</td>"
                f"<td>{html.escape(str(row.get('title') or ''))}</td>"
                f"<td><a href=\"{html.escape(str(row.get('url') or ''))}\">"
                f"{html.escape(str(row.get('url') or ''))}</a></td>"
                f"<td>{html.escape(str(row.get('status') or ''))}</td>"
                f"<td>{note}</td></tr>"
            )
        doc = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
            "<title>Scrape report</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:960px;"
            "margin:2rem auto;padding:0 1rem}table{border-collapse:collapse;"
            "width:100%}th,td{border:1px solid #ddd;padding:.5rem;text-align:left}"
            "th{background:#f4f4f4}</style></head><body>"
            f"<h1>Scrape report</h1><p>{len(records)} page(s) - {ok} ok, "
            f"{len(records) - ok} with errors.</p>"
            "<table><thead><tr><th>#</th><th>Title</th><th>URL</th>"
            "<th>Status</th><th>Notes</th></tr></thead><tbody>"
            + "".join(rows_html)
            + "</tbody></table></body></html>"
        )
        path.write_text(doc, encoding="utf-8")
    elif fmt in ("xlsx", "excel"):
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ValueError(
                "xlsx export in Python fallback needs openpyxl "
                "(or rebuild navin-core with make native)."
            ) from exc
        wb = Workbook()
        ws = wb.active
        ws.title = "pages"
        ws.append(["url", "title", "status", "text", "error"])
        for row in records:
            text = str(row.get("text") or row.get("markdown") or "")[:32000]
            ws.append(
                [
                    row.get("url") or row.get("finalUrl") or "",
                    row.get("title") or "",
                    row.get("status") or "",
                    text,
                    row.get("error") or "",
                ]
            )
        wb.save(path)
    else:
        raise ValueError(
            f"unsupported format '{fmt}' (csv, json, jsonl, xml, xlsx, md, report)"
        )
    return {"path": str(path), "format": fmt, "count": len(records)}


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Action to run: fetch (parallel GET/POST+extract), crawl (BFS + pagination), "
            "paginate (follow next/page links from a listing), "
            "extract (HTML → structured), clean (normalize text), export "
            "(csv/json/jsonl/xml/xlsx/md/report), pipeline (crawl/fetch then export), "
            "diagnose (classify captcha/cloudflare/paywall/login/empty_shell on a URL or HTML), "
            "sitemap (parse sitemap.xml → URL list), enrich (attach wordCount/lang/fetchedAt), "
            "tables (extract HTML tables from html), "
            "solve_captcha (opt-in: get a solution token for a known challenge via the "
            "configured solver, to inject in a real browser session).",
            enum=[
                "fetch", "crawl", "paginate", "extract", "clean", "export", "pipeline",
                "diagnose", "sitemap", "enrich", "tables", "solve_captcha",
            ],
        ),
        url=StringSchema("Single seed/target URL."),
        urls=StringSchema(
            "Multiple URLs as a JSON array, or a newline/comma-separated list."
        ),
        html=StringSchema("Raw HTML for action=extract, diagnose, or tables."),
        text=StringSchema("Raw text for action=clean or diagnose."),
        records=StringSchema(
            "JSON array of page records (or {pages:[...]}) for action=export or enrich."
        ),
        format=StringSchema(
            "Export format: csv, json, jsonl, xml, xlsx, md, report.",
            enum=list(_EXPORT_FORMATS),
        ),
        path=StringSchema(
            "Workspace-relative output path for export/pipeline "
            "(e.g. scrape/results.csv)."
        ),
        max_pages=IntegerSchema(
            description="Crawl/pipeline/sitemap page cap (default from config).",
            minimum=1,
            maximum=_HARD_MAX_PAGES,
        ),
        max_depth=IntegerSchema(
            description="Crawl link depth (0 = seeds only).",
            minimum=0,
            maximum=5,
        ),
        concurrency=IntegerSchema(
            description="Parallel fetch workers.",
            minimum=1,
            maximum=32,
        ),
        method=StringSchema(
            "HTTP method for fetch (GET/POST/PUT/DELETE). Defaults to GET.",
            enum=["GET", "POST", "PUT", "DELETE"],
        ),
        json_body=StringSchema("JSON object/array request body for fetch."),
        form_body=StringSchema("JSON object encoded as an HTTP form body for fetch."),
        body_base64=StringSchema("Raw request bytes encoded as base64 for fetch."),
        headers=StringSchema(
            "JSON object of allowed request headers. Secrets are never returned or logged."
        ),
        cookies=StringSchema("JSON object of cookies to send."),
        browser_session=StringSchema(
            "Optional active Playwright session key whose cookies may be handed off."
        ),
        allow=StringSchema(
            "Optional regex: only crawl/paginate URLs matching this pattern.",
        ),
        deny=StringSchema(
            "Optional regex: skip crawl/paginate URLs matching this pattern.",
        ),
        checkpoint=StringSchema(
            "Workspace-relative checkpoint JSON path for resumeable mass crawls "
            "(e.g. scrape/checkpoint.json).",
        ),
        captcha_kind=StringSchema(
            "For action=solve_captcha: challenge type.",
            enum=["recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile"],
        ),
        sitekey=StringSchema("For action=solve_captcha: the challenge sitekey."),
        captcha_action=StringSchema(
            "For action=solve_captcha: reCAPTCHA v3 / Turnstile action name (optional)."
        ),
        required=["action"],
    )
)
class ScrapeTool(Tool):
    """Fetch, crawl, clean and export web pages at scale (Rust-accelerated)."""

    config_key = "scrape"

    @classmethod
    def config_cls(cls):
        return ScrapeToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.scrape.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace, config=ctx.config.scrape)

    def __init__(self, *, workspace: str | Path, config: ScrapeToolConfig) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "scrape"

    @property
    def description(self) -> str:
        backend = "rust" if _native_scrape() is not None else "python"
        assisted = "on" if self.config.assisted else "off"
        evasion = "on" if self.config.evasion else "off"
        return (
            "Scrape the open web at scale: parallel fetch, same-domain crawl, "
            "HTML → clean text/markdown (readability), and export to "
            "csv/json/jsonl/xml/xlsx/md/report. Requests present a realistic, "
            "rotating browser identity by default. "
            f"Backend: {backend}. Assisted walls: {assisted}. Evasion: {evasion}. "
            "When evasion is OFF, never bypass captcha/Cloudflare/paywall/login: pause "
            "and ask the user to solve/sign in via the browser tool, then resume with "
            "browser action=content. When evasion is ON, you may use the proxy pool and "
            "action=solve_captcha (user's own solver credits) to pass a challenge via a "
            "real browser session. Never touch paywalls or login walls without the user. "
            "For empty JS shells, escalate to the browser tool. Always write large "
            "results to workspace files via export/pipeline - do not dump corpora in chat."
        )

    @property
    def read_only(self) -> bool:
        return False

    def _resolve_out_path(self, raw: str) -> Path:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(raw, workspace, [access.allowed_root]),
                workspace=workspace,
                allowed_root=access.allowed_root,
                strict=False,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError(
                f"path must stay inside the project ({access.allowed_root})"
            ) from exc
        return Path(resolved)

    async def execute(
        self,
        action: str,
        url: str | None = None,
        urls: str | None = None,
        html: str | None = None,
        text: str | None = None,
        records: str | None = None,
        format: str | None = None,  # noqa: A002 - tool schema name
        path: str | None = None,
        max_pages: int | None = None,
        max_depth: int | None = None,
        concurrency: int | None = None,
        method: str | None = None,
        json_body: str | None = None,
        form_body: str | None = None,
        body_base64: str | None = None,
        headers: str | None = None,
        cookies: str | None = None,
        browser_session: str | None = None,
        allow: str | None = None,
        deny: str | None = None,
        checkpoint: str | None = None,
        captcha_kind: str | None = None,
        sitekey: str | None = None,
        captcha_action: str | None = None,
        **kwargs: Any,
    ) -> Any:
        action = (action or "").strip().lower()
        try:
            if action == "solve_captcha":
                return await self._solve_captcha(
                    kind=captcha_kind,
                    sitekey=sitekey,
                    url=url,
                    captcha_action=captcha_action,
                )

            if action == "clean":
                if not text:
                    return ToolResult.error("Error: text is required for action=clean")
                return await asyncio.to_thread(self._clean, text)

            if action == "diagnose":
                return await self._diagnose(url=url, urls=urls, html=html, text=text)

            if action == "tables":
                if not html:
                    return ToolResult.error("Error: html is required for action=tables")
                return json.dumps(
                    {"tables": extract_tables(html)},
                    ensure_ascii=False,
                    indent=2,
                )

            if action == "enrich":
                if not records:
                    return ToolResult.error("Error: records JSON is required for action=enrich")
                data = json.loads(records)
                if isinstance(data, dict) and "pages" in data:
                    pages = enrich_pages(list(data["pages"] or []))
                    data["pages"] = pages
                    return json.dumps(data, ensure_ascii=False, indent=2)
                if isinstance(data, list):
                    return json.dumps(enrich_pages(data), ensure_ascii=False, indent=2)
                return json.dumps(enrich_pages([data])[0], ensure_ascii=False, indent=2)

            if action == "sitemap":
                return await self._sitemap(url=url, urls=urls, max_pages=max_pages)

            if action == "extract":
                if not html:
                    return ToolResult.error("Error: html is required for action=extract")
                base = (url or "https://example.invalid/").strip()
                extracted = await asyncio.to_thread(self._extract, html, base)
                payload = json.loads(extracted) if isinstance(extracted, str) else extracted
                payload["tables"] = extract_tables(html)
                payload["pagination"] = discover_pagination_urls(html, base)
                if self.config.enrich:
                    payload = enrich_pages([payload])[0]
                wall = detect_wall(
                    title=str(payload.get("title") or ""),
                    text=str(payload.get("text") or ""),
                    html=html,
                )
                if wall:
                    payload["wall"] = wall
                return json.dumps(payload, ensure_ascii=False, indent=2)

            if action == "export":
                if not records:
                    return ToolResult.error("Error: records JSON is required for action=export")
                if not format:
                    return ToolResult.error("Error: format is required for action=export")
                if not path:
                    return ToolResult.error("Error: path is required for action=export")
                return await asyncio.to_thread(self._export, records, format, path)

            if action in ("fetch", "crawl", "paginate", "pipeline"):
                seed_list = _parse_url_list(urls, url)
                if not seed_list:
                    return ToolResult.error("Error: provide url or urls")
                safe, bad = _validate_urls(
                    seed_list,
                    enforce_ssrf=not self.config.allow_private_network,
                )
                if not safe:
                    return ToolResult.error(
                        "Error: no safe URLs to fetch. " + "; ".join(bad[:5])
                    )
                checkpoint_path = None
                if checkpoint:
                    checkpoint_path = str(self._resolve_out_path(checkpoint))
                parsed_json_body = json.loads(json_body) if json_body else None
                parsed_form_body = json.loads(form_body) if form_body else None
                parsed_headers = json.loads(headers) if headers else {}
                parsed_cookies = json.loads(cookies) if cookies else {}
                if parsed_form_body is not None and not isinstance(parsed_form_body, dict):
                    raise ValueError("form_body must be a JSON object")
                if not isinstance(parsed_headers, dict):
                    raise ValueError("headers must be a JSON object")
                if not isinstance(parsed_cookies, dict):
                    raise ValueError("cookies must be a JSON object")
                if browser_session:
                    handed_off = await self._browser_cookies(browser_session, safe)
                    parsed_cookies.update(handed_off)
                opts = _options_payload(
                    self.config,
                    max_pages=max_pages or self.config.max_pages,
                    max_depth=0 if max_depth is None and action == "fetch" else (max_depth if max_depth is not None else 1),
                    concurrency=concurrency or self.config.concurrency,
                    method=(method or "GET").upper(),
                    allow=allow,
                    deny=deny,
                    checkpoint_path=checkpoint_path,
                    body=body_base64,
                    json_body=parsed_json_body,
                    form_body=parsed_form_body,
                    headers=parsed_headers,
                    cookies=parsed_cookies,
                )

                if action == "fetch":
                    raw = await asyncio.to_thread(self._fetch, safe, opts)
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                    pages = annotate_pages(list(payload.get("pages") or []))
                    if self.config.enrich:
                        pages = enrich_pages(pages)
                    # The Python fetch keeps raw HTML for wall detection; strip it
                    # before it reaches the agent (up to 250 KB per page).
                    pages = _strip_internal_fields(pages)
                    payload["pages"] = pages
                    payload["stats"] = crawl_stats(pages)
                    if bad:
                        payload["rejected"] = bad
                    return await self._finalize_with_walls(payload, pages)

                if action == "paginate":
                    raw = await asyncio.to_thread(self._paginate, safe[0], opts)
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                    pages = annotate_pages(list(payload.get("pages") or []))
                    if self.config.enrich:
                        pages = enrich_pages(pages)
                    payload["pages"] = pages
                    if bad:
                        payload["rejected"] = bad
                    return await self._finalize_with_walls(payload, pages)

                if action == "crawl":
                    raw = await asyncio.to_thread(self._crawl, safe, opts)
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                    pages = annotate_pages(list(payload.get("pages") or []))
                    if self.config.enrich:
                        pages = enrich_pages(pages)
                    payload["pages"] = pages
                    if bad:
                        payload["rejected"] = bad
                    return await self._finalize_with_walls(payload, pages)

                # pipeline - export usable pages; still surface walls for the rest
                fmt = (format or "json").lower()
                out_path = path or (
                    f"scrape/export-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
                    f".{_ext_for(fmt)}"
                )
                crawl_raw = await asyncio.to_thread(self._crawl, safe, opts)
                crawl_payload = json.loads(crawl_raw) if isinstance(crawl_raw, str) else crawl_raw
                pages = annotate_pages(list(crawl_payload.get("pages") or []))
                if self.config.enrich:
                    pages = enrich_pages(pages)
                usable = [
                    p for p in pages
                    if not (isinstance(p.get("wall"), dict) and p["wall"].get("human"))
                ]
                export_info = await asyncio.to_thread(
                    self._export,
                    json.dumps({"pages": usable}),
                    fmt,
                    out_path,
                )
                export_payload = json.loads(export_info) if isinstance(export_info, str) else export_info
                summary: dict[str, Any] = {
                    "pages": len(pages),
                    "exported_pages": len(usable),
                    "ok": sum(1 for p in usable if not p.get("error") and not p.get("wall")),
                    "stats": crawl_stats(pages),
                    "export": export_payload,
                    "sample": [
                        {
                            "url": p.get("url"),
                            "title": p.get("title"),
                            "status": p.get("status"),
                            "chars": len(p.get("text") or ""),
                            "wall": (p.get("wall") or {}).get("kind") if p.get("wall") else None,
                        }
                        for p in pages[:8]
                    ],
                }
                if bad:
                    summary["rejected"] = bad
                return await self._finalize_with_walls(summary, pages)

            return ToolResult.error(
                "Error: unknown action. Use fetch, crawl, paginate, extract, clean, export, "
                "pipeline, diagnose, sitemap, enrich, or tables."
            )
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Error: scrape failed: {exc}")

    async def _solve_captcha(
        self,
        *,
        kind: str | None,
        sitekey: str | None,
        url: str | None,
        captcha_action: str | None,
    ) -> str:
        """Return a solution token for a known challenge via the configured solver.

        Opt-in: needs tools.scrape.evasion=true and a captcha_provider with an API
        key. The token is meant to be injected into a real browser session so the
        user's own solver credits pass the challenge - the scraper does not break
        any protection itself.
        """
        if not self.config.evasion:
            return ToolResult.error(
                "Error: captcha solving is opt-in. Set tools.scrape.evasion=true "
                "and tools.scrape.captcha_provider (capsolver or 2captcha) with an API key."
            )
        provider_name = (self.config.captcha_provider or "").strip()
        if not provider_name:
            return ToolResult.error(
                "Error: no captcha_provider configured (set capsolver or 2captcha)."
            )
        if not kind or not sitekey or not (url and url.strip()):
            return ToolResult.error(
                "Error: solve_captcha needs captcha_kind, sitekey, and url."
            )
        from navin.providers.captcha import (
            CaptchaChallenge,
            CaptchaSolverError,
            create_captcha_solver,
        )

        try:
            solver = create_captcha_solver(provider_name)
            solution = await solver.solve(
                CaptchaChallenge(
                    kind=kind,
                    sitekey=sitekey.strip(),
                    url=url.strip(),
                    action=(captcha_action or None),
                )
            )
        except CaptchaSolverError as exc:
            return ToolResult.error(f"Error: captcha solve failed: {exc}")
        return json.dumps(
            {
                "token": solution.token,
                "provider": solution.provider,
                "kind": solution.kind,
                "next_steps": [
                    "Inject this token into the challenge field in a real browser "
                    "session (e.g. g-recaptcha-response / cf-turnstile-response), "
                    "submit, then continue with browser action=content.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )

    async def _browser_cookies(
        self, session_key: str, urls: list[str]
    ) -> dict[str, str]:
        """Copy cookies from an existing Playwright context without exposing them."""
        from navin.agent.tools import browser as browser_mod

        async with browser_mod._SESSIONS_LOCK:
            session = browser_mod._SESSIONS.get(session_key)
        if session is None or session._context is None:
            raise ValueError(f"No active browser session named '{session_key}'")
        async with session.lock:
            rows = await session._context.cookies(urls)
        return {
            str(row["name"]): str(row["value"])
            for row in rows
            if row.get("name") and row.get("value") is not None
        }

    async def _sitemap(
        self,
        *,
        url: str | None,
        urls: str | None,
        max_pages: int | None,
    ) -> str:
        seed_list = _parse_url_list(urls, url)
        if not seed_list:
            return ToolResult.error("Error: provide sitemap url (or urls)")
        safe, bad = _validate_urls(
            seed_list,
            enforce_ssrf=not self.config.allow_private_network,
        )
        if not safe:
            return ToolResult.error("Error: no safe URLs. " + "; ".join(bad[:5]))
        limit = min(int(max_pages or self.config.max_pages), _HARD_MAX_PAGES)
        opts = _options_payload(self.config, max_pages=limit, max_depth=0, enrich=False)
        # Fetch sitemap XML without treating it as HTML extract for walls.
        collected: list[str] = []
        errors: list[str] = []
        timeout = float(opts.get("timeoutSecs", _DEFAULT_TIMEOUT))
        headers = {"User-Agent": opts.get("userAgent") or _DEFAULT_UA}
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": headers,
            "follow_redirects": False,
        }
        enforce_ssrf = not self.config.allow_private_network
        from navin.security.network import PinnedDNSSyncTransport

        kwargs["transport"] = PinnedDNSSyncTransport(enforce_ssrf=enforce_ssrf)
        sitemap_proxy = _select_proxy(opts, safe)
        if sitemap_proxy:
            if enforce_ssrf:
                return ToolResult.error(
                    "Error: proxy sitemap fetch requires allow_private_network=true"
                )
            kwargs.pop("transport", None)
            kwargs["proxy"] = sitemap_proxy
        with httpx.Client(**kwargs) as client:
            for sitemap_url in safe:
                try:
                    resp = request_with_retries(
                        client,
                        sitemap_url,
                        max_retries=int(opts.get("maxRetries", 2)),
                        max_bytes=int(opts.get("maxBytes", _HARD_MAX_BYTES)),
                        enforce_ssrf=enforce_ssrf,
                    )
                    if resp.status_code >= 400:
                        errors.append(f"{sitemap_url}: HTTP {resp.status_code}")
                        continue
                    found = parse_sitemap_urls(resp.text, limit=limit - len(collected))
                    # Nested sitemapindex → fetch child sitemaps (one level).
                    child_sitemaps = [
                        u for u in found
                        if u.rstrip("/").endswith(".xml") or "sitemap" in u.lower()
                    ]
                    page_urls = [u for u in found if u not in child_sitemaps]
                    collected.extend(page_urls)
                    for child in child_sitemaps[:10]:
                        if len(collected) >= limit:
                            break
                        try:
                            child_resp = request_with_retries(
                                client,
                                child,
                                max_retries=1,
                                max_bytes=int(opts.get("maxBytes", _HARD_MAX_BYTES)),
                                enforce_ssrf=enforce_ssrf,
                            )
                            if child_resp.status_code < 400:
                                collected.extend(
                                    parse_sitemap_urls(
                                        child_resp.text, limit=limit - len(collected)
                                    )
                                )
                        except Exception as exc:  # noqa: BLE001
                            errors.append(f"{child}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{sitemap_url}: {exc}")
                if len(collected) >= limit:
                    break
        # Dedupe preserve order
        seen: set[str] = set()
        unique: list[str] = []
        for item in collected:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        payload: dict[str, Any] = {
            "urls": unique[:limit],
            "count": min(len(unique), limit),
            "sources": safe,
        }
        if bad:
            payload["rejected"] = bad
        if errors:
            payload["errors"] = errors
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _diagnose(
        self,
        *,
        url: str | None,
        urls: str | None,
        html: str | None,
        text: str | None,
    ) -> str:
        """Classify obstacles without fetching more than necessary."""
        if html or text:
            wall = detect_wall(title="", text=text or "", html=html or "")
            return json.dumps(
                {
                    "wall": wall,
                    "policy": (
                        "Never bypass captcha/Cloudflare/paywall/login. "
                        "Ask the user to solve or sign in via the browser tool."
                        if wall and wall.get("human")
                        else "No human wall detected."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        seed_list = _parse_url_list(urls, url)
        if not seed_list:
            return ToolResult.error("Error: provide url/urls or html/text for diagnose")
        safe, bad = _validate_urls(seed_list)
        if not safe:
            return ToolResult.error("Error: no safe URLs. " + "; ".join(bad[:5]))
        raw = await asyncio.to_thread(
            self._fetch, safe[:5], _options_payload(self.config, max_pages=5, max_depth=0)
        )
        payload = json.loads(raw) if isinstance(raw, str) else raw
        pages = annotate_pages(list(payload.get("pages") or []))
        return await self._finalize_with_walls({"pages": pages, "rejected": bad}, pages)

    async def _finalize_with_walls(
        self,
        payload: dict[str, Any],
        pages: list[dict[str, Any]],
    ) -> str:
        """Attach wall summary; pause for human walls when assisted is on."""
        summary = wall_summary(pages)
        payload["walls"] = summary
        payload["bypass_policy"] = (
            "No automatic bypass. Captcha / Cloudflare / paywall / login require "
            "the user in a real browser session."
        )

        human_urls = summary.get("urls_human") or []
        soft_urls = summary.get("urls_soft") or []
        next_steps: list[str] = []

        by_kind = summary.get("by_kind") or {}
        solvable = any(k in by_kind for k in ("captcha", "cloudflare"))
        if self.config.evasion and self.config.captcha_provider and solvable and human_urls:
            payload["evasion"] = {
                "enabled": True,
                "provider": self.config.captcha_provider,
                "solvable_kinds": [
                    k for k in ("captcha", "cloudflare") if k in by_kind
                ],
            }
            next_steps.append(
                "Evasion is on: open the URL in the browser tool, read the challenge "
                "sitekey, call scrape action=solve_captcha (captcha_kind + sitekey + "
                "url) to get a token, inject it into the browser session, then submit."
            )

        if soft_urls:
            next_steps.append(
                "For empty/JS shells: browser action=navigate then action=content "
                f"(or network + response_body) on: {', '.join(soft_urls[:5])}"
            )

        if human_urls and self.config.assisted:
            decision = await self._ask_human_wall_pause(human_urls, summary)
            payload["assisted"] = {
                "paused": True,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "urls": human_urls,
            }
            if decision.allowed:
                next_steps.append(
                    "User acknowledged the wall. Open each blocked URL with the "
                    "browser tool so they can solve the captcha / Cloudflare challenge "
                    "or sign in with their own account. Then browser action=content and "
                    "scrape action=extract/export. Do NOT attempt any bypass."
                )
            else:
                next_steps.append(
                    "User declined or could not be asked. Stop on these URLs. "
                    "Explain which walls blocked the scrape and wait for instructions."
                )
        elif human_urls:
            payload["assisted"] = {"paused": False, "allowed": None, "urls": human_urls}
            next_steps.append(
                "Human wall(s) detected but tools.scrape.assisted=false. "
                "Still do NOT bypass - tell the user and offer a browser session."
            )

        if next_steps:
            payload["next_steps"] = next_steps
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def _ask_human_wall_pause(
        self,
        urls: list[str],
        summary: dict[str, Any],
    ) -> Any:
        from navin.agent.approval import ApprovalRequest, request_approval

        kinds = ", ".join(f"{k}×{v}" for k, v in sorted((summary.get("by_kind") or {}).items()))
        detail_urls = "\n".join(f"- {u}" for u in urls[:12])
        if len(urls) > 12:
            detail_urls += f"\n- ... and {len(urls) - 12} more"
        return await request_approval(
            ApprovalRequest(
                tool="scrape",
                action=f"Pause: {len(urls)} URL(s) need you in the browser",
                reason=(
                    "A captcha, Cloudflare challenge, paywall, or login wall was "
                    "detected. Navin will not bypass it. Allow to continue after "
                    "you solve or sign in via the browser tool."
                ),
                detail=f"Walls: {kinds or 'human'}\n{detail_urls}",
                consequence=(
                    "If you allow, the agent will drive the browser so you can "
                    "complete the challenge or log in yourself - no auto-bypass."
                ),
                scope="scrape.human_wall",
                # Unattended runs (cron/CLI) must not hang: return the wall report.
                allow_when_unattended=True,
            )
        )

    def _clean(self, text: str) -> str:
        mod = _native_scrape()
        if mod is not None:
            return mod.scrape_clean(text)
        return _clean_text(text)

    def _extract(self, html_text: str, base_url: str) -> str:
        mod = _native_scrape()
        if mod is not None:
            return mod.scrape_extract(html_text, base_url)
        return json.dumps(_extract_html_py(html_text, base_url), ensure_ascii=False, indent=2)

    def _fetch(self, urls: list[str], opts: dict[str, Any]) -> str:
        method = str(opts.get("method") or "GET").upper()
        mod = _native_scrape()
        native_compatible = (
            mod is not None
            and method == "GET"
            and not bool(opts.get("enforceSsrf", True))
            and not opts.get("body")
            and opts.get("jsonBody") is None
            and opts.get("formBody") is None
            and not opts.get("headers")
            and not opts.get("cookies")
        )
        if native_compatible:
            allowed = urls
            rejected: list[dict[str, Any]] = []
            if bool(opts.get("respectRobots", True)):
                headers = {"User-Agent": opts.get("userAgent") or _DEFAULT_UA}
                robots = RobotsCache(headers["User-Agent"], enforce_ssrf=False)
                with httpx.Client(
                    timeout=float(opts.get("timeoutSecs", _DEFAULT_TIMEOUT)),
                    headers=headers,
                    follow_redirects=False,
                ) as client:
                    allowed = []
                    for url in urls:
                        if robots.allowed(client, url):
                            allowed.append(url)
                        else:
                            rejected.append(
                                {
                                    "url": url,
                                    "status": 0,
                                    "error": "Blocked by robots.txt",
                                }
                            )
            if allowed:
                payload = json.loads(
                    mod.scrape_fetch(json.dumps(allowed), json.dumps(opts))
                )
            else:
                payload = {"pages": []}
            by_url = {
                str(page.get("url")): page for page in payload.get("pages", [])
            }
            rejected_by_url = {str(page["url"]): page for page in rejected}
            payload["pages"] = [
                by_url.get(url)
                or rejected_by_url.get(url)
                or {"url": url, "status": 0, "error": "Native fetch returned no record"}
                for url in urls
            ]
            payload["backend"] = "rust"
            payload["observability"] = {
                "requested": len(urls),
                "completed": len(payload["pages"]),
                "concurrency": int(opts.get("concurrency") or 1),
                "errors": sum(1 for page in payload["pages"] if page.get("error")),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return json.dumps(_fetch_many_py(urls, opts), ensure_ascii=False, indent=2)

    def _crawl(self, seeds: list[str], opts: dict[str, Any]) -> str:
        # Mass crawl / pagination / checkpoint / allow-deny live in Python for V1+.
        return json.dumps(_crawl_py(seeds, opts), ensure_ascii=False, indent=2)

    def _paginate(self, seed: str, opts: dict[str, Any]) -> str:
        return json.dumps(_paginate_py(seed, opts), ensure_ascii=False, indent=2)

    def _export(self, records_json: str, fmt: str, path: str) -> str:
        out = self._resolve_out_path(path)
        mod = _native_scrape()
        if mod is not None:
            return mod.scrape_export(records_json, fmt, str(out))
        data = json.loads(records_json)
        if isinstance(data, dict) and "pages" in data:
            rows = data["pages"]
        elif isinstance(data, list):
            rows = data
        else:
            rows = [data]
        return json.dumps(_export_py(rows, fmt, out), ensure_ascii=False, indent=2)


def _ext_for(fmt: str) -> str:
    return {
        "csv": "csv",
        "json": "json",
        "jsonl": "jsonl",
        "xml": "xml",
        "xlsx": "xlsx",
        "excel": "xlsx",
        "md": "md",
        "markdown": "md",
        "report": "html",
        "html": "html",
    }.get(fmt, "json")
