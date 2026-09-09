"""Tenders desk loop: collect official notices on a wall-clock schedule.

Heartbeat still only runs silent follow. This loop is the autonomous hunt:
collect official portals, score, then watch. Never send a buyer mail.

Start / stop / schedule always persist, even while a collect holds the desk
lock. The cycle applies that intent when it finishes so Pause cannot be
overwritten.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable

from loguru import logger

from navin.loop_schedule import (
    LoopScheduleError,
    describe_schedule,
    format_due,
    next_due_after,
    normalize_schedule,
    with_default_schedule,
)
from navin.tenders.errors import TenderError
from navin.tenders.lock import tenders_desk_lock
from navin.tenders.profile import wizard_ready
from navin.tenders.store import TenderStore

CollectFn = Callable[..., dict[str, Any]]
WatchFn = Callable[..., dict[str, Any]]

# Wall clock for one collect+watch. A hung portal must not freeze the desk.
MAX_HUNT_S = 8 * 60.0
WATCH_S = 45.0
# After a failed cycle, retry soon instead of waiting for the next daily slot.
ERROR_BACKOFF_S = (120.0, 300.0, 900.0, 1800.0)

# In-process hunts. Survives a crashed collect: a new process has an empty set
# and recover_stale_hunt can clear a leftover phase=hunt so heartbeat works.
_live_hunts: set[str] = set()


def _now() -> float:
    return time.time()


def call_with_deadline(fn: Callable[[], Any], *, timeout_s: float, label: str) -> Any:
    """Run fn, or raise TenderError when it exceeds timeout_s.

    The worker is a daemon thread so a hung portal cannot freeze the gateway
    or block interpreter shutdown. It is not joined on timeout.
    """
    if timeout_s <= 0:
        return fn()
    box: list[Any] = []
    err: list[BaseException] = []
    done = Event()

    def _run() -> None:
        try:
            box.append(fn())
        except BaseException as exc:
            err.append(exc)
        finally:
            done.set()

    Thread(target=_run, name=f"navin-tenders-{label}", daemon=True).start()
    if not done.wait(timeout_s):
        raise TenderError(f"{label} exceeded {int(timeout_s)}s", status=504)
    if err:
        raise err[0]
    return box[0]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _retry_due(state: dict[str, Any], *, clock: float) -> float:
    try:
        streak = int(state.get("error_streak") or 0)
    except (TypeError, ValueError):
        streak = 1
    delay = ERROR_BACKOFF_S[min(max(streak - 1, 0), len(ERROR_BACKOFF_S) - 1)]
    scheduled = next_due_after(state, now=clock, fallback_s=86400)
    retry = clock + delay
    if scheduled > clock:
        return min(retry, scheduled)
    return retry


def hunt_expired(state: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """True when phase=hunt is older than MAX_HUNT_S (crash, PID reuse, hang)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") != "hunt":
        return False
    started = _as_float(row.get("hunt_started_at"))
    if started <= 0:
        return False
    clock = now if now is not None else _now()
    return (clock - started) > MAX_HUNT_S


def _sched(state: dict[str, Any]) -> dict[str, Any] | None:
    raw = state.get("schedule")
    return raw if isinstance(raw, dict) else None


def _busy_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "did_work": False,
        "phase": "busy",
        "reason": "a collect is already running",
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


def _hunt_key(desk: TenderStore) -> str:
    return str(Path(desk.root).resolve())


def mark_loop_hunting(desk: TenderStore, live: bool) -> None:
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


def hunt_is_live(desk: TenderStore, state: dict[str, Any] | None = None) -> bool:
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


def recover_stale_hunt(desk: TenderStore, *, now: float | None = None) -> dict[str, Any]:
    """Clear a leftover hunt and persist a leftover Stop/Horaires when the lock is free."""
    clock = now if now is not None else _now()
    disk = with_default_schedule(desk.load_loop())
    intent = desk.load_loop_intent()
    stale = str(disk.get("phase") or "") == "hunt" and not hunt_is_live(desk, disk)
    if hunt_is_live(desk, disk) or (not stale and not intent):
        return _apply_intent(disk, intent, now=clock)
    with tenders_desk_lock(desk, wait_s=0) as got:
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


