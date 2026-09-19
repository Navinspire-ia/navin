# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bound cleanup waits without wait_for's unbounded cancellation handshake."""

from __future__ import annotations

import asyncio

from loguru import logger


def consume_result(task: asyncio.Task) -> None:
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.debug("CLI shutdown task {}: {}", task.get_name(), error)


async def drain(tasks: list[asyncio.Task], *, timeout: float) -> bool:
    if not tasks:
        return True
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in done:
        consume_result(task)
    for task in pending:
        task.cancel()
        task.add_done_callback(consume_result)
    if pending:
        # Let cancellation close subprocess transports, without letting a
        # remote MCP server or an integration hold the terminal indefinitely.
        await asyncio.wait(pending, timeout=0.1)
    return not pending
