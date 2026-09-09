# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Process lock so Trading cycle and watch never overlap."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from navin.trading.store import TradingStore


@contextmanager
def trading_desk_lock(store: TradingStore, *, wait_s: float = 0) -> Iterator[bool]:
    """Yield True when this process owns the desk. False if another cycle is live."""
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
