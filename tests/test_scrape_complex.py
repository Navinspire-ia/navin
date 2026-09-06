"""Deep tests: pagination, mass crawl, checkpoint, walls, scroll helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from navin.agent.tools.scrape import (
    ScrapeTool,
    ScrapeToolConfig,
    _crawl_py,
    _paginate_py,
    annotate_pages,
    detect_wall,
)
from navin.agent.tools.scrape_pagination import (
    content_hash,
    crawl_stats,
    discover_pagination_urls,
    link_allowed,
    normalize_url,
    synthesize_next_pages,
)

LISTING_P1 = """
<html><head><title>Shop p1</title></head><body>
<main>
  <a href="/item/1">Item 1</a>
  <a href="/item/2">Item 2</a>
  <a rel="next" href="/shop?page=2">Next</a>
  <a href="/shop?page=2">2</a>
  <a href="/shop?page=3">3</a>
</main>
</body></html>
"""

LISTING_P2 = """
<html><head><title>Shop p2</title></head><body>
<main>
  <a href="/item/3">Item 3</a>
  <a rel="next" href="/shop?page=3">Next</a>
</main>
</body></html>
"""

LISTING_P3 = """
<html><head><title>Shop p3</title></head><body>
<main>
  <a href="/item/4">Item 4</a>
