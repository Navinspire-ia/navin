"""Process-wide registry of workbench debug sessions (one per project root)."""

from __future__ import annotations

import threading
from pathlib import Path

from navin.dap.session import DebugSession


class DebugManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, DebugSession] = {}
        self._fake_adapter: str | None = None

    def configure_fake_adapter(self, path: str | None) -> None:
        """Test hook: force every new session to use a fake DAP adapter script."""
        self._fake_adapter = path

    def session_for(self, root: Path | str) -> DebugSession:
        resolved = Path(root).expanduser().resolve(strict=False)
        key = str(resolved)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = DebugSession(resolved, fake_adapter=self._fake_adapter)
                self._sessions[key] = session
            return session

    def drop(self, root: Path | str) -> None:
        key = str(Path(root).expanduser().resolve(strict=False))
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass


_MANAGER = DebugManager()


def get_debug_manager() -> DebugManager:
    return _MANAGER
