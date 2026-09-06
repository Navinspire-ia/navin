"""Silent Trading watch for heartbeat. No scan, no quote fetch, no tick."""

from __future__ import annotations

from typing import Any

from loguru import logger

from navin.trading.lock import trading_desk_lock
from navin.trading.store import TradingStore

PENDING = frozenset({"pending", "approval", "awaiting", "awaiting_approval", "proposed"})


def pending_alerts(store: TradingStore) -> list[dict[str, Any]]:
    """Paper orders waiting for a human that were not reported yet."""
    events: list[dict[str, Any]] = []
    for row in store.load_orders():
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status not in PENDING:
            continue
        oid = str(row.get("id") or "").strip()
        symbol = str(row.get("symbol") or "").strip().upper()
        if not oid or not symbol:
            continue
        sent = {str(item) for item in (row.get("alerts_sent") or [])}
        if "pending" in sent:
            continue
        events.append(
            {
                "id": oid,
                "key": "pending",
                "kind": "approval",
                "symbol": symbol,
                "side": str(row.get("side") or "buy"),
                "qty": row.get("qty"),
                "price": row.get("price"),
                "status": status,
            }
        )
    return events


def digest_text(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in events[:12]:
        lines.append(
            f"Approval: {row.get('side')} {row.get('qty')} {row.get('symbol')} "
            f"@ {row.get('price')}"
        )
    extra = len(events) - 12
    if extra > 0:
        lines.append(f"+{extra} more pending paper orders")
    return "\n".join(lines)


def digest_was_delivered(sent: dict[str, Any] | None) -> bool:
    """True when at least one channel accepted the digest."""
    if not isinstance(sent, dict):
        return False
    return any(
        bool(sent.get(name))
        for name in ("webui", "telegram", "whatsapp", "email", "teams", "slack")
    )


def mark_sent(store: TradingStore, events: list[dict[str, Any]]) -> None:
    orders = store.load_orders()
    by_id = {str(row.get("id") or ""): row for row in orders if isinstance(row, dict)}
    changed = False
    for event in events:
        row = by_id.get(str(event.get("id") or ""))
        if row is None:
            continue
        sent = {str(item) for item in (row.get("alerts_sent") or [])}
        sent.add(str(event.get("key") or "pending"))
        row["alerts_sent"] = sorted(sent)
        changed = True
    if changed:
        store.save_orders(orders)


def run_watch(store: TradingStore, *, already_locked: bool = False) -> dict[str, Any]:
    """One pass over the local book. Silent when nothing new is actionable."""
    if not already_locked:
        with trading_desk_lock(store, wait_s=0) as got:
            if not got:
                return {
                    "events": [],
                    "count": 0,
                    "digest": "",
                    "sent": {},
                    "skipped": "busy",
                }
            return _watch_pass(store)
    return _watch_pass(store)


def _watch_pass(store: TradingStore) -> dict[str, Any]:
    events = pending_alerts(store)
    payload: dict[str, Any] = {"events": events, "count": len(events), "digest": "", "sent": {}}
    if not events:
        return payload
    payload["digest"] = digest_text(events)
    from navin.trading.notify import deliver_alert

    title = f"{len(events)} trading alert{'s' if len(events) != 1 else ''}"
    try:
        payload["sent"] = deliver_alert(store, title=title, detail=payload["digest"])
    except Exception:
        logger.exception("trading watch notify failed")
        payload["sent"] = {}
    if not digest_was_delivered(payload["sent"]):
        payload["delivered"] = False
        return payload
    payload["delivered"] = True
    mark_sent(store, events)
    store.append_journal({"kind": "watch", "text": f"{len(events)} trading alerts"})
    return payload
