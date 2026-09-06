"""Career desk loop: hunt authorized sources on a wall-clock schedule.

Heartbeat still only runs silent watch. This loop is the autonomous hunt:
collect (Remotive, ATS, official APIs, open web) then watch. Never apply.
Never scrape LinkedIn.

Start / stop / schedule always persist, even while a hunt holds the desk lock.
The hunt applies that intent when it finishes so Pause cannot be overwritten.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.career.errors import CareerError
from navin.career.heartbeat import profile_is_armed
from navin.career.lock import career_desk_lock
from navin.career.store import CareerStore
from navin.loop_runtime import as_float, call_with_deadline, retry_due_after
from navin.loop_schedule import (
    LoopScheduleError,
    describe_schedule,
    format_due,
    next_due_after,
    normalize_schedule,
    with_default_schedule,
)

CollectFn = Callable[..., dict[str, Any]]
WatchFn = Callable[..., dict[str, Any]]

# In-process hunts. A new process has an empty set, so recover_stale_hunt
# can clear a leftover phase=hunt after a crash.
_live_hunts: set[str] = set()

# Wall clock for one collect+watch. A hung source must not freeze the desk.
MAX_HUNT_S = 8 * 60.0
WATCH_S = 45.0


def _now() -> float:
    return time.time()


def _sched(state: dict[str, Any]) -> dict[str, Any] | None:
    raw = state.get("schedule")
    return raw if isinstance(raw, dict) else None


def _busy_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "did_work": False,
        "phase": "busy",
        "reason": "a hunt is already running",
        "loop": state,
    }


def _apply_intent(
    state: dict[str, Any],
    intent: dict[str, Any] | None,
    *,
    now: float,
    persist_due: bool = True,
) -> dict[str, Any]:
    row = dict(state)
    if not isinstance(intent, dict):
        return row
    if "schedule" in intent and isinstance(intent["schedule"], dict):
        try:
            row["schedule"] = normalize_schedule(intent["schedule"])
        except LoopScheduleError:
            pass
        else:
            if persist_due:
                row["next_due"] = next_due_after(row, now=now, fallback_s=86400)
    if "enabled" in intent:
        row["enabled"] = bool(intent["enabled"])
        if not row["enabled"]:
            row["phase"] = "paused"
        elif row.get("phase") == "paused":
            row["phase"] = "armed"
    return row


def _hunt_key(desk: CareerStore) -> str:
    return str(Path(desk.root).resolve())


def mark_loop_hunting(desk: CareerStore, live: bool) -> None:
    key = _hunt_key(desk)
    if live:
        _live_hunts.add(key)
    else:
        _live_hunts.discard(key)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def hunt_expired(state: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """True when phase=hunt is older than MAX_HUNT_S (crash, PID reuse, hang)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") != "hunt":
        return False
    started = as_float(row.get("hunt_started_at"))
    if started <= 0:
        return False
    clock = now if now is not None else _now()
    return (clock - started) > MAX_HUNT_S


