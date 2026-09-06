"""End-to-end gate: boot a real gateway and drive the real socket path.

Unlike test_live_gateway_smoke (which skips when nothing is running), this
suite starts its own gateway subprocess on an ephemeral port with an
isolated HOME, so it runs on every CI OS (Linux, macOS, Windows) and fails
loudly instead of skipping. It certifies the exact path the IDE uses:

    HTTP health -> SPA shell -> /webui/bootstrap token -> WebSocket
    handshake -> new_chat -> message envelope -> bus -> agent loop ->
    outbound events back on the same socket.

The isolated HOME means no user config and no API keys: the turn lands on
the unconfigured-provider reply, which is exactly what we want - the full
round trip is exercised without any network or model dependency.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

_STARTUP_TIMEOUT_S = 120.0
_EVENT_TIMEOUT_S = 90.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _get(base: str, path: str, timeout: float = 10.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class GatewayEndToEndTest(unittest.TestCase):
    process: subprocess.Popen[bytes] | None = None
    base: str = ""
    port: int = 0
    tmp: str = ""
    log_path: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="navin-e2e-")
        home = Path(cls.tmp) / "home"
        workspace = Path(cls.tmp) / "workspace"
        home.mkdir()
        workspace.mkdir()
        (workspace / "hello.py").write_text("print('hi')\n", encoding="utf-8")

        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.log_path = Path(cls.tmp) / "gateway.log"

        # The WebSocket/WebUI channel reads its port from the config file
        # (the --port flag only drives the standalone health listener), so
        # the ephemeral port must be pinned there.
        config_path = Path(cls.tmp) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "channels": {
                        "websocket": {
                            "enabled": True,
                            "host": "127.0.0.1",
                            "port": cls.port,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        env = dict(os.environ)
        # Full isolation: never read the developer's real config or keys, so
        # the turn deterministically reaches the unconfigured-provider reply.
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
        env["PYTHONUTF8"] = "1"

        with open(cls.log_path, "wb") as log:
            cls.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "navin",
                    "gateway",
                    "--foreground",
                    "--port",
                    str(cls.port),
                    "--workspace",
                    str(workspace),
                    "--config",
                    str(config_path),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(workspace),
            )

        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise RuntimeError(
                    f"gateway exited early (rc={cls.process.returncode}):\n"
                    + cls._log_tail()
                )
            try:
                status, body = _get(cls.base, "/health", timeout=2.0)
                if status == 200 and b"ok" in body:
                    return
            except Exception as e:  # noqa: BLE001 - retried until deadline
                last_error = e
            time.sleep(0.5)
        raise RuntimeError(
            f"gateway did not become healthy in {_STARTUP_TIMEOUT_S}s "
            f"(last error: {last_error}):\n" + cls._log_tail()
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.process is not None and cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=15)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def _log_tail(cls, limit: int = 4000) -> str:
        try:
            text = cls.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "<no gateway log>"
        return text[-limit:]

    # -- HTTP surface --------------------------------------------------------

    def test_health_answers_ok(self) -> None:
        status, body = _get(self.base, "/health")
        self.assertEqual(status, 200, self._log_tail())
        self.assertIn(b"ok", body)

    def test_spa_shell_is_served(self) -> None:
        # A concurrent `npm run build` rewrites navin/web/dist in place, so a
        # single read can catch a half-written bundle; short retries keep this
        # focused on real regressions (same rationale as the live smoke test).
        status, body = _get(self.base, "/")
        for _ in range(4):
            if status == 200 and b'<div id="root">' in body:
                break
            time.sleep(2.0)
            status, body = _get(self.base, "/")
        self.assertEqual(status, 200, self._log_tail())
        self.assertIn(b'<div id="root">', body)

    def test_bootstrap_issues_a_token_and_ws_url(self) -> None:
        status, body = _get(self.base, "/webui/bootstrap")
        self.assertEqual(status, 200, self._log_tail())
        payload = json.loads(body)
        self.assertTrue(payload.get("token"))
        self.assertTrue(payload.get("ws_url"))

    # -- Full socket round trip ----------------------------------------------

    def test_websocket_chat_round_trip(self) -> None:
        """new_chat -> attached, then a message crosses bus + agent loop and
        events come back on the same socket."""
        asyncio.run(self._chat_round_trip())

    async def _chat_round_trip(self) -> None:
        import websockets

        status, body = _get(self.base, "/webui/bootstrap")
        self.assertEqual(status, 200, self._log_tail())
        payload = json.loads(body)
        token = payload["token"]
        ws_url = str(payload["ws_url"])
        if ws_url.startswith("/"):
            ws_url = f"ws://127.0.0.1:{self.port}{ws_url}"
        separator = "&" if "?" in ws_url else "?"
        url = f"{ws_url}{separator}client_id=e2e-test&token={token}"

        received: list[dict] = []

        async def _await_event(ws, predicate, what: str) -> dict:
            deadline = asyncio.get_event_loop().time() + _EVENT_TIMEOUT_S
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise AssertionError(
                        f"timed out waiting for {what}; received={received!r}\n"
                        + self._log_tail()
                    )
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if isinstance(data, dict):
                    received.append(data)
                    if predicate(data):
                        return data

        async with websockets.connect(url, max_size=2**24) as ws:
            await ws.send(json.dumps({"type": "new_chat"}))
            attached = await _await_event(
                ws, lambda d: d.get("event") == "attached", "attached"
            )
            chat_id = attached.get("chat_id")
            self.assertTrue(chat_id, f"attached without chat_id: {attached!r}")

            await ws.send(
                json.dumps(
                    {
                        "type": "message",
                        "chat_id": chat_id,
                        "content": "ping from the e2e gate",
                        "webui": True,
                    }
                )
            )
            # Any turn-output event for this chat proves the full path:
            # inbound envelope -> bus -> agent loop -> outbound -> socket.
            turn_events = {"message", "stream_end", "error", "reasoning_delta"}
            reply = await _await_event(
                ws,
                lambda d: d.get("event") in turn_events
                and d.get("chat_id") == chat_id,
                f"a turn event among {sorted(turn_events)}",
            )
            self.assertIn(reply.get("event"), turn_events)

    def test_a_second_client_can_attach_to_the_same_chat(self) -> None:
        asyncio.run(self._attach_second_client())

    async def _attach_second_client(self) -> None:
        import websockets

        async def _open(client_id: str):
            # Issued tokens are single-use: each connection needs its own.
            _, body = _get(self.base, "/webui/bootstrap")
            payload = json.loads(body)
            token = payload["token"]
            ws_url = str(payload["ws_url"])
            if ws_url.startswith("/"):
                ws_url = f"ws://127.0.0.1:{self.port}{ws_url}"
            separator = "&" if "?" in ws_url else "?"
            return await websockets.connect(
                f"{ws_url}{separator}client_id={client_id}&token={token}",
                max_size=2**24,
            )

        async def _next_event(ws, wanted: str) -> dict:
            deadline = asyncio.get_event_loop().time() + _EVENT_TIMEOUT_S
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                self.assertGreater(remaining, 0, f"timed out waiting for {wanted}")
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("event") == wanted:
                    return data

        first = await _open("e2e-owner")
        try:
            await first.send(json.dumps({"type": "new_chat"}))
            attached = await _next_event(first, "attached")
            chat_id = attached["chat_id"]

            second = await _open("e2e-follower")
            try:
                await second.send(json.dumps({"type": "attach", "chat_id": chat_id}))
                follower_attached = await _next_event(second, "attached")
                self.assertEqual(follower_attached.get("chat_id"), chat_id)
            finally:
                await second.close()
        finally:
            await first.close()


if __name__ == "__main__":
    unittest.main()