</main>
</body></html>
"""

ITEM = """
<html><head><title>Item {n}</title></head><body>
<main><h1>Item {n}</h1><p>Product detail number {n} with enough text.</p></main>
</body></html>
"""


def _response(url: str, html: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        text=html,
        headers={"content-type": "text/html; charset=utf-8"},
        request=httpx.Request("GET", url),
    )


class PaginationHelpersTest(unittest.TestCase):
    def test_discover_rel_next_and_page_links(self) -> None:
        urls = discover_pagination_urls(LISTING_P1, "https://shop.example/shop?page=1")
        joined = " ".join(urls)
        self.assertIn("page=2", joined)
        self.assertTrue(any("page=2" in u for u in urls))

    def test_synthesize_query_pages(self) -> None:
        nxt = synthesize_next_pages("https://shop.example/list?page=4&sort=new", count=2)
        self.assertEqual(len(nxt), 2)
        self.assertIn("page=5", nxt[0])
        self.assertIn("page=6", nxt[1])

    def test_synthesize_path_pages(self) -> None:
        nxt = synthesize_next_pages("https://shop.example/blog/page/2", count=1)
        self.assertTrue(nxt[0].endswith("/page/3") or "/page/3" in nxt[0])

    def test_normalize_and_hash(self) -> None:
        a = normalize_url("https://Example.com/a/?x=1#frag")
        b = normalize_url("https://example.com/a?x=1")
        self.assertEqual(a, b)
        self.assertEqual(content_hash("same"), content_hash("same"))
        self.assertNotEqual(content_hash("a"), content_hash("b"))

    def test_link_allowed_allow_deny(self) -> None:
        import re

        self.assertTrue(
            link_allowed(
                "https://shop.example/item/1",
                seed_hosts={"shop.example"},
                same_domain=True,
            )
        )
        self.assertFalse(
            link_allowed(
                "https://other.example/x",
                seed_hosts={"shop.example"},
                same_domain=True,
            )
        )
        self.assertFalse(
            link_allowed(
                "https://shop.example/logout",
                seed_hosts={"shop.example"},
                same_domain=True,
                deny_re=re.compile(r"logout"),
            )
        )


class MassCrawlMockTest(unittest.TestCase):
    def _handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(404, text="Not found", request=request)
        if "/shop?page=1" in url or url.rstrip("/").endswith("/shop"):
            if "page=" not in url:
                return _response(url, LISTING_P1)
            return _response(url, LISTING_P1)
        if "page=2" in url:
            return _response(url, LISTING_P2)
        if "page=3" in url:
            return _response(url, LISTING_P3)
        for n in range(1, 5):
            if f"/item/{n}" in url:
                return _response(url, ITEM.format(n=n))
        return httpx.Response(404, text="missing", request=request)

    def test_paginate_walks_next_links(self) -> None:
        transport = httpx.MockTransport(self._handler)
        with patch("navin.agent.tools.scrape.httpx.Client") as client_cls:
            client_cls.return_value.__enter__.return_value = httpx.Client(
                transport=transport, follow_redirects=True
            )
            # Also patch Client used inside as context - MockTransport via real Client
            pass

        # Use real Client with MockTransport by patching constructor
        real_client = httpx.Client(transport=transport, follow_redirects=True)

        def client_factory(**kwargs):
            return real_client

        with patch("navin.agent.tools.scrape.httpx.Client", side_effect=lambda **kw: _ClientCM(real_client)):
            result = _paginate_py(
                "https://shop.example/shop?page=1",
                {
                    "maxPages": 5,
                    "timeoutSecs": 5,
                    "maxBytes": 500_000,
                    "userAgent": "test",
                    "respectRobots": False,
                    "enrich": True,
                    "maxRetries": 0,
                    "sameDomain": True,
                },
            )
        self.assertGreaterEqual(result["stats"]["pages"], 2)
        titles = [p.get("title") for p in result["pages"]]
        self.assertTrue(any(t and "p1" in t for t in titles))
        self.assertTrue(any(t and "p2" in t for t in titles))

    def test_crawl_mass_with_pagination_and_items(self) -> None:
        transport = httpx.MockTransport(self._handler)
        real_client = httpx.Client(transport=transport, follow_redirects=True)
        with patch(
            "navin.agent.tools.scrape.httpx.Client",
            side_effect=lambda **kw: _ClientCM(real_client),
        ):
            result = _crawl_py(
                ["https://shop.example/shop?page=1"],
                {
                    "maxPages": 20,
                    "maxDepth": 2,
                    "timeoutSecs": 5,
                    "maxBytes": 500_000,
                    "userAgent": "test",
                    "respectRobots": False,
                    "enrich": True,
                    "maxRetries": 0,
                    "sameDomain": True,
                    "followPagination": True,
                    "dedupeContent": True,
                    "delayMs": 0,
                },
            )
        self.assertGreaterEqual(result["stats"]["pages"], 4)
        urls = " ".join(p.get("url") or "" for p in result["pages"])
        self.assertIn("item", urls)
        self.assertIn("stats", result)

    def test_checkpoint_resume(self) -> None:
        transport = httpx.MockTransport(self._handler)
        real_client = httpx.Client(transport=transport, follow_redirects=True)
        with tempfile.TemporaryDirectory() as tmp:
            ckpt = Path(tmp) / "ckpt.json"
            opts = {
                "maxPages": 3,
                "maxDepth": 2,
                "timeoutSecs": 5,
                "maxBytes": 500_000,
                "userAgent": "test",
                "respectRobots": False,
                "enrich": False,
                "maxRetries": 0,
                "sameDomain": True,
                "followPagination": True,
                "dedupeContent": True,
                "delayMs": 0,
                "checkpointPath": str(ckpt),
            }
            with patch(
                "navin.agent.tools.scrape.httpx.Client",
                side_effect=lambda **kw: _ClientCM(real_client),
            ):
                first = _crawl_py(["https://shop.example/shop?page=1"], opts)
            self.assertTrue(ckpt.is_file())
            self.assertEqual(len(first["pages"]), 3)
            # Resume with higher cap - should keep prior pages and continue
            opts["maxPages"] = 6
            with patch(
                "navin.agent.tools.scrape.httpx.Client",
                side_effect=lambda **kw: _ClientCM(real_client),
            ):
                second = _crawl_py(["https://shop.example/shop?page=1"], opts)
            self.assertGreaterEqual(len(second["pages"]), len(first["pages"]))


class _ClientCM:
    """Context-manager wrapper so `with httpx.Client()` returns a fixed client."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __enter__(self) -> httpx.Client:
        return self._client

    def __exit__(self, *args: object) -> None:
        return None


