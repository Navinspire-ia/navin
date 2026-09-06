"""Organization-level tool policy (optional allowlist).

When ``config["tool_allowlist"]`` is absent or empty, every tool is allowed.
When set, only listed tool names may run.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def normalize_tool_allowlist(raw: Any) -> frozenset[str] | None:
    """Return a frozenset of tool names, or None when unrestricted."""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray)):
        items = [str(item).strip() for item in raw if str(item).strip()]
    else:
        return None
    if not items:
        return None
    return frozenset(items)


def tool_allowlist_from_config(config: Mapping[str, Any] | None) -> frozenset[str] | None:
    """Extract an optional tool allowlist from an org / runtime config mapping."""
    if not config:
        return None
    return normalize_tool_allowlist(config.get("tool_allowlist"))


def is_tool_allowed(
    tool_name: str,
    config: Mapping[str, Any] | None = None,
    *,
    allowlist: frozenset[str] | None = None,
) -> bool:
    """True if ``tool_name`` may run under the optional org allowlist."""
    name = (tool_name or "").strip()
    if not name:
        return False
    active = allowlist if allowlist is not None else tool_allowlist_from_config(config)
    if active is None:
        return True
    return name in active


def filter_tools(
    tool_names: Iterable[str],
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Keep only tools permitted by the org policy."""
    allowlist = tool_allowlist_from_config(config)
    return [name for name in tool_names if is_tool_allowed(name, allowlist=allowlist)]