def hunt_is_live(desk: CareerStore, state: dict[str, Any] | None = None) -> bool:
    """True only while a collect is actually running (this process or another)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") != "hunt":
        return False
    if hunt_expired(row):
        return False
    if _hunt_key(desk) in _live_hunts:
        return True
    try:
        pid = int(row.get("hunt_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid and pid != os.getpid():
        return _pid_alive(pid)
    return False


def recover_stale_hunt(desk: CareerStore, *, now: float | None = None) -> dict[str, Any]:
    """Clear a leftover hunt and persist a leftover Stop/Horaires when the lock is free."""
    clock = now if now is not None else _now()
    disk = with_default_schedule(desk.load_loop())
    intent = desk.load_loop_intent()
    stale = str(disk.get("phase") or "") == "hunt" and not hunt_is_live(desk, disk)
    if hunt_is_live(desk, disk) or (not stale and not intent):
        return _apply_intent(disk, intent, now=clock)
    with career_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _apply_intent(disk, intent, now=clock)
        current = with_default_schedule(desk.load_loop())
        if hunt_is_live(desk, current):
            return _apply_intent(current, desk.load_loop_intent(), now=clock)
        repaired = False
        if str(current.get("phase") or "") == "hunt":
            current.pop("hunt_pid", None)
            current.pop("hunt_started_at", None)
            current["skipped_reason"] = "stale_hunt"
            current["last_result"] = "hunt recovered - previous collect did not finish"
            repaired = True
        current = _apply_intent(current, desk.load_loop_intent(), now=clock)
        if repaired:
            current["phase"] = "paused" if not current.get("enabled") else "idle"
        desk.save_loop(current)
        desk.clear_loop_intent()
        if repaired:
            desk.append_journal({"kind": "loop", "text": "stale hunt recovered"})
        return current


def peek_loop(store: CareerStore, *, now: float | None = None) -> dict[str, Any]:
    """Loop state the desk must show, including Pause/Horaires written mid-hunt."""
    clock = now if now is not None else _now()
    recover_stale_hunt(store, now=clock)
    state = with_default_schedule(store.load_loop())
    return _apply_intent(state, store.load_loop_intent(), now=clock)


def _merge_intent(desk: CareerStore, **fields: Any) -> dict[str, Any]:
    intent = dict(desk.load_loop_intent())
    for key, value in fields.items():
        if value is not None:
            intent[key] = value
    desk.save_loop_intent(intent)
    return intent


def _commit_control(
    desk: CareerStore,
    *,
    now: float,
    journal: str,
    last_result: str | None = None,
    phase: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write operator intent, then apply it now if the hunt lock is free."""
    intent = _merge_intent(desk, **fields)
    with career_desk_lock(desk, wait_s=0) as got:
        state = _apply_intent(with_default_schedule(desk.load_loop()), intent, now=now)
        if last_result:
            state["last_result"] = last_result
        if phase:
            if state.get("enabled") or phase == "paused":
                state["phase"] = phase
        if got:
            desk.save_loop(state)
            desk.clear_loop_intent()
    desk.append_journal({"kind": "loop", "text": journal})
    return peek_loop(desk, now=now)


def _finish_hunt_state(
    desk: CareerStore,
    state: dict[str, Any],
    *,
    clock: float,
) -> dict[str, Any]:
    """Hunt fields first, then operator intent so Stop always wins."""
    merged = _apply_intent(state, desk.load_loop_intent(), now=clock)
    if not merged.get("enabled"):
        merged["phase"] = "paused"
    merged.pop("hunt_pid", None)
    merged.pop("hunt_started_at", None)
    desk.save_loop(merged)
    desk.clear_loop_intent()
    return merged


def maybe_tick(
    store: CareerStore | None = None,
    *,
    now: float | None = None,
    force: bool = False,
    collect_fn: CollectFn | None = None,
    watch_fn: WatchFn | None = None,
) -> dict[str, Any]:
    from navin.career.collect import collect
    from navin.career.retention import apply_retention
    from navin.career.watch import run_watch

    desk = store or CareerStore()
    clock = now if now is not None else _now()
    state = peek_loop(desk, now=clock)
    profile = desk.load_profile()
    if not profile_is_armed(profile) and not force:
        return {
            "did_work": False,
            "phase": "unarmed",
            "reason": "career profile is not set up yet",
            "loop": state,
        }
    if not state.get("enabled") and not force:
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }
    if not force and float(state.get("next_due") or 0) > clock + 0.01:
        return {
            "did_work": False,
            "phase": "sleep",
            "reason": "not due yet",
            "loop": state,
        }

    with career_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _busy_payload(peek_loop(desk, now=clock))
        return _run_hunt(
            desk,
            profile,
            clock=clock,
            force=force,
            collect_fn=collect_fn or collect,
            watch_fn=watch_fn,
            run_watch=run_watch,
            apply_retention=apply_retention,
        )


