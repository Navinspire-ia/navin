"""Background process manager for the web UI.

Exposes the agent's exec-session registry (dev servers, watchers, workers
started with background=true) so the editor can list them, read their output
tail, and stop them - like an activity monitor scoped to agent processes.
"""

from __future__ import annotations

from typing import Any

from navin.agent.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    ExecSessionManager,
)


async def list_processes(
    manager: ExecSessionManager | None = None,
    *,
    include_tail: bool = True,
) -> list[dict[str, Any]]:
    """Snapshot of all live background sessions, most recent first."""
    mgr = manager or DEFAULT_EXEC_SESSION_MANAGER
    infos = await mgr.list()
    items: list[dict[str, Any]] = []
    for info in infos:
        tail = ""
        if include_tail:
            try:
                tail = await mgr.peek(info.session_id)
            except KeyError:
                # Session ended between list() and peek(); skip it entirely.
                continue
        items.append(
            {
                "id": info.session_id,
                "command": info.command,
                "cwd": info.cwd,
                "elapsed_s": round(info.elapsed_s, 1),
                "idle_s": round(info.idle_s, 1),
                "returncode": info.returncode,
                "owner": info.owner_session_key,
                "tail": tail,
            }
        )
    items.sort(key=lambda item: item["elapsed_s"])
    return items


async def kill_process(
    session_id: str,
    manager: ExecSessionManager | None = None,
) -> bool:
    """Kill one background session. Returns False if it no longer exists."""
    mgr = manager or DEFAULT_EXEC_SESSION_MANAGER
    try:
        await mgr.kill_session(session_id)
    except KeyError:
        return False
    return True