def format_loop_status(state: dict[str, Any] | None) -> str:
    """One block the agent, CLI and book all read. Autonomy rules stay here."""
    row = state if isinstance(state, dict) else {}
    mode = "ON" if row.get("enabled") else "OFF"
    phase = str(row.get("phase") or "paused")
    label = describe_schedule(_sched(row))
    due = "-"
    if row.get("next_due"):
        due = format_due(row.get("next_due"), _sched(row))
    last = str(row.get("last_result") or "-")
    return (
        f"LOOP {mode} | {phase} | {label} | next {due} | {last}. "
        "Autonomous hunt: tenders action=start / stop / schedule / tick "
        "(Studio #/tenders, Tauri, navin tenders, python -m navin.tenders.desk_cli). "
        "Heartbeat is follow only. Never collect, write, start or tick from heartbeat. "
        "Never send a buyer mail. Do not create a chat cron. "
        "Desk self-heals a stale or hung hunt."
    )


def peek_loop(store: TenderStore | None = None, *, now: float | None = None) -> dict[str, Any]:
    """Loop state the desk must show, including Pause/Horaires written mid-collect."""
    desk = store or TenderStore()
    clock = now if now is not None else _now()
    recover_stale_hunt(desk, now=clock)
    state = with_default_schedule(desk.load_loop())
    return _apply_intent(state, desk.load_loop_intent(), now=clock)


def _merge_intent(desk: TenderStore, **fields: Any) -> dict[str, Any]:
    intent = dict(desk.load_loop_intent())
    for key, value in fields.items():
        if value is not None:
            intent[key] = value
    desk.save_loop_intent(intent)
    return intent


