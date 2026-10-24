# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin share relay: a dumb, end-to-end encrypted pipe between a host and guests.

The host's Navin opens an outbound WebSocket (works behind any NAT) and
registers a share; guests open the share link in a browser and connect too.
The relay forwards opaque frames between them. It never sees content: frames
are AES-GCM encrypted with a key that lives in the link's ``#fragment``, which
browsers never send to a server. Nothing is stored; a restart drops rooms and
the host reconnects.

Protocol (JSON text frames):

host  -> relay  {"type":"register","share_id","host_secret","expires_at","max_guests"}
relay -> host   {"type":"registered"} | {"type":"error","code"}
host  -> relay  {"type":"send","guest":"<id>"|"*","data":"<b64>"}
host  -> relay  {"type":"kick","guest":"<id>"} | {"type":"revoke"}
relay -> host   {"type":"guest_join","guest"} | {"type":"guest_leave","guest"}
                {"type":"from_guest","guest","data"}
guest -> relay  (connect /guest/<share_id>) then {"type":"data","data":"<b64>"}
relay -> guest  {"type":"welcome","guest"} | {"type":"data","data"}
                {"type":"host_online"} | {"type":"host_offline"} | {"type":"closed","reason"[,"retry":true]}

``closed`` is final (revoked, expired, removed, full) unless ``retry`` is set:
the room is unknown because the host is reconnecting or the relay restarted,
and the guest should connect again shortly.

Only the host may register or re-register a share (it proves the secret it
set first; the relay keeps only its SHA-256). A revoked share stays refused
until it would have expired.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger("navin.relay")

SHARE_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_FRAME_BYTES = 1024 * 1024
MAX_GUESTS_CAP = 50
MAX_TTL_S = 7 * 24 * 3600
GUEST_RATE_PER_S = 30
# Abuse limits: the relay is public. Per client IP (the ALB's X-Forwarded-For),
# at once; rooms overall; and how long an empty room waits for its host.
MAX_HOSTS_PER_IP = int(os.environ.get("RELAY_MAX_HOSTS_PER_IP", "30"))
MAX_GUESTS_PER_IP = int(os.environ.get("RELAY_MAX_GUESTS_PER_IP", "60"))
MAX_ROOMS = int(os.environ.get("RELAY_MAX_ROOMS", "20000"))
EMPTY_ROOM_TTL_S = 15 * 60
STATIC_DIR = Path(os.environ.get("RELAY_STATIC_DIR", Path(__file__).parent / "guest"))


def _digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


@dataclass
class Room:
    share_id: str
    secret_digest: bytes
    expires_at: float
    max_guests: int
    host: ServerConnection | None = None
    guests: dict[str, ServerConnection] = field(default_factory=dict)
    # Last moment the room had a host or a guest (empty rooms are pruned).
    active_at: float = field(default_factory=time.time)


