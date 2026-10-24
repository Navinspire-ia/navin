"""A dev server listening on ::1 only (Vite / Next defaults on Windows and recent
macOS) is found, reached, proxied and kept, and a process folder is never faked."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil  # noqa: F401 - loaded for the real platform before sys.platform is patched
import pytest

from navin import ports
from navin.agent.tools import preview_server as ps
from navin.webui.preview_proxy import PreviewProxyRegistry


def _ipv6_loopback_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.bind(("::1", 0))
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _ipv6_loopback_available(), reason="no IPv6 loopback")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"<!doctype html><html><head><title>shop</title></head><body>shop</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


@pytest.fixture
def ipv6_only_server():
    """An HTTP server on [::1] only, on a port that is free on 127.0.0.1 too."""
    for _ in range(20):
        server = _V6Server(("::1", 0), _Handler)
        port = server.server_address[1]
        if not ports.port_in_use("127.0.0.1", port):
            break
        server.server_close()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


def test_loopback_host_finds_an_ipv6_only_server(ipv6_only_server):
    port = ipv6_only_server
    assert ports.port_in_use("127.0.0.1", port) is False
    assert ports.loopback_host(port) == "::1"
    assert ports.loopback_url(port) == f"http://[::1]:{port}"


def test_loopback_host_prefers_ipv4_and_reports_free_ports():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert ports.loopback_host(port) == "127.0.0.1"
        assert ports.loopback_url(port) == f"http://127.0.0.1:{port}"
    assert ports.loopback_host(port) is None
    assert ports.loopback_url(port) == f"http://127.0.0.1:{port}"


def test_a_127_url_counts_when_the_server_is_on_ipv6_only(ipv6_only_server):
    port = ipv6_only_server
    assert ps.http_reachable(f"http://127.0.0.1:{port}") is True
    assert ps.http_reachable(f"http://localhost:{port}/") is True
    assert asyncio.run(ps.wait_for_url(f"http://127.0.0.1:{port}", timeout_s=3)) is True


def test_preview_discovery_finds_the_ipv6_only_app_of_the_open_project(ipv6_only_server):
    port = ipv6_only_server
    # The listener is this test process: its folder is the current directory.
    url = ps.discover_running_project_url(preferred_port=port, workspace=Path.cwd())
    assert url == f"http://[::1]:{port}"


def test_a_port_busy_on_ipv6_only_is_not_handed_out_as_free(ipv6_only_server, monkeypatch):
    port = ipv6_only_server
    monkeypatch.setattr(ps, "_navin_ports", lambda: frozenset())
    assert ps._free_port_after(port - 1, span=1) is None


def test_preview_proxy_reaches_an_ipv6_only_app(ipv6_only_server):
    port = ipv6_only_server

    async def run() -> bytes:
        registry = PreviewProxyRegistry()
        proxy_port = await registry.ensure(port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{proxy_port}\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), timeout=5)
            writer.close()
            return data
        finally:
            await registry.stop_all()

    data = asyncio.run(run())
    assert b" 200 " in data.split(b"\r\n", 1)[0]
    assert b"shop" in data


def test_process_folder_is_real_or_none_never_a_fake_proc_path(monkeypatch):
    assert ports._cwd_for_pid(os.getpid()) == os.getcwd()
    assert ports._cwd_for_pid(2**22 + 12345) is None
    # macOS / Windows have no /proc: the folder comes from psutil, or nothing.
    monkeypatch.setattr(ports.sys, "platform", "darwin")
    assert ports._cwd_for_pid(os.getpid()) == os.getcwd()
    assert ports._cwd_for_pid(2**22 + 12345) is None
    assert "pytest" in (ports._cmdline_for_pid(os.getpid()) or "")


def test_who_listens_without_ss_or_lsof_reads_the_os_table(monkeypatch):
    """Windows, and slim Linux images, have neither ss nor lsof."""
    monkeypatch.setattr(ports, "_who_via_ss", lambda _port: None)
    monkeypatch.setattr(ports, "_who_via_lsof", lambda _port: None)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        info = ports.who_listens(port)
    assert info is not None and info.source == "psutil"
    assert info.pid == os.getpid()
    assert info.cwd == os.getcwd()
