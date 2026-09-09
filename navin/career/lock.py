# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Process lock so Career hunt and watch never overlap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from navin.career.store import CareerStore


@contextmanager
def career_desk_lock(store: CareerStore, *, wait_s: float = 0) -> Iterator[bool]:
    """Yield True when this process owns the desk. False if another hunt is live."""
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
