"""Gateway heartbeat policy for the Leads desk.

The LLM turn may still call watch (idempotent). Hunt, enrich, keys and CRM
never run here. The gateway ticks the book so a silent turn cannot skip a
new 80+ lead. The Studio Start loop hunts on its own clock.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.leads.store import LeadsStore

# If the desk loop just watched, heartbeat stays silent. Avoids double alerts.
LOOP_WATCH_GRACE_S = 90.0
# Cap so a hung digest never blocks the gateway LLM turn.
HEARTBEAT_WATCH_S = 20.0

HEARTBEAT_LEADS_ACTIONS = frozenset(
    {
        "status",
        "snapshot",
        "watch",
        "follow",
        "rescore",
        "score",
    }
)


def profile_is_armed(profile: dict[str, Any] | None) -> bool:
    """True when HEARTBEAT.md would run the Leads check."""
    if not isinstance(profile, dict):
        return False
    if not profile.get("wizard_ready"):
        return False
    return bool(str(profile.get("icp_name") or profile.get("sector") or "").strip())


def _skip(reason: str) -> dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "digest": "",
        "sent": {},
        "skipped": reason,
    }


def _last_watch_ts(loop: dict[str, Any], profile: dict[str, Any]) -> float:
    """Newest stamp wins. Loop cycle or a watch that already alerted."""
    return max(float(loop.get("last_watch") or 0), float(profile.get("last_watch") or 0))


def tick_watch(store: LeadsStore | None = None) -> dict[str, Any] | None:
    """Commit the local watch pass. None when the desk is not set up yet."""
    from navin.leads.watch import run_watch
    from navin.loop_runtime import call_with_deadline

    desk = store if store is not None else LeadsStore()
    from navin.leads.loop import hunt_is_live, recover_stale_hunt

    recover_stale_hunt(desk)
    loop = desk.load_loop()
    if hunt_is_live(desk, loop):
        return _skip("loop_hunting")
    profile = desk.load_profile()
    if not profile_is_armed(profile):
        return None
    last_watch = _last_watch_ts(loop, profile)
    if last_watch and (time.time() - last_watch) < LOOP_WATCH_GRACE_S:
        return _skip("loop_just_watched")
    try:
        return call_with_deadline(
            lambda: run_watch(desk),
            timeout_s=HEARTBEAT_WATCH_S,
            label="watch",
            thread_prefix="navin-leads",
        )
    except Exception:
        logger.exception("Leads heartbeat watch failed")
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
        "\n\nLeads watch already ran before this turn. "
        f"watch.count={count}. Report only this digest:\n"
        f"{digest}\n"
        "Never scrape LinkedIn. Never hunt, never send a sequence, never spend provider credits.\n"
    )
