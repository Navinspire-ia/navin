# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pagination, link rules, and mass-crawl helpers for the scrape tool."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

# rel=next, classic query pages, path pages, "Load more" style hints in href text.
_REL_NEXT_RE = re.compile(
    r"""<a\b[^>]*\brel\s*=\s*["'][^"']*\bnext\b[^"']*["'][^>]*href\s*=\s*["']([^"']+)["']"""
    r"""|"""
    r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*\brel\s*=\s*["'][^"']*\bnext\b[^"']*["']""",
    re.I,
)
_LINK_HREF_RE = re.compile(
    r"""<a\b([^>]*)href\s*=\s*["']([^"']+)["']([^>]*)>([\s\S]*?)</a>""",
    re.I,
)
_NEXT_LABEL_RE = re.compile(
    r"^\s*(next|suivant|siguiente|weiter|próxima|prossima|→|»|›)\s*$",
    re.I,
)
_PAGE_QUERY_KEYS = ("page", "p", "pg", "paged", "offset", "start", "pageNumber", "page_num")
_PATH_PAGE_RE = re.compile(r"^(.*?)/(?:page|p|pg)/(\d+)/?(?:\?.*)?$", re.I)


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def normalize_url(url: str) -> str:
    """Drop fragment; keep query. Lowercase scheme/host."""
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return url
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        )
    )


def discover_pagination_urls(html: str, base_url: str, *, limit: int = 40) -> list[str]:
    """Find next/numbered pagination URLs from HTML."""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        if not raw or raw.startswith("#") or raw.lower().startswith("javascript:"):
            return
        absolute = normalize_url(urljoin(base_url, raw.strip()))
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            return
        if absolute in seen or absolute == normalize_url(base_url):
            return
        seen.add(absolute)
        found.append(absolute)

    for m in _REL_NEXT_RE.finditer(html or ""):
        add(m.group(1) or m.group(2) or "")

    for m in _LINK_HREF_RE.finditer(html or ""):
        attrs = f"{m.group(1) or ''}{m.group(3) or ''}"
        href = m.group(2) or ""
        label = re.sub(r"<[^>]+>", " ", m.group(4) or "")
        label = re.sub(r"\s+", " ", label).strip()
        if re.search(r"\brel\s*=\s*['\"][^'\"]*\bnext\b", attrs, re.I):
            add(href)
            continue
        if _NEXT_LABEL_RE.match(label):
            add(href)
            continue
        # Numbered page links: page=2, /page/3, etc.
        if _looks_like_page_href(href, base_url):
            add(href)

    # Synthesize next query page from current URL when HTML has no links but
    # the URL already looks paginated (agent can still walk manually).
    for synthesized in synthesize_next_pages(base_url, count=3):
        add(synthesized)

    return found[:limit]


def _looks_like_page_href(href: str, base_url: str) -> bool:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    qs = parse_qs(parsed.query)
    for key in _PAGE_QUERY_KEYS:
        if key in qs:
            try:
                return int(qs[key][0]) >= 1
            except (TypeError, ValueError):
                return True
    if _PATH_PAGE_RE.match(parsed.path or ""):
        return True
    # Bare ?page=N style relative
    if re.search(r"[?&](?:page|p|pg|paged|offset)=\d+", href, re.I):
        return True
    if re.search(r"/(?:page|p|pg)/\d+", href, re.I):
        return True
    return False


def synthesize_next_pages(url: str, *, count: int = 5) -> list[str]:
    """If URL already has a page param/path, emit the next N pages."""
    parsed = urlparse(url)
    out: list[str] = []
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for key in _PAGE_QUERY_KEYS:
        if key not in qs:
            continue
        try:
            current = int(qs[key][0])
        except (TypeError, ValueError):
            current = 1
        for i in range(1, count + 1):
            new_qs = {k: list(v) for k, v in qs.items()}
            new_qs[key] = [str(current + i)]
            # Flatten for urlencode
            flat = []
            for k, vals in new_qs.items():
                for v in vals:
                    flat.append((k, v))
            out.append(
                urlunparse(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        "",
                        urlencode(flat),
                        "",
                    )
                )
            )
        return out

    path_m = _PATH_PAGE_RE.match(parsed.path or "")
    if path_m:
        prefix, num_s = path_m.group(1), path_m.group(2)
        try:
            current = int(num_s)
        except ValueError:
            current = 1
        for i in range(1, count + 1):
            out.append(
                urlunparse(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        f"{prefix}/page/{current + i}",
                        "",
                        parsed.query,
                        "",
                    )
                )
            )
    return out


def link_allowed(
    link: str,
    *,
    seed_hosts: set[str],
    same_domain: bool,
    allow_re: re.Pattern[str] | None = None,
    deny_re: re.Pattern[str] | None = None,
) -> bool:
    host = (urlparse(link).hostname or "").lower()
    if same_domain and host not in seed_hosts:
        return False
    if deny_re and deny_re.search(link):
        return False
    if allow_re and not allow_re.search(link):
        return False
    return True


def compile_optional(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern or not str(pattern).strip():
        return None
    return re.compile(str(pattern), re.I)


def crawl_stats(pages: list[dict[str, Any]]) -> dict[str, Any]:
    ok = sum(1 for p in pages if not p.get("error") and not (p.get("wall") or {}).get("human"))
    errors = sum(1 for p in pages if p.get("error"))
    walls = sum(1 for p in pages if p.get("wall"))
    bytes_total = sum(int(p.get("bytes") or 0) for p in pages)
    return {
        "pages": len(pages),
        "ok": ok,
        "errors": errors,
        "walls": walls,
        "bytes": bytes_total,
        "uniqueHosts": len({
            (urlparse(str(p.get("url") or "")).hostname or "").lower()
            for p in pages
            if p.get("url")
        }),
    }
