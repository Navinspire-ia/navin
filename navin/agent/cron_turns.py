# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Coordination for scheduled cron turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from navin.agent.automation_turns import AutomationTurnCoordinator
from navin.bus.events import InboundMessage
from navin.cron.session_turns import (
    cron_job_id,
    cron_run_id,
    defer_cron_until_session_idle,
)


class CronTurnCoordinator(AutomationTurnCoordinator):
    """Manage scheduled cron turns without mixing them into live injections."""

    def __init__(
        self,
        *,
        publish_inbound: Callable[[InboundMessage], Awaitable[None]],
        dispatch: Callable[[InboundMessage], Awaitable[object]],
        is_running: Callable[[], bool],
        deferred_queues: dict[str, list[InboundMessage]] | None = None,
    ) -> None:
        super().__init__(
            publish_inbound=publish_inbound,
            dispatch=dispatch,
            is_running=is_running,
            turn_id=lambda msg: cron_run_id(msg.metadata),
            pending_id=_cron_job_id,
            should_defer_turn=_should_defer_cron_turn,
            missing_id_error="cron turn metadata must include a run_id",
            duplicate_id_error=lambda run_id: f"cron run {run_id!r} is already pending",
            deferred_queues=deferred_queues,
        )

    def pending_job_ids_for_session(self, session_key: str) -> set[str]:
        """Return cron jobs that are waiting for or running in *session_key*."""
        return self.pending_ids_for_session(session_key)


def _should_defer_cron_turn(
    msg: InboundMessage,
    session_key: str,
    active_session_keys: Iterable[str],
) -> bool:
    return defer_cron_until_session_idle(msg.metadata) and session_key in active_session_keys


def _cron_job_id(msg: InboundMessage) -> str | None:
    return cron_job_id(msg.metadata)
