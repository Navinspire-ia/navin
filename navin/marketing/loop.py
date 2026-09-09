# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing growth loop: publish, measure, learn, create. Never a busy-wait of fake tasks.

Heartbeat still only runs silent watch. This loop is the autonomous cycle.
Start / stop / schedule always persist, even while a cycle holds the desk lock.
The cycle applies that intent when it finishes so Pause cannot be overwritten.
A crash mid-cycle is healed on the next load (stuck measure/learn/busy).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.loop_runtime import as_float, call_with_deadline, retry_due_after
from navin.loop_schedule import (
    LoopScheduleError,
    describe_schedule,
    format_due,
    next_due_after,
    normalize_schedule,
    with_default_schedule,
)
from navin.marketing.errors import MarketingError
from navin.marketing.growth import run_growth_cycle
from navin.marketing.heartbeat import brand_is_armed
from navin.marketing.lock import marketing_desk_lock
from navin.marketing.measure import collect_metrics
from navin.marketing.publish import poll_pending_publications, publish_due
from navin.marketing.store import MarketingStore
from navin.marketing.watch import run_watch

WatchFn = Callable[..., dict[str, Any]]
ImproveFn = Callable[..., dict[str, Any]]

LIVE_PHASES = frozenset({"publish", "measure", "learn", "create", "busy"})
_live_cycles: set[str] = set()
MAX_CYCLE_S = 8 * 60.0


def _now() -> float:
    return time.time()


def _sched(state: dict[str, Any]) -> dict[str, Any] | None:
    raw = state.get("schedule")
    return raw if isinstance(raw, dict) else None


def _busy_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "did_work": False,
        "phase": "busy",
        "reason": "a cycle is already running",
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


def _cycle_key(desk: MarketingStore) -> str:
    return str(Path(desk.root).resolve())


def mark_loop_cycling(desk: MarketingStore, live: bool) -> None:
    key = _cycle_key(desk)
    if live:
        _live_cycles.add(key)
    else:
        _live_cycles.discard(key)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def cycle_expired(state: dict[str, Any] | None, *, now: float | None = None) -> bool:
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") not in LIVE_PHASES:
        return False
    started = as_float(row.get("cycle_started_at"))
    if started <= 0:
        return False
    clock = now if now is not None else _now()
    return (clock - started) > MAX_CYCLE_S


def cycle_is_live(desk: MarketingStore, state: dict[str, Any] | None = None) -> bool:
    """True only while a cycle is actually running (this process or another)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") not in LIVE_PHASES:
        return False
    if cycle_expired(row):
        return False
    if _cycle_key(desk) in _live_cycles:
        return True
    try:
        pid = int(row.get("cycle_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid and pid != os.getpid():
        return _pid_alive(pid)
    return False


def recover_stale_cycle(desk: MarketingStore, *, now: float | None = None) -> dict[str, Any]:
    """Clear a leftover cycle and persist a leftover Stop/Horaires when the lock is free."""
    clock = now if now is not None else _now()
    disk = with_default_schedule(desk.load_loop())
    intent = desk.load_loop_intent()
    stale = str(disk.get("phase") or "") in LIVE_PHASES and not cycle_is_live(desk, disk)
    if cycle_is_live(desk, disk) or (not stale and not intent):
        return _apply_intent(disk, intent, now=clock)
    with marketing_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _apply_intent(disk, intent, now=clock)
        current = with_default_schedule(desk.load_loop())
        if cycle_is_live(desk, current):
            return _apply_intent(current, desk.load_loop_intent(), now=clock)
        repaired = False
        if str(current.get("phase") or "") in LIVE_PHASES:
            current.pop("cycle_pid", None)
            current.pop("cycle_started_at", None)
            current["skipped_reason"] = "stale_cycle"
            current["last_result"] = "cycle recovered - previous measure did not finish"
            repaired = True
        current = _apply_intent(current, desk.load_loop_intent(), now=clock)
        if repaired:
            current["phase"] = "paused" if not current.get("enabled") else "idle"
        desk.save_loop(current)
        desk.clear_loop_intent()
        if repaired:
            desk.append_journal({"kind": "loop", "text": "stale cycle recovered"})
        return current


def peek_loop(store: MarketingStore, *, now: float | None = None) -> dict[str, Any]:
    """Loop state the desk must show, including Pause/Horaires written mid-cycle."""
    clock = now if now is not None else _now()
    recover_stale_cycle(store, now=clock)
    state = with_default_schedule(store.load_loop())
    return _apply_intent(state, store.load_loop_intent(), now=clock)


def _merge_intent(desk: MarketingStore, **fields: Any) -> dict[str, Any]:
    intent = dict(desk.load_loop_intent())
    for key, value in fields.items():
        if value is not None:
            intent[key] = value
    desk.save_loop_intent(intent)
    return intent


def _commit_control(
    desk: MarketingStore,
    *,
    now: float,
    journal: str,
    last_result: str | None = None,
    phase: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write operator intent, then apply it now if the cycle lock is free."""
    intent = _merge_intent(desk, **fields)
    with marketing_desk_lock(desk, wait_s=0) as got:
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


