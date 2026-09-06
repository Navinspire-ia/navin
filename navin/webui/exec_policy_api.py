"""WebUI API for the agent exec permission policy (allow / deny rules).

The graphical permissions panel edits ``tools.exec.allow_patterns`` /
``tools.exec.deny_patterns`` and ``tools.restrict_to_workspace`` in
``~/.navin/config.json``. Changes are hot-applied to the running agent
loop through the runtime-control channel (same mechanism as MCP reload).

Rules can be written either as plain command prefixes (``git push``) or
as regexes (``docker\\s+rm.*``); prefixes are compiled to anchored regexes
before being persisted so the ExecTool matching semantics stay unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from navin.agent.tools.shell import BUILTIN_DENY_RULES
from navin.config.loader import load_config, save_config

MAX_RULES = 100
MAX_RULE_LENGTH = 300
APPROVAL_MODES = frozenset({"autonomous", "risky", "always"})

_REGEX_HINT_CHARS = set("\\^$.|?*+()[]{}")


class ExecPolicyError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _looks_like_regex(rule: str) -> bool:
    return any(ch in _REGEX_HINT_CHARS for ch in rule)


def normalize_rule(raw: str, *, kind: str) -> str:
    """Turn a UI rule into a valid pattern for ExecTool.

    - Plain text ("git push") becomes a prefix regex.
    - Regex-looking input is validated and kept as-is.
    - ``allow`` rules are matched with ``re.fullmatch`` by ExecTool, so a
      plain prefix gets a trailing ``.*``; ``deny`` rules use ``re.search``.
    """
    rule = raw.strip()
    if not rule:
        raise ExecPolicyError("empty rule")
    if len(rule) > MAX_RULE_LENGTH:
        raise ExecPolicyError("rule too long")
    if not _looks_like_regex(rule):
        escaped = re.escape(rule.lower()).replace(r"\ ", r"\s+")
        rule = rf"{escaped}.*" if kind == "allow" else rf"(?:^|[;&|]\s*){escaped}\b"
    try:
        re.compile(rule)
    except re.error as exc:
        raise ExecPolicyError(f"invalid pattern: {exc}") from exc
    return rule


def _clean_rules(value: Any, *, kind: str) -> list[str]:
    if not isinstance(value, list):
        raise ExecPolicyError(f"{kind}_patterns must be a list")
    if len(value) > MAX_RULES:
        raise ExecPolicyError(f"too many {kind} rules (max {MAX_RULES})")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ExecPolicyError("rules must be strings")
        normalized = normalize_rule(item, kind=kind)
        if normalized not in out:
            out.append(normalized)
    return out


def read_approval_mode(config: Any) -> str:
    """Map stored knobs to the Settings > Security confirmation control."""
    if not config.tools.approvals.enabled:
        return "autonomous"
    if getattr(config.tools.approvals, "exec_ask", "destructive") == "always":
        return "always"
    return "risky"


def apply_approval_mode(config: Any, mode: str) -> None:
    """Persist the confirmation posture without wiping custom allow/deny lists."""
    if mode not in APPROVAL_MODES:
        raise ExecPolicyError("approval_mode must be autonomous, risky, or always")
    if mode == "autonomous":
        config.tools.approvals.enabled = False
        config.tools.approvals.exec_ask = "destructive"
        config.tools.security_profile = "autonomous"
        return
    config.tools.approvals.enabled = True
    config.tools.approvals.remember = True
    if mode == "always":
        config.tools.approvals.exec_ask = "always"
    else:
        config.tools.approvals.exec_ask = "destructive"
        config.tools.exec.builtin_deny_rules = True
    if config.tools.security_profile in (None, "autonomous"):
        config.tools.security_profile = "assisted"


def exec_policy_payload(config: Any | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    return {
        "exec_enabled": bool(cfg.tools.exec.enable),
        "restrict_to_workspace": bool(cfg.tools.restrict_to_workspace),
        "allow_patterns": list(cfg.tools.exec.allow_patterns),
        "deny_patterns": list(cfg.tools.exec.deny_patterns),
        "builtin_deny": [dict(rule) for rule in BUILTIN_DENY_RULES],
        # The ready-made set is off unless asked for, so the panel needs the
        # switch and not just the list of what the switch would turn on.
        "builtin_deny_enabled": bool(cfg.tools.exec.builtin_deny_rules),
        "approval_mode": read_approval_mode(cfg),
    }


def update_exec_policy(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist the exec policy; returns the fresh payload."""
    config = load_config()

    if "allow_patterns" in data:
        config.tools.exec.allow_patterns = _clean_rules(data["allow_patterns"], kind="allow")
    if "deny_patterns" in data:
        config.tools.exec.deny_patterns = _clean_rules(data["deny_patterns"], kind="deny")
    if "restrict_to_workspace" in data:
        if not isinstance(data["restrict_to_workspace"], bool):
            raise ExecPolicyError("restrict_to_workspace must be a boolean")
        config.tools.restrict_to_workspace = data["restrict_to_workspace"]
    if "exec_enabled" in data:
        if not isinstance(data["exec_enabled"], bool):
            raise ExecPolicyError("exec_enabled must be a boolean")
        config.tools.exec.enable = data["exec_enabled"]
    if "builtin_deny_enabled" in data:
        if not isinstance(data["builtin_deny_enabled"], bool):
            raise ExecPolicyError("builtin_deny_enabled must be a boolean")
        config.tools.exec.builtin_deny_rules = data["builtin_deny_enabled"]
    if "approval_mode" in data:
        if not isinstance(data["approval_mode"], str):
            raise ExecPolicyError("approval_mode must be a string")
        apply_approval_mode(config, data["approval_mode"].strip().lower())

    save_config(config)
    return exec_policy_payload(config)
