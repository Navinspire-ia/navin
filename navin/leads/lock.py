# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Process lock so hunt, watch and outreach never overwrite each other."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from navin.leads.store import LeadsStore


@contextmanager
def leads_desk_lock(store: LeadsStore, *, wait_s: float = 0) -> Iterator[bool]:
    """Yield True when this process owns the desk. False if another mutation is live."""
    lock = FileLock(str(store.root / "desk.lock"))
    try:
        lock.acquire(timeout=wait_s)
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        lock.release()
