"""Gateway heartbeat policy for the Tenders desk.

The LLM turn may still call follow (idempotent). Collect, write and send never
run here. The gateway itself pushes the company digest so a silent or CLI-only
turn cannot skip deadline alerts.

The Studio Start loop hunts official notices on its own calendar. Heartbeat
only watches GO / deadlines between those hunts, and stays out of the way
while a collect holds the desk.
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from navin.tenders.store import TenderStore

# If the desk loop just watched, heartbeat stays silent. Avoids double alerts.
LOOP_WATCH_GRACE_S = 90.0
# Cap so a hung digest never blocks the gateway LLM turn.
HEARTBEAT_WATCH_S = 20.0

# Reads + company digest. Canonical names and the aliases the desk/agent share.
HEARTBEAT_TENDERS_ACTIONS = frozenset(
    {
        "status",
        "snapshot",
        "get",
        "search",
        "list",
        "index",
        "file",
        "read-file",
        "read_file",
        "follow",
        "watch",
        "follow-up",
    }
)


def profile_is_armed(profile: dict[str, Any] | None) -> bool:
    """True when HEARTBEAT.md would run the Tenders check (company name on file)."""
    if not isinstance(profile, dict):
        return False
    return bool(str(profile.get("name") or "").strip())


def _skip(reason: str) -> dict[str, Any]:
    return {
        "events": [],
        "count": 0,
        "digest": "",
        "sent": {},
        "skipped": reason,
    }


def tick_watch(store: TenderStore | None = None) -> dict[str, Any] | None:
    """Push the company digest. None when the desk is not set up yet."""
    from navin.tenders.lock import tenders_desk_lock
    from navin.tenders.retention import apply_retention
    from navin.tenders.watch import run_watch

    desk = store if store is not None else TenderStore()
    if not profile_is_armed(desk.load_profile()):
        return None
    if hasattr(desk, "load_loop"):
        from navin.tenders.loop import hunt_is_live, recover_stale_hunt

        recover_stale_hunt(desk)
        loop = desk.load_loop()
    else:
        loop = {}
    if not isinstance(loop, dict):
        loop = {}
    if hunt_is_live(desk, loop) if hasattr(desk, "load_loop") else str(loop.get("phase") or "") == "hunt":
        return _skip("loop_hunting")
    last_watch = float(loop.get("last_watch") or 0)
    if last_watch and (time.time() - last_watch) < LOOP_WATCH_GRACE_S:
        return _skip("loop_just_watched")
    if hasattr(desk, "load_tenders"):
        apply_retention(desk)
    from navin.tenders.loop import call_with_deadline

    if hasattr(desk, "root"):
        with tenders_desk_lock(desk, wait_s=0) as got:
            if not got:
                return _skip("loop_busy")
            try:
                result = call_with_deadline(
                    lambda: run_watch(desk, send=True),
                    timeout_s=HEARTBEAT_WATCH_S,
                    label="heartbeat watch",
                )
            except Exception:
                logger.exception("Tenders heartbeat watch failed")
                return _skip("watch_error")
            _stamp_watch(desk, loop)
            return result
    return run_watch(desk, send=True)


def _stamp_watch(desk: TenderStore, loop: dict[str, Any]) -> None:
    """Record that heartbeat already watched, without clobbering a live hunt."""
    if not hasattr(desk, "save_loop") or not hasattr(desk, "load_loop"):
        return
    current = desk.load_loop()
    if not isinstance(current, dict):
        current = dict(loop)
    if hasattr(desk, "root"):
        from navin.tenders.loop import hunt_is_live

        if hunt_is_live(desk, current):
            return
    elif str(current.get("phase") or "") == "hunt":
        return
    current["last_watch"] = time.time()
    desk.save_loop(current)


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
        "\n\nTenders watch already ran before this turn. "
        f"watch.count={count}. Report only this digest:\n"
        f"{digest}\n"
        "Never collect. Never write. Never send a buyer mail. "
        "The desk loop hunts on its own schedule. That is not this heartbeat.\n"
    )
