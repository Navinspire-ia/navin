# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Clock-anchored recurrences for scheduled jobs.

A ``CronSchedule`` of kind ``every`` fires ``interval`` after the previous run,
so its firing times drift with every restart: "every 8 hours" started at 03:12
keeps running at 03:12, 11:12, 19:12. Users who ask for "every 8 hours" mean
00:00, 08:00 and 16:00, which is what a cron expression gives.

``Recurrence`` is the structured form the UI edits. It compiles to a cron
expression, and parses back from one, so a job saved here can be reopened and
edited instead of showing a raw expression.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from navin.cron.types import CronSchedule

RecurrenceKind = Literal["minutes", "hourly", "every_hours", "daily", "weekly", "monthly"]

LAST_DAY = "last"

# Only divisors give evenly spaced runs: ``*/7`` in the minute field fires at
# :00 :07 ... :56 and then :00 again, a 4-minute gap. Offering just the divisors
# keeps the promise the label makes.
MINUTE_INTERVALS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)
HOUR_INTERVALS: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12)

# Day 29-31 exists in some months only, and a job set on the 31st would fire
# seven times a year. Callers who want the end of the month ask for LAST_DAY.
MAX_MONTH_DAY = 28

_ALL = "*"
_STEP_RE = re.compile(r"^\*/(\d+)$")
_NUMBER_RE = re.compile(r"^(\d+)$")


class RecurrenceError(ValueError):
    """Raised when a recurrence cannot describe a runnable schedule."""


@dataclass(frozen=True, slots=True)
class Recurrence:
    """A repeating schedule anchored to the wall clock of ``tz``."""

    kind: RecurrenceKind
    # Minutes for "minutes", hours for "every_hours"; unused by the others.
    interval: int = 1
    minute: int = 0
    hour: int = 0
    # ISO weekday, 1=Monday through 7=Sunday, so that no caller has to remember
    # whether this particular cron dialect starts the week on Sunday.
    weekday: int = 1
    day: int | str = 1
    tz: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.kind == "minutes":
            self._require_interval(MINUTE_INTERVALS, "minutes")
        elif self.kind == "every_hours":
            self._require_interval(HOUR_INTERVALS, "hours")
            self._require_range("minute", self.minute, 0, 59)
        elif self.kind == "hourly":
            self._require_range("minute", self.minute, 0, 59)
        elif self.kind in ("daily", "weekly", "monthly"):
            self._require_range("minute", self.minute, 0, 59)
            self._require_range("hour", self.hour, 0, 23)
            if self.kind == "weekly":
                self._require_range("weekday", self.weekday, 1, 7)
            if self.kind == "monthly" and self.day != LAST_DAY:
                if not isinstance(self.day, int):
                    raise RecurrenceError(f"day must be an integer or '{LAST_DAY}'")
                self._require_range("day", self.day, 1, MAX_MONTH_DAY)
        else:
            raise RecurrenceError(f"unknown recurrence kind '{self.kind}'")

    def _require_interval(self, allowed: tuple[int, ...], unit: str) -> None:
        if self.interval not in allowed:
            listed = ", ".join(str(value) for value in allowed)
            raise RecurrenceError(
                f"interval must be one of {listed} {unit} for evenly spaced runs, "
                f"got {self.interval}"
            )

    @staticmethod
    def _require_range(field: str, value: object, low: int, high: int) -> None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise RecurrenceError(f"{field} must be an integer, got {value!r}")
        if not low <= value <= high:
            raise RecurrenceError(f"{field} must be between {low} and {high}, got {value}")

    def to_expr(self) -> str:
        """Return the cron expression firing at this recurrence."""
        if self.kind == "minutes":
            minute = _ALL if self.interval == 1 else f"*/{self.interval}"
            return f"{minute} * * * *"
        if self.kind == "hourly":
            return f"{self.minute} * * * *"
        if self.kind == "every_hours":
            hour = _ALL if self.interval == 1 else f"*/{self.interval}"
            return f"{self.minute} {hour} * * *"
        if self.kind == "daily":
            return f"{self.minute} {self.hour} * * *"
        if self.kind == "weekly":
            # This dialect numbers Sunday 0, so ISO 7 wraps back to 0.
            return f"{self.minute} {self.hour} * * {self.weekday % 7}"
        day = "L" if self.day == LAST_DAY else self.day
        return f"{self.minute} {self.hour} {day} * *"

    def to_schedule(self) -> CronSchedule:
        """Return the stored schedule for this recurrence."""
        return CronSchedule(kind="cron", expr=self.to_expr(), tz=self.tz)

    def with_tz(self, tz: str | None) -> Recurrence:
        return replace(self, tz=tz)


def _field(value: str) -> int | None:
    match = _NUMBER_RE.match(value)
    return int(match.group(1)) if match else None


def _step(value: str) -> int | None:
    match = _STEP_RE.match(value)
    return int(match.group(1)) if match else None


