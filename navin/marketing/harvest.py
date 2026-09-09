# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Harvest a live product site the user bound: brand, pages, images, colors."""

from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from navin.marketing.assets import download_image
from navin.marketing.store import MarketingStore

_SKIP_HEX = {"#fff", "#ffffff", "#000", "#000000", "#111", "#111111", "#222", "#222222"}
_PAGE_HINTS = ("about", "pricing", "product", "features", "faq", "blog", "docs", "contact", "solutions")
_SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "twitter.com": "x",
    "www.twitter.com": "x",
    "x.com": "x",
    "www.x.com": "x",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "youtu.be": "youtube",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
}


def fetch_html(url: str, *, timeout_s: float = 10.0, limit: int = 180_000) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    request = Request(
        raw,
        headers={"User-Agent": "NavinMarketing/1.0 (+local desk harvest)"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - user-bound product URL
            return response.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _clean(text: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()[:limit]


def _attr(tag: str, name: str) -> str:
    match = re.search(
        rf'''{name}\s*=\s*["']([^"']+)["']''',
        tag,
        re.I,
    )
    return (match.group(1) if match else "").strip()


def _metas(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in re.findall(r"<meta\b[^>]*>", html, re.I):
        key = (_attr(tag, "property") or _attr(tag, "name") or "").lower()
        content = _attr(tag, "content")
        if key and content:
            found[key] = _clean(content, 320)
    return found


def _abs(base: str, href: str) -> str:
    raw = (href or "").strip()
    if not raw or raw.startswith("data:") or raw.startswith("javascript:"):
        return ""
    return urljoin(base, raw)


def parse_site_html(html: str, base: str) -> dict[str, Any]:
    """Deterministic extract from HTML. Used by harvest and tests (no network)."""
    metas = _metas(html)
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if match:
        title = _clean(match.group(1), 160)
    name = metas.get("og:site_name") or title.split("|")[0].split("·")[0].strip()
    one_liner = metas.get("og:description") or metas.get("description") or metas.get("twitter:description") or ""
    logo = ""
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = _attr(tag, "rel").lower()
        href = _abs(base, _attr(tag, "href"))
        if href and any(part in rel for part in ("icon", "apple-touch-icon")):
            logo = logo or href
    og_image = metas.get("og:image") or metas.get("twitter:image") or ""
    if og_image:
        og_image = _abs(base, og_image)
    images: list[str] = []
    if og_image:
        images.append(og_image)
    if logo:
        images.append(logo)
    for tag in re.findall(r"<img\b[^>]*>", html, re.I):
        src = _abs(base, _attr(tag, "src") or _attr(tag, "data-src"))
        if src and src not in images:
            images.append(src)
        if len(images) >= 10:
            break
    colors: list[str] = []
    theme = metas.get("theme-color")
    if theme and theme.lower() not in _SKIP_HEX:
        colors.append(theme if theme.startswith("#") else f"#{theme.lstrip('#')}")
    for hex_color in re.findall(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b", html):
        if hex_color.lower() in _SKIP_HEX:
            continue
        if hex_color not in colors:
            colors.append(hex_color)
        if len(colors) >= 6:
            break
    fonts: list[str] = []
    for href in re.findall(r'https?://fonts\.googleapis\.com/css2?\?[^"\']+', html, re.I):
        families = re.findall(r"family=([^&:]+)", href)
        for family in families:
            label = family.replace("+", " ").strip()
            if label and label not in fonts:
                fonts.append(label)
    headings = [
        _clean(re.sub(r"<[^>]+>", "", block), 120)
        for block in re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, re.I | re.S)
    ]
    headings = [item for item in headings if item][:8]
    ctas: list[str] = []
    for tag in re.findall(r"<a\b[^>]*>.*?</a>|<button\b[^>]*>.*?</button>", html, re.I | re.S):
        label = _clean(re.sub(r"<[^>]+>", "", tag), 48)
        if label and 2 < len(label) < 40 and label not in ctas:
            ctas.append(label)
        if len(ctas) >= 8:
            break
    pages: list[dict[str, str]] = []
    social: dict[str, str] = {}
    seen_pages: set[str] = set()
    host = urlparse(base).netloc.lower()
    for tag in re.findall(r"<a\b[^>]*>", html, re.I):
        href = _abs(base, _attr(tag, "href"))
        if not href:
            continue
        parsed = urlparse(href)
        netloc = parsed.netloc.lower()
        channel = _SOCIAL_HOSTS.get(netloc)
        if channel and channel not in social:
            social[channel] = href.split("?")[0]
            continue
        if netloc and netloc != host:
            continue
        path = (parsed.path or "/").lower()
        if any(hint in path for hint in _PAGE_HINTS) and href not in seen_pages:
            seen_pages.add(href)
            pages.append({"url": href, "path": parsed.path or "/"})
        if len(pages) >= 6:
            break
    keywords = [part.strip() for part in (metas.get("keywords") or "").split(",") if part.strip()]
    if not keywords:
        keywords = [item.lower() for item in headings[:6] if item]
    return {
        "site": base,
        "name": name,
        "one_liner": one_liner[:240],
        "description": one_liner[:400],
        "logo": logo,
        "og_image": og_image,
        "images": images[:8],
        "colors": colors[:6],
        "fonts": fonts[:4],
        "headings": headings,
        "ctas": ctas,
        "pages": pages,
        "social": social,
        "keywords": keywords[:16],
        "title": title,
    }


def harvest_live_site(url: str, *, timeout_s: float = 10.0, fetch=fetch_html) -> dict[str, Any]:
    """Fetch the homepage plus a few same-origin pages. Empty dict if unbound."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    home = fetch(raw, timeout_s=timeout_s)
    if not home:
        return {}
    report = parse_site_html(home, raw)
    extra_pages: list[dict[str, Any]] = []
    for page in report.get("pages") or []:
        href = str(page.get("url") or "")
        if not href or href.rstrip("/") == raw.rstrip("/"):
            continue
        html = fetch(href, timeout_s=min(timeout_s, 8.0))
        if not html:
            extra_pages.append({**page, "title": "", "description": ""})
            continue
        parsed_page = parse_site_html(html, href)
        extra_pages.append(
            {
                "url": href,
                "path": page.get("path") or "",
                "title": parsed_page.get("name") or parsed_page.get("title") or "",
                "description": parsed_page.get("one_liner") or "",
            }
        )
        for image in parsed_page.get("images") or []:
            if image not in report["images"] and len(report["images"]) < 10:
                report["images"].append(image)
        if len(extra_pages) >= 3:
            break
    if extra_pages:
        report["pages"] = extra_pages
    return report


def apply_harvest(store: MarketingStore, report: dict[str, Any], *, download: bool = True) -> dict[str, Any]:
    """Write harvest + fill empty brand / product / SEO / site creatives."""
    if not isinstance(report, dict) or not report.get("site"):
        return store.save_harvest(report if isinstance(report, dict) else {})
    images: list[dict[str, Any]] = []
    for index, url in enumerate(report.get("images") or []):
        if url == report.get("logo"):
            role = "logo"
        elif url == report.get("og_image"):
            role = "og"
        else:
            role = "site"
        if download:
            saved = download_image(store, str(url), hint=f"{role}-{index}")
            if saved:
                images.append({**saved, "role": role})
                continue
        images.append({"url": url, "preview": url, "name": "", "path": "", "role": role})
    logo_preview = next((item.get("preview") for item in images if item.get("role") == "logo"), report.get("logo") or "")
    saved_report = store.save_harvest(
        {
            **report,
            "images": images,
            "logo_preview": logo_preview,
        }
    )
    brand = store.load_brand()
    brand_patch: dict[str, Any] = {"site": report["site"]}
    if report.get("description") and not str(brand.get("description") or "").strip():
        brand_patch["description"] = report["description"]
    if report.get("name") and not str(brand.get("company") or "").strip():
        brand_patch["company"] = report["name"]
        brand_patch["product"] = brand.get("product") or report["name"]
    if report.get("colors"):
        brand_patch["colors"] = report["colors"]
    if report.get("fonts"):
        brand_patch["fonts"] = report["fonts"]
    if logo_preview or report.get("logo"):
        brand_patch["logo"] = logo_preview or report.get("logo")
    store.save_brand(brand_patch)
    product = store.load_product()
    shots = [str(item.get("preview") or item.get("url") or "") for item in images if item.get("preview") or item.get("url")]
    store.save_product(
        {
            "site": report["site"],
            "name": product.get("name") or report.get("name") or "",
            "one_liner": product.get("one_liner") or report.get("one_liner") or "",
            "screenshots": shots or product.get("screenshots") or [],
            "docs": list(dict.fromkeys([*(product.get("docs") or []), *[str(p.get("url") or "") for p in (report.get("pages") or []) if p.get("url")]])),
        }
    )
    pages = []
    pages.append(
        {
            "url": report["site"],
            "title": report.get("title") or report.get("name") or "Home",
            "description": report.get("one_liner") or "",
            "kind": "home",
        }
    )
    for page in report.get("pages") or []:
        if not isinstance(page, dict):
            continue
        pages.append(
            {
                "url": page.get("url") or "",
                "title": page.get("title") or page.get("path") or page.get("url") or "",
                "description": page.get("description") or "",
                "kind": "inner",
            }
        )
    store.save_seo(
        {
            "keywords": report.get("keywords") or [],
            "pages": pages,
            "rankings": store.load_seo().get("rankings") or [],
        }
    )
    known = {
        str(row.get("source_url") or "")
        for row in store.load_creatives()
        if str(row.get("source_url") or "").strip()
    }
    for item in images[:8]:
        preview = str(item.get("preview") or item.get("url") or "")
        source_url = str(item.get("url") or "")
        if not preview or (source_url and source_url in known):
            continue
        role = str(item.get("role") or "site")
        store.upsert_creative(
            {
                "kind": "image",
                "placement": f"site {role}",
                "prompt": f"Harvested {role} from {report['site']}",
                "status": "harvested",
                "preview": preview,
                "source": "site",
                "source_url": source_url,
                "asset": item.get("name") or "",
                "path": item.get("path") or "",
            }
        )
    store.append_journal({"kind": "harvest", "text": f"harvested {report['site']} ({len(images)} images, {len(pages)} pages)"})
    return saved_report
