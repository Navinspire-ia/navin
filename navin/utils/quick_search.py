# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Small synchronous web search for desk research (DuckDuckGo, no key).

Uses the `ddgs` package when installed and falls back to the DuckDuckGo HTML
endpoint otherwise. Returns title / url / snippet rows only: callers decide
what to fetch, and never fetch closed hosts.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

from loguru import logger

_DDG_HTML = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _text(raw: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", raw or "")).split())


def _unwrap(href: str) -> str:
    try:
        parsed = urllib.parse.urlparse(href if "//" in href else f"https://{href}")
        if parsed.path.startswith("/l/") and "uddg=" in (parsed.query or ""):
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return urllib.parse.unquote(target)
    except ValueError:
        return href
    return href


def _search_html(query: str, limit: int) -> list[dict[str, str]]:
    data = urllib.parse.urlencode({"q": query}).encode("utf-8")
    req = urllib.request.Request(
        _DDG_HTML,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; NavinDesk/1.0; +https://navin.ai)",
            "Accept": "text/html",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.info("quick search skipped: {}", exc)
        return []
    if "anomaly-modal" in body:
        return []
    links = _RESULT_RE.findall(body)
    snippets = [_text(item) for item in _SNIPPET_RE.findall(body)]
    rows: list[dict[str, str]] = []
    for index, (href, raw_title) in enumerate(links[:limit]):
        rows.append(
            {
                "title": _text(raw_title),
                "url": _unwrap(href),
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
    return rows


def search_text(query: str, limit: int = 6) -> list[dict[str, str]]:
    """Top results for *query*: ``[{"title", "url", "snippet"}, ...]``."""
    query = " ".join(str(query or "").split())
    if not query:
        return []
    try:
        from ddgs import DDGS
    except ImportError:
        return _search_html(query, limit)
    try:
        raw = DDGS(timeout=10).text(query, max_results=limit)
    except Exception as exc:  # noqa: BLE001 - provider hiccup, fall back to HTML
        logger.info("ddgs skipped: {}", exc)
        return _search_html(query, limit)
    rows: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("href") or item.get("url") or ""),
                "snippet": str(item.get("body") or ""),
            }
        )
    return rows[:limit]
