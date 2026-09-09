# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cron service for scheduled agent tasks."""

from navin.cron.types import CronJob, CronSchedule

__all__ = ["CronService", "CronJob", "CronSchedule"]

_LAZY = {"CronService": ".service"}


def __getattr__(name: str):
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    mod = import_module(module_path, __name__)
    val = getattr(mod, name)
    globals()[name] = val
    return val
