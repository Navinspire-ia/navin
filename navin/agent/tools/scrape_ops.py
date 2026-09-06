"""Scrape HTTP helpers: retry, robots, sitemap, enrich, tables.

Used by the Python scrape fallback and shared with agent-facing actions.
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

_RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_MAX_REDIRECTS = 5


def enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    """Add wordcount / lang / fetched_at / OG hints without dropping fields."""
    text = str(record.get("text") or record.get("markdown") or "")
    meta = dict(record.get("meta") or {})
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    record["wordCount"] = len(words)
    record["fetchedAt"] = record.get("fetchedAt") or datetime.now(timezone.utc).isoformat()
    lang = meta.get("og:locale") or meta.get("language") or meta.get("lang")
    if not lang:
        html_blob = str(record.get("html") or "")
        m = re.search(r'<html[^>]*\blang=["\']([^"\']+)', html_blob, re.I)
        if m:
            lang = m.group(1)
    if lang:
        record["lang"] = lang
    if meta.get("og:title") and not record.get("title"):
        record["title"] = meta["og:title"]
    if meta.get("og:description") and "description" not in record:
        record["description"] = meta["og:description"]
    record["meta"] = meta
    return record


def enrich_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_record(dict(p)) for p in pages]


def extract_tables(html_text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Extract simple HTML tables as list-of-rows (header + cells)."""
    tables: list[dict[str, Any]] = []
    for i, match in enumerate(re.finditer(r"<table\b[\s\S]*?</table>", html_text, re.I)):
        if i >= limit:
            break
        chunk = match.group(0)
        rows: list[list[str]] = []
        for tr in re.finditer(r"<tr\b[\s\S]*?</tr>", chunk, re.I):
            cells = [
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c.group(1))).strip()
                for c in re.finditer(r"<t[dh]\b[^>]*>([\s\S]*?)</t[dh]>", tr.group(0), re.I)
            ]
            if cells:
                rows.append(cells)
        if rows:
            tables.append({"index": i, "rows": rows, "rowCount": len(rows)})
    return tables


class RobotsCache:
    """Thread-safe per-origin robots.txt cache for one scrape run."""

    def __init__(self, user_agent: str, *, enforce_ssrf: bool = True) -> None:
        self.user_agent = user_agent
        self.enforce_ssrf = enforce_ssrf
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def allowed(self, client: httpx.Client, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._guard:
            lock = self._locks.setdefault(origin, threading.Lock())
        with lock:
            if origin not in self._parsers:
                rp = RobotFileParser()
                robots_url = urljoin(origin + "/", "robots.txt")
                try:
                    resp = request_with_retries(
                        client,
                        robots_url,
                        max_retries=1,
                        enforce_ssrf=self.enforce_ssrf,
                    )
                    if resp.status_code >= 400:
                        self._parsers[origin] = None
                    else:
                        rp.parse(resp.text.splitlines())
                        self._parsers[origin] = rp
                except Exception:
                    self._parsers[origin] = None
        rp = self._parsers[origin]
        if rp is None:
            return True
        try:
            return bool(rp.can_fetch(self.user_agent, url))
        except Exception:
            return True

    def crawl_delay(self, url: str) -> float | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._parsers.get(origin)
        if rp is None:
            return None
        try:
            delay = rp.crawl_delay(self.user_agent)
            return float(delay) if delay is not None else None
        except Exception:
            return None


class OriginRateLimiter:
    """Reserve request slots independently for each URL origin."""

    def __init__(self, default_delay_seconds: float = 0.0) -> None:
        self.default_delay_seconds = max(0.0, default_delay_seconds)
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, url: str, crawl_delay: float | None = None) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        delay = max(self.default_delay_seconds, float(crawl_delay or 0.0))
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(origin, now))
            self._next[origin] = slot + delay
        wait_for = slot - time.monotonic()
        if wait_for > 0:
            time.sleep(wait_for)


