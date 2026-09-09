# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the instrumented preview: probe injection, telemetry store,
and the local injection proxy (end to end against a real asyncio upstream)."""

from __future__ import annotations

import asyncio
import json

import pytest

from navin.webui.preview_probe import (
    PROBE_JS,
    PROBE_SCRIPT_PATH,
    PROBE_TELEMETRY_PATH,
    TelemetryStore,
    inject_probe,
)
from navin.webui.preview_proxy import (
    TELEMETRY,
    PreviewProxyRegistry,
    _read_chunked,
    parse_head,
)

PROBE_TAG = f'<script src="{PROBE_SCRIPT_PATH}"></script>'.encode()


# -- inject_probe ------------------------------------------------------------


def test_inject_probe_before_head_close() -> None:
    html = b"<html><head><title>x</title></head><body>hi</body></html>"
    out = inject_probe(html)
    assert PROBE_TAG in out
    assert out.index(PROBE_TAG) < out.index(b"</head>")


def test_inject_probe_falls_back_to_body_close() -> None:
    html = b"<html><body>no head here</body></html>"
    out = inject_probe(html)
    assert out.index(PROBE_TAG) < out.index(b"</body>")


def test_inject_probe_html_tag_only() -> None:
    html = b'<html lang="fr">content without head or body'
    out = inject_probe(html)
    assert out.startswith(b'<html lang="fr">' + PROBE_TAG)


def test_inject_probe_fragment_prepends() -> None:
    html = b"<div>fragment</div>"
    out = inject_probe(html)
    assert out.startswith(PROBE_TAG)
    assert out.endswith(html)


def test_inject_probe_case_insensitive() -> None:
    html = b"<HTML><HEAD></HEAD><BODY></BODY></HTML>"
    out = inject_probe(html)
    assert out.index(PROBE_TAG) < out.index(b"</HEAD>")


# -- PROBE_JS contract -------------------------------------------------------


def test_probe_js_speaks_the_design_mode_protocol() -> None:
    """The workbench drives the picker through these exact command names and
    listens for these exact events; the vitest suite exercises the behaviour,
    this guards the vocabulary on the Python side."""
    for token in (
        '"pick-start"',
        '"pick-resume"',
        '"pick-stop"',
        'event: "pick"',
        'event: "pick-cancel"',
        "elementFromPoint",
        "__reactFiber$",
        "_debugOwner",
        "_debugSource",
        "_debugStack",
        "__vueParentComponent",
        "data-navin-pick",
    ):
        assert token in PROBE_JS, token
    # Never installed twice, never runs before the parent asks.
    assert "window.__navinProbeInstalled" in PROBE_JS
    assert "active: false" in PROBE_JS
    # No em / en dashes in what ends up in the user's page.
    assert "\u2014" not in PROBE_JS and "\u2013" not in PROBE_JS


# -- TelemetryStore ----------------------------------------------------------


def test_store_records_and_sanitizes() -> None:
    store = TelemetryStore()
    accepted = store.record(
        3000,
        [
            {"level": "error", "text": "boom", "ts": 123},
            {"level": "nope", "text": "rejected level"},
            {"level": "log", "text": ""},
            "not a dict",
            {"level": "network", "text": "GET /x -> 500", "status": 500.0,
             "url": "/x", "method": "GET", "durationMs": 12.5},
        ],
    )
    assert accepted == 2
    entries = store.entries(3000)
    assert [e["level"] for e in entries] == ["error", "network"]
    assert entries[1]["status"] == 500
    assert entries[1]["durationMs"] == 12


def test_store_truncates_long_text() -> None:
    store = TelemetryStore()
    store.record(1, [{"level": "log", "text": "x" * 10_000}])
    assert len(store.entries(1)[0]["text"]) == 2000


def test_store_ring_buffer_trims() -> None:
    store = TelemetryStore(max_entries=5)
    store.record(1, [{"level": "log", "text": f"m{i}"} for i in range(10)])
    entries = store.entries(1)
    assert len(entries) == 5
    assert entries[0]["text"] == "m5"


def test_store_after_id_filter() -> None:
    store = TelemetryStore()
    store.record(1, [{"level": "log", "text": "a"}, {"level": "log", "text": "b"}])
    all_entries = store.entries(1)
    newer = store.entries(1, after_id=all_entries[0]["id"])
    assert [e["text"] for e in newer] == ["b"]


def test_store_ports_are_isolated() -> None:
    store = TelemetryStore()
    store.record(1, [{"level": "log", "text": "one"}])
    store.record(2, [{"level": "log", "text": "two"}])
    assert [e["text"] for e in store.entries(1)] == ["one"]
    assert [e["text"] for e in store.entries(2)] == ["two"]
    store.clear(1)
    assert store.entries(1) == []
    assert store.entries(2) != []


def test_store_counts_and_digest() -> None:
    store = TelemetryStore()
    store.record(
        5173,
        [
            {"level": "log", "text": "fine"},
            {"level": "error", "text": "TypeError: x is not a function\n  at app.js:1"},
            {"level": "network", "text": "GET /api/users -> HTTP 500"},
        ],
    )
    counts = store.counts(5173)
    assert counts == {"log": 1, "error": 1, "network": 1}
    digest = store.digest(5173)
    assert "http://localhost:5173" in digest
    assert "TypeError: x is not a function" in digest
    assert "GET /api/users -> HTTP 500" in digest
    assert "fine" not in digest


def test_store_digest_empty_when_no_problems() -> None:
    store = TelemetryStore()
    store.record(1, [{"level": "log", "text": "all good"}])
    assert store.digest(1) == ""


def test_store_record_payload_parses_json_body() -> None:
    store = TelemetryStore()
    body = json.dumps({"items": [{"level": "warn", "text": "deprecated"}]}).encode()
    assert store.record_payload(9000, body) == 1
    assert store.record_payload(9000, b"not json") == 0
    assert store.record_payload(9000, b'["not a dict"]') == 0
    assert store.entries(9000)[0]["text"] == "deprecated"


# -- proxy helpers -----------------------------------------------------------


def test_parse_head_extracts_start_line_and_headers() -> None:
    head = b"GET /x HTTP/1.1\r\nHost: a:1\r\nX-Test: v: w\r\n\r\n"
    start, headers = parse_head(head)
    assert start == "GET /x HTTP/1.1"
    assert ("Host", "a:1") in headers
    assert ("X-Test", "v: w") in headers


def test_read_chunked_decodes() -> None:
    async def run() -> bytes:
        reader = asyncio.StreamReader()
        reader.feed_data(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n")
        reader.feed_eof()
        return await _read_chunked(reader)

    assert asyncio.run(run()) == b"Wikipedia"


# -- proxy end to end --------------------------------------------------------


HTML_PAGE = b"<html><head><title>t</title></head><body>app</body></html>"
JSON_BODY = b'{"ok":true}'


async def _upstream_handler(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    head = await reader.readuntil(b"\r\n\r\n")
    start, headers = parse_head(head)
    path = start.split(" ")[1]
    if path == "/":
        body = HTML_PAGE
        ctype = "text/html; charset=utf-8"
    elif path == "/chunked":
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        half = len(HTML_PAGE) // 2
        for part in (HTML_PAGE[:half], HTML_PAGE[half:]):
            writer.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    elif path == "/api/data":
        body = JSON_BODY
        ctype = "application/json"
    else:
        body = b"nope"
        ctype = "text/plain"
    writer.write(
        f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
    )
    writer.write(body)
    await writer.drain()
    writer.close()


async def _http_get(port: int, path: str) -> tuple[str, dict[str, str], bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Connection: close\r\n\r\n".encode()
    )
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    start, header_pairs = parse_head(head + b"\r\n\r\n")
    return start, {k.lower(): v for k, v in header_pairs}, body


async def _http_post(
    port: int, path: str, body: bytes
) -> tuple[str, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, resp_body = raw.partition(b"\r\n\r\n")
    return head.decode("latin-1").split("\r\n")[0], resp_body


@pytest.fixture()
def proxy_env():
    async def setup():
        upstream = await asyncio.start_server(
            _upstream_handler, host="127.0.0.1", port=0
        )
        upstream_port = upstream.sockets[0].getsockname()[1]
        registry = PreviewProxyRegistry()
        proxy_port = await registry.ensure(upstream_port)
        return upstream, upstream_port, registry, proxy_port

    async def teardown(upstream, registry):
        await registry.stop_all()
        upstream.close()
        await upstream.wait_closed()

    return setup, teardown


def _run_proxy_test(proxy_env, scenario) -> None:
    setup, teardown = proxy_env

    async def run() -> None:
        upstream, upstream_port, registry, proxy_port = await setup()
        try:
            await scenario(upstream_port, proxy_port)
        finally:
            await teardown(upstream, registry)

    asyncio.run(run())


def test_proxy_injects_probe_into_html(proxy_env) -> None:
    async def scenario(upstream_port: int, proxy_port: int) -> None:
        start, headers, body = await _http_get(proxy_port, "/")
        assert "200" in start
        assert PROBE_TAG in body
        assert b"app" in body
        assert int(headers["content-length"]) == len(body)

    _run_proxy_test(proxy_env, scenario)


def test_proxy_injects_into_chunked_html(proxy_env) -> None:
    async def scenario(upstream_port: int, proxy_port: int) -> None:
        start, headers, body = await _http_get(proxy_port, "/chunked")
        assert "200" in start
        assert PROBE_TAG in body
        assert "transfer-encoding" not in headers
        assert int(headers["content-length"]) == len(body)

    _run_proxy_test(proxy_env, scenario)


def test_proxy_passes_json_through_untouched(proxy_env) -> None:
    async def scenario(upstream_port: int, proxy_port: int) -> None:
        start, headers, body = await _http_get(proxy_port, "/api/data")
        assert "200" in start
        assert body == JSON_BODY
        assert PROBE_TAG not in body

    _run_proxy_test(proxy_env, scenario)


def test_proxy_serves_probe_script(proxy_env) -> None:
    async def scenario(upstream_port: int, proxy_port: int) -> None:
        start, headers, body = await _http_get(proxy_port, PROBE_SCRIPT_PATH)
        assert "200" in start
        assert "javascript" in headers["content-type"]
        assert body.decode("utf-8") == PROBE_JS

    _run_proxy_test(proxy_env, scenario)


def test_proxy_records_telemetry_post(proxy_env) -> None:
    async def scenario(upstream_port: int, proxy_port: int) -> None:
        TELEMETRY.clear(upstream_port)
        payload = json.dumps(
            {"items": [{"level": "error", "text": "from the page"}]}
        ).encode()
        start, body = await _http_post(proxy_port, PROBE_TELEMETRY_PATH, payload)
        assert "200" in start
        assert json.loads(body) == {"accepted": 1}
        entries = TELEMETRY.entries(upstream_port)
        assert entries and entries[-1]["text"] == "from the page"
        TELEMETRY.clear(upstream_port)

    _run_proxy_test(proxy_env, scenario)


def test_proxy_registry_reuses_and_stops(proxy_env) -> None:
    async def scenario_all() -> None:
        upstream = await asyncio.start_server(
            _upstream_handler, host="127.0.0.1", port=0
        )
        upstream_port = upstream.sockets[0].getsockname()[1]
        registry = PreviewProxyRegistry()
        try:
            port_a = await registry.ensure(upstream_port)
            port_b = await registry.ensure(upstream_port)
            assert port_a == port_b
            status = registry.status()
            assert status == [
                {"targetPort": upstream_port, "proxyPort": port_a, "running": True}
            ]
            assert await registry.stop(upstream_port) is True
            assert await registry.stop(upstream_port) is False
            assert registry.status() == []
        finally:
            await registry.stop_all()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(scenario_all())


def test_proxy_rejects_invalid_port() -> None:
    from navin.webui.preview_proxy import PreviewProxyError

    async def run() -> None:
        registry = PreviewProxyRegistry()
        with pytest.raises(PreviewProxyError):
            await registry.ensure(0)
        with pytest.raises(PreviewProxyError):
            await registry.ensure(70000)

    asyncio.run(run())
