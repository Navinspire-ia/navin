"""Gateway heartbeat policy for the Marketing desk.

The LLM turn may still call watch (idempotent). Understand, publish, launch and
loop ticks never run here. The gateway itself ticks the book so a silent or
CLI-only turn cannot skip a winning angle.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.marketing.store import MarketingStore

LOOP_WATCH_GRACE_S = 90.0
HEARTBEAT_WATCH_S = 20.0

HEARTBEAT_MARKETING_ACTIONS = frozenset(
    {
        "status",
        "snapshot",
        "watch",
    }
)


def brand_is_armed(brand: dict[str, Any] | None, product: dict[str, Any] | None = None) -> bool:
    """True when HEARTBEAT.md would run the Marketing check."""
    from navin.marketing.understand import _usable_product_name

    if isinstance(product, dict):
        if _usable_product_name(product.get("name")) or str(product.get("site") or "").strip():
            return True
    if not isinstance(brand, dict):
        return False
    return bool(
        _usable_product_name(brand.get("company"))
        or _usable_product_name(brand.get("product"))
        or str(brand.get("site") or "").strip()
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _skip(reason: str) -> dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "digest": "",
        "sent": {},
        "skipped": reason,
    }


def tick_watch(store: MarketingStore | None = None) -> dict[str, Any] | None:
    """Commit the local watch pass. None when the desk is not set up yet."""
    from navin.loop_runtime import call_with_deadline
    from navin.marketing.loop import cycle_is_live, recover_stale_cycle
    from navin.marketing.watch import run_watch

    try:
        desk = store if store is not None else MarketingStore()
        recover_stale_cycle(desk)
        if not brand_is_armed(desk.load_brand(), desk.load_product()):
            return None
        loop = desk.load_loop()
        if cycle_is_live(desk, loop):
            return _skip("busy")
        last_watch = _safe_float(loop.get("last_watch"))
        if last_watch and (time.time() - last_watch) < LOOP_WATCH_GRACE_S:
            return _skip("loop_just_watched")
        return call_with_deadline(
            lambda: run_watch(desk),
            timeout_s=HEARTBEAT_WATCH_S,
            label="watch",
            thread_prefix="navin-marketing",
        )
    except Exception:
        logger.exception("Marketing heartbeat watch failed")
        return _skip("error")


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
        "\n\nMarketing watch already ran before this turn. "
        f"watch.count={count}. Report only this digest:\n"
        f"{digest}\n"
        "Never publish. Never spend ad budget. Never start the growth loop from heartbeat.\n"
    )
