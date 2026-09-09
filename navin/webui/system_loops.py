# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Reconfiguring the loops navin runs for itself.

A system loop is re-registered from the config file on every start, so a change
written only to the cron store would be silently undone by the next restart.
These helpers write the config and hand back the schedule to apply live.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from navin.config.loader import load_config, save_config
from navin.config.schema import RecurrenceConfig
from navin.cron.recurrence import Recurrence, RecurrenceError, parse_schedule
from navin.cron.service import validate_schedule
from navin.cron.types import CronLimits, CronSchedule

DREAM = "dream"
HEARTBEAT = "heartbeat"
SYSTEM_LOOP_IDS: tuple[str, ...] = (DREAM, HEARTBEAT)


def is_configurable_system_loop(job_id: str) -> bool:
    return job_id in SYSTEM_LOOP_IDS


def _recurrence_config(recurrence: Recurrence) -> RecurrenceConfig:
    return RecurrenceConfig(
        kind=recurrence.kind,
        interval=recurrence.interval,
        minute=recurrence.minute,
        hour=recurrence.hour,
        weekday=recurrence.weekday,
        day=recurrence.day,
    )


def update_system_loop(
    job_id: str,
    *,
    schedule: CronSchedule | None = None,
    limits: CronLimits | None = None,
    enabled: bool | None = None,
) -> str | None:
    """Persist a system loop's settings. Returns an error message, or None.

    The message is meant to be shown to the user, so it says what to do rather
    than which field failed validation.
    """
    if not is_configurable_system_loop(job_id):
        return "this system loop cannot be reconfigured"

    recurrence: Recurrence | None = None
    if schedule is not None:
        if schedule.kind == "at":
            return "a built-in loop repeats, so it cannot be scheduled for a single time"
        try:
            validate_schedule(schedule)
        except ValueError as exc:
            return str(exc)
        recurrence = parse_schedule(schedule)
        if recurrence is None and schedule.kind != "cron":
            return "unsupported schedule for a built-in loop"

    config = load_config()
    target: Any = (
        config.agents.defaults.dream if job_id == DREAM else config.gateway.heartbeat
    )

    if schedule is not None:
        try:
            if recurrence is not None:
                target.schedule = _recurrence_config(recurrence)
                if job_id == DREAM:
                    # The legacy override would otherwise keep winning.
                    target.cron = None
            elif job_id == DREAM:
                target.schedule = None
                target.cron = schedule.expr
            else:
                return "a raw expression is only supported for the memory loop"
        except RecurrenceError as exc:
            return str(exc)

    if limits is not None:
        target.daily_token_budget = limits.daily_token_budget
        target.max_consecutive_failures = limits.max_consecutive_failures
    if enabled is not None:
        target.enabled = enabled

    try:
        save_config(config)
    except Exception:
        logger.exception("failed to save the config for system loop {}", job_id)
        return "the configuration file could not be written"
    return None
