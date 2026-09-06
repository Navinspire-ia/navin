"""Org-role ACL for hybrid collab (admin / member / viewer).

Roles come from the license validate payload (``role``) and are stored on
``config.license.org_role``. Viewers may observe a shared chat but must not
trigger tool execution or approval decisions.
"""

from __future__ import annotations

from typing import Any

ORG_ROLES = ("admin", "member", "viewer")

# Actions that mutate the agent runtime (blocked for viewers).
_TOOL_EXEC_ACTIONS = frozenset(
    {
        "message",
        "approval_decision",
        "choice_answer",
        "terminal_open",
        "terminal_input",
        "mobile_preview_open",
        "mobile_preview_input",
        "agent_browser_input",
    }
)


def normalize_org_role(raw: Any) -> str | None:
    """Return a known org role, or None when unset / unknown."""
    if not isinstance(raw, str):
        return None
    role = raw.strip().lower()
    return role if role in ORG_ROLES else None


def role_from_license(config: Any) -> str | None:
    """Read the persisted org role from a Config / LicenseConfig-like object."""
    lic = getattr(config, "license", config)
    return normalize_org_role(getattr(lic, "org_role", None) or getattr(lic, "role", None))


def can_execute_tools(role: str | None) -> bool:
    """True when the role may start agent turns / tool-driving actions.

    Empty role (solo host, non-team) keeps full access.
    """
    normalized = normalize_org_role(role)
    if normalized is None:
        return True
    return normalized != "viewer"


def can_approve(role: str | None) -> bool:
    """True when the role may answer approval prompts."""
    return can_execute_tools(role)


def is_read_only(role: str | None) -> bool:
    """Viewer (or equivalent) - observe only."""
    return normalize_org_role(role) == "viewer"


def action_allowed(role: str | None, action: str) -> bool:
    """Gate a WS / runtime action name against the org role."""
    if action not in _TOOL_EXEC_ACTIONS:
        return True
    if action in {"approval_decision", "choice_answer"}:
        return can_approve(role)
    return can_execute_tools(role)
