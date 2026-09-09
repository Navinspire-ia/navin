# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SEO crawler built on the Scraping transport and request primitives."""

from __future__ import annotations

import html
import re
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from navin.agent.tools.scrape import _fetch_one_py
from navin.agent.tools.scrape_ops import OriginRateLimiter, RobotsCache
from navin.security.network import PinnedDNSSyncTransport
from navin.seo.models import PageRecord

Fetch = Callable[[httpx.Client, str], dict[str, Any]]


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _attr(tag: str, name: str) -> str | None:
    match = re.search(
        rf"\b{name}\s*=\s*(?:[\"']([^\"']*)[\"']|([^\s>]+))",
        tag,
        re.I,
    )
    return (match.group(1) or match.group(2)).strip() if match else None


def parse_page(record: dict[str, Any]) -> PageRecord:
    raw = str(record.get("_rawHtml") or record.get("html") or "")
    final_url = str(record.get("finalUrl") or record.get("final_url") or record.get("url") or "")
    titles = re.findall(r"<title\b[^>]*>([\s\S]*?)</title>", raw, re.I)
    h1 = [_text(value) for value in re.findall(r"<h1\b[^>]*>([\s\S]*?)</h1>", raw, re.I)]
    description = ""
    robots = ""
    for tag in re.findall(r"<meta\b[^>]*>", raw, re.I):
        name = (_attr(tag, "name") or _attr(tag, "property") or "").lower()
        content = html.unescape(_attr(tag, "content") or "")
        if name == "description":
            description = content
        elif name == "robots":
            robots = content
    canonical = None
    hreflang: dict[str, str] = {}
    for tag in re.findall(r"<link\b[^>]*>", raw, re.I):
        rel = (_attr(tag, "rel") or "").lower().split()
        href = _attr(tag, "href")
        if not href:
            continue
        absolute = urljoin(final_url, href)
        if "canonical" in rel:
            canonical = absolute
        if "alternate" in rel and (lang := _attr(tag, "hreflang")):
            hreflang[lang.lower()] = absolute
    return PageRecord(
        url=str(record.get("url") or final_url),
        final_url=final_url,
        status=int(record.get("status") or 0),
        content_type=str(record.get("contentType") or ""),
        html=raw,
        text=str(record.get("text") or ""),
        title=_text(titles[0]) if titles else str(record.get("title") or ""),
        meta_description=description,
        h1=[value for value in h1 if value],
        canonical=canonical,
        hreflang=hreflang,
        robots=robots,
        links=sorted({str(value) for value in record.get("links") or []}),
        redirect_chain=list(record.get("redirectChain") or []),
        error=record.get("error"),
    )


class SeoCrawler:
    """Bounded same-origin crawler with injectable transport for tests."""

    def __init__(
        self,
        *,
        timeout: float = 30,
        max_bytes: int = 5 * 1024 * 1024,
        user_agent: str = "NavinSEO/1.0",
        respect_robots: bool = True,
        allow_private_network: bool = False,
        transport: httpx.BaseTransport | None = None,
        fetch: Fetch | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        self.allow_private_network = allow_private_network
        self.transport = transport
        self.fetch = fetch

    def crawl(self, seed: str, *, max_pages: int = 50, max_depth: int = 2) -> list[PageRecord]:
        host = (urlparse(seed).hostname or "").lower()
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        seen: set[str] = set()
        pages: list[PageRecord] = []
        transport = self.transport or PinnedDNSSyncTransport(
            enforce_ssrf=not self.allow_private_network
        )
        robots = (
            RobotsCache(self.user_agent, enforce_ssrf=not self.allow_private_network)
            if self.respect_robots
            else None
        )
        limiter = OriginRateLimiter()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=False,
            transport=transport,
        ) as client:
            while queue and len(pages) < max_pages:
                url, depth = queue.popleft()
                normalized = url.split("#", 1)[0]
                if normalized in seen:
                    continue
                seen.add(normalized)
                if self.fetch:
                    record = self.fetch(client, url)
                else:
                    record = _fetch_one_py(
                        client,
                        url,
                        max_bytes=self.max_bytes,
                        max_retries=2,
                        robots=robots,
                        limiter=limiter,
                        request_options={
                            "followRedirects": True,
                            "enforceSsrf": not self.allow_private_network,
                        },
                    )
                page = parse_page(record)
                pages.append(page)
                if depth >= max_depth or page.error:
                    continue
                for link in page.links:
                    parsed = urlparse(link)
                    if parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() == host:
                        queue.append((link, depth + 1))
        return pages
