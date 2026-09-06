"""Live install feed for Montage setup (agent_exec frames on the WebUI bus)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

from loguru import logger

from navin.bus.outbound_events import AgentExecEvent, outbound_message_for_event

LogFn = Callable[[str], None]


def chat_id_from_session_key(session_key: str | None) -> str | None:
    """Extract the websocket chat id from a WebUI session key."""
    raw = (session_key or "").strip()
    if not raw:
        return None
    if raw.startswith("websocket:"):
        chat_id = raw.split(":", 1)[1].strip()
        return chat_id or None
    # Bare uuid already used as chat_id in some call sites.
    return raw


class MontageSetupStream:
    """Thread-safe log sink that mirrors Cursor-style ``agent_exec`` frames.

    HTTP handlers collect NDJSON lines for the final response body while also
    pushing live frames onto the outbound bus when a chat id is known.
    """

    def __init__(
        self,
        bus: Any | None,
        chat_id: str | None,
        *,
        command: str,
        cwd: str | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.exec_id = uuid.uuid4().hex[:12]
        self._bus = bus
        # Prefer the caller's chat; fall back to "*" so the install console still
        # receives live frames when Montage is open without an attached session.
        raw_chat = (chat_id or "").strip()
        self._chat_id = raw_chat or "*"
        self._command = command
        self._cwd = cwd
        self._loop = loop
        self._lines: list[dict[str, Any]] = []
        self._pending: list[str] = []
        self._done = False
        self._emit_start()

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._lines)

    def log(self, text: str) -> None:
        if self._done or not text:
            return
        self._pending.append(text)
        # Flush promptly so the UI feels live without a 500ms debounce.
        self._flush_now()

    def finish(self, exit_code: int | None) -> None:
        if self._done:
            return
        self._done = True
        self._flush_now()
        event = {
            "type": "done",
            "exit_code": exit_code,
            "id": self.exec_id,
        }
        self._lines.append(event)
        self._emit_agent(
            AgentExecEvent(
                exec_id=self.exec_id,
                phase="exit",
                exit_code=exit_code,
            )
        )

    def ndjson_body(self) -> bytes:
        return ("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self._lines)).encode(
            "utf-8"
        )

    def _emit_start(self) -> None:
        event = {
            "type": "start",
            "id": self.exec_id,
            "command": self._command,
            "cwd": self._cwd,
        }
        self._lines.append(event)
        self._emit_agent(
            AgentExecEvent(
                exec_id=self.exec_id,
                phase="start",
                command=self._command,
                cwd=self._cwd,
                background=False,
            )
        )

    def _flush_now(self) -> None:
        if not self._pending:
            return
        data = "".join(self._pending)
        self._pending.clear()
        self._lines.append({"type": "log", "data": data, "id": self.exec_id})
        self._emit_agent(
            AgentExecEvent(
                exec_id=self.exec_id,
                phase="output",
                data=data,
            )
        )

    def _emit_agent(self, event: AgentExecEvent) -> None:
        if self._bus is None:
            return

        def _put() -> None:
            try:
                self._bus.outbound.put_nowait(
                    outbound_message_for_event(
                        channel="websocket",
                        chat_id=self._chat_id,
                        event=event,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - never fail install on UI feed
                logger.debug("montage setup stream emit failed: {}", exc)

        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                _put()
            else:
                loop.call_soon_threadsafe(_put)
            return
        _put()
