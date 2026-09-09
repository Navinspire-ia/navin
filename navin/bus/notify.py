# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Send one entry to the WebUI notification centre.

The counterpart of :mod:`navin.board.notify`, for anything that needs to tell
the user something outside a chat transcript: a scheduled job that failed, a
provider backing off, an agent asking for a decision.

Delivery is best-effort. A notification is a courtesy, never the mechanism by
which work gets done, so a full queue or a closed bus must not fail the caller.
"""

from __future__ import annotations

from typing import Any

from navin.bus.outbound_events import NotificationEvent, outbound_message_for_event

LEVELS = ("info", "success", "warning", "error")

# Set once when the gateway starts. Subsystems that raise notifications -
# the scheduler, the indexer, the language servers - sit well below the layer
# that owns the bus, and threading it through every signature to carry an
# optional courtesy message would be a poor trade. They call ``notify`` instead.
_default_bus: Any = None


def set_notification_bus(bus: Any) -> None:
    global _default_bus
    _default_bus = bus


def clear_notification_bus() -> None:
    """Drop the process-wide bus. For tests, and for a clean shutdown."""
    global _default_bus
    _default_bus = None


def notify(
    *,
    title: str,
    detail: str | None = None,
    level: str = "info",
    source: str = "session",
    key: str | None = None,
    chat_id: str = "*",
) -> bool:
    """Notify through the process-wide bus, if one has been installed.

    Returns False when there is no bus - a CLI run, a test - which is a normal
    outcome and not a condition to report.
    """
    return publish_notification(
        _default_bus,
        title=title,
        detail=detail,
        level=level,
        source=source,
        key=key,
        chat_id=chat_id,
    )


def publish_notification(
    bus: Any,
    *,
    title: str,
    detail: str | None = None,
    level: str = "info",
    source: str = "session",
    key: str | None = None,
    chat_id: str = "*",
) -> bool:
    """Queue a notification. Returns whether it was accepted for delivery.

    ``chat_id`` defaults to every connection: the user is usually looking at
    something other than the chat that raised the condition, which is exactly
    why they need telling.
    """
    if bus is None:
        return False
    clean_title = (title or "").strip()
    if not clean_title:
        return False
    try:
        bus.outbound.put_nowait(
            outbound_message_for_event(
                channel="websocket",
                chat_id=chat_id,
                event=NotificationEvent(
                    title=clean_title,
                    level=level if level in LEVELS else "info",
                    detail=(detail or "").strip() or None,
                    key=(key or "").strip() or None,
                    source=source,
                ),
            )
        )
    except Exception:
        return False
    return True
