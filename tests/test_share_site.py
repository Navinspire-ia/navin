# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The host's running site, opened by a share guest through the encrypted pipe."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from navin.share import host as share_host
from navin.share.site import SiteError, clean_path, normalize_site_url
from tests.test_share_host import _join, _run, _wait


def test_only_a_local_http_server_can_be_shared():
    assert normalize_site_url("localhost:5173") == "http://localhost:5173"
    assert normalize_site_url("http://127.0.0.1:5175/app?x=1") == "http://127.0.0.1:5175"
    assert normalize_site_url("https://[::1]:8443/") == "https://[::1]:8443"
    for bad in ("http://192.168.1.20:3000", "http://example.com", "file:///etc/passwd", "ftp://localhost:21", ""):
        with pytest.raises(SiteError):
            normalize_site_url(bad)


def test_a_guest_reaches_paths_of_the_shared_server_only():
    assert clean_path("/src/main.tsx?t=1") == "/src/main.tsx?t=1"
    for bad in ("//evil.example/x", "http://evil.example/", "src/main.tsx", "/a\\b", "/a\r\nHost: x", ""):
        assert clean_path(bad) is None


async def _local_site():
    """A tiny HTTP server that echoes what it got (method, path, Host, Cookie)."""
    seen: list[dict[str, str]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        lines = head.split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:] if ": " in line)}
        body = await reader.readexactly(int(headers.get("content-length", "0") or 0))
        seen.append({"method": method, "path": path, "host": headers.get("host", ""), "cookie": headers.get("cookie", "")})
        port = writer.get_extra_info("sockname")[1]
        if path == "/old":
            reply = f"HTTP/1.1 302 Found\r\nLocation: http://localhost:{port}/new?a=1\r\nContent-Length: 0\r\n"
            reply += "Set-Cookie: s=1\r\nConnection: close\r\n\r\n"
            writer.write(reply.encode())
        else:
            payload = json.dumps({"method": method, "path": path, "body": body.decode()}).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Secret: no\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload
            )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], seen


def test_a_guest_uses_the_shared_site_through_the_share(tmp_path, monkeypatch):
    monkeypatch.setattr(share_host, "_is_navin", lambda url: False)

    async def scenario(manager, channel, base):
        server, port, seen = await _local_site()
        try:
            info = manager.create("chat-site", ttl="1h")
            await _wait(lambda: manager.describe(info["share_id"])["online"])
            guest = await _join(manager, base, info)
            assert (await guest.next("snapshot"))["site"] is False

            # Not shared yet: nothing reaches this machine's servers.
            await guest.send({"type": "site", "requests": [{"id": "a", "method": "GET", "path": "/"}]})
            assert (await guest.next("site_res"))["error"] == "off"
            assert not seen

            described = await manager.set_site(info["share_id"], True, f"http://localhost:{port}/")
            assert described["site_url"] == f"http://localhost:{port}"
            assert (await guest.next("site"))["enabled"] is True

            # A batch: a page, a form post, a redirect and a path that tries to leave.
            await guest.send({"type": "site", "requests": [
                {"id": "page", "method": "GET", "path": "/index.html?x=1",
                 "headers": [["Accept", "text/html"], ["Cookie", "guest=1"], ["Host", "evil.example"]]},
                {"id": "post", "method": "POST", "path": "/api/save",
                 "headers": [["Content-Type", "text/plain"]], "body": base64.b64encode(b"hello").decode()},
                {"id": "move", "method": "GET", "path": "/old"},
                {"id": "out", "method": "GET", "path": "//evil.example/"},
            ]})
            answers = {}
            while len(answers) < 4:
                res = await guest.next("site_res")
                answers[res["id"]] = res
            page = json.loads(base64.b64decode(answers["page"]["data"]))
            assert answers["page"]["status"] == 200 and page["path"] == "/index.html?x=1"
            assert ["content-type", "application/json"] in answers["page"]["headers"]
            assert all(name != "x-secret" for name, _ in answers["page"]["headers"])
            assert json.loads(base64.b64decode(answers["post"]["data"]))["body"] == "hello"
            # Redirects to the shared server stay inside the guest's frame; no cookie travels.
            assert answers["move"]["status"] == 302
            assert ["location", "/new?a=1"] in answers["move"]["headers"]
            assert all(name != "set-cookie" for name, _ in answers["move"]["headers"])
            assert answers["out"]["error"] == "bad_request"
            # The server saw this machine as localhost, never the guest's Host or cookies.
            first = next(item for item in seen if item["path"] == "/index.html?x=1")
            assert first["host"] == f"localhost:{port}" and first["cookie"] == ""

            await manager.set_site(info["share_id"], False)
            assert (await guest.next("site"))["enabled"] is False
            assert manager.describe(info["share_id"])["site_url"] == ""
        finally:
            server.close()
    _run(scenario, tmp_path)