def _commit_control(
    desk: TenderStore,
    *,
    now: float,
    journal: str,
    last_result: str | None = None,
    phase: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write operator intent, then apply it now if the collect lock is free."""
    intent = _merge_intent(desk, **fields)
    with tenders_desk_lock(desk, wait_s=0) as got:
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
    desk: TenderStore,
    state: dict[str, Any],
    *,
    clock: float,
) -> dict[str, Any]:
    """Cycle fields first, then operator intent so Stop always wins."""
    merged = _apply_intent(state, desk.load_loop_intent(), now=clock)
    if not merged.get("enabled"):
        merged["phase"] = "paused"
    merged.pop("hunt_pid", None)
    merged.pop("hunt_started_at", None)
    desk.save_loop(merged)
    desk.clear_loop_intent()
    return merged


def maybe_tick(
    store: TenderStore | None = None,
    *,
    now: float | None = None,
    force: bool = False,
    collect_fn: CollectFn | None = None,
    watch_fn: WatchFn | None = None,
) -> dict[str, Any]:
    from navin.tenders.desk import run_collect
    from navin.tenders.retention import apply_retention
    from navin.tenders.watch import run_watch

    desk = store or TenderStore()
    clock = now if now is not None else _now()
    state = peek_loop(desk, now=clock)
    profile = desk.load_profile()
    if not wizard_ready(profile) and not force:
        return {
            "did_work": False,
            "phase": "unarmed",
            "reason": "tenders profile is not set up yet",
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

    with tenders_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _busy_payload(peek_loop(desk, now=clock))
        return _run_cycle(
            desk,
            clock=clock,
            force=force,
            collect_fn=collect_fn or run_collect,
            watch_fn=watch_fn,
            run_watch=run_watch,
            apply_retention=apply_retention,
        )


def _added_from_hunt(hunt: dict[str, Any] | None) -> int:
    if not isinstance(hunt, dict):
        return 0
    reports = hunt.get("collect")
    if isinstance(reports, list):
        total = 0
        for row in reports:
            if isinstance(row, dict):
                try:
                    total += int(row.get("count") or 0)
                except (TypeError, ValueError):
                    continue
        if total:
            return total
    try:
        return int(hunt.get("added") or 0)
    except (TypeError, ValueError):
        return 0


def _run_cycle(
    desk: TenderStore,
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
    # Wall clock, never the injected schedule `clock`, so hang detection stays real.
    state["hunt_started_at"] = _now()
    desk.save_loop(state)
    mark_loop_hunting(desk, True)
    try:
        try:
            hunt = call_with_deadline(
                lambda: collect_fn(desk),
                timeout_s=MAX_HUNT_S,
                label="collect",
            )
        except Exception as exc:
            logger.exception("Tenders loop collect failed")
            try:
                streak = int(state.get("error_streak") or 0) + 1
            except (TypeError, ValueError):
                streak = 1
            due = _retry_due({**state, "error_streak": streak}, clock=clock)
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
            state = _finish_cycle_state(desk, state, clock=clock)
            desk.append_journal({"kind": "loop", "text": summary})
            return {
                "did_work": False,
                "phase": state.get("phase") or "idle",
                "reason": summary,
                "loop": state,
            }

        remaining = max(8.0, MAX_HUNT_S - max(0.0, _now() - clock))
        watch_budget = min(WATCH_S, remaining)
        watch_error = ""
        try:
            if watch_fn is None:
                watch = call_with_deadline(
                    lambda: run_watch(desk, send=True),
                    timeout_s=watch_budget,
                    label="watch",
                )
            else:
                watch = call_with_deadline(
                    lambda: watch_fn(desk),
                    timeout_s=watch_budget,
                    label="watch",
                )
        except Exception as exc:
            logger.exception("Tenders loop watch failed")
            watch_error = str(exc)
            watch = {"count": 0, "delivered": False, "sent": {}, "error": watch_error}

        added = _added_from_hunt(hunt)
        alerts = int((watch or {}).get("count") or 0)
        from navin.tenders.watch import digest_was_delivered

        watch_ok = (
            not watch_error
            and (alerts == 0 or (watch or {}).get("delivered") is True or digest_was_delivered((watch or {}).get("sent")))
        )
        due = next_due_after(state, now=clock, fallback_s=86400)
        cycle = int(state.get("cycle") or 0) + 1
        stamp = format_due(due, _sched(state))
        if watch_error:
            summary = (
                f"cycle {cycle}: {added} new notices, watch failed - {watch_error}, next {stamp}"
            )
        else:
            summary = f"cycle {cycle}: {added} new notices, {alerts} alerts, next {stamp}"
        state.update(
            {
                "enabled": was_enabled,
                "phase": "idle",
                "cycle": cycle,
                "last_tick": clock,
                "last_watch": clock if watch_ok else state.get("last_watch") or 0,
                "next_due": due,
                "last_result": summary,
                "skipped_reason": "watch_error" if watch_error else "",
                "error_streak": 0,
                "added": added,
                "alerts": alerts,
                "alerts_confirmed": alerts if watch_ok else 0,
                "alerts_pending": alerts if not watch_ok else 0,
            }
        )
        state = _finish_cycle_state(desk, state, clock=clock)
        due = next_due_after(state, now=clock, fallback_s=86400)
        stamp = format_due(due, _sched(state))
        if watch_error:
            summary = (
                f"cycle {cycle}: {added} new notices, watch failed - {watch_error}, next {stamp}"
            )
        else:
            summary = f"cycle {cycle}: {added} new notices, {alerts} alerts, next {stamp}"
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
    store: TenderStore | None = None,
    *,
    schedule: dict[str, Any] | None = None,
    run_now: bool = False,
    now: float | None = None,
    tz: str | None = None,
    collect_fn: CollectFn | None = None,
    watch_fn: WatchFn | None = None,
) -> dict[str, Any]:
    desk = store or TenderStore()
    if not wizard_ready(desk.load_profile()):
        raise TenderError(
            "Finish the Tenders company setup before starting the loop",
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
    with tenders_desk_lock(desk, wait_s=0) as got:
        if not got:
            desk.append_journal({"kind": "loop", "text": journal})
            return {
                "did_work": False,
                "phase": "busy",
                "reason": "a collect is already running",
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
        from navin.tenders.desk import run_collect
        from navin.tenders.retention import apply_retention
        from navin.tenders.watch import run_watch

        return _run_cycle(
            desk,
            clock=clock,
            force=True,
            collect_fn=collect_fn or run_collect,
            watch_fn=watch_fn,
            run_watch=run_watch,
            apply_retention=apply_retention,
        )


def update_loop_schedule(
    store: TenderStore | None = None,
    *,
    schedule: dict[str, Any],
    now: float | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    desk = store or TenderStore()
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


def stop_loop(store: TenderStore | None = None, *, now: float | None = None) -> dict[str, Any]:
    desk = store or TenderStore()
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
