# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The share relay forwards opaque frames and enforces ownership, expiry, revocation."""

from __future__ import annotations

import asyncio
import json
import time

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from navin.share.relay import Relay, process_request

SHARE = "AbCdEfGhIjKlMnOpQrSt"
SECRET = "s" * 40


async def _recv(ws, timeout=2):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


def _run(scenario):
    async def go():
        relay = Relay()
        async with serve(relay.handler, "127.0.0.1", 0, process_request=process_request) as server:
            port = server.sockets[0].getsockname()[1]
            await scenario(relay, f"ws://127.0.0.1:{port}")
    asyncio.run(go())


async def _register(base, *, secret=SECRET, ttl=600, max_guests=10):
    host = await connect(f"{base}/host")
    await host.send(json.dumps({
        "type": "register", "share_id": SHARE, "host_secret": secret,
        "expires_at": time.time() + ttl, "max_guests": max_guests,
    }))
    return host, await _recv(host)


def test_frames_flow_both_ways_and_the_relay_only_sees_opaque_data():
    async def scenario(relay, base):
        host, reply = await _register(base)
        assert reply["type"] == "registered"
        guest = await connect(f"{base}/guest/{SHARE}")
        welcome = await _recv(guest)
        assert welcome["type"] == "welcome" and welcome["host_online"] is True
        joined = await _recv(host)
        assert joined == {"type": "guest_join", "guest": welcome["guest"]}
        await host.send(json.dumps({"type": "send", "guest": "*", "data": "Y2lwaGVy"}))
        assert await _recv(guest) == {"type": "data", "data": "Y2lwaGVy"}
        await guest.send(json.dumps({"type": "data", "data": "cmVwbHk="}))
        assert await _recv(host) == {"type": "from_guest", "guest": welcome["guest"], "data": "cmVwbHk="}
        await guest.close()
        assert (await _recv(host))["type"] == "guest_leave"
        await host.close()
    _run(scenario)


def test_nobody_else_can_take_over_a_share_and_a_revoked_link_stays_dead():
    async def scenario(relay, base):
        host, _ = await _register(base)
        thief, reply = await _register(base, secret="x" * 40)
        assert reply == {"type": "error", "code": "not_owner"}
        guest = await connect(f"{base}/guest/{SHARE}")
        await _recv(guest)
        await _recv(host)
        await host.send(json.dumps({"type": "revoke"}))
        assert (await _recv(guest))["type"] == "closed"
        again, reply = await _register(base)
        assert reply == {"type": "error", "code": "revoked"}
        late = await connect(f"{base}/guest/{SHARE}")
        assert (await _recv(late))["type"] == "closed"
    _run(scenario)


def test_expired_links_and_full_rooms_are_refused():
    async def scenario(relay, base):
        _, reply = await _register(base, ttl=-5)
        assert reply == {"type": "error", "code": "expired"}
        host, _ = await _register(base, max_guests=1)
        first = await connect(f"{base}/guest/{SHARE}")
        assert (await _recv(first))["type"] == "welcome"
        second = await connect(f"{base}/guest/{SHARE}")
        assert (await _recv(second)) == {"type": "closed", "reason": "this session is full"}
        await host.close()
    _run(scenario)


def test_a_guest_arriving_before_the_host_is_told_to_retry_not_that_the_link_is_dead():
    """Relay restarted or host still in its reconnect backoff: the link lives on."""
    async def scenario(relay, base):
        early = await connect(f"{base}/guest/{SHARE}")
        closed = await _recv(early)
        assert closed["type"] == "closed" and closed["retry"] is True
        host, _ = await _register(base)
        guest = await connect(f"{base}/guest/{SHARE}")
        assert (await _recv(guest))["type"] == "welcome"
        await _recv(host)
        await host.send(json.dumps({"type": "revoke"}))
        await _recv(guest)
        late = await connect(f"{base}/guest/{SHARE}")
        final = await _recv(late)
        assert final["type"] == "closed" and "retry" not in final
    _run(scenario)


def test_guests_wait_while_the_host_reconnects():
    async def scenario(relay, base):
        host, _ = await _register(base)
        guest = await connect(f"{base}/guest/{SHARE}")
        await _recv(guest)
        await _recv(host)
        await host.close()
        assert (await _recv(guest))["type"] == "host_offline"
        host, reply = await _register(base)
        assert reply["type"] == "registered" and len(reply["guests"]) == 1
        assert (await _recv(guest))["type"] == "host_online"
    _run(scenario)


