# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The host's running site, opened by share guests through the encrypted pipe.

A guest's browser asks for a path (its service worker catches the requests of
the site frame); this machine fetches that path from the one local server the
host shared and seals the answer back. Nothing else is reachable: no other
port, no other machine, never Navin itself. The site is reached as
``localhost`` by this machine, so dev servers that check the Host header
(Vite, Next.js) answer as they do for the host.

Limits of a tunnel built on plain requests: no WebSocket (hot reload stays
on the host, the guest reloads), no streaming answers, no cookies set by the
server (a service worker cannot set them).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

SITE_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A guest frame carries a batch of requests and the relay drops frames over 1 MB.
MAX_SITE_REQUEST_BYTES = 256 * 1024
MAX_SITE_RESPONSE_BYTES = 40 * 1024 * 1024
# First hits on a dev server compile on the fly (Next.js can take a while).
SITE_TIMEOUT_S = 60.0
MAX_PATH_CHARS = 4096
# What this machine sends on its own: the guest's browser never sets these.
_DROP_REQUEST_HEADERS = frozenset({
    "host", "origin", "referer", "cookie", "connection", "content-length", "transfer-encoding",
    "accept-encoding", "upgrade", "te", "trailer", "keep-alive", "proxy-authorization",
    "proxy-connection", "service-worker", "service-worker-navigation-preload",
})
_KEEP_RESPONSE_HEADERS = frozenset({
    "content-type", "cache-control", "etag", "last-modified", "expires", "vary", "location",
    "content-language", "content-disposition", "access-control-allow-origin",
    "access-control-allow-methods", "access-control-allow-headers", "access-control-expose-headers",
    "x-vite-ping",
})
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")


class SiteError(ValueError):
    """``code``: bad_url (not a local http(s) address), navin (Navin itself), no_site."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_site_url(url: str) -> str:
    """``http(s)://<loopback>:<port>``, the only kind of target a share may expose."""
    raw = (url or "").strip()
    if raw and "://" not in raw:
        raw = f"http://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise SiteError("bad_url") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or host not in LOOPBACK_HOSTS:
        raise SiteError("bad_url")
    port = port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{'[::1]' if host == '::1' else host}:{port}"


def site_port(origin: str) -> int:
    return int(urlsplit(origin).port or 80)


def clean_path(raw: Any) -> str | None:
    """A path on the shared origin: never a full URL, never another host."""
    path = str(raw or "")
    if not path.startswith("/") or path.startswith("//") or "\\" in path or len(path) > MAX_PATH_CHARS:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return None
    return path


def _request_headers(raw: Any) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, value = str(item[0]).strip(), str(item[1])
        lowered = name.lower()
        if (
            not _HEADER_NAME.match(name)
            or lowered in _DROP_REQUEST_HEADERS
            or lowered.startswith(("sec-", "proxy-"))
            or "\n" in value
            or "\r" in value
        ):
            continue
        headers[name] = value[:8192]
    return headers


def _local_location(location: str, origin: str) -> str:
    """A redirect to the shared server itself stays inside the guest's frame."""
    try:
        target = urlsplit(location)
        port = target.port or (443 if target.scheme == "https" else 80)
    except ValueError:
        return location
    if (target.hostname or "").lower() in LOOPBACK_HOSTS and port == site_port(origin):
        return (target.path or "/") + (f"?{target.query}" if target.query else "")
    return location


@dataclass
class SiteAnswer:
    status: int
    headers: list[list[str]]
    body: bytes


async def fetch_site(client: httpx.AsyncClient, origin: str, request: dict[str, Any]) -> SiteAnswer:
    """One guest request against the shared server. Raises SiteError / httpx errors."""
    method = str(request.get("method") or "GET").upper()
    path = clean_path(request.get("path"))
    if method not in SITE_METHODS or path is None:
        raise SiteError("bad_request")
    body = b""
    if request.get("body"):
        try:
            body = base64.b64decode(str(request["body"]), validate=True)
        except ValueError as exc:
            raise SiteError("bad_request") from exc
    if len(body) > MAX_SITE_REQUEST_BYTES:
        raise SiteError("too_large")
    upstream = client.build_request(
        method, origin + path, headers=_request_headers(request.get("headers")),
        content=body if method not in {"GET", "HEAD"} else None,
    )
    response = await client.send(upstream, stream=True, follow_redirects=False)
    try:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_SITE_RESPONSE_BYTES:
                raise SiteError("too_large")
            chunks.append(chunk)
    finally:
        await response.aclose()
    headers = []
    for name, value in response.headers.multi_items():
        lowered = name.lower()
        if lowered not in _KEEP_RESPONSE_HEADERS:
            continue
        headers.append([lowered, _local_location(value, origin) if lowered == "location" else value])
    return SiteAnswer(response.status_code, headers, b"".join(chunks))


def new_site_client() -> httpx.AsyncClient:
    # Local dev servers only: no proxy from the environment, self-signed HTTPS allowed.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(SITE_TIMEOUT_S, connect=5.0), trust_env=False, verify=False,
        limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
    )
