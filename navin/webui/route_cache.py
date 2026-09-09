# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Stop identical slow routes from being fetched several times at once.

Every WebUI window polls the same handful of network-backed routes: the
account plan, the CI rollup, the version check, the LSP server catalogue. Each
one runs its blocking call through ``asyncio.to_thread``, which draws from the
default executor - ten threads on a six-core machine. Three windows polling
four routes is enough to hold most of them, and once the pool is full every
other ``to_thread`` waits its turn, including the work a chat turn needs. That
is how a one-word prompt ends up taking minutes while the log shows five
unrelated routes all going slow at the same instant.

Two things fix that, and both live here. Concurrent callers asking for the same
key share one in-flight call instead of starting their own, and the answer is
reused for a few seconds afterwards. When that TTL lapses, pollers get the last
good answer immediately while a refresh runs in the background: a 5s
navin.live round-trip must not sit in front of the first model token.

Deliberately not a general cache: entries are small, short-lived, and keyed by
the caller. Anything that must be current passes ``force`` to skip the stored
value, and still gets coalesced with whatever is already running.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class CoalescingCache:
    """Short-lived per-key cache that also collapses concurrent lookups."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    def invalidate(self, key: str | None = None) -> None:
        """Drop a stored answer, or all of them when *key* is None.

        In-flight calls are left alone: cancelling them would fail the very
        callers that are waiting for a fresh value.
        """
        if key is None:
            self._values.clear()
        else:
            self._values.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Drop every stored answer whose key starts with *prefix*."""
        if not prefix:
            return
        for key in list(self._values):
            if key.startswith(prefix):
                self._values.pop(key, None)

    async def get(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        ttl: float,
        force: bool = False,
    ) -> Any:
        """Return a cached value, or the result of one shared *factory* call.

        When a previous answer exists but its TTL has lapsed, callers get that
        stale value immediately and a refresh runs in the background. Pollers
        must not sit in front of the first model token for a 5s navin.live
        round-trip. ``force=True`` still waits for a fresh answer (sign-in,
        logout, plan change).
        """
        now = time.monotonic()
        hit = self._values.get(key)
        if not force:
            if hit is not None and hit[0] > now:
                return hit[1]
            if hit is not None:
                if self._inflight.get(key) is None:
                    self._spawn_refresh(key, factory, ttl)
                return hit[1]

        running = self._inflight.get(key)
        if running is not None:
            # Someone is already asking. Shielding matters: if the first caller
            # disconnects and its handler is cancelled, the others must still
            # get their answer rather than inherit the cancellation.
            return await asyncio.shield(running)

        return await self._run_factory(key, factory, ttl)

    def _spawn_refresh(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: float,
    ) -> None:
        async def _safe() -> None:
            try:
                await self._run_factory(key, factory, ttl)
            except Exception:
                # Keep serving the stale value. The next poll retries.
                return

        loop = asyncio.get_running_loop()
        loop.create_task(_safe(), name=f"route-cache:{key}")

    async def _run_factory(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl: float,
    ) -> Any:
        running = self._inflight.get(key)
        if running is not None:
            return await asyncio.shield(running)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._inflight[key] = future
        try:
            value = await factory()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            self._inflight.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            # Nobody may be waiting on this future; without this the loop logs
            # "exception was never retrieved" for an error we already raised.
            future.exception()
            raise
        self._inflight.pop(key, None)
        if ttl > 0:
            self._values[key] = (time.monotonic() + ttl, value)
        if not future.done():
            future.set_result(value)
        return value
