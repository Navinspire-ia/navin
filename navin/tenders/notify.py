"""Tender alerts use the common persistent delivery ledger."""

from __future__ import annotations

from typing import Any

from navin.bus.alerts import CHANNELS, channel_readiness
from navin.bus.alerts import deliver_alert as deliver_event
from navin.bus.notify import notify
from navin.tenders.store import TenderStore

__all__ = ["channel_readiness", "deliver_alert"]


def deliver_alert(store: TenderStore | None, *, title: str, detail: str, level: str = "info",
                  event_id: str = "", event_type: str = "alert", retry: bool = False) -> dict[str, Any]:
    try:
        result = deliver_event(store, module="tenders", title=title, detail=detail, level=level,
                               event_id=event_id, event_type=event_type, retry=retry, webui_notify=notify)
    except Exception:
        result = {**dict.fromkeys(CHANNELS, False), "webui": False, "confirmed": False, "complete": False,
                  "receipts": {}, "event_id": event_id, "error": "delivery_state_unavailable"}
    if store and (result.get("new_event") or result.get("queued_count") or result.get("error")):
        store.append_journal({"kind": "alert", "event_id": result.get("event_id"),
                              "text": f"{title} - {result.get('queued_count', 0)} alertes en attente de confirmation",
                              "delivery": result})
    return result