class Relay:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        # share_id -> expiry: revoked links stay refused until they would have expired.
        self.revoked: dict[str, float] = {}
        # Open connections per client IP, by role.
        self.per_ip: dict[tuple[str, str], int] = {}

    # -- helpers ---------------------------------------------------------------

    def _prune(self) -> None:
        now = time.time()
        for share_id in [sid for sid, until in self.revoked.items() if until <= now]:
            self.revoked.pop(share_id, None)
        # Expired rooms, and rooms left empty (no host, no guest) too long.
        for room in list(self.rooms.values()):
            empty = room.host is None and not room.guests
            if room.expires_at <= now or (empty and now - room.active_at > EMPTY_ROOM_TTL_S):
                self.rooms.pop(room.share_id, None)

    @staticmethod
    def client_ip(ws: ServerConnection) -> str:
        """The caller's IP: the ALB appends it last to X-Forwarded-For."""
        forwarded = ws.request.headers.get("X-Forwarded-For", "") if ws.request else ""
        if forwarded:
            return forwarded.split(",")[-1].strip()
        remote = getattr(ws, "remote_address", None)
        return str(remote[0]) if remote else "?"

    def _enter(self, ip: str, role: str, limit: int) -> bool:
        key = (ip, role)
        if self.per_ip.get(key, 0) >= limit:
            return False
        self.per_ip[key] = self.per_ip.get(key, 0) + 1
        return True

    def _leave(self, ip: str, role: str) -> None:
        key = (ip, role)
        left = self.per_ip.get(key, 0) - 1
        if left > 0:
            self.per_ip[key] = left
        else:
            self.per_ip.pop(key, None)

    @staticmethod
    async def _send(ws: ServerConnection | None, payload: dict[str, Any]) -> None:
        if ws is None:
            return
        try:
            await ws.send(json.dumps(payload))
        except ConnectionClosed:
            pass

    async def _close_room(self, room: Room, reason: str) -> None:
        for guest in list(room.guests.values()):
            await self._send(guest, {"type": "closed", "reason": reason})
            await guest.close(1000, reason)
        room.guests.clear()
        self.rooms.pop(room.share_id, None)

    # -- host ------------------------------------------------------------------

    async def host(self, ws: ServerConnection) -> None:
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        except (asyncio.TimeoutError, ValueError, ConnectionClosed):
            await ws.close(1008, "register first")
            return
        share_id = str(first.get("share_id") or "")
        host_secret = str(first.get("host_secret") or "")
        if first.get("type") != "register" or not SHARE_ID.match(share_id) or len(host_secret) < 32:
            await self._send(ws, {"type": "error", "code": "invalid_register"})
            await ws.close(1008, "invalid register")
            return
        self._prune()
        if share_id in self.revoked:
            await self._send(ws, {"type": "error", "code": "revoked"})
            await ws.close(1008, "revoked")
            return
        now = time.time()
        try:
            expires_at = min(float(first.get("expires_at") or 0), now + MAX_TTL_S)
            max_guests = max(1, min(int(first.get("max_guests") or 10), MAX_GUESTS_CAP))
        except (TypeError, ValueError):
            expires_at, max_guests = 0.0, 10
        if expires_at <= now:
            await self._send(ws, {"type": "error", "code": "expired"})
            await ws.close(1008, "expired")
            return
        digest = _digest(host_secret)
        room = self.rooms.get(share_id)
        if room is None and len(self.rooms) >= MAX_ROOMS:
            await self._send(ws, {"type": "error", "code": "busy"})
            await ws.close(1013, "relay full")
            return
        if room is None:
            room = Room(share_id, digest, expires_at, max_guests)
            self.rooms[share_id] = room
        elif not hmac.compare_digest(room.secret_digest, digest):
            # Someone else holding the id: never take over a live share.
            await self._send(ws, {"type": "error", "code": "not_owner"})
            await ws.close(1008, "not owner")
            return
        else:
            room.expires_at, room.max_guests = expires_at, max_guests
            if room.host is not None and room.host is not ws:
                await room.host.close(1000, "replaced by a new connection")
        room.host = ws
        await self._send(ws, {"type": "registered", "guests": list(room.guests)})
        for guest in room.guests.values():
            await self._send(guest, {"type": "host_online"})

        try:
            async for raw in ws:
                if isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                kind = msg.get("type")
                if kind == "send":
                    target = str(msg.get("guest") or "*")
                    frame = {"type": "data", "data": msg.get("data")}
                    if target == "*":
                        await asyncio.gather(*(self._send(g, frame) for g in list(room.guests.values())))
                    else:
                        await self._send(room.guests.get(target), frame)
                elif kind == "kick":
                    guest = room.guests.pop(str(msg.get("guest") or ""), None)
                    if guest is not None:
                        await self._send(guest, {"type": "closed", "reason": "removed by the host"})
                        await guest.close(1000, "removed")
                elif kind == "revoke":
                    self.revoked[share_id] = room.expires_at
                    await self._close_room(room, "the host stopped sharing")
                    break
                if time.time() >= room.expires_at:
                    await self._close_room(room, "the link expired")
                    break
        except ConnectionClosed:
            pass
        finally:
            if room.host is ws:
                room.host = None
                room.active_at = time.time()
                for guest in room.guests.values():
                    await self._send(guest, {"type": "host_offline"})

    # -- guest -----------------------------------------------------------------

    async def guest(self, ws: ServerConnection, share_id: str) -> None:
        self._prune()
        room = self.rooms.get(share_id)
        if room is None and share_id not in self.revoked:
            # Nobody holds this id right now: the host is reconnecting (its
            # backoff reaches 30 s) or the relay restarted and dropped its
            # rooms. Not a dead link: the guest keeps the seat and retries.
            await self._send(ws, {"type": "closed", "reason": "the host is not connected", "retry": True})
            await ws.close(1008, "host offline")
            return
        if room is None or share_id in self.revoked or time.time() >= room.expires_at:
            await self._send(ws, {"type": "closed", "reason": "this link is not active"})
            await ws.close(1008, "inactive")
            return
        if len(room.guests) >= room.max_guests:
            await self._send(ws, {"type": "closed", "reason": "this session is full"})
            await ws.close(1008, "full")
            return
        guest_id = secrets.token_urlsafe(9)
        room.guests[guest_id] = ws
        await self._send(ws, {"type": "welcome", "guest": guest_id, "host_online": room.host is not None})
        await self._send(room.host, {"type": "guest_join", "guest": guest_id})
        window_start, count = time.monotonic(), 0
        try:
            async for raw in ws:
                if isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES:
                    continue
                now = time.monotonic()
                if now - window_start >= 1:
                    window_start, count = now, 0
                count += 1
                if count > GUEST_RATE_PER_S:
                    continue  # flood: dropped, the connection stays
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") == "data" and isinstance(msg.get("data"), str):
                    await self._send(room.host, {"type": "from_guest", "guest": guest_id, "data": msg["data"]})
        except ConnectionClosed:
            pass
        finally:
            if room.guests.get(guest_id) is ws:
                room.guests.pop(guest_id, None)
                room.active_at = time.time()
                await self._send(room.host, {"type": "guest_leave", "guest": guest_id})

    # -- routing -----------------------------------------------------------------

    async def handler(self, ws: ServerConnection) -> None:
        path = ws.request.path.split("?", 1)[0] if ws.request else ""
        match = re.fullmatch(r"/guest/([A-Za-z0-9_-]{16,64})", path)
        if path != "/host" and not match:
            await ws.close(1008, "unknown path")
            return
        role, limit = ("host", MAX_HOSTS_PER_IP) if path == "/host" else ("guest", MAX_GUESTS_PER_IP)
        ip = self.client_ip(ws)
        if not self._enter(ip, role, limit):
            await self._send(ws, {"type": "closed", "reason": "too many connections"} if role == "guest" else {"type": "error", "code": "busy"})
            await ws.close(1013, "too many connections")
            return
        try:
            if match:
                await self.guest(ws, match.group(1))
            else:
                await self.host(ws)
        finally:
            self._leave(ip, role)

    async def prune_forever(self, every_s: float = 60.0) -> None:
        while True:
            await asyncio.sleep(every_s)
            self._prune()


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}
_SECURITY_HEADERS = [
    ("Content-Security-Policy",
     "default-src 'self'; connect-src 'self' wss:; img-src 'self' data: blob:; font-src 'self' data:; "
     "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
]
_COMPRESSIBLE = {".html", ".js", ".css", ".svg"}
GUEST_WORKER = re.compile(r"guest-sw-[A-Za-z0-9_-]+\.js")
# Static files never change inside a relay image: read and gzip each once.
_STATIC_CACHE: dict[Path, tuple[bytes, bytes | None]] = {}


def _http(
    status: HTTPStatus,
    body: bytes,
    content_type: str = "text/plain; charset=utf-8",
    *,
    cache: str = "no-store",
    extra: list[tuple[str, str]] | None = None,
) -> Response:
    headers = Headers([
        ("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", cache),
        *_SECURITY_HEADERS, *(extra or []),
    ])
    return Response(status.value, status.phrase, headers, body)


def _static(target: Path, accept_encoding: str) -> Response:
    """A guest page file: gzipped when the browser takes it, hashed assets cached for good."""
    cached = _STATIC_CACHE.get(target)
    if cached is None:
        raw = target.read_bytes()
        cached = (raw, gzip.compress(raw, 6) if target.suffix in _COMPRESSIBLE else None)
        _STATIC_CACHE[target] = cached
    raw, packed = cached
    # Vite names assets by content hash: a new build is a new URL.
    cache = "public, max-age=31536000, immutable" if target.parent.name == "assets" else "no-cache"
    content_type = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
    extra = [("Vary", "Accept-Encoding")]
    if GUEST_WORKER.fullmatch(target.name):
        # The guest's service worker serves the host's shared site at the page's root.
        extra.append(("Service-Worker-Allowed", "/"))
    if packed is not None and "gzip" in accept_encoding.lower():
        return _http(HTTPStatus.OK, packed, content_type, cache=cache, extra=[("Content-Encoding", "gzip"), *extra])
    return _http(HTTPStatus.OK, raw, content_type, cache=cache, extra=extra)


def process_request(connection: ServerConnection, request: Request) -> Response | None:
    """Plain HTTP: health, and the guest page (the WebSocket paths fall through)."""
    path = request.path.split("?", 1)[0]
    if request.headers.get("Upgrade", "").lower() == "websocket":
        # Refused before the handshake: a shared site's hot-reload client (Vite,
        # Next.js) that knocks here gives up quietly instead of reloading in a loop.
        if path == "/host" or re.fullmatch(r"/guest/[A-Za-z0-9_-]{16,64}", path):
            return None
        return _http(HTTPStatus.NOT_FOUND, b"not found")
    if path == "/healthz":
        return _http(HTTPStatus.OK, b"ok")
    # /s/<share_id> serves the guest app (webui: npm run build:guest).
    if re.fullmatch(r"/s/[A-Za-z0-9_-]{16,64}/?", path):
        path = "/guest.html"
    if path.startswith("/assets/") or path == "/guest.html":
        root = STATIC_DIR.resolve()
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        # Exactly guest.html or a file directly in assets/: nothing else is reachable.
        allowed = target == root / "guest.html" if path == "/guest.html" else target.parent == root / "assets"
        if allowed and target.is_file():
            return _static(target, request.headers.get("Accept-Encoding", ""))
    return _http(HTTPStatus.NOT_FOUND, b"not found")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    relay = Relay()
    port = int(os.environ.get("RELAY_PORT", "8787"))
    async with serve(
        relay.handler, "0.0.0.0", port,
        process_request=process_request,
        max_size=MAX_FRAME_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ) as server:
        log.info("navin relay listening on :%s", port)
        pruner = asyncio.create_task(relay.prune_forever())
        try:
            await server.serve_forever()
        finally:
            pruner.cancel()


if __name__ == "__main__":
    asyncio.run(main())
