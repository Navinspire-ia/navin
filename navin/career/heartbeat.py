"""Gateway heartbeat policy for the Career desk.

The LLM turn may still call watch (idempotent). Search, collect and apply never
run here. The gateway itself ticks the book so a silent or CLI-only turn cannot
skip Perfect/Good matches or due follow-ups.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.career.store import CareerStore

# If the desk loop just watched, heartbeat stays silent. Avoids double alerts.
LOOP_WATCH_GRACE_S = 90.0
# Cap so a hung digest never blocks the gateway LLM turn.
HEARTBEAT_WATCH_S = 20.0

# Reads + silent watch. Same set the agent tool and the desk API enforce.
HEARTBEAT_CAREER_ACTIONS = frozenset(
    {
        "status",
        "dossier",
        "snapshot",
        "read",
        "book",
        "watch",
    }
)


def profile_is_armed(profile: dict[str, Any] | None) -> bool:
    """True when HEARTBEAT.md would run the Career check (titles or wizard)."""
    if not isinstance(profile, dict):
        return False
    titles = profile.get("titles") or []
    if any(str(item).strip() for item in titles):
        return True
    return bool(profile.get("wizard_complete"))


def _skip(reason: str) -> dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "digest": "",
        "sent": {},
        "skipped": reason,
    }


def tick_watch(store: CareerStore | None = None) -> dict[str, Any] | None:
    """Commit the local watch pass. None when the desk is not set up yet."""
    from navin.career.watch import run_watch
    from navin.loop_runtime import call_with_deadline

    desk = store if store is not None else CareerStore()
    from navin.career.loop import hunt_is_live, recover_stale_hunt
    from navin.career.retention import apply_retention

    recover_stale_hunt(desk)
    loop = desk.load_loop()
    if hunt_is_live(desk, loop):
        return _skip("loop_hunting")
    apply_retention(desk)
    if not profile_is_armed(desk.load_profile()):
        return None
    last_watch = float(loop.get("last_watch") or 0)
    if last_watch and (time.time() - last_watch) < LOOP_WATCH_GRACE_S:
        return _skip("loop_just_watched")
    try:
        return call_with_deadline(
            lambda: run_watch(desk),
            timeout_s=HEARTBEAT_WATCH_S,
            label="watch",
            thread_prefix="navin-career",
        )
    except Exception:
        logger.exception("Career heartbeat watch failed")
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
        "\n\nCareer watch already ran before this turn. "
        f"watch.count={count}. Report only this digest:\n"
        f"{digest}\n"
        "Never scrape LinkedIn. Never apply. Never search.\n"
    )
