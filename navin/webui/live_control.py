# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Route live-view controls to the exact desktop or browser the user sees."""

from __future__ import annotations

from typing import Any


def _target(session_key: str, live_id: str | None) -> tuple[Any, str]:
    from navin.agent.tools import browser, computer

    candidates: list[tuple[Any, str]] = []
    desktop = computer._SESSIONS.get(session_key)
    if desktop is not None and not desktop.closed:
        candidates.append((computer, f"desktop-{desktop.id}"))
    page = browser._SESSIONS.get(session_key)
    if page is not None and not page.closed:
        candidates.append((browser, page.live_id))
    if live_id:
        for module, candidate in candidates:
            if live_id == candidate:
                return module, candidate
        raise ValueError("this live session is no longer open; refresh the live view")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("no live desktop or browser is open in this chat")
    raise ValueError("both a desktop and browser are open; select the live session to control")


async def dispatch_input(
    session_key: str, action: str, payload: dict[str, Any], *, live_id: str | None = None
) -> dict[str, Any]:
    module, selected = _target(session_key, live_id)
    return await module.dispatch_live_input(session_key, action, payload, live_id=selected)


async def close_session(session_key: str, *, live_id: str | None = None) -> bool:
    module, selected = _target(session_key, live_id)
    closer = getattr(module, "close_computer_session", None) or module.close_browser_session
    return await closer(session_key, live_id=selected)
