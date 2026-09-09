# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Carry the chat session key down to provider requests for cache affinity.

OpenRouter pins a conversation to one upstream host when a stable
``session_id`` is sent with each request (sticky routing). Without it, the
router re-picks a host per request and the prompt cache goes cold, billing
the full prefix again. The agent runner knows the session key but the
provider ``chat()`` signature does not carry it, so it travels in a context
variable set around the model request - same pattern as cron spend
attribution.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_MAX_SESSION_ID_LEN = 256

_current_session: ContextVar[str | None] = ContextVar(
    "provider_session_affinity", default=None
)


@contextmanager
def session_affinity(session_key: str | None) -> Iterator[None]:
    """Tag provider requests inside this block with ``session_key``."""
    if not session_key:
        yield
        return
    token = _current_session.set(session_key[:_MAX_SESSION_ID_LEN])
    try:
        yield
    finally:
        _current_session.reset(token)


def current_session_id() -> str | None:
    """Sticky-routing key for the request being built, if any."""
    return _current_session.get()