def test_navin_itself_and_remote_hosts_are_never_shared(tmp_path, monkeypatch):
    monkeypatch.setattr(share_host, "_is_navin", lambda url: url.endswith(":8765"))

    async def scenario(manager, channel, base):
        info = manager.create("chat-site", ttl="1h")
        with pytest.raises(SiteError) as navin:
            await manager.set_site(info["share_id"], True, "http://127.0.0.1:8765")
        assert navin.value.code == "navin"
        with pytest.raises(SiteError) as remote:
            await manager.set_site(info["share_id"], True, "http://10.0.0.5:3000")
        assert remote.value.code == "bad_url"
        # Nothing running for the chat's project: told, not guessed.
        monkeypatch.setattr(
            "navin.agent.tools.preview_server.discover_running_project_url", lambda **_kw: None,
        )
        with pytest.raises(SiteError) as missing:
            await manager.set_site(info["share_id"], True)
        assert missing.value.code == "no_site"
        assert manager.describe(info["share_id"])["site_url"] == ""
    _run(scenario, tmp_path)


def test_the_relay_lets_the_guest_worker_serve_the_site_and_refuses_stray_sockets(tmp_path, monkeypatch):
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from navin.share import relay

    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "guest-sw-AbC123_x.js").write_text("self.addEventListener('fetch', () => {});")
    (tmp_path / "assets" / "guest-abc.js").write_text("console.log(1);")
    monkeypatch.setattr(relay, "STATIC_DIR", tmp_path)
    relay._STATIC_CACHE.clear()

    def get(path, *headers):
        return relay.process_request(None, Request(path, Headers(list(headers))))  # type: ignore[arg-type]

    assert get("/assets/guest-sw-AbC123_x.js").headers["Service-Worker-Allowed"] == "/"
    assert "Service-Worker-Allowed" not in get("/assets/guest-abc.js").headers
    # A shared site's hot-reload client knocking at the root: refused before the handshake.
    assert get("/?token=abc", ("Upgrade", "websocket")).status_code == 404
    assert get("/_next/webpack-hmr", ("Upgrade", "websocket")).status_code == 404
    assert get("/host", ("Upgrade", "websocket")) is None
    assert get("/guest/abcdefghijklmnop1234", ("Upgrade", "websocket")) is None


def test_an_app_link_opens_the_site_and_nothing_of_the_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(share_host, "_is_navin", lambda url: False)

    async def scenario(manager, channel, base):
        server, port, seen = await _local_site()
        try:
            info = manager.create("chat-app", ttl="1h", access="full", scope="site")
            assert info["scope"] == "site" and info["access"] == "read" and "?app#" in info["link"]
            await manager.set_site(info["share_id"], True, f"http://localhost:{port}")
            await _wait(lambda: manager.describe(info["share_id"])["online"])
            guest = await _join(manager, base, info)
            snapshot = await guest.next("snapshot")
            assert snapshot["scope"] == "site" and snapshot["site"] is True
            assert snapshot["thread"] is None and snapshot["context"] is None and snapshot["chat_id"] == ""
            # Never attached to the chat: no event, no message, no history can reach it.
            assert channel.attached == []
            await guest.send({"type": "envelope", "envelope": {"type": "message", "content": "montre le code"}})
            await guest.send({"type": "history"})
            await guest.send({"type": "request_download"})
            await guest.send({"type": "site", "requests": [{"id": "home", "method": "GET", "path": "/"}]})
            first = await guest.next()
            assert first["type"] == "site_res" and first["id"] == "home" and first["status"] == 200
            assert channel.dispatched == []
            assert await manager.set_access(info["share_id"], "full") is False
            guest_id = manager.describe(info["share_id"])["guests"][0]["guest_id"]
            await manager.set_guest(info["share_id"], guest_id, drive=True, download=True)
            assert manager.describe(info["share_id"])["guests"][0]["drive"] is False
        finally:
            server.close()
    _run(scenario, tmp_path)


def test_a_guest_with_the_download_right_gets_project_files_but_never_secrets(tmp_path):
    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "rapport.pdf").write_bytes(b"%PDF-1.4 rapport")
    (project / "data.csv").write_text("a,b\n1,2\n")
    (project / ".env").write_text("SECRET=1")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "x.js").write_text("x")

    async def scenario(manager, channel, base):
        manager.project_root_for = lambda chat_id: project
        info = manager.create("chat-files", ttl="1h", access="read")
        await _wait(lambda: manager.describe(info["share_id"])["online"])
        guest = await _join(manager, base, info)
        await guest.next("snapshot")

        # No download right: refused.
        await guest.send({"type": "files", "id": "l1"})
        assert (await guest.next("file_error"))["reason"] == "not_allowed"

        await manager.set_access(info["share_id"], "full")
        await guest.next("rights")
        await guest.send({"type": "files", "id": "l2"})
        listing = await guest.next("files")
        paths = {item["path"] for item in listing["files"]}
        assert paths == {"docs/rapport.pdf", "data.csv"}

        await guest.send({"type": "file", "id": "f1", "path": "docs/rapport.pdf"})
        chunk = await guest.next("file_chunk")
        assert base64.b64decode(chunk["data"]) == b"%PDF-1.4 rapport" and chunk["mime"] == "application/pdf"
        for bad in (".env", "../outside.txt", "node_modules/x.js"):
            await guest.send({"type": "file", "id": "bad", "path": bad})
            assert (await guest.next("file_error"))["reason"] in {"not_allowed", "not_found"}
    _run(scenario, tmp_path)