class ExtractPaginationActionTest(unittest.IsolatedAsyncioTestCase):
    async def test_extract_surfaces_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = ScrapeTool(workspace=tmp, config=ScrapeToolConfig(enrich=True))
            raw = await tool.execute(
                action="extract",
                html=LISTING_P1,
                url="https://shop.example/shop?page=1",
            )
            data = json.loads(raw)
            self.assertIn("pagination", data)
            self.assertTrue(any("page=2" in u for u in data["pagination"]))
            self.assertIn("tables", data)
            self.assertGreaterEqual(data["wordCount"], 1)


class WallsStillStrictTest(unittest.TestCase):
    def test_human_walls_unchanged(self) -> None:
        for kind, kwargs in (
            ("cloudflare", {"status": 403, "title": "Just a moment...", "text": "Checking your browser"}),
            ("captcha", {"html": '<div class="g-recaptcha" data-sitekey="x"></div>'}),
            ("paywall", {"text": "Subscribe to continue reading this premium article"}),
            ("login", {"status": 401, "title": "Sign in to continue", "text": "please log in"}),
        ):
            wall = detect_wall(**kwargs)
            assert wall is not None
            self.assertEqual(wall["kind"], kind)
            self.assertTrue(wall["human"], kind)

    def test_crawl_stats(self) -> None:
        pages = annotate_pages(
            [
                {
                    "url": "https://a.example/ok",
                    "status": 200,
                    "text": "hello world enough readable content for a normal product page listing.",
                    "title": "A",
                    "html": "<html><body><p>hello world enough readable content for a normal product page listing.</p></body></html>",
                },
                {
                    "url": "https://b.example/cf",
                    "status": 403,
                    "title": "Just a moment...",
                    "text": "cf-browser-verification",
                },
            ]
        )
        stats = crawl_stats(pages)
        self.assertEqual(stats["pages"], 2)
        self.assertEqual(stats["walls"], 1)
        self.assertEqual(stats["ok"], 1)


class BrowserScrollInfiniteUnitTest(unittest.IsolatedAsyncioTestCase):
    async def test_scroll_infinite_stops_when_height_stable(self) -> None:
        from navin.agent.tools.browser import BrowserTool, BrowserToolConfig, _BrowserSession

        tool = BrowserTool(config=BrowserToolConfig())
        session = _BrowserSession(tool.config)
        page = MagicMock()
        heights = [1000, 2000, 2000, 2000]
        page.evaluate = AsyncMockSideEffect(
            [
                *heights,  # scrollHeight reads
                *heights,
                5000,  # text length at end - actually code calls evaluate multiple times
            ]
        )
        # Simpler: custom async evaluate
        state = {"h": 1000, "n": 0}

        async def evaluate(script, *args):
            if "innerText" in script:
                return 1234
            if "scrollTo" in script:
                return None
            # height probe
            state["n"] += 1
            if state["n"] <= 2:
                state["h"] = 1000 + state["n"] * 500
            return state["h"]

        page.evaluate = evaluate
        page.url = "https://example.com/feed"
        session.page = page
        session.pages = [page]
        session.ensure_page = AsyncMock(return_value=page)  # type: ignore[method-assign]

        # Bypass ensure by calling handler path with patched ensure
        async def ensure():
            return page

        session.ensure_page = ensure  # type: ignore[method-assign]
        result = await tool._dispatch(session, "scroll_infinite", {"index": 5})
        self.assertIn("Infinite scroll finished", result)
        self.assertIn("example.com", result)


class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super().__call__(*args, **kwargs)


class AsyncMockSideEffect:
    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    async def __call__(self, *args, **kwargs):
        if self.i >= len(self.values):
            return self.values[-1]
        v = self.values[self.i]
        self.i += 1
        return v


if __name__ == "__main__":
    unittest.main()