def parse_expr(expr: str, *, tz: str | None = None) -> Recurrence | None:
    """Return the recurrence behind ``expr``, or None if it has another shape.

    Returning None is expected: a hand-written expression such as
    ``0 9 * * 1-5`` is a valid schedule that no preset describes, and callers
    show it as-is rather than rounding it to something it is not.
    """
    fields = expr.split()
    if len(fields) != 5:
        return None
    minute, hour, day, month, weekday = fields
    if month != _ALL:
        return None

    every_day = day == _ALL
    every_weekday = weekday == _ALL

    try:
        if every_day and every_weekday:
            return _parse_time_of_day(minute, hour, tz)
        if every_day and not every_weekday:
            return _parse_weekly(minute, hour, weekday, tz)
        if not every_day and every_weekday:
            return _parse_monthly(minute, hour, day, tz)
    except RecurrenceError:
        return None
    return None


def _parse_time_of_day(minute: str, hour: str, tz: str | None) -> Recurrence | None:
    minute_step = _step(minute) if minute != _ALL else 1
    if minute_step is not None and hour == _ALL:
        if minute_step not in MINUTE_INTERVALS:
            return None
        return Recurrence(kind="minutes", interval=minute_step, tz=tz)

    exact_minute = _field(minute)
    if exact_minute is None:
        return None
    if hour == _ALL:
        return Recurrence(kind="hourly", minute=exact_minute, tz=tz)

    hour_step = _step(hour)
    if hour_step is not None:
        if hour_step not in HOUR_INTERVALS:
            return None
        return Recurrence(
            kind="every_hours",
            interval=hour_step,
            minute=exact_minute,
            tz=tz,
        )

    exact_hour = _field(hour)
    if exact_hour is None:
        return None
    return Recurrence(kind="daily", minute=exact_minute, hour=exact_hour, tz=tz)


def _parse_weekly(minute: str, hour: str, weekday: str, tz: str | None) -> Recurrence | None:
    exact_minute, exact_hour, exact_weekday = _field(minute), _field(hour), _field(weekday)
    if exact_minute is None or exact_hour is None or exact_weekday is None:
        return None
    if exact_weekday > 7:
        return None
    # Both 0 and 7 mean Sunday in cron; ISO calls it 7.
    return Recurrence(
        kind="weekly",
        minute=exact_minute,
        hour=exact_hour,
        weekday=7 if exact_weekday == 0 else exact_weekday,
        tz=tz,
    )


def _parse_monthly(minute: str, hour: str, day: str, tz: str | None) -> Recurrence | None:
    exact_minute, exact_hour = _field(minute), _field(hour)
    if exact_minute is None or exact_hour is None:
        return None
    if day.upper() == "L":
        return Recurrence(
            kind="monthly",
            minute=exact_minute,
            hour=exact_hour,
            day=LAST_DAY,
            tz=tz,
        )
    exact_day = _field(day)
    if exact_day is None or not 1 <= exact_day <= MAX_MONTH_DAY:
        return None
    return Recurrence(
        kind="monthly",
        minute=exact_minute,
        hour=exact_hour,
        day=exact_day,
        tz=tz,
    )


def parse_schedule(schedule: CronSchedule) -> Recurrence | None:
    """Return the recurrence behind a stored schedule, when it has one."""
    if schedule.kind != "cron" or not schedule.expr:
        return None
    return parse_expr(schedule.expr, tz=schedule.tz)


def recurrence_payload(schedule: CronSchedule) -> dict[str, object] | None:
    """Return the JSON form of a schedule's recurrence, for the UI to edit."""
    recurrence = parse_schedule(schedule)
    if recurrence is None:
        return None
    return {
        "kind": recurrence.kind,
        "interval": recurrence.interval,
        "minute": recurrence.minute,
        "hour": recurrence.hour,
        "weekday": recurrence.weekday,
        "day": recurrence.day,
        "tz": recurrence.tz,
    }


def recurrence_from_payload(raw: object, *, tz: str | None = None) -> Recurrence:
    """Build a recurrence from untrusted client JSON.

    Raises ``RecurrenceError`` with a message meant to be shown to the user.
    """
    if not isinstance(raw, dict):
        raise RecurrenceError("recurrence must be an object")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        raise RecurrenceError("recurrence.kind is required")

    day: int | str = 1
    raw_day = raw.get("day", 1)
    if isinstance(raw_day, str):
        if raw_day != LAST_DAY:
            raise RecurrenceError(f"day must be an integer or '{LAST_DAY}'")
        day = LAST_DAY
    elif isinstance(raw_day, int) and not isinstance(raw_day, bool):
        day = raw_day
    else:
        raise RecurrenceError(f"day must be an integer or '{LAST_DAY}'")

    raw_tz = raw.get("tz", tz)
    if raw_tz is not None and not isinstance(raw_tz, str):
        raise RecurrenceError("tz must be a string")

    return Recurrence(
        kind=kind,  # type: ignore[arg-type]
        interval=_int_field(raw, "interval", 1),
        minute=_int_field(raw, "minute", 0),
        hour=_int_field(raw, "hour", 0),
        weekday=_int_field(raw, "weekday", 1),
        day=day,
        tz=raw_tz or None,
    )


def _int_field(raw: dict[str, object], field: str, default: int) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecurrenceError(f"{field} must be an integer, got {value!r}")
    return value
