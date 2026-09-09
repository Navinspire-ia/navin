# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Minimal synchronous DAP client over stdio (Content-Length framing)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from navin.utils.proc import no_window_kwargs

_DEFAULT_TIMEOUT_S = 20.0
_INIT_TIMEOUT_S = 45.0
_READ_CHUNK = 65536


class DapError(RuntimeError):
    """A debug adapter could not be started or did not answer."""


@dataclass(slots=True)
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    body: Any = None
    success: bool = False
    message: str = ""


class DapClient:
    """One debug adapter process, driven over stdio with DAP framing."""

    def __init__(
        self,
        argv: list[str],
        root: Path,
        *,
        name: str = "dap",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.name = name
        self.root = root.resolve(strict=False)
        self._argv = argv
        self._on_event = on_event
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_seq = 1
        self._pending: dict[int, _Pending] = {}
        self._alive = False
        self._stderr_tail: list[str] = []

    @property
    def alive(self) -> bool:
        return self._alive and self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        try:
            self._process = subprocess.Popen(  # noqa: S603
                self._argv,
                cwd=str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DapError(f"cannot start {self.name}: {exc}") from exc

        self._alive = True
        self._reader = threading.Thread(
            target=self._read_loop, name=f"dap-{self.name}", daemon=True
        )
        self._reader.start()
        threading.Thread(
            target=self._drain_stderr, name=f"dap-{self.name}-err", daemon=True
        ).start()

    def stop(self) -> None:
        self._alive = False
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry.success = False
            entry.message = f"{self.name} stopped"
            entry.event.set()

    def request(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> Any:
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            entry = _Pending()
            self._pending[seq] = entry
        payload: dict[str, Any] = {
            "seq": seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            payload["arguments"] = arguments
        self._write(payload)
        if not entry.event.wait(timeout):
            with self._lock:
                self._pending.pop(seq, None)
            raise DapError(f"{self.name} {command} timed out after {timeout}s")
        if not entry.success:
            raise DapError(entry.message or f"{self.name} {command} failed")
        return entry.body

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise DapError(f"{self.name} is not running")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        try:
            process.stdin.write(header + body)
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._alive = False
            raise DapError(f"{self.name} stdin closed: {exc}") from exc

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        stream = process.stdout
        buffer = b""
        while True:
            if process.poll() is not None and not buffer:
                break
            try:
                chunk = stream.read1(_READ_CHUNK) if hasattr(stream, "read1") else stream.read(1)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            while True:
                header_end = buffer.find(b"\r\n\r\n")
                if header_end < 0:
                    break
                header_text = buffer[:header_end].decode("utf-8", errors="replace")
                length = 0
                for line in header_text.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            length = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            length = 0
                body_start = header_end + 4
                if len(buffer) < body_start + length:
                    break
                raw = buffer[body_start : body_start + length]
                buffer = buffer[body_start + length :]
                try:
                    message = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        self._alive = False
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry.success = False
            entry.message = f"{self.name} exited"
            entry.event.set()

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "response":
            request_seq = message.get("request_seq")
            if request_seq is None:
                return
            with self._lock:
                entry = self._pending.pop(int(request_seq), None)
            if entry is None:
                return
            entry.success = bool(message.get("success"))
            entry.body = message.get("body")
            if not entry.success:
                entry.message = str(
                    message.get("message")
                    or (message.get("body") or {}).get("error")
                    or f"{message.get('command')} failed"
                )
            entry.event.set()
            return

        if kind == "event":
            event = str(message.get("event") or "")
            body = message.get("body")
            if not isinstance(body, dict):
                body = {}
            if self._on_event is not None:
                try:
                    self._on_event(event, body)
                except Exception:
                    pass
            return

        if kind == "request":
            # Reverse request from the adapter - answer minimally.
            seq = message.get("seq")
            command = str(message.get("command") or "")
            reply_body: Any = {}
            success = True
            if command == "runInTerminal":
                # Workbench owns the process launch via launch args; ignore.
                reply_body = {"processId": None}
            try:
                self._write(
                    {
                        "seq": self._next_out_seq(),
                        "type": "response",
                        "request_seq": seq,
                        "success": success,
                        "command": command,
                        "body": reply_body,
                    }
                )
            except DapError:
                pass

    def _next_out_seq(self) -> int:
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            return seq

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self._stderr_tail.append(line)
                    if len(self._stderr_tail) > 40:
                        self._stderr_tail = self._stderr_tail[-40:]
        except (OSError, ValueError):
            return

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-20:])

    def wait_event(
        self,
        name: str,
        *,
        timeout: float = _INIT_TIMEOUT_S,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Block until an event arrives (used by tests / launch handshake)."""
        done = threading.Event()
        holder: dict[str, Any] = {}

        def _hook(event: str, body: dict[str, Any]) -> None:
            if event != name:
                return
            if predicate is not None and not predicate(body):
                return
            holder.update(body)
            done.set()

        previous = self._on_event

        def _combined(event: str, body: dict[str, Any]) -> None:
            if previous is not None:
                previous(event, body)
            _hook(event, body)

        self._on_event = _combined
        try:
            # Events may already have been delivered before wait starts; poll state
            # via a short sleep loop is handled by caller for stopped.
            if not done.wait(timeout):
                raise DapError(f"timed out waiting for DAP event {name!r}")
            return holder
        finally:
            self._on_event = previous