def test_http_serves_health_and_never_leaks_files_outside_the_guest_app():
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    def get(path):
        return process_request(None, Request(path, Headers()))  # type: ignore[arg-type]

    assert get("/healthz").status_code == 200
    assert get("/../../etc/passwd").status_code == 404
    assert get("/assets/../../../etc/passwd").status_code == 404
    assert get("/host").status_code == 404


def test_frames_are_end_to_end_encrypted_and_bound_to_their_share():
    import pytest

    from navin.share.crypto import ShareCipher, new_key

    key = new_key()
    host = ShareCipher(key, SHARE)
    guest = ShareCipher(key, SHARE)
    frame = host.seal({"type": "event", "text": "secret code"})
    assert "secret" not in frame
    assert guest.open(frame) == {"type": "event", "text": "secret code"}
    with pytest.raises(ValueError):
        ShareCipher(new_key(), SHARE).open(frame)  # wrong key
    with pytest.raises(ValueError):
        ShareCipher(key, "OtherShareIdentifier").open(frame)  # replayed into another share
    with pytest.raises(ValueError):
        guest.open(frame[:-4] + "AAAA")  # tampered


def test_the_guest_page_is_served_gzipped_with_hashed_assets_cached_for_good(tmp_path, monkeypatch):
    from websockets.datastructures import Headers
    from websockets.http11 import Request

    from navin.share import relay

    (tmp_path / "assets").mkdir()
    (tmp_path / "guest.html").write_text("<html>" + "x" * 2000)
    (tmp_path / "assets" / "guest-abc.js").write_text("console.log(1);" * 200)
    monkeypatch.setattr(relay, "STATIC_DIR", tmp_path)
    relay._STATIC_CACHE.clear()

    def get(path, encoding="gzip, br"):
        return relay.process_request(None, Request(path, Headers([("Accept-Encoding", encoding)])))

    page = get("/s/abcdefghijklmnop1234")
    assert page.status_code == 200 and page.headers["Content-Encoding"] == "gzip"
    assert page.headers["Cache-Control"] == "no-cache"
    asset = get("/assets/guest-abc.js")
    assert "immutable" in asset.headers["Cache-Control"] and len(asset.body) < 3000
    plain = get("/assets/guest-abc.js", encoding="")
    assert "Content-Encoding" not in plain.headers and plain.body.startswith(b"console.log")
    assert get("/assets/../guest.html").status_code == 404
    assert get("/etc/passwd").status_code == 404


def test_the_public_relay_caps_connections_per_ip_and_forgets_empty_rooms(monkeypatch):
    import time as _time

    from navin.share import relay as relay_mod

    relay = relay_mod.Relay()
    assert all(relay._enter("203.0.113.9", "host", 2) for _ in range(2))
    assert relay._enter("203.0.113.9", "host", 2) is False
    assert relay._enter("198.51.100.1", "host", 2) is True
    relay._leave("203.0.113.9", "host")
    assert relay._enter("203.0.113.9", "host", 2) is True

    now = _time.time()
    relay.rooms["a" * 16] = relay_mod.Room("a" * 16, b"x", now + 3600, 5, active_at=now - relay_mod.EMPTY_ROOM_TTL_S - 1)
    relay.rooms["b" * 16] = relay_mod.Room("b" * 16, b"x", now + 3600, 5, active_at=now)
    relay.rooms["c" * 16] = relay_mod.Room("c" * 16, b"x", now - 1, 5, active_at=now)
    relay._prune()
    assert list(relay.rooms) == ["b" * 16]


def test_client_ip_is_the_last_forwarded_hop():
    from types import SimpleNamespace

    from websockets.datastructures import Headers

    from navin.share.relay import Relay

    ws = SimpleNamespace(request=SimpleNamespace(headers=Headers([("X-Forwarded-For", "6.6.6.6, 203.0.113.7")])), remote_address=("10.0.0.5", 1))
    assert Relay.client_ip(ws) == "203.0.113.7"
    ws = SimpleNamespace(request=SimpleNamespace(headers=Headers()), remote_address=("10.0.0.5", 1))
    assert Relay.client_ip(ws) == "10.0.0.5"