def _run_hunt(
    desk: CareerStore,
    profile: dict[str, Any],
    *,
    clock: float,
    force: bool,
    collect_fn: CollectFn,
    watch_fn: WatchFn | None,
    run_watch: Callable[..., dict[str, Any]],
    apply_retention: Callable[..., Any],
) -> dict[str, Any]:
    state = peek_loop(desk, now=clock)
    was_enabled = bool(state.get("enabled"))
    if not was_enabled and not force:
        state["phase"] = "paused"
        desk.save_loop(state)
        desk.clear_loop_intent()
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }
    apply_retention(desk)
    state["phase"] = "hunt"
    state["hunt_pid"] = os.getpid()
    state["hunt_started_at"] = time.time()
    desk.save_loop(state)
    mark_loop_hunting(desk, True)
    try:
        try:
            hunt = call_with_deadline(
                lambda: collect_fn(desk, query="", track=str(profile.get("track") or "")),
                timeout_s=MAX_HUNT_S,
                label="collect",
                thread_prefix="navin-career",
            )
        except Exception as exc:
            logger.exception("Career loop hunt failed")
            try:
                streak = int(state.get("error_streak") or 0) + 1
            except (TypeError, ValueError):
                streak = 1
            due = retry_due_after({**state, "error_streak": streak}, clock=clock, fallback_s=86400)
            summary = f"cycle failed - {exc}"
            state.update(
                {
                    "enabled": was_enabled,
                    "phase": "idle",
                    "last_tick": clock,
                    "next_due": due,
                    "last_result": summary,
                    "skipped_reason": "error",
                    "error_streak": streak,
                }
            )
            state = _finish_hunt_state(desk, state, clock=clock)
            desk.append_journal({"kind": "loop", "text": summary})
            return {
                "did_work": False,
                "phase": state.get("phase") or "idle",
                "reason": summary,
                "loop": state,
            }

        remaining = max(8.0, MAX_HUNT_S - max(0.0, time.time() - as_float(state.get("hunt_started_at"))))
        watch_budget = min(WATCH_S, remaining)
        watch_error = ""
        try:
            if watch_fn is None:
                watch = call_with_deadline(
                    lambda: run_watch(desk, already_locked=True),
                    timeout_s=watch_budget,
                    label="watch",
                    thread_prefix="navin-career",
                )
            else:
                watch = call_with_deadline(
                    lambda: watch_fn(desk),
                    timeout_s=watch_budget,
                    label="watch",
                    thread_prefix="navin-career",
                )
        except Exception as exc:
            logger.exception("Career loop watch failed")
            watch_error = str(exc)
            watch = {"count": 0, "delivered": False, "sent": {}, "error": watch_error}

        added = int((hunt or {}).get("added") or 0)
        scanned = int((hunt or {}).get("scanned") or (hunt or {}).get("count") or 0)
        alerts = int((watch or {}).get("count") or 0)
        watch_ok = (
            not watch_error
            and (alerts == 0 or bool((watch or {}).get("delivered")))
        )
        due = next_due_after(state, now=clock, fallback_s=86400)
        cycle = int(state.get("cycle") or 0) + 1
        stamp = format_due(due, _sched(state))
        summary = f"cycle {cycle}: {added} new offers, {alerts} alerts, next {stamp}"
        state.update(
            {
                "enabled": was_enabled,
                "phase": "idle",
                "cycle": cycle,
                "last_tick": clock,
                "last_watch": clock if watch_ok else state.get("last_watch") or 0,
                "next_due": due,
                "last_result": summary,
                "skipped_reason": "",
                "error_streak": 0,
                "added": added,
                "scanned": scanned,
                "alerts": alerts,
            }
        )
        state = _finish_hunt_state(desk, state, clock=clock)
        due = next_due_after(state, now=clock, fallback_s=86400)
        stamp = format_due(due, _sched(state))
        summary = f"cycle {cycle}: {added} new offers, {alerts} alerts, next {stamp}"
        if state.get("last_result") != summary and state.get("skipped_reason") != "error":
            state["next_due"] = due
            state["last_result"] = summary
            desk.save_loop(state)
        desk.append_journal({"kind": "loop", "text": summary, "added": added, "alerts": alerts})
        return {
            "did_work": True,
            "phase": state.get("phase") or "idle",
            "reason": summary,
            "loop": state,
            "hunt": hunt,
            "watch": watch,
        }
    finally:
        mark_loop_hunting(desk, False)


