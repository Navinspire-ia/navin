# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime, transport, document, and native contracts for scrape."""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from contextlib import contextmanager
from unittest import mock

import httpx
import pytest
from pypdf import PdfWriter

from navin.agent.tools import scrape as scrape_mod
from navin.agent.tools.scrape import (
    _extract_html_py,
    _extract_pdf,
    _fetch_many_py,
    _fetch_one_py,
)
from navin.agent.tools.scrape_ops import RobotsCache, request_with_retries
from navin.security.network import PinnedDNSSyncTransport


class _ClientContext:
    def __init__(self, client: object) -> None:
        self.client = client

    def __enter__(self) -> object:
        return self.client

    def __exit__(self, *args: object) -> None:
        return None


def test_python_pool_is_bounded_and_ordered() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_fetch(_client: object, url: str, **_kwargs: object) -> dict[str, object]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03 if url.endswith("0") else 0.01)
        with lock:
            active -= 1
        return {"url": url, "error": None}

    urls = [f"https://example.test/{index}" for index in range(6)]
    opts = {
        "concurrency": 2,
        "respectRobots": False,
        "enrich": False,
        "enforceSsrf": False,
    }
    with mock.patch.object(scrape_mod.httpx, "Client", return_value=_ClientContext(object())), \
            mock.patch.object(scrape_mod, "_fetch_one_py", side_effect=fake_fetch):
        result = _fetch_many_py(urls, opts)
    assert peak == 2
    assert [page["url"] for page in result["pages"]] == urls
    assert result["observability"]["concurrency"] == 2