def _finish_cycle_state(
    desk: MarketingStore,
    state: dict[str, Any],
    *,
    clock: float,
) -> dict[str, Any]:
    """Cycle fields first, then operator intent so Stop always wins."""
    merged = _apply_intent(state, desk.load_loop_intent(), now=clock)
    if not merged.get("enabled"):
        merged["phase"] = "paused"
    desk.save_loop(merged)
    desk.clear_loop_intent()
    return merged


def maybe_tick(
    store: MarketingStore | None = None,
    *,
    now: float | None = None,
    force: bool = False,
    watch_fn: WatchFn | None = None,
    improve_fn: ImproveFn | None = None,
) -> dict[str, Any]:
    desk = store or MarketingStore()
    clock = now if now is not None else _now()
    state = peek_loop(desk, now=clock)
    if not brand_is_armed(desk.load_brand(), desk.load_product()) and not force:
        return {
            "did_work": False,
            "phase": "unarmed",
            "reason": "marketing brand or product is not set up yet",
            "loop": state,
        }
    if not state.get("enabled") and not force:
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }
    try:
        due_at = float(state.get("next_due") or 0)
    except (TypeError, ValueError):
        due_at = 0.0
    if not force and due_at > clock + 0.01:
        continued = poll_pending_publications(desk, now=clock)
        if continued.get("checked"):
            return {
                "did_work": True, "phase": "publishing", "reason": "checking accepted publications",
                "publish": continued, "loop": state,
            }
        return {
            "did_work": False,
            "phase": "sleep",
            "reason": "not due yet",
            "loop": state,
        }
    with marketing_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _busy_payload(peek_loop(desk, now=clock))
        return _run_cycle(
            desk,
            clock=clock,
            force=force,
            watch_fn=watch_fn,
            improve_fn=improve_fn,
        )


def _run_cycle(
    desk: MarketingStore,
    *,
    clock: float,
    force: bool,
    watch_fn: WatchFn | None,
    improve_fn: ImproveFn | None,
) -> dict[str, Any]:
    state = peek_loop(desk, now=clock)
    was_enabled = bool(state.get("enabled"))
    if not was_enabled and not force:
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }
    mark_loop_cycling(desk, True)
    try:
        try:
            return call_with_deadline(
                lambda: _cycle_body(
                    desk,
                    state,
                    was_enabled=was_enabled,
                    clock=clock,
                    watch_fn=watch_fn,
                    improve_fn=improve_fn,
                ),
                timeout_s=MAX_CYCLE_S,
                label="cycle",
                thread_prefix="navin-marketing",
            )
        except Exception as exc:
            return _fail_cycle(desk, state, clock=clock, was_enabled=was_enabled, exc=exc)
    finally:
        mark_loop_cycling(desk, False)


def _cycle_body(
    desk: MarketingStore,
    state: dict[str, Any],
    *,
    was_enabled: bool,
    clock: float,
    watch_fn: WatchFn | None,
    improve_fn: ImproveFn | None,
) -> dict[str, Any]:
    state["cycle_pid"] = os.getpid()
    state["cycle_started_at"] = clock
    # publish: scheduled posts that are due, approved posts, then auto-publish when allowed.
    state["phase"] = "publish"
    desk.save_loop(state)
    shipped = _safe_step("publish", lambda: publish_due(desk, now=clock), {"sent": [], "failed": [], "skipped": []})
    # measure: channel counters and UTM traffic first, then the alert watch on the fresh numbers.
    state["phase"] = "measure"
    desk.save_loop(state)
    measured = _safe_step("measure", lambda: collect_metrics(desk), {"measured": 0})
    if watch_fn is None:
        watch = run_watch(desk, already_locked=True)
    else:
        watch = watch_fn(desk)
    state["phase"] = "learn"
    desk.save_loop(state)
    growth = (improve_fn or run_growth_cycle)(desk)
    # create: keep the queue fed for the campaign that is running.
    state["phase"] = "create"
    desk.save_loop(state)
    created = _safe_step("create", lambda: refill_content(desk), {"content": []})

    winners = list((growth or {}).get("winners") or (watch or {}).get("winners") or [])
    variants = list((growth or {}).get("variants") or [])
    try:
        alerts = int((watch or {}).get("count") or 0)
    except (TypeError, ValueError):
        alerts = 0
    due = next_due_after(state, now=clock, fallback_s=86400)
    try:
        cycle = int(state.get("cycle") or 0) + 1
    except (TypeError, ValueError):
        cycle = 1
    stamp = format_due(due, _sched(state))
    sent = len(shipped.get("sent") or [])
    failed = len(shipped.get("failed") or [])
    fresh = len(variants) + len(created.get("content") or [])
    summary = (
        f"cycle {cycle}: {sent} published"
        + (f" ({failed} failed)" if failed else "")
        + f", {int(measured.get('measured') or 0)} measured, {len(winners)} winners, "
        f"{fresh} new posts, {alerts} alerts, next {stamp}"
    )
    state.update(
        {
            "enabled": was_enabled,
            "phase": "idle",
            "cycle": cycle,
            "last_tick": clock,
            "last_watch": clock,
            "next_due": due,
            "last_result": summary,
            "skipped_reason": "",
            "error_streak": 0,
            "winners": [row.get("id") for row in winners if isinstance(row, dict)],
        }
    )
    state.pop("cycle_pid", None)
    state.pop("cycle_started_at", None)
    state = _finish_cycle_state(desk, state, clock=clock)
    desk.append_journal({"kind": "loop", "text": summary})
    return {
        "did_work": True,
        "phase": state.get("phase") or "idle",
        "reason": summary,
        "loop": state,
        "publish": shipped,
        "measure": measured,
        "watch": watch,
        "growth": growth,
        "create": created,
    }


