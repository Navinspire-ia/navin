# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn-level guardrails visible to tools that start background work.

The runner binds the active spec's inheritable policy here for the duration
of the turn. A spawn launched mid-turn reads it back, so a Build parent's
verify gate and the module denylist follow the work into the subagent
instead of evaporating at the process boundary: without this, delegating a
task was a policy escape hatch (the subagent could skip verify and use
tools the module had locked out).

Only product-level guardrails travel. Mode denials (plan/ask) stay behind:
spawn is refused in those modes anyway, and a subagent always runs as a
full agent on its own task.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TurnPolicy:
    """The inheritable slice of an AgentRunSpec."""

    requires_verify_before_done: bool = False
    locked_denied_tools: frozenset[str] = field(default_factory=frozenset)
    # None = no allowlist (all tools except denials). A frozenset is the
    # parent's /forge surface; subagents must inherit it or they get every
    # desk tool the parent just paid to hide.
    allowed_tools: frozenset[str] | None = None
    validate_code_changes: bool = True


_CURRENT_TURN_POLICY: contextvars.ContextVar[TurnPolicy | None] = contextvars.ContextVar(
    "navin_current_turn_policy", default=None
)


def bind_turn_policy(policy: TurnPolicy) -> contextvars.Token[TurnPolicy | None]:
    return _CURRENT_TURN_POLICY.set(policy)


def reset_turn_policy(token: contextvars.Token[TurnPolicy | None]) -> None:
    _CURRENT_TURN_POLICY.reset(token)


def current_turn_policy() -> TurnPolicy | None:
    return _CURRENT_TURN_POLICY.get()
