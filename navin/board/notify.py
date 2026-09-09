# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Board change fan-out to connected WebUI clients.

Both the HTTP API (human edits) and the agent ``board`` tool call
:func:`publish_board_update` after a successful mutation. The websocket
channel broadcasts the resulting event to every open connection; clients
showing the same project refetch the board.
"""

from __future__ import annotations

from typing import Any

from navin.bus.outbound_events import BoardUpdatedEvent, outbound_message_for_event


def publish_board_update(bus: Any, project_path: str) -> None:
    if bus is None:
        return
    try:
        bus.outbound.put_nowait(
            outbound_message_for_event(
                channel="websocket",
                chat_id="*",
                event=BoardUpdatedEvent(project_path=project_path),
            )
        )
    except Exception:
        # Notification is best-effort: clients poll as a fallback.
        pass