def _safe_step(name: str, run: Callable[[], dict[str, Any]], fallback: dict[str, Any]) -> dict[str, Any]:
    """A connector or analytics outage is logged, never fatal for the cycle."""
    try:
        result = run()
    except Exception as exc:  # noqa: BLE001 - the cycle must reach learn/create
        logger.warning("marketing {} step failed: {}", name, exc)
        return {**fallback, "error": str(exc)[:240]}
    return result if isinstance(result, dict) else fallback


def refill_content(desk: MarketingStore) -> dict[str, Any]:
    """Write the next batch when a running campaign has nothing left to send.

    Only the autonomous mode refills on its own; in approval mode the operator
    presses Content when the queue is empty, so nothing piles up unread.
    """
    settings = desk.load_settings()
    if settings.get("execution_mode") != "autonomous":
        return {"content": [], "reason": "approval mode"}
    from navin.marketing import ai

    if not ai.enabled(settings):
        # Templates would only repeat the last batch word for word.
        return {"content": [], "reason": "no model routed: press Content for a new batch"}
    campaign = next(
        (row for row in desk.load_campaigns() if row.get("status") in {"running", "approved"}),
        None,
    )
    if campaign is None:
        return {"content": [], "reason": "no active campaign"}
    rows = [row for row in desk.load_content() if str(row.get("campaign_id") or "") == campaign.get("id")]
    pending = [row for row in rows if row.get("status") in {"ready", "approved", "scheduled"}]
    if pending:
        return {"content": [], "reason": f"{len(pending)} posts waiting"}
    from navin.marketing.content import REFILL_ANGLES, generate_content

    batch = len([row for row in rows if not row.get("parent_id")]) // max(1, len(campaign.get("channels") or [1]))
    angle = REFILL_ANGLES[batch % len(REFILL_ANGLES)]
    created = generate_content(desk, campaign_id=str(campaign.get("id")), angle=angle, fresh=True)
    return {"content": created, "reason": f"refilled ({angle})"}


def _fail_cycle(
    desk: MarketingStore,
    state: dict[str, Any],
    *,
    clock: float,
    was_enabled: bool,
    exc: BaseException,
) -> dict[str, Any]:
    logger.exception("Marketing loop cycle failed")
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
    state.pop("cycle_pid", None)
    state.pop("cycle_started_at", None)
    state = _finish_cycle_state(desk, state, clock=clock)
    desk.append_journal({"kind": "loop", "text": summary})
    return {"did_work": False, "phase": state.get("phase") or "idle", "reason": summary, "loop": state}


def start_loop(
    store: MarketingStore | None = None,
    *,
    schedule: dict[str, Any] | None = None,
    run_now: bool = False,
    now: float | None = None,
    tz: str | None = None,
    watch_fn: WatchFn | None = None,
    improve_fn: ImproveFn | None = None,
) -> dict[str, Any]:
    desk = store or MarketingStore()
    if not brand_is_armed(desk.load_brand(), desk.load_product()):
        raise MarketingError(
            "Set a brand company or understand a product before starting the loop",
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
    preview = _apply_intent(
        with_default_schedule(desk.load_loop()),
        {**desk.load_loop_intent(), **fields},
        now=clock,
    )
    label = describe_schedule(_sched(preview))
    stamp = format_due(next_due_after(preview, now=clock, fallback_s=86400), _sched(preview))
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
    with marketing_desk_lock(desk, wait_s=0) as got:
        if not got:
            desk.append_journal({"kind": "loop", "text": journal})
            return {
                "did_work": False,
                "phase": "busy",
                "reason": "a cycle is already running",
                "loop": peek_loop(desk, now=clock),
            }
        state = _apply_intent(
            with_default_schedule(desk.load_loop()),
            desk.load_loop_intent(),
            now=clock,
        )
        state["enabled"] = True
        state["next_due"] = 0
        state["phase"] = "armed"
        state["last_result"] = f"loop armed - {label}, next {stamp}"
        desk.save_loop(state)
        desk.append_journal({"kind": "loop", "text": journal})
        return _run_cycle(
            desk,
            clock=clock,
            force=True,
            watch_fn=watch_fn,
            improve_fn=improve_fn,
        )


def update_loop_schedule(
    store: MarketingStore | None = None,
    *,
    schedule: dict[str, Any],
    now: float | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    desk = store or MarketingStore()
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


def stop_loop(store: MarketingStore | None = None, *, now: float | None = None) -> dict[str, Any]:
    desk = store or MarketingStore()
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
