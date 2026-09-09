# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Attributing token spend to the scheduled loop that caused it.

A loop's daily budget can only be enforced if the tokens a run burns are
charged back to that run's job. The turn does not carry the job id: it is
started by the cron runner but can be deferred and executed later, from the
agent loop's own task, so the id is carried in a context variable set around
the turn and read by the hook that sees the usage.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol

from loguru import logger

from navin.agent.hook import AgentHook, AgentHookContext

_current_job_id: ContextVar[str | None] = ContextVar("cron_spend_job_id", default=None)


class SpendRecorder(Protocol):
    def record_job_tokens(self, job_id: str, tokens: int) -> None: ...


@contextmanager
def crediting_job(job_id: str | None) -> Iterator[None]:
    """Charge token spend inside this block to ``job_id``."""
    if not job_id:
        yield
        return
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


def current_job_id() -> str | None:
    """Return the loop whose run is executing here, if any."""
    return _current_job_id.get()


def usage_total(usage: Mapping[str, Any] | None) -> int:
    """Return the tokens an iteration consumed, as reported by the provider."""
    if not usage:
        return 0
    total = _as_int(usage.get("total_tokens"))
    if total > 0:
        return total
    # Some providers report only the two halves.
    return _as_int(usage.get("prompt_tokens")) + _as_int(usage.get("completion_tokens"))


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


class CronSpendHook(AgentHook):
    """Charge each iteration's tokens to the loop that triggered the turn."""

    def __init__(self, recorder: SpendRecorder) -> None:
        super().__init__()
        self._recorder = recorder

    async def after_iteration(self, context: AgentHookContext) -> None:
        job_id = current_job_id()
        if not job_id:
            return
        tokens = usage_total(context.usage)
        if tokens <= 0:
            return
        try:
            self._recorder.record_job_tokens(job_id, tokens)
        except Exception:
            # Losing a spend record must not fail the run that earned it.
            logger.exception("failed to record cron spend for job {}", job_id)
