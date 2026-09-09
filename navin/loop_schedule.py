# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Wall-clock schedules for Studio desk loops (Trading, Career, ...).

A gateway supervisor calls ``maybe_tick`` while Navin is up. This module
decides *when* a due tick is allowed: every day, weekdays, weekend, one
weekday, or one day each month, at a chosen hour.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

LoopScheduleKind = Literal["daily", "weekdays", "weekend", "weekly", "monthly"]

SCHEDULE_KINDS: tuple[LoopScheduleKind, ...] = (
    "daily",
    "weekdays",
    "weekend",
    "weekly",
    "monthly",
)

LAST_DAY = "last"
MAX_MONTH_DAY = 28
WEEKDAY_NAMES = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


class LoopScheduleError(ValueError):
    """Raised when a desk loop schedule cannot run."""


def default_schedule() -> dict[str, Any]:
    return normalize_schedule(
        {
            "kind": "daily",
            "hour": 9,
            "minute": 0,
            "weekday": 1,
            "day": 1,
            "tz": None,
        }
    )


def _as_int(value: object, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise LoopScheduleError(f"{field} must be an integer") from exc
    return int(value)


def _require_range(field: str, value: int, low: int, high: int) -> int:
    if not low <= value <= high:
        raise LoopScheduleError(f"{field} must be between {low} and {high}")
    return value


def resolve_tz(name: str | None) -> ZoneInfo | tzinfo:
    text = str(name or "").strip()
    if text:
        try:
            return ZoneInfo(text)
        except ZoneInfoNotFoundError as exc:
            raise LoopScheduleError(f"unknown timezone '{text}'") from exc
    local = datetime.now().astimezone().tzinfo
    return local or ZoneInfo("UTC")


def schedule_to_expr(schedule: dict[str, Any]) -> str:
    kind = str(schedule.get("kind") or "daily")
    minute = int(schedule.get("minute") or 0)
    hour = int(schedule.get("hour") or 0)
    if kind == "daily":
        return f"{minute} {hour} * * *"
    if kind == "weekdays":
        return f"{minute} {hour} * * 1-5"
    if kind == "weekend":
        return f"{minute} {hour} * * 0,6"
    if kind == "weekly":
        weekday = int(schedule.get("weekday") or 1)
        return f"{minute} {hour} * * {weekday % 7}"
    day = schedule.get("day") or 1
    token = "L" if day == LAST_DAY else int(day)
    return f"{minute} {hour} {token} * *"


def normalize_schedule(raw: object, *, tz: str | None = None) -> dict[str, Any]:
    if raw is None:
        payload: dict[str, Any] = {}
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        raise LoopScheduleError("schedule must be an object")

    kind = str(payload.get("kind") or "daily").strip().lower()
    if kind not in SCHEDULE_KINDS:
        raise LoopScheduleError(
            "schedule.kind must be daily, weekdays, weekend, weekly or monthly"
        )

    hour = _require_range("hour", _as_int(payload.get("hour"), "hour", 9), 0, 23)
    minute = _require_range("minute", _as_int(payload.get("minute"), "minute", 0), 0, 59)
    weekday = _require_range(
        "weekday",
        _as_int(payload.get("weekday"), "weekday", 1),
        1,
        7,
    )

    raw_day = payload.get("day", 1)
    day: int | str
    if raw_day == LAST_DAY or str(raw_day).strip().lower() == LAST_DAY:
        day = LAST_DAY
    else:
        day = _require_range("day", _as_int(raw_day, "day", 1), 1, MAX_MONTH_DAY)

    raw_tz = payload.get("tz", tz)
    tz_name = str(raw_tz).strip() if isinstance(raw_tz, str) and raw_tz.strip() else None
    if tz_name:
        resolve_tz(tz_name)

    row = {
        "kind": kind,
        "hour": hour,
        "minute": minute,
        "weekday": weekday,
        "day": day,
        "tz": tz_name,
    }
    row["expr"] = schedule_to_expr(row)
    if not croniter.is_valid(str(row["expr"])):
        raise LoopScheduleError(f"schedule does not compile: {row['expr']}")
    return row


def next_due_ts(schedule: dict[str, Any], now: float) -> float:
    row = normalize_schedule(schedule)
    zone = resolve_tz(row.get("tz") if isinstance(row.get("tz"), str) else None)
    base = datetime.fromtimestamp(now, tz=zone)
    nxt = croniter(str(row["expr"]), base).get_next(datetime)
    return float(nxt.timestamp())


def describe_schedule(schedule: dict[str, Any] | None) -> str:
    if not isinstance(schedule, dict) or not schedule.get("kind"):
        return "no schedule"
    row = normalize_schedule(schedule)
    time = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    kind = str(row["kind"])
    if kind == "daily":
        return f"every day at {time}"
    if kind == "weekdays":
        return f"weekdays at {time}"
    if kind == "weekend":
        return f"weekends at {time}"
    if kind == "weekly":
        name = WEEKDAY_NAMES.get(int(row["weekday"]), "Monday")
        return f"every {name} at {time}"
    if row["day"] == LAST_DAY:
        return f"the last day of each month at {time}"
    return f"day {row['day']} of each month at {time}"


def format_due(ts: float, schedule: dict[str, Any] | None = None) -> str:
    tz_name = None
    if isinstance(schedule, dict) and isinstance(schedule.get("tz"), str):
        tz_name = schedule.get("tz")
    try:
        zone = resolve_tz(tz_name)
    except LoopScheduleError:
        zone = datetime.now().astimezone().tzinfo
    stamp = datetime.fromtimestamp(ts, tz=zone)
    return stamp.strftime("%Y-%m-%d %H:%M")


def with_default_schedule(state: dict[str, Any]) -> dict[str, Any]:
    row = dict(state)
    raw = row.get("schedule")
    if isinstance(raw, dict) and raw.get("kind"):
        try:
            row["schedule"] = normalize_schedule(raw)
            return row
        except LoopScheduleError:
            pass
    row["schedule"] = default_schedule()
    return row


def next_due_after(
    state: dict[str, Any],
    *,
    now: float,
    fallback_s: float,
) -> float:
    raw = state.get("schedule")
    if isinstance(raw, dict) and raw.get("kind"):
        try:
            return next_due_ts(raw, now)
        except LoopScheduleError:
            pass
    return now + max(1.0, float(fallback_s or 900))