def test_robots_cache_fetches_once_across_threads() -> None:
    calls = 0
    lock = threading.Lock()
    response = httpx.Response(
        200,
        text="User-agent: *\nDisallow: /private\nCrawl-delay: 1",
        request=httpx.Request("GET", "https://example.test/robots.txt"),
    )

    def fake_request(*_args: object, **_kwargs: object) -> httpx.Response:
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        return response

    robots = RobotsCache("test")
    with mock.patch(
        "navin.agent.tools.scrape_ops.request_with_retries", side_effect=fake_request
    ):
        threads = [
            threading.Thread(
                target=robots.allowed,
                args=(object(), "https://example.test/public"),
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert calls == 1
    assert robots.crawl_delay("https://example.test/public") == 1.0


def test_redirect_is_validated_before_second_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with mock.patch(
        "navin.security.network.validate_url_target",
        side_effect=[(True, ""), (False, "metadata blocked")],
    ):
        try:
            request_with_retries(
                client,
                "https://public.example/start",
                max_retries=0,
                enforce_ssrf=True,
            )
        except ValueError as exc:
            assert "Redirect blocked" in str(exc)
        else:
            raise AssertionError("unsafe redirect was accepted")
    assert seen == ["https://public.example/start"]


def test_sync_transport_pins_the_validated_resolution() -> None:
    captured: list[tuple[str, ...]] = []

    class Inner(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, content=b"ok")

        def close(self) -> None:
            return None

    @contextmanager
    def fake_pin(_url: str, ips: tuple[str, ...]):
        captured.append(ips)
        yield

    transport = PinnedDNSSyncTransport(inner=Inner(), enforce_ssrf=True)
    request = httpx.Request("GET", "https://safe.example/")
    with mock.patch(
        "navin.security.network.resolve_url_target",
        return_value=(True, "", ("203.0.113.7",)),
    ), mock.patch("navin.security.network.pin_resolved_url_dns", fake_pin):
        response = transport.handle_request(request)
    assert response.status_code == 200
    assert captured == [("203.0.113.7",)]


def test_pdf_sniffing_returns_structured_pages() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    raw = io.BytesIO()
    writer.write(raw)
    result = _extract_pdf(raw.getvalue())
    assert result["document"]["type"] == "pdf"
    assert result["document"]["extractor"] == "pypdf"
    assert result["document"]["pageCount"] == 1
    assert result["document"]["pages"][0]["page"] == 1


def test_json_body_auth_and_cookie_are_sent_but_errors_are_redacted() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        page = _fetch_one_py(
            client,
            "https://api.example.test/items",
            max_bytes=100_000,
            method="POST",
            max_retries=0,
            request_options={
                "jsonBody": {"name": "navin"},
                "headers": {"Authorization": "Bearer secret-token"},
                "cookies": {"session": "cookie-secret"},
                "enforceSsrf": False,
            },
        )
    assert json.loads(captured["body"]) == {"name": "navin"}
    headers = captured["headers"]
    assert headers["authorization"] == "Bearer secret-token"
    assert headers["cookie"] == "session=cookie-secret"
    assert "secret-token" not in json.dumps(page)
    assert "cookie-secret" not in json.dumps(page)


def test_raw_bytes_body_uses_base64_contract() -> None:
    captured = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request.content
        return httpx.Response(200, content=b"accepted")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _fetch_one_py(
            client,
            "https://api.example.test/upload",
            max_bytes=100_000,
            method="PUT",
            max_retries=0,
            request_options={
                "body": base64.b64encode(b"\x00\x01payload").decode(),
                "enforceSsrf": False,
            },
        )
    assert captured == b"\x00\x01payload"


def test_a_markup_only_captcha_is_flagged_at_fetch_time() -> None:
    """The challenge lives only in the HTML; stripped-record detection missed it."""
    html_body = (
        "<html><head><title>Verify</title></head><body>"
        '<div class="g-recaptcha" data-sitekey="abc"></div>'
        "<p>Please complete the challenge to continue browsing our catalog "
        "of products and services today.</p>"
        "</body></html>"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=html_body, headers={"content-type": "text/html"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        page = _fetch_one_py(
            client,
            "https://shop.example.test/catalog",
            max_bytes=100_000,
            max_retries=0,
            request_options={"enforceSsrf": False},
        )
    assert isinstance(page.get("wall"), dict)
    assert page["wall"]["kind"] == "captcha"
    assert page["wall"]["human"] is True


def test_annotate_preserves_a_fetch_time_wall_verdict() -> None:
    pages = [
        {"url": "https://x.test", "status": 200, "wall": {"kind": "captcha", "human": True}},
    ]
    out = scrape_mod.annotate_pages(pages)
    assert out[0]["wall"]["kind"] == "captcha"


def test_annotate_still_detects_from_retained_raw_html() -> None:
    """Rust fetch / replayed checkpoints carry no verdict but may keep raw HTML."""
    pages = [
        {
            "url": "https://x.test",
            "status": 200,
            "text": "short",
            "_rawHtml": '<html><body><div class="h-captcha"></div></body></html>',
        }
    ]
    out = scrape_mod.annotate_pages(pages)
    assert out[0]["wall"]["kind"] == "captcha"


def _capture_client_headers(urls: list[str], opts: dict[str, object]) -> dict[str, str]:
    """Run _fetch_many_py, capturing the headers set on the httpx client."""
    captured: dict[str, str] = {}

    def fake_client(**kwargs: object) -> object:
        captured.update(dict(kwargs.get("headers") or {}))
        return _ClientContext(object())

    with mock.patch.object(scrape_mod.httpx, "Client", side_effect=fake_client), \
            mock.patch.object(
                scrape_mod, "_fetch_one_py", return_value={"url": urls[0], "error": None}
            ):
        _fetch_many_py(urls, opts)
    return captured


def test_rotation_sends_a_realistic_browser_identity() -> None:
    """The default honest bot UA is the easiest thing to filter; rotate to a browser."""
    headers = _capture_client_headers(
        ["https://shop.example.test/a"],
        {
            "concurrency": 1,
            "respectRobots": False,
            "enrich": False,
            "enforceSsrf": False,
            "rotateUserAgent": True,
        },
    )
    ua = headers.get("User-Agent", "")
    assert "NavinScrape" not in ua
    assert "Mozilla/5.0" in ua
    assert headers.get("Accept-Language", "").startswith("en")


def test_rotation_is_stable_per_host() -> None:
    from navin.agent.tools.scrape_stealth import profile_for_url

    a1 = profile_for_url("https://alpha.example.test/x")
    a2 = profile_for_url("https://alpha.example.test/y")
    assert a1["user_agent"] == a2["user_agent"]  # one origin, one identity


def test_an_explicit_user_agent_still_wins_over_rotation() -> None:
    headers = _capture_client_headers(
        ["https://shop.example.test/a"],
        {
            "concurrency": 1,
            "respectRobots": False,
            "enrich": False,
            "enforceSsrf": False,
            "rotateUserAgent": True,
            "userAgent": "MyCustomAgent/9.9",
        },
    )
    assert headers.get("User-Agent") == "MyCustomAgent/9.9"


def test_proxy_pool_rotates_per_host_and_is_stable() -> None:
    from navin.agent.tools.scrape import _select_proxy

    pool = ["http://p1:8000", "http://p2:8000", "http://p3:8000"]
    opts = {"proxyPool": pool}
    a = _select_proxy(opts, ["https://alpha.test/x"])
    a2 = _select_proxy(opts, ["https://alpha.test/y"])
    assert a in pool and a == a2  # one host -> one exit IP for the session
    # A single proxy still works when no pool is set.
    assert _select_proxy({"proxy": "http://solo:9"}, ["https://x.test"]) == "http://solo:9"
    assert _select_proxy({}, ["https://x.test"]) is None


def test_solve_captcha_is_opt_in() -> None:
    import asyncio

    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    tool = ScrapeTool(workspace="/tmp", config=ScrapeToolConfig(evasion=False))
    result = asyncio.run(
        tool.execute(
            action="solve_captcha",
            captcha_kind="recaptcha_v2",
            sitekey="k",
            url="https://x.test",
        )
    )
    assert "opt-in" in str(result).lower()


def test_solve_captcha_requires_a_provider_when_enabled() -> None:
    import asyncio

    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    tool = ScrapeTool(
        workspace="/tmp", config=ScrapeToolConfig(evasion=True, captcha_provider=None)
    )
    result = asyncio.run(
        tool.execute(
            action="solve_captcha",
            captcha_kind="recaptcha_v2",
            sitekey="k",
            url="https://x.test",
        )
    )
    assert "captcha_provider" in str(result)


def test_solve_captcha_returns_a_token_when_evasion_is_on() -> None:
    import asyncio
    from types import SimpleNamespace

    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    class _Solver:
        async def solve(self, challenge, *, timeout=120):  # noqa: ANN001
            return SimpleNamespace(
                token="TOKEN-OK",
                provider="capsolver",
                kind=challenge.normalized_kind(),
            )

    tool = ScrapeTool(
        workspace="/tmp",
        config=ScrapeToolConfig(evasion=True, captcha_provider="capsolver"),
    )
    with mock.patch(
        "navin.providers.captcha.create_captcha_solver", return_value=_Solver()
    ):
        raw = asyncio.run(
            tool.execute(
                action="solve_captcha",
                captcha_kind="recaptcha_v2",
                sitekey="site",
                url="https://shop.example/checkout",
            )
        )
    payload = json.loads(raw)
    assert payload["token"] == "TOKEN-OK"
    assert payload["provider"] == "capsolver"


def test_fetch_keeps_the_wall_and_strips_raw_html() -> None:
    """The agent must see the captcha verdict, never the 250 KB raw page."""
    import asyncio

    from navin.agent.tools.scrape import ScrapeTool, ScrapeToolConfig

    html_body = (
        "<html><head><title>Verify</title></head><body>"
        '<div class="g-recaptcha" data-sitekey="abc"></div>'
        "<p>Please complete the challenge to continue browsing our catalog.</p>"
        "</body></html>"
    )
    fetched = {
        "pages": [
            {
                "url": "https://example.com/catalog",
                "status": 200,
                "title": "Verify",
                "text": "Please complete the challenge",
                "wall": {"kind": "captcha", "human": True},
                "_rawHtml": html_body,
            }
        ],
        "backend": "python",
    }
    tool = ScrapeTool(
        workspace="/tmp",
        config=ScrapeToolConfig(assisted=False, enrich=False),
    )
    with mock.patch.object(tool, "_fetch", return_value=json.dumps(fetched)), \
            mock.patch.object(
                scrape_mod,
                "_validate_urls",
                return_value=(["https://example.com/catalog"], []),
            ):
        raw = asyncio.run(
            tool.execute(action="fetch", url="https://example.com/catalog")
        )
    payload = json.loads(raw)
    page = payload["pages"][0]
    assert page["wall"]["kind"] == "captcha"
    assert "_rawHtml" not in page
    assert payload["walls"]["human_walls"] == 1


def test_native_batch_keeps_input_order_and_announces_backend() -> None:
    class Native:
        @staticmethod
        def scrape_fetch(urls: str, _opts: str) -> str:
            parsed = json.loads(urls)
            return json.dumps({"pages": [{"url": url, "error": None} for url in parsed]})

    urls = ["https://example.test/b", "https://example.test/a"]
    opts = {
        "method": "GET",
        "respectRobots": False,
        "enforceSsrf": False,
        "concurrency": 2,
    }
    with mock.patch.object(scrape_mod, "_native_scrape", return_value=Native()):
        tool = object.__new__(scrape_mod.ScrapeTool)
        result = json.loads(tool._fetch(urls, opts))
    assert result["backend"] == "rust"
    assert [page["url"] for page in result["pages"]] == urls


def test_native_extract_matches_python_core_contract() -> None:
    navin_core = pytest.importorskip("navin_core")
    sample = (
        "<html><head><title>Parity</title></head><body><main>"
        "<h1>Parity</h1><p>Same readable body.</p>"
        '<a href="/next">Next</a></main></body></html>'
    )
    python_result = _extract_html_py(sample, "https://example.test/start")
    native_result = json.loads(
        navin_core.scrape_extract(sample, "https://example.test/start")
    )
    assert native_result["title"] == python_result["title"]
    assert native_result["text"].split() == python_result["text"].split()
    assert native_result["links"] == python_result["links"]
