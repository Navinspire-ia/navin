# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Carry a model substitution from the provider layer up to the UI.

``make_provider`` builds the failover wrapper with no access to the runtime event
bus, and threading a bus through every provider construction site would couple
the provider layer to the agent's plumbing for one message. Instead the agent
registers a publisher once at startup, and the wrapper calls it.

Without this, a failover is only visible in the logs: the UI keeps displaying the
model the user picked while another model answers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

ModelSwitchPublisher = Callable[[str, str], Awaitable[None] | None]

_publisher: ModelSwitchPublisher | None = None


def set_model_switch_publisher(publisher: ModelSwitchPublisher | None) -> None:
    """Install the process-wide publisher, or clear it with ``None``."""
    global _publisher
    _publisher = publisher


def current_model_switch_publisher() -> ModelSwitchPublisher | None:
    return _publisher


async def notify_model_switch(chosen_model: str, served_model: str) -> None:
    """Report that *served_model* answered instead of *chosen_model*.

    Silent when nothing is registered (CLI one-shots, tests, embedded use) and
    when the two models match. A failing publisher is logged, never raised: a
    notice must not cost the user their answer.
    """
    publisher = _publisher
    if publisher is None or not served_model or served_model == chosen_model:
        return
    try:
        result: Any = publisher(chosen_model, served_model)
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:  # noqa: BLE001 - a notice must never break a turn
        logger.warning("Model switch notification failed: {}", exc)
