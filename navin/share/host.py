# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The host side of a live share: this machine, its chat, and remote guests.

A share is one chat, one link (``https://<relay>/s/<id>#<key>``), an expiry
and a guest limit. This machine opens an outbound WebSocket to the relay
(works behind any NAT) and serves the chat to guests, end-to-end encrypted
(navin.share.crypto): the relay only forwards ciphertext.

Each guest is a *virtual connection* of the WebSocket channel, pinned to the
shared chat: it receives the transcript on arrival, then every live event.
Guests start with no rights (read only). The host may grant, per guest:

- ``drive``: send messages (and answer the agent's questions), so the guest
  works in this session, git included. Slash commands are limited to the
  work modes and /stop. Approval decisions always stay with the host: a
  push, a reset or any risky step waits for the host's own click.
- ``download``: receive the whole session (conversation, project with its git
  history and pending changes, memory) as a .navin-session file, to continue
  on their own machine.

The host may also share the project's running site (navin.share.site): every
guest of the link then opens it live in their page, through the same pipe.
An app share (``scope="site"``) carries nothing but that site: no
conversation, no events, no session details, no rights to ask for.

Revoking a share disconnects every guest and the relay refuses the link
until it would have expired.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from navin.share.crypto import ShareCipher, new_key
from navin.share.site import SiteError, fetch_site, new_site_client, normalize_site_url, site_port

DEFAULT_RELAY_URL = "wss://relay.navin.live"
TTL_CHOICES_S = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
BUNDLE_CHUNK_BYTES = 512 * 1024
HISTORY_PAGE = 120
# The relay drops a connection on frames over 1 MB; sealing and base64 add ~80%.
MAX_PAGE_BYTES = 450 * 1024
# Query results include complete tool output. Split text before encryption so
# even one large action fits through the relay without losing its contents.
EVENT_PART_CHARS = 16 * 1024
MAX_RELAY_FRAME = 1000 * 1024
# Link access levels: (may drive the agent, may download the session).
ACCESS_LEVELS = {"read": (False, False), "write": (True, False), "full": (True, True)}
MAX_MEDIA_BYTES = 40 * 1024 * 1024
MEDIA_URL = re.compile(r"^/api/media/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+$")
GUEST_QUEUE_LIMIT = 2000
# What a driving guest may send through the channel. Never approval_decision,
# terminals, previews, browser input, new chats, forks or attaching elsewhere.
DRIVER_ENVELOPES = frozenset({"message", "choice_answer"})
AGENT_READ_ENVELOPES = frozenset({"subagents_list", "subagent_history"})
AGENT_DRIVE_ENVELOPES = frozenset({"subagent_recover", "subagent_relaunch", "subagent_retry", "subagent_cancel"})
# The only fields a driving guest's envelope keeps. Anything else (file
# mentions, open files, media paths, CLI apps, MCP presets, model presets,
# workspace scope...) could make the agent read or reach beyond the shared
# conversation, so it is dropped before the channel sees the envelope.
GUEST_ENVELOPE_FIELDS = {
    "message": frozenset({"type", "content", "turn_id"}),
    "choice_answer": frozenset({"type", "request_id", "option_id", "skipped", "custom_text"}),
}
for _agent_kind in AGENT_READ_ENVELOPES | AGENT_DRIVE_ENVELOPES:
    GUEST_ENVELOPE_FIELDS[_agent_kind] = frozenset({"type", "request_id", "task_id", "after", "limit", "offset"})
MAX_GUEST_MESSAGE_CHARS = 20_000
MAX_GUEST_BUNDLE_BYTES = 150 * 1024 * 1024
CONTEXT_CACHE_S = 5.0
# Frames already seen per share: a replayed guest frame is ignored.
SEEN_NONCES_PER_SHARE = 4096
# A site page loads many files at once (a dev server: hundreds of modules).
SITE_BATCH_MAX = 64
SITE_CONCURRENCY = 6
SAFE_SLASH = frozenset({
    "/stop", "/ask", "/blueprint", "/forge", "/cruise", "/inspect", "/fortify", "/debug",
    "/reverse",
})


def relay_url() -> str:
    return (os.environ.get("NAVIN_RELAY_URL") or DEFAULT_RELAY_URL).rstrip("/")


def relay_http_base(url: str | None = None) -> str:
    base = url or relay_url()
    return "https://" + base.split("://", 1)[1] if base.startswith("wss://") else base.replace("ws://", "http://", 1)


class ChannelLike(Protocol):
    """The parts of WebSocketChannel a share uses (see navin/channels/websocket.py)."""

    _conn_identity: dict[Any, dict[str, Any]]
    _conn_default: dict[Any, str]

    def _attach(self, conn: Any, chat_id: str) -> None: ...
    async def _presence_join(self, conn: Any, chat_id: str) -> None: ...
    async def _send_event(self, conn: Any, event: str, **fields: Any) -> None: ...
    async def _hydrate_after_subscribe(self, chat_id: str) -> None: ...
    async def _dispatch_envelope(self, conn: Any, client_id: str, envelope: dict[str, Any]) -> None: ...
    async def _cleanup_connection(self, conn: Any) -> None: ...


class VirtualConnection:
    """A guest as the channel sees it: something to ``await send(str)`` on."""

    remote_address = ("navin-share", 0)

    def __init__(self, guest_id: str, chat_id: str) -> None:
        self.guest_id = guest_id
        self.chat_id = chat_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=GUEST_QUEUE_LIMIT)
        self.closed = False
        # Live browser frames are coalesced: only the newest waits, so a slow
        # guest always sees the current screen and never falls behind.
        self._frames: dict[str, str] = {}

    async def send(self, raw: str) -> None:
        if self.closed:
            from websockets.exceptions import ConnectionClosedOK

            raise ConnectionClosedOK(None, None)
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        # Pinned to the shared chat: the channel's global events (notifications,
        # other chats, Board, Montage, account...) never reach a guest.
        if not isinstance(payload, dict) or payload.get("chat_id") != self.chat_id:
            return
        if payload.get("event") == "agent_browser" and payload.get("phase") == "frame":
            frame_key = str(payload.get("id") or "browser")
            queued = frame_key in self._frames
            self._frames[frame_key] = raw
            if queued:
                return
            raw = FRAME_SLOT + frame_key
        try:
            self.queue.put_nowait(raw)
        except asyncio.QueueFull:
            # A guest that cannot keep up is dropped rather than slowing the chat.
            self.closed = True
            from websockets.exceptions import ConnectionClosedError

            raise ConnectionClosedError(None, None) from None

    async def close(self, *_args: Any) -> None:
        self.closed = True

    def take(self, raw: str) -> str | None:
        """Resolve a queued item: a frame slot yields the newest frame for it."""
        if raw.startswith(FRAME_SLOT):
            return self._frames.pop(raw[len(FRAME_SLOT):], None)
        return raw


FRAME_SLOT = "\x00frame:"


@dataclass
class _Bucket:
    """Token bucket: ``burst`` requests at once, then ``rate`` per second."""

    burst: float
    rate: float
    tokens: float = -1.0
    at: float = field(default_factory=time.monotonic)

    def allow(self) -> bool:
        now = time.monotonic()
        if self.tokens < 0:
            self.tokens = self.burst
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


@dataclass
class Guest:
    guest_id: str
    name: str = ""
    drive: bool = False
    download: bool = False
    requested_control: bool = False
    requested_download: bool = False
    joined_at: float = field(default_factory=time.time)
    conn: VirtualConnection | None = None
    # Registered with the channel (session shares): cleaned up when leaving.
    in_channel: bool = False
    pump: asyncio.Task[None] | None = None
    context_task: asyncio.Task[None] | None = None
    # A guest cannot make this machine work harder than a person would.
    requests: _Bucket = field(default_factory=lambda: _Bucket(burst=8, rate=2))
    media_requests: _Bucket = field(default_factory=lambda: _Bucket(burst=30, rate=10))
    messages: _Bucket = field(default_factory=lambda: _Bucket(burst=5, rate=1))
    site_requests: _Bucket = field(default_factory=lambda: _Bucket(burst=600, rate=150))
    downloading: bool = False
    media_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    site_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(SITE_CONCURRENCY))

    def public(self) -> dict[str, Any]:
        return {
            "guest_id": self.guest_id,
            "name": self.name or "Invité",
            "drive": self.drive,
            "download": self.download,
            "requested_control": self.requested_control,
            "requested_download": self.requested_download,
            "joined_at": self.joined_at,
        }


