"""Org-level tool allow/deny policy for Enterprise.

Config shape (dict or JSON-compatible)::

    {
      "allow": ["shell", "read_file", "web_search"],   # optional allowlist
      "deny": ["browser", "exec_remote"]              # optional denylist
    }

Rules (fail-closed when an allowlist is present):

1. Empty / missing tool name → deny.
2. If ``deny`` contains the tool → deny.
3. If ``allow`` is non-empty and the tool is not listed → deny.
4. Otherwise → allow.

Deny wins over allow when both lists mention the same tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def _normalize_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _as_name_set(values: Iterable[Any] | None) -> frozenset[str]:
    if not values:
        return frozenset()
    out: set[str] = set()
    for item in values:
        if isinstance(item, str):
            cleaned = _normalize_name(item)
            if cleaned:
                out.add(cleaned)
    return frozenset(out)


@dataclass(frozen=True)
class ToolPolicy:
    """Immutable allow/deny lists for org tool gating."""

    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> ToolPolicy:
        if not config:
            return cls()
        return cls(
            allow=_as_name_set(config.get("allow")),
            deny=_as_name_set(config.get("deny")),
        )

    def is_allowed(self, tool_name: str | None) -> bool:
        name = _normalize_name(tool_name)
        if not name:
            return False
        if name in self.deny:
            return False
        if self.allow and name not in self.allow:
            return False
        return True


def check_tool_allowed(
    tool_name: str | None,
    config: Mapping[str, Any] | None,
) -> bool:
    """Return True if ``tool_name`` passes the org policy in ``config``."""
    return ToolPolicy.from_config(config).is_allowed(tool_name)