def _read_response_capped(response: httpx.Response, max_bytes: int) -> httpx.Response:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    try:
        for chunk in response.iter_bytes():
            remaining = max_bytes - total
            if remaining <= 0:
                truncated = True
                break
            chunks.append(chunk[:remaining])
            total += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
                break
    finally:
        response.close()
    headers = response.headers.copy()
    # iter_bytes() already inflated gzip / deflate / brotli. Keeping the
    # Content-Encoding header would make the rebuilt Response decode the
    # plain bytes a second time ("incorrect header check", "brotli: decoder
    # failed") and every compressed page would come back as a fetch error.
    for name in ("content-encoding", "content-length", "transfer-encoding"):
        if name in headers:
            del headers[name]
    if truncated:
        headers["x-navin-truncated"] = "true"
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=b"".join(chunks),
        request=response.request,
        extensions=response.extensions,
    )
def request_with_retries(
    client: httpx.Client,
    url: str,
    *,
    method: str = "GET",
    max_retries: int = 2,
    body: bytes | None = None,
    json_body: Any | None = None,
    form_body: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    follow_redirects: bool = True,
    enforce_ssrf: bool = True,
) -> httpx.Response:
    """Bounded request with safe redirects and transient retries."""
    method = (method or "GET").upper()
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            current = url
            current_method = method
            for redirect_count in range(_MAX_REDIRECTS + 1):
                from navin.security.network import validate_url_target

                if enforce_ssrf:
                    ok, reason = validate_url_target(current, enforce_ssrf=True)
                    if not ok:
                        raise ValueError(f"URL blocked: {reason}")
                request = client.build_request(
                    current_method,
                    current,
                    content=body if json_body is None and form_body is None else None,
                    json=json_body,
                    data=form_body,
                    headers=headers,
                )
                streamed = client.send(request, stream=True, follow_redirects=False)
                resp = _read_response_capped(streamed, max_bytes)
                location = resp.headers.get("location")
                if not (
                    follow_redirects
                    and location
                    and 300 <= resp.status_code < 400
                ):
                    break
                if redirect_count >= _MAX_REDIRECTS:
                    raise httpx.TooManyRedirects(
                        f"Exceeded {_MAX_REDIRECTS} redirects", request=request
                    )
                current = urljoin(str(resp.url), location)
                if enforce_ssrf:
                    ok, reason = validate_url_target(current, enforce_ssrf=True)
                    if not ok:
                        raise ValueError(f"Redirect blocked: {reason}")
                if resp.status_code == 303 or (
                    resp.status_code in (301, 302) and current_method == "POST"
                ):
                    current_method = "GET"
                    body = None
                    json_body = None
                    form_body = None
            if resp.status_code in _RETRY_STATUSES and attempt < max_retries:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 30))
                else:
                    time.sleep(min(2**attempt, 8))
                continue
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_retries:
                raise
            time.sleep(min(2**attempt, 8))
    assert last_exc is not None
    raise last_exc


def parse_sitemap_urls(xml_text: str, *, limit: int = 500) -> list[str]:
    """Parse sitemap / sitemapindex XML into absolute URL list."""
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        # Fallback: regex for <loc>
        for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, re.I):
            urls.append(m.group(1).strip())
            if len(urls) >= limit:
                break
        return urls

    tag = root.tag.lower()
    # Handle default namespaces by ignoring NS in tag names.
    def local(name: str) -> str:
        if "}" in name:
            return name.rsplit("}", 1)[-1].lower()
        return name.lower()

    if local(root.tag) == "sitemapindex":
        for child in root:
            if local(child.tag) != "sitemap":
                continue
            for loc in child:
                if local(loc.tag) == "loc" and (loc.text or "").strip():
                    urls.append(loc.text.strip())
                    if len(urls) >= limit:
                        return urls
    else:
        for child in root:
            if local(child.tag) != "url":
                continue
            for loc in child:
                if local(loc.tag) == "loc" and (loc.text or "").strip():
                    urls.append(loc.text.strip())
                    if len(urls) >= limit:
                        return urls
    # Some sitemaps nest loc at top level
    if not urls:
        for el in root.iter():
            if local(el.tag) == "loc" and (el.text or "").strip():
                urls.append(el.text.strip())
                if len(urls) >= limit:
                    break
    _ = tag  # silence unused in some paths
    return urls