@dataclass
class Share:
    share_id: str
    chat_id: str
    key: str
    host_secret: str
    expires_at: float
    max_guests: int
    created_at: float = field(default_factory=time.time)
    relay: str = field(default_factory=relay_url)
    # What every guest gets on joining (the host still adjusts each guest).
    access: str = "read"
    # The running site guests may open (``http://localhost:5173``), or "" when not shared.
    site_url: str = ""
    # "session": the chat (and the site when shared); "site": the app alone.
    scope: str = "session"

    @property
    def link(self) -> str:
        # ``?app`` only shapes the guest page; the host enforces the scope.
        query = "?app" if self.scope == "site" else ""
        return f"{relay_http_base(self.relay)}/s/{self.share_id}{query}#{self.key}"


class ShareStore:
    """Active shares on disk (0600): links survive a restart until they expire."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Share]:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        now = time.time()
        shares = []
        for row in rows if isinstance(rows, list) else []:
            try:
                share = Share(**row)
            except TypeError:
                continue
            if share.expires_at > now:
                shares.append(share)
        return shares

    def save(self, shares: list[Share]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(share) for share in shares]), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        tmp.replace(self.path)


class ShareManager:
    def __init__(
        self,
        channel: ChannelLike,
        *,
        store_path: Path,
        sessions_workspace: Path,
        project_root_for: Any = None,
        connect: Any = None,
        thread_for: Any = None,
        media_fetch: Any = None,
        session_key_for: Any = None,
    ) -> None:
        self.channel = channel
        self.store = ShareStore(store_path)
        self.sessions_workspace = sessions_workspace
        # chat_id -> project folder (the desktop chat's linked project).
        self.project_root_for = project_root_for or (lambda _chat_id: sessions_workspace)
        # chat_id -> saved session key: a desktop chat is ``websocket:<id>``,
        # the CLI shares its own ``cli:<chat>`` session.
        self.session_key_for = session_key_for or (lambda chat_id: f"websocket:{chat_id}")
        self._connect = connect
        # (chat_id, limit, before) -> WebuiThreadPersistedPayload with signed media URLs.
        self.thread_for = thread_for
        # signed "/api/media/..." URL -> (bytes, mime) or None.
        self.media_fetch = media_fetch
        self.shares: dict[str, Share] = {}
        self.guests: dict[str, dict[str, Guest]] = {}
        self.ciphers: dict[str, ShareCipher] = {}
        self.sockets: dict[str, Any] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.listeners: list[Any] = []
        self._seen: dict[str, dict[str, None]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._context_cache: dict[str, tuple[float, Any]] = {}
        self._context_locks: dict[str, asyncio.Lock] = {}
        self._site_client: Any = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Resume the shares saved before a restart (call from a running loop)."""
        if self._started:
            return
        self._started = True
        for share in self.store.load():
            self._activate(share)

    async def stop(self) -> None:
        for task in list(self.tasks.values()):
            task.cancel()
        for share_id in list(self.guests):
            await self._drop_all_guests(share_id)
        if self._site_client is not None:
            with contextlib.suppress(Exception):
                await self._site_client.aclose()
            self._site_client = None

    def _activate(self, share: Share) -> None:
        self.shares[share.share_id] = share
        self.guests.setdefault(share.share_id, {})
        self.ciphers[share.share_id] = ShareCipher(share.key, share.share_id)
        self.tasks[share.share_id] = asyncio.create_task(self._run(share), name=f"share-{share.share_id[:6]}")

    def _persist(self) -> None:
        self.store.save(list(self.shares.values()))

    # -- host API (desktop) --------------------------------------------------------

    def create(
        self, chat_id: str, *, ttl: str = "24h", max_guests: int = 5, access: str = "read", scope: str = "session",
    ) -> dict[str, Any]:
        seconds = TTL_CHOICES_S.get(ttl, TTL_CHOICES_S["24h"])
        site_only = scope == "site"
        share = Share(
            share_id=secrets.token_urlsafe(16),
            chat_id=chat_id,
            key=new_key(),
            host_secret=secrets.token_urlsafe(32),
            expires_at=time.time() + seconds,
            max_guests=max(1, min(int(max_guests), 20)),
            # An app share never drives the agent nor downloads the session.
            access="read" if site_only or access not in ACCESS_LEVELS else access,
            scope="site" if site_only else "session",
        )
        self._activate(share)
        self._persist()
        return self.describe(share.share_id)

    def describe(self, share_id: str) -> dict[str, Any]:
        share = self.shares[share_id]
        return {
            "share_id": share.share_id,
            "chat_id": share.chat_id,
            "link": share.link,
            "expires_at": share.expires_at,
            "max_guests": share.max_guests,
            "access": share.access,
            "site_url": share.site_url,
            "scope": share.scope,
            "online": share_id in self.sockets,
            "guests": [guest.public() for guest in self.guests.get(share_id, {}).values() if guest.conn],
        }

    @staticmethod
    def _new_guest(share: Share, guest_id: str) -> Guest:
        drive, download = ACCESS_LEVELS.get(share.access, ACCESS_LEVELS["read"])
        return Guest(guest_id, drive=drive, download=download)

    async def set_access(self, share_id: str, access: str) -> bool:
        """Change the link's access level; applies to everyone already in, too."""
        share = self.shares.get(share_id)
        if share is None or access not in ACCESS_LEVELS or share.scope == "site":
            return False
        share.access = access
        self._persist()
        drive, download = ACCESS_LEVELS[access]
        for guest_id in list(self.guests.get(share_id, {})):
            await self.set_guest(share_id, guest_id, drive=drive, download=download)
        self._notify(share_id)
        return True

    async def set_site(self, share_id: str, enabled: bool, url: str = "") -> dict[str, Any]:
        """Share the running site with the link's guests, or stop. Raises KeyError, SiteError.

        Without ``url``, the site running from the chat's project is looked up.
        """
        share = self.shares[share_id]
        site = ""
        if enabled:
            site = normalize_site_url(url) if url.strip() else await self._find_site(share)
            if not site:
                raise SiteError("no_site")
            if await asyncio.to_thread(_is_navin, site):
                raise SiteError("navin")
        if site != share.site_url:
            share.site_url = site
            self._persist()
            for guest in list(self.guests.get(share_id, {}).values()):
                if guest.conn is not None:
                    await self._to_guest(share_id, guest.guest_id, {"type": "site", "enabled": bool(site)})
            self._notify(share_id)
        return self.describe(share_id)

    async def _find_site(self, share: Share, *, proven_only: bool = False) -> str:
        """The site started from the chat's project, never another project's.

        ``proven_only`` refuses a server whose folder cannot be read (macOS).
        """
        from navin.agent.tools.preview_server import discover_running_project_url

        root = self.project_root_for(share.chat_id)
        found = await asyncio.to_thread(
            discover_running_project_url, workspace=Path(root) if root else None, allow_unproven=not proven_only,
        )
        return normalize_site_url(found) if found else ""

    def for_chat(self, chat_id: str) -> list[dict[str, Any]]:
        return [self.describe(sid) for sid, share in self.shares.items() if share.chat_id == chat_id]

    async def revoke(self, share_id: str) -> bool:
        share = self.shares.pop(share_id, None)
        if share is None:
            return False
        ws = self.sockets.get(share_id)
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"type": "revoke"}))
        task = self.tasks.pop(share_id, None)
        if task is not None:
            task.cancel()
        await self._drop_all_guests(share_id)
        self.ciphers.pop(share_id, None)
        self.guests.pop(share_id, None)
        self._persist()
        return True

    async def set_guest(self, share_id: str, guest_id: str, *, drive: bool | None = None,
                        download: bool | None = None, remove: bool = False) -> bool:
        guest = self.guests.get(share_id, {}).get(guest_id)
        if guest is None:
            return False
        share = self.shares.get(share_id)
        if share is not None and share.scope == "site":
            drive = download = None  # an app visitor has no rights to grant
        if remove:
            await self._send_relay(share_id, {"type": "kick", "guest": guest_id})
            await self._drop_guest(share_id, guest_id)
            return True
        if drive is not None:
            guest.drive = drive
            guest.requested_control = False
            if guest.conn is not None:
                # The channel's ACL follows the role: viewers cannot send.
                self.channel._conn_identity[guest.conn]["role"] = "member" if drive else "viewer"
        if download is not None:
            guest.download = download
            guest.requested_download = False
        await self._to_guest(share_id, guest_id, {"type": "rights", "drive": guest.drive, "download": guest.download})
        self._notify(share_id)
        return True

    # -- relay connection ------------------------------------------------------------

    async def _open(self, url: str) -> Any:
        if self._connect is not None:
            return await self._connect(url)
        from websockets.asyncio.client import connect

        return await connect(url, max_size=2 * 1024 * 1024, ping_interval=20, ping_timeout=20)

    async def _run(self, share: Share) -> None:
        backoff = 1.0
        while share.share_id in self.shares and time.time() < share.expires_at:
            try:
                ws = await self._open(f"{share.relay}/host")
                await ws.send(json.dumps({
                    "type": "register", "share_id": share.share_id, "host_secret": share.host_secret,
                    "expires_at": share.expires_at, "max_guests": share.max_guests,
                }))
                reply = json.loads(await ws.recv())
                if reply.get("type") != "registered":
                    logger.warning("share {}: relay refused ({})", share.share_id[:6], reply.get("code"))
                    if reply.get("code") in {"revoked", "expired", "not_owner"}:
                        self.shares.pop(share.share_id, None)
                        self._persist()
                        return
                    raise ConnectionError(str(reply.get("code")))
                self.sockets[share.share_id] = ws
                backoff = 1.0
                # Guests the relay still holds after a reconnect: keep the known
                # ones, learn the others (they say hello again), forget the gone.
                present = {str(g) for g in reply.get("guests") or []}
                for gone in set(self.guests.get(share.share_id, {})) - present:
                    await self._drop_guest(share.share_id, gone)
                for guest_id in present:
                    self.guests.setdefault(share.share_id, {}).setdefault(guest_id, self._new_guest(share, guest_id))
                self._notify(share.share_id)
                async for raw in ws:
                    await self._on_relay(share, json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network: retry with backoff
                logger.debug("share {} relay link: {}", share.share_id[:6], exc)
            finally:
                self.sockets.pop(share.share_id, None)
                self._notify(share.share_id)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _send_relay(self, share_id: str, payload: dict[str, Any]) -> None:
        ws = self.sockets.get(share_id)
        if ws is None:
            return
        raw = json.dumps(payload)
        if len(raw) > MAX_RELAY_FRAME:
            # The relay would drop the whole link: skip this one frame instead.
            logger.debug("share {}: frame of {} bytes not relayed", share_id[:6], len(raw))
            return
        with contextlib.suppress(Exception):
            await ws.send(raw)

    async def _to_guest(self, share_id: str, guest_id: str, payload: dict[str, Any]) -> None:
        cipher = self.ciphers.get(share_id)
        if cipher is not None:
            await self._send_relay(share_id, {"type": "send", "guest": guest_id, "data": cipher.seal(payload)})

    async def _on_relay(self, share: Share, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        guest_id = str(msg.get("guest") or "")
        if kind == "guest_join":
            self.guests.setdefault(share.share_id, {})[guest_id] = self._new_guest(share, guest_id)
        elif kind == "guest_leave":
            await self._drop_guest(share.share_id, guest_id)
        elif kind == "from_guest":
            frame = str(msg.get("data") or "")
            try:
                payload = self.ciphers[share.share_id].open(frame)
            except (KeyError, ValueError):
                return  # not encrypted with this share's key: ignored
            # The 12-byte nonce opens every frame: one already seen is a replay.
            seen = self._seen.setdefault(share.share_id, {})
            nonce = frame[:16]
            if nonce in seen:
                return
            seen[nonce] = None
            if len(seen) > SEEN_NONCES_PER_SHARE:
                seen.pop(next(iter(seen)))
            await self._on_guest(share, guest_id, payload)

    # -- guests ------------------------------------------------------------------------

    async def _on_guest(self, share: Share, guest_id: str, payload: dict[str, Any]) -> None:
        guest = self.guests.get(share.share_id, {}).get(guest_id)
        if guest is None:
            return
        kind = payload.get("type")
        if share.scope == "site" and kind not in {"hello", "site"}:
            return  # an app visitor only opens the site
        if kind == "hello" and guest.conn is None:
            guest.name = str(payload.get("name") or "").strip()[:40] or "Invité"
            await self._attach_guest(share, guest)
        elif kind == "envelope" and guest.conn is not None:
            if not guest.messages.allow():
                await self._to_guest(share.share_id, guest.guest_id, {"type": "refused", "reason": "slow_down"})
                return
            await self._guest_envelope(share, guest, payload.get("envelope"))
        elif kind == "media" and guest.conn is not None:
            request_id = str(payload.get("id") or "")
            if not guest.media_requests.allow():
                await self._to_guest(share.share_id, guest.guest_id, {"type": "media_error", "id": request_id})
                return
            self._background(self._send_media(share, guest, request_id, str(payload.get("url") or "")))
        elif kind == "site" and guest.conn is not None:
            requests = payload.get("requests")
            for request in requests[:SITE_BATCH_MAX] if isinstance(requests, list) else []:
                if not isinstance(request, dict):
                    continue
                request_id = str(request.get("id") or "")[:64]
                if not share.site_url:
                    await self._to_guest(share.share_id, guest.guest_id, {"type": "site_res", "id": request_id, "error": "off"})
                elif not guest.site_requests.allow():
                    await self._to_guest(share.share_id, guest.guest_id, {"type": "site_res", "id": request_id, "error": "slow_down"})
                else:
                    self._background(self._serve_site(share, guest, request_id, request))
        elif kind in {"files", "file"} and guest.conn is not None:
            # The project's files, one by one: the download right, as for the session.
            request_id = str(payload.get("id") or "")[:64]
            bucket = guest.requests if kind == "files" else guest.media_requests
            if not guest.download:
                await self._to_guest(share.share_id, guest.guest_id, {"type": "file_error", "id": request_id, "reason": "not_allowed"})
            elif not bucket.allow():
                await self._to_guest(share.share_id, guest.guest_id, {"type": "file_error", "id": request_id, "reason": "slow_down"})
            elif kind == "files":
                self._background(self._send_file_list(share, guest, request_id))
            else:
                self._background(self._send_project_file(
                    share, guest, request_id, str(payload.get("path") or ""), bool(payload.get("preview")),
                ))
        elif kind in {"history", "context", "request_control", "request_download"} and not guest.requests.allow():
            if kind == "history":
                await self._to_guest(share.share_id, guest.guest_id, {"type": "history", "before": None, "thread": None})
        elif kind == "history" and guest.conn is not None:
            self._background(self._send_history(share, guest, str(payload.get("before") or "") or None))
        elif kind == "context" and guest.conn is not None:
            self._background(self._push_context(share, guest))
        elif kind == "request_control":
            guest.requested_control = True
            self._notify(share.share_id, request={"guest_id": guest_id, "name": guest.name, "what": "drive"})
        elif kind == "request_download":
            if guest.download:
                if not guest.downloading:
                    self._background(self._send_bundle(share, guest))
            else:
                guest.requested_download = True
                self._notify(share.share_id, request={"guest_id": guest_id, "name": guest.name, "what": "download"})

    def _background(self, coro: Any) -> None:
        """Heavy guest work runs aside: the relay loop keeps serving everyone."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_history(self, share: Share, guest: Guest, before: str | None) -> None:
        thread = await asyncio.to_thread(self._transcript, share.chat_id, before)
        await self._to_guest(share.share_id, guest.guest_id, {"type": "history", "before": before, "thread": thread})

    async def _attach_guest(self, share: Share, guest: Guest) -> None:
        conn = VirtualConnection(guest.guest_id, share.chat_id)
        guest.conn = conn
        if share.scope == "site":
            # Never registered with the channel: no chat event can reach it.
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "snapshot", "scope": "site", "chat_id": "", "thread": None, "context": None,
                "rights": {"drive": False, "download": False},
                "expires_at": share.expires_at, "site": bool(share.site_url),
            })
            self._notify(share.share_id)
            return
        guest.in_channel = True
        ch = self.channel
        ch._conn_identity[conn] = {
            "member_id": f"guest:{guest.guest_id}",
            "display_name": guest.name,
            # Never "host": that falls back to the license role (full access).
            "role": "member" if guest.drive else "viewer",
            "org_id": "",
        }
        ch._conn_default[conn] = share.chat_id
        guest.pump = asyncio.create_task(self._pump(share, guest))
        await self._to_guest(share.share_id, guest.guest_id, {
            "type": "snapshot",
            "chat_id": share.chat_id,
            "thread": await asyncio.to_thread(self._transcript, share.chat_id),
            "context": await self._cached_context(share.chat_id, CONTEXT_CACHE_S),
            "rights": {"drive": guest.drive, "download": guest.download},
            "expires_at": share.expires_at,
            "site": bool(share.site_url),
            "scope": "session",
        })
        ch._attach(conn, share.chat_id)
        await ch._presence_join(conn, share.chat_id)
        await ch._hydrate_after_subscribe(share.chat_id)
        self._notify(share.share_id)

    def _transcript(self, chat_id: str, before: str | None = None) -> Any:
        """One page of history (latest first, older on request): a frame stays far below the relay's limit."""
        try:
            from navin.webui.transcript import build_webui_thread_response

            limit = HISTORY_PAGE
            while True:
                if self.thread_for is not None:
                    page = self.thread_for(chat_id, limit, before)
                else:
                    page = build_webui_thread_response(
                        self.session_key_for(chat_id), limit=limit, direction=None if before else "latest", before=before,
                    )
                if limit <= 8 or len(json.dumps(page, ensure_ascii=False)) <= MAX_PAGE_BYTES:
                    return page
                limit //= 2
        except Exception as exc:  # noqa: BLE001 - a guest still gets the live events
            logger.debug("share transcript failed: {}", exc)
            return None

    def _context(self, chat_id: str) -> Any:
        """Dates, project, git, prompts: the full picture for someone joining."""
        try:
            from navin.session.bundle import BundleError, _session_file
            from navin.share.context import session_context

            try:
                session_file = _session_file(self.sessions_workspace, self.session_key_for(chat_id))
            except BundleError:
                session_file = None
            root = self.project_root_for(chat_id)
            return session_context(session_file, Path(root) if root else None)
        except Exception as exc:  # noqa: BLE001 - the share works without it
            logger.debug("share context failed: {}", exc)
            return None

    async def _cached_context(self, chat_id: str, max_age_s: float) -> Any:
        """git log/status once for all guests of a chat, not once per guest."""
        lock = self._context_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            cached = self._context_cache.get(chat_id)
            if cached and time.monotonic() - cached[0] < max_age_s:
                return cached[1]
            context = await asyncio.to_thread(self._context, chat_id)
            self._context_cache[chat_id] = (time.monotonic(), context)
            return context

    async def _push_context(self, share: Share, guest: Guest, max_age_s: float = CONTEXT_CACHE_S) -> None:
        context = await self._cached_context(share.chat_id, max_age_s)
        await self._to_guest(share.share_id, guest.guest_id, {"type": "context", "context": context})

    async def _pump(self, share: Share, guest: Guest) -> None:
        conn = guest.conn
        assert conn is not None
        while not conn.closed:
            item = conn.take(await conn.queue.get())
            if item is None:
                continue
            raw = item
            if len(raw.encode("utf-8")) > MAX_PAGE_BYTES:
                transfer_id = secrets.token_hex(12)
                total = (len(raw) + EVENT_PART_CHARS - 1) // EVENT_PART_CHARS
                for index in range(total):
                    await self._to_guest(share.share_id, guest.guest_id, {
                        "type": "event_part", "id": transfer_id, "index": index, "total": total,
                        "frame_part": raw[index * EVENT_PART_CHARS:(index + 1) * EVENT_PART_CHARS],
                    })
            else:
                await self._to_guest(share.share_id, guest.guest_id, {"type": "event", "frame": raw})
            # A turn ended: commits, changes and prompts may have moved.
            if '"turn_end"' in raw and (guest.context_task is None or guest.context_task.done()):
                guest.context_task = asyncio.create_task(self._push_context(share, guest, 1.0))

    async def _guest_envelope(self, share: Share, guest: Guest, envelope: Any) -> None:
        if not isinstance(envelope, dict) or guest.conn is None:
            return
        kind = envelope.get("type")
        refusal = None
        if kind not in DRIVER_ENVELOPES | AGENT_READ_ENVELOPES | AGENT_DRIVE_ENVELOPES:
            refusal = "not_allowed_for_guests"
        elif not guest.drive and kind not in AGENT_READ_ENVELOPES:
            refusal = "read_only"
        elif kind == "message":
            content = str(envelope.get("content") or "").strip()
            command = content.split(None, 1)[0].lower() if content.startswith("/") else ""
            if command and command not in SAFE_SLASH:
                refusal = "command_not_allowed"
        if not refusal and kind == "message" and len(str(envelope.get("content") or "")) > MAX_GUEST_MESSAGE_CHARS:
            refusal = "too_long"
        if refusal:
            await self._to_guest(share.share_id, guest.guest_id, {"type": "refused", "reason": refusal})
            return
        # Only the allowed fields, pinned to the shared chat: a guest never
        # reaches another conversation, another folder or a file by path.
        allowed = GUEST_ENVELOPE_FIELDS[str(kind)]
        pinned = {key: value for key, value in envelope.items() if key in allowed}
        pinned["chat_id"] = share.chat_id
        if kind == "message":
            pinned["webui"] = True
        await self.channel._dispatch_envelope(guest.conn, f"guest:{guest.guest_id}", pinned)

    async def _send_bundle(self, share: Share, guest: Guest) -> None:
        guest.downloading = True
        try:
            await self._send_bundle_once(share, guest)
        finally:
            guest.downloading = False

    async def _send_bundle_once(self, share: Share, guest: Guest) -> None:
        from navin.session.bundle import BundleError, export_session_bundle

        await self._to_guest(share.share_id, guest.guest_id, {"type": "bundle_start"})
        with tempfile.TemporaryDirectory(prefix="navin-share-") as tmp:
            try:
                result = await asyncio.to_thread(
                    export_session_bundle, self.sessions_workspace, self.session_key_for(share.chat_id),
                    Path(tmp) / "session.navin-session", project_root=self.project_root_for(share.chat_id),
                    for_someone_else=True,
                )
            except BundleError as exc:
                await self._to_guest(share.share_id, guest.guest_id, {"type": "bundle_error", "reason": str(exc)})
                return
            if result.path.stat().st_size > MAX_GUEST_BUNDLE_BYTES:
                # Too heavy for a browser (a phone above all): the host exports
                # it from the Share dialog and sends the file another way.
                await self._to_guest(share.share_id, guest.guest_id, {"type": "bundle_error", "reason": "too_large"})
                return
            data = await asyncio.to_thread(result.path.read_bytes)
        total = (len(data) + BUNDLE_CHUNK_BYTES - 1) // BUNDLE_CHUNK_BYTES or 1
        name = f"{share.chat_id[:8]}.navin-session"
        for index in range(total):
            chunk = data[index * BUNDLE_CHUNK_BYTES:(index + 1) * BUNDLE_CHUNK_BYTES]
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "bundle_chunk", "index": index, "total": total, "name": name,
                "data": base64.b64encode(chunk).decode("ascii"),
            })

    async def _serve_site(self, share: Share, guest: Guest, request_id: str, request: dict[str, Any]) -> None:
        """One request of the guest's site frame, answered from the shared local server."""
        import httpx

        if self._site_client is None:
            self._site_client = new_site_client()
        answer = None
        error = ""
        async with guest.site_slots:
            for attempt in range(2):
                origin = share.site_url
                if not origin:
                    error = "off"
                    break
                try:
                    answer = await fetch_site(self._site_client, origin, request)
                    break
                except SiteError as exc:
                    error = exc.code
                    break
                except httpx.ConnectError:
                    # The dev server came back on another port: follow it, same project only.
                    error = "unreachable"
                    if attempt or not await self._follow_site(share, origin):
                        break
                except httpx.HTTPError:
                    error = "unreachable"
                    break
        if answer is None:
            await self._to_guest(share.share_id, guest.guest_id, {"type": "site_res", "id": request_id, "error": error or "unreachable"})
            return
        data = answer.body
        total = (len(data) + BUNDLE_CHUNK_BYTES - 1) // BUNDLE_CHUNK_BYTES or 1
        for index in range(total):
            chunk = data[index * BUNDLE_CHUNK_BYTES:(index + 1) * BUNDLE_CHUNK_BYTES]
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "site_res", "id": request_id, "index": index, "total": total,
                "status": answer.status, "headers": answer.headers,
                "data": base64.b64encode(chunk).decode("ascii"),
            })

    async def _follow_site(self, share: Share, gone: str) -> bool:
        """True when the project's site now answers elsewhere (share updated)."""
        if share.site_url != gone:
            return True  # another request already followed it
        try:
            found = await self._find_site(share, proven_only=True)
        except SiteError:
            return False
        if not found or found == gone or site_port(found) == site_port(gone):
            return False
        if share.site_url == gone:
            share.site_url = found
            self._persist()
            self._notify(share.share_id)
        return True

    def _project_root(self, share: Share) -> Path | None:
        root = self.project_root_for(share.chat_id)
        if not root:
            return None
        root = Path(root)
        # The sessions folder is not a project: nothing to list there.
        return root if root.is_dir() and root.resolve() != Path(self.sessions_workspace).resolve() else None

    async def _send_file_list(self, share: Share, guest: Guest, request_id: str) -> None:
        from navin.share.files import list_project_files

        root = self._project_root(share)
        listing = await asyncio.to_thread(list_project_files, root) if root else {"files": [], "truncated": False}
        # A long listing travels in pages: each frame stays far below the relay's limit.
        files = listing["files"]
        page = 1500
        total = max(1, (len(files) + page - 1) // page)
        for index in range(total):
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "files", "id": request_id, "index": index, "total": total,
                "files": files[index * page:(index + 1) * page], "truncated": listing["truncated"],
                "project": Path(root).name if root else "",
            })

    async def _send_project_file(
        self, share: Share, guest: Guest, request_id: str, rel: str, preview: bool = False,
    ) -> None:
        from navin.share.files import preview_project_file, read_project_file

        root = self._project_root(share)
        try:
            if root is None:
                raise ValueError("not_found")
            async with guest.media_lock:
                reader = preview_project_file if preview else read_project_file
                data, mime, name = await asyncio.to_thread(reader, root, rel)
        except ValueError as exc:
            await self._to_guest(share.share_id, guest.guest_id, {"type": "file_error", "id": request_id, "reason": str(exc)})
            return
        except OSError:
            await self._to_guest(share.share_id, guest.guest_id, {"type": "file_error", "id": request_id, "reason": "not_found"})
            return
        total = (len(data) + BUNDLE_CHUNK_BYTES - 1) // BUNDLE_CHUNK_BYTES or 1
        for index in range(total):
            chunk = data[index * BUNDLE_CHUNK_BYTES:(index + 1) * BUNDLE_CHUNK_BYTES]
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "file_chunk", "id": request_id, "index": index, "total": total,
                "name": name, "mime": mime, "data": base64.b64encode(chunk).decode("ascii"),
            })

    async def _send_media(self, share: Share, guest: Guest, request_id: str, url: str) -> None:
        """An image, audio or file of the thread. Only URLs the host signed resolve."""
        got = None
        if self.media_fetch is not None and MEDIA_URL.match(url):
            async with guest.media_lock:
                got = await asyncio.to_thread(self.media_fetch, url)
        if not got or len(got[0]) > MAX_MEDIA_BYTES:
            await self._to_guest(share.share_id, guest.guest_id, {"type": "media_error", "id": request_id})
            return
        data, mime = got
        total = (len(data) + BUNDLE_CHUNK_BYTES - 1) // BUNDLE_CHUNK_BYTES or 1
        for index in range(total):
            chunk = data[index * BUNDLE_CHUNK_BYTES:(index + 1) * BUNDLE_CHUNK_BYTES]
            await self._to_guest(share.share_id, guest.guest_id, {
                "type": "media_chunk", "id": request_id, "index": index, "total": total, "mime": mime,
                "data": base64.b64encode(chunk).decode("ascii"),
            })

    async def _drop_guest(self, share_id: str, guest_id: str) -> None:
        guest = self.guests.get(share_id, {}).pop(guest_id, None)
        if guest is None:
            return
        if guest.pump is not None:
            guest.pump.cancel()
        if guest.conn is not None:
            guest.conn.closed = True
            if guest.in_channel:
                with contextlib.suppress(Exception):
                    await self.channel._cleanup_connection(guest.conn)
        self._notify(share_id)

    async def _drop_all_guests(self, share_id: str) -> None:
        for guest_id in list(self.guests.get(share_id, {})):
            await self._drop_guest(share_id, guest_id)

    def _notify(self, share_id: str, **extra: Any) -> None:
        """Tell the host's UI (share list, control requests)."""
        for listener in list(self.listeners):
            with contextlib.suppress(Exception):
                listener(share_id, extra)


def _is_navin(url: str) -> bool:
    """Navin's own editor or API must never be exposed as a guest's site."""
    from navin.agent.tools.preview_server import url_looks_like_navin

    return url_looks_like_navin(url)
