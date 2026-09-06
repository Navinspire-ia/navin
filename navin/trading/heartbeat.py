"""Gateway heartbeat policy for the Trading desk.

The LLM turn may still call watch (idempotent). Scan, tick, start and stop
never run here. The desk loop is the autonomous cycle.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.trading.store import TradingStore

LOOP_WATCH_GRACE_S = 90.0
# Cap so a hung digest never blocks the gateway LLM turn.
HEARTBEAT_WATCH_S = 20.0

HEARTBEAT_TRADING_ACTIONS = frozenset(
    {
        "status",
        "snapshot",
        "journal",
        "watch",
    }
)


def desk_is_armed(store: TradingStore) -> bool:
    """True when HEARTBEAT.md would run the Trading check."""
    loop = store.load_loop()
    if loop.get("enabled") or float(loop.get("last_tick") or 0) > 0:
        return True
    if str(loop.get("phase") or "") in {
        "scan",
        "screen",
        "analyze",
        "debate",
        "risk",
        "execute",
        "journal",
        "busy",
        "hunt",
    }:
        return True
    book = store.load_portfolio()
    if any(isinstance(row, dict) and row.get("symbol") for row in (book.get("positions") or [])):
        return True
    from navin.trading.watch import PENDING

    return any(
        str(row.get("status") or "").strip().lower() in PENDING
        for row in store.load_orders()
        if isinstance(row, dict)
    )


def _skip(reason: str) -> dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "digest": "",
        "sent": {},
        "skipped": reason,
    }


def tick_watch(store: TradingStore | None = None) -> dict[str, Any] | None:
    """Commit the local watch pass. None when the desk was never used."""
    from navin.loop_runtime import call_with_deadline
    from navin.trading.loop import cycle_is_live, recover_stale_cycle
    from navin.trading.watch import run_watch

    desk = store if store is not None else TradingStore()
    recover_stale_cycle(desk)
    if cycle_is_live(desk, desk.load_loop()):
        return _skip("loop_cycling")
    if not desk_is_armed(desk):
        return None
    loop = desk.load_loop()
    last_watch = float(loop.get("last_watch") or 0)
    if last_watch and (time.time() - last_watch) < LOOP_WATCH_GRACE_S:
        return _skip("loop_just_watched")
    try:
        return call_with_deadline(
            lambda: run_watch(desk),
            timeout_s=HEARTBEAT_WATCH_S,
            label="watch",
            thread_prefix="navin-trading",
        )
    except Exception:
        logger.exception("Trading heartbeat watch failed")
        return _skip("watch_error")


def heartbeat_prompt_note(payload: dict[str, Any] | None) -> str:
    """Text the gateway appends so the agent reports a tick that already ran."""
    if not isinstance(payload, dict):
        return ""
    try:
        count = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        return ""
    if count <= 0:
        return ""
    digest = str(payload.get("digest") or "").strip()
    return (
        "\n\nTrading watch already ran before this turn. "
        f"watch.count={count}. Report only this digest:\n"
        f"{digest}\n"
        "Never tick, start or stop the desk loop from heartbeat. Paper only.\n"
    )