def start_loop(
    store: CareerStore | None = None,
    *,
    schedule: dict[str, Any] | None = None,
    run_now: bool = False,
    now: float | None = None,
    tz: str | None = None,
    collect_fn: CollectFn | None = None,
    watch_fn: WatchFn | None = None,
) -> dict[str, Any]:
    desk = store or CareerStore()
    if not profile_is_armed(desk.load_profile()):
        raise CareerError(
            "Set titles or finish the Career start screen before starting the loop",
            status=400,
        )
    clock = now if now is not None else _now()
    fields: dict[str, Any] = {"enabled": True}
    if schedule is not None:
        fields["schedule"] = normalize_schedule(schedule, tz=tz)
    elif tz:
        current = peek_loop(desk, now=clock)
        if isinstance(current.get("schedule"), dict):
            fields["schedule"] = normalize_schedule(current["schedule"], tz=tz)
    preview = _apply_intent(with_default_schedule(desk.load_loop()), {**desk.load_loop_intent(), **fields}, now=clock)
    label = describe_schedule(_sched(preview))
    stamp = format_due(
        next_due_after(preview, now=clock, fallback_s=86400),
        _sched(preview),
    )
    journal = f"loop started - {label}, next {stamp}"

    if not run_now:
        state = _commit_control(
            desk,
            now=clock,
            journal=journal,
            last_result=f"loop armed - {label}, next {stamp}",
            phase="armed",
            **fields,
        )
        return {
            "did_work": False,
            "phase": "armed",
            "reason": state.get("last_result") or journal,
            "loop": state,
        }

    _merge_intent(desk, **fields)
    with career_desk_lock(desk, wait_s=0) as got:
        if not got:
            desk.append_journal({"kind": "loop", "text": journal})
            return {
                "did_work": False,
                "phase": "busy",
                "reason": "a hunt is already running",
                "loop": peek_loop(desk, now=clock),
            }
        state = _apply_intent(with_default_schedule(desk.load_loop()), desk.load_loop_intent(), now=clock)
        state["enabled"] = True
        state["next_due"] = 0
        state["phase"] = "armed"
        state["last_result"] = f"loop armed - {label}, next {stamp}"
        desk.save_loop(state)
        desk.append_journal({"kind": "loop", "text": journal})
        from navin.career.collect import collect
        from navin.career.retention import apply_retention
        from navin.career.watch import run_watch

        return _run_hunt(
            desk,
            desk.load_profile(),
            clock=clock,
            force=True,
            collect_fn=collect_fn or collect,
            watch_fn=watch_fn,
            run_watch=run_watch,
            apply_retention=apply_retention,
        )


def update_loop_schedule(
    store: CareerStore | None = None,
    *,
    schedule: dict[str, Any],
    now: float | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    desk = store or CareerStore()
    clock = now if now is not None else _now()
    normalized = normalize_schedule(schedule, tz=tz)
    preview = _apply_intent(
        with_default_schedule(desk.load_loop()),
        {**desk.load_loop_intent(), "schedule": normalized},
        now=clock,
    )
    label = describe_schedule(normalized)
    stamp = format_due(next_due_after(preview, now=clock, fallback_s=86400), normalized)
    journal = (
        f"schedule updated - {label}, next {stamp}"
        if preview.get("enabled")
        else f"schedule saved - {label}"
    )
    return _commit_control(
        desk,
        now=clock,
        journal=journal,
        last_result=journal,
        phase="armed" if preview.get("enabled") else None,
        schedule=normalized,
    )


def stop_loop(store: CareerStore | None = None, *, now: float | None = None) -> dict[str, Any]:
    desk = store or CareerStore()
    clock = now if now is not None else _now()
    label = describe_schedule(_sched(peek_loop(desk, now=clock)))
    journal = f"loop paused - {label}"
    return _commit_control(
        desk,
        now=clock,
        journal=journal,
        last_result=journal,
        phase="paused",
        enabled=False,
    )
