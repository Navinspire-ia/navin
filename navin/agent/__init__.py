# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent core module.

The public names load on first access: importing a light submodule such as
``navin.agent.approval`` (the config schema does) must not import the whole
agent loop, its tools and MCP.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navin.agent.context import ContextBuilder
    from navin.agent.hook import (
        AgentHook,
        AgentHookContext,
        AgentRunHookContext,
        AgentTurnHookContext,
        AgentTurnHookFactory,
        CompositeHook,
    )
    from navin.agent.loop import AgentLoop
    from navin.agent.memory import MemoryStore
    from navin.agent.skills import SkillsLoader
    from navin.agent.subagent import SubagentManager

_EXPORTS = {
    "AgentHook": "navin.agent.hook",
    "AgentHookContext": "navin.agent.hook",
    "AgentRunHookContext": "navin.agent.hook",
    "AgentTurnHookContext": "navin.agent.hook",
    "AgentTurnHookFactory": "navin.agent.hook",
    "AgentLoop": "navin.agent.loop",
    "CompositeHook": "navin.agent.hook",
    "ContextBuilder": "navin.agent.context",
    "MemoryStore": "navin.agent.memory",
    "SkillsLoader": "navin.agent.skills",
    "SubagentManager": "navin.agent.subagent",
}

__all__ = [
    "AgentHook",
    "AgentHookContext",
    "AgentRunHookContext",
    "AgentTurnHookContext",
    "AgentTurnHookFactory",
    "AgentLoop",
    "CompositeHook",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    "SubagentManager",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
