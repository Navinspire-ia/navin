# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Loopback HTTP server that feeds a composition to headless Chromium.

ES modules (three.js) and seekable <video> layers both need real HTTP: module
scripts are blocked on file:// and media seeking relies on Range requests. The
server only answers under a random secret prefix, binds 127.0.0.1 on an
ephemeral port, and serves three kinds of files:

- ``/<secret>/assets/...``     the vendored runtime (three.js, GSAP, runtime JS);
- ``/<secret>/templates/...``  the built-in template library;
- ``/<secret>/root/<id>/...``  a directory registered for a custom composition;
- ``/<secret>/media/<id>/<name>`` one explicitly registered file (images,
  video proxies, fonts, logos).

Nothing else on disk is reachable.
"""

from __future__ import annotations

import hashlib
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

_CHUNK = 256 * 1024

# Custom compositions write ``<script src="navin-motion://assets/gsap.min.js">``;
# the server rewrites the token to this job's loopback asset URL.
ASSETS_TOKEN = "navin-motion://assets/"

_EXTRA_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _inside(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


class MotionServer:
    """Serve one render job's files over loopback HTTP (context manager)."""

    def __init__(self, assets_dir: Path, templates_dir: Path) -> None:
        self._secret = secrets.token_urlsafe(12)
        self._roots: dict[str, Path] = {
            "assets": Path(assets_dir).resolve(),
            "templates": Path(templates_dir).resolve(),
        }
        self._dirs: dict[str, Path] = {}
        self._files: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- registration -----------------------------------------------------

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("motion server is not running")
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}/{self._secret}"

    def template_url(self, name: str) -> str:
        return f"{self.base_url}/templates/{quote(name)}.html"

    def register_dir(self, directory: Path) -> str:
        """Expose a whole directory (custom composition); returns its base URL."""
        resolved = Path(directory).resolve()
        key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        with self._lock:
            self._dirs[key] = resolved
        return f"{self.base_url}/root/{key}"

    def register_file(self, path: Path) -> str:
        """Expose one file; returns a URL that keeps its file name and suffix."""
        resolved = Path(path).resolve()
        key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        with self._lock:
            self._files[key] = resolved
        return f"{self.base_url}/media/{key}/{quote(resolved.name)}"

    # -- lifecycle --------------------------------------------------------

    def start(self) -> MotionServer:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve(head=True)

            def do_GET(self) -> None:  # noqa: N802
                self._serve(head=False)

            def _send_html(self, target: Path, *, head: bool) -> None:
                # Custom compositions reference the vendored runtime through a
                # stable token, so they never depend on this job's random URL.
                try:
                    text = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = text.replace(ASSETS_TOKEN, server.base_url + "/assets/").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head:
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return

            def _serve(self, *, head: bool) -> None:
                raw_path = urlsplit(self.path).path
                target = server._lookup(raw_path)
                if target is None or not target.is_file():
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if target.suffix.lower() in {".html", ".htm"}:
                    self._send_html(target, head=head)
                    return
                try:
                    size = target.stat().st_size
                except OSError:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = 0, size - 1
                status = 200
                header = self.headers.get("Range") or ""
                if header.startswith("bytes=") and size > 0:
                    spec = header[6:].split(",")[0].strip()
                    first, _, last = spec.partition("-")
                    try:
                        if first:
                            start = int(first)
                            end = int(last) if last else size - 1
                        elif last:
                            start = max(0, size - int(last))
                        end = min(end, size - 1)
                    except ValueError:
                        start, end = 0, size - 1
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = 206
                length = max(0, end - start + 1)
                self.send_response(status)
                self.send_header("Content-Type", _content_type(target))
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head or length == 0:
                    return
                try:
                    with target.open("rb") as handle:
                        handle.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = handle.read(min(_CHUNK, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="navin-motion-http", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> MotionServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- routing ----------------------------------------------------------

    def _lookup(self, raw_path: str) -> Path | None:
        parts = [unquote(p) for p in raw_path.split("/") if p]
        if len(parts) < 3 or parts[0] != self._secret:
            return None
        kind, rest = parts[1], parts[2:]
        if any(p in {"..", "."} or "\\" in p or ":" in p for p in rest):
            return None
        with self._lock:
            if kind in self._roots:
                base = self._roots[kind]
                candidate = base.joinpath(*rest).resolve()
                return candidate if _inside(base, candidate) else None
            if kind == "root" and rest[0] in self._dirs:
                base = self._dirs[rest[0]]
                if len(rest) < 2:
                    return None
                candidate = base.joinpath(*rest[1:]).resolve()
                return candidate if _inside(base, candidate) else None
            if kind == "media" and len(rest) == 2 and rest[0] in self._files:
                found = self._files[rest[0]]
                return found if found.name == rest[1] else None
        return None


def is_loopback_url(url: str, base_url: str) -> bool:
    return url.startswith(base_url + "/") or url == base_url


__all__ = ["ASSETS_TOKEN", "MotionServer", "is_loopback_url"]
