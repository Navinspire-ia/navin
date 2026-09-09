# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Compatibility export. Desk schedules live in ``navin.loop_schedule``."""

from navin.loop_schedule import (
    LAST_DAY,
    MAX_MONTH_DAY,
    SCHEDULE_KINDS,
    WEEKDAY_NAMES,
    LoopScheduleError,
    LoopScheduleKind,
    default_schedule,
    describe_schedule,
    format_due,
    next_due_after,
    next_due_ts,
    normalize_schedule,
    resolve_tz,
    schedule_to_expr,
    with_default_schedule,
)

__all__ = [
    "LAST_DAY",
    "MAX_MONTH_DAY",
    "SCHEDULE_KINDS",
    "WEEKDAY_NAMES",
    "LoopScheduleError",
    "LoopScheduleKind",
    "default_schedule",
    "describe_schedule",
    "format_due",
    "next_due_after",
    "next_due_ts",
    "normalize_schedule",
    "resolve_tz",
    "schedule_to_expr",
    "with_default_schedule",
]
