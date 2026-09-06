"""Mobile preview sessions streamed over the WebUI WebSocket.

Mirrors the terminal pattern: one session per connection id, frames and logs
pushed as JSON events with base64 PNG payloads.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger

from navin.mobile.preview import (
    PreviewError,
    PreviewSession,
    frame_to_b64,
    friendly_device_gone_message,
    is_device_gone_error,
    open_preview,
)

MAX_PREVIEWS_PER_CONNECTION = 2


class MobilePreviewManager:
    """Preview sessions grouped per WebSocket connection."""

    def __init__(self) -> None:
        self._by_connection: dict[Any, dict[str, _LivePreview]] = {}

    def get(self, connection: Any, preview_id: str) -> PreviewSession | None:
        live = self._by_connection.get(connection, {}).get(preview_id)
        return live.session if live else None

    def open(
        self,
        connection: Any,
        *,
        preview_id: str,
        serial: str | None,
        fps: float,
        on_frame: Callable[[str, dict[str, Any]], Awaitable[None]],
        on_logs: Callable[[str, list[str]], Awaitable[None]],
        on_error: Callable[[str, str], Awaitable[None]],
        on_exit: Callable[[str], Awaitable[None]],
    ) -> PreviewSession:
        sessions = self._by_connection.setdefault(connection, {})
        if preview_id in sessions:
            raise PreviewError("preview already open")
        if len(sessions) >= MAX_PREVIEWS_PER_CONNECTION:
            raise PreviewError("too many mobile previews open")
        session = open_preview(serial=serial, fps=fps)
        live = _LivePreview(
            preview_id=preview_id,
            session=session,
            on_frame=on_frame,
            on_logs=on_logs,
            on_error=on_error,
            on_exit=on_exit,
        )
        sessions[preview_id] = live
        live.start()
        return session

    def close(self, connection: Any, preview_id: str) -> None:
        sessions = self._by_connection.get(connection)
        if not sessions:
            return
        live = sessions.pop(preview_id, None)
        if live is not None:
            live.stop()
        if not sessions:
            self._by_connection.pop(connection, None)

    def cleanup_connection(self, connection: Any) -> None:
        sessions = self._by_connection.pop(connection, {})
        for live in sessions.values():
            live.stop()


class _LivePreview:
    def __init__(
        self,
        *,
        preview_id: str,
        session: PreviewSession,
        on_frame: Callable[[str, dict[str, Any]], Awaitable[None]],
        on_logs: Callable[[str, list[str]], Awaitable[None]],
        on_error: Callable[[str, str], Awaitable[None]],
        on_exit: Callable[[str], Awaitable[None]],
    ) -> None:
        self.preview_id = preview_id
        self.session = session
        self._on_frame = on_frame
        self._on_logs = on_logs
        self._on_error = on_error
        self._on_exit = on_exit
        self._task: asyncio.Task[None] | None = None
        self._last_seq = 0
        self._log_cursor = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._pump(), name=f"mobile-preview-{self.preview_id}")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            self.session.kill()
        except Exception:
            logger.exception("mobile preview kill failed")

    async def _pump(self) -> None:
        try:
            while self.session.is_alive():
                frame = await asyncio.to_thread(
                    self.session.poll_frame, 400, self._last_seq
                )
                seq = int(frame.get("seq") or 0)
                if seq > self._last_seq and frame.get("png"):
                    self._last_seq = seq
                    await self._on_frame(self.preview_id, frame_to_b64(frame))
                elif frame.get("error"):
                    detail = str(frame["error"])
                    if is_device_gone_error(detail):
                        await self._on_error(
                            self.preview_id,
                            friendly_device_gone_message(detail),
                        )
                        # End the pump so the UI leaves "running" (native + python).
                        try:
                            self.session.kill()
                        except Exception:
                            pass
                        break
                    await self._on_error(self.preview_id, detail)

                logs = await asyncio.to_thread(self.session.poll_logs, 120)
                if len(logs) > self._log_cursor:
                    new_lines = logs[self._log_cursor :]
                    self._log_cursor = len(logs)
                    if new_lines:
                        await self._on_logs(self.preview_id, new_lines)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("mobile preview pump failed")
            try:
                await self._on_error(self.preview_id, str(exc))
            except Exception:
                pass
        finally:
            try:
                await self._on_exit(self.preview_id)
            except Exception:
                pass
