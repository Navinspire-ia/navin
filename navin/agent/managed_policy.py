# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Organization tier: managed settings deployed by an administrator.

The file sits above every personal, project and plugin setting. Lookup order
(first existing file wins):

1. ``NAVIN_MANAGED_SETTINGS`` (an explicit path, for tests and MDM wrappers)
2. ``/etc/navin/managed-settings.json`` (Linux and other POSIX)
3. ``/Library/Application Support/Navin/managed-settings.json`` (macOS)
4. ``%ProgramData%\\Navin\\managed-settings.json`` (Windows)

Keys::

    {
      "hooks": {...},                    # same shape as hooks.json, always run first
      "allowManagedHooksOnly": true,     # drop personal, project and plugin hooks
      "allowManagedPluginsOnly": true,   # only plugins in allowedPlugins load
      "allowedPlugins": ["linter"],
      "mcpAllowlist": ["github"],        # MCP server names allowed (others refused)
      "env": {"HTTPS_PROXY": "..."},     # pinned variables, nobody below overrides
      "permissions": {"deny": ["browser"], "allow": []},
      "disableBypassPermissions": true
    }

Fail closed: a file that exists but cannot be read or parsed puts a lockdown
policy in force (managed hooks only, no plugins, no MCP, risky tools denied)
and says so in ``summary()``. No file at all means no behaviour change.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.enterprise.policy import ToolPolicy

ENV_PATH = "NAVIN_MANAGED_SETTINGS"

# Variables that change where policy is read from; pinned for every child.
POLICY_SOURCE_VARS = (ENV_PATH, "ProgramData", "PROGRAMDATA")

# Denied when the managed file is corrupt: anything that runs code, writes,
# or reaches the network.
LOCKDOWN_DENY = (
    "exec", "shell", "bash", "write_file", "edit_file", "apply_patch", "manage_files",
    "notebook_edit", "browser", "web_fetch", "web_search", "spawn", "mobile", "project",
    "start_app", "git", "forge", "pr_comments", "exec_remote", "infra", "appfactory",
)


@dataclass(frozen=True)
class ManagedPolicy:
    path: str = ""
    error: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    allow_managed_hooks_only: bool = False
    allow_managed_plugins_only: bool = False
    allowed_plugins: frozenset[str] = frozenset()
    mcp_allowlist: frozenset[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    tools: ToolPolicy = field(default_factory=ToolPolicy)
    disable_bypass_permissions: bool = False

    @property
    def active(self) -> bool:
        return bool(self.path)

    @property
    def locked_down(self) -> bool:
        return bool(self.error)


NONE = ManagedPolicy()


def candidate_paths() -> list[Path]:
    """Every place a managed file may live, in lookup order."""
    out: list[Path] = []
    explicit = os.environ.get(ENV_PATH, "").strip()
    if explicit:
        out.append(Path(explicit).expanduser())
    if sys.platform == "win32":
        base = os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        out.append(Path(base) / "Navin" / "managed-settings.json")
    elif sys.platform == "darwin":
        out.append(Path("/Library/Application Support/Navin/managed-settings.json"))
        out.append(Path("/etc/navin/managed-settings.json"))
    else:
        out.append(Path("/etc/navin/managed-settings.json"))
    return out


def find_file() -> Path | None:
    for path in candidate_paths():
        try:
            if path.exists():
                return path
        except OSError:
            return path  # cannot even stat it: treat as present, fail closed
    return None


def _flag(value: Any) -> bool:
    # Anything but an explicit false/absent locks: a mistyped "true" or 1 still counts.
    return value is not None and value is not False and value != "false"


def _names(value: Any, key: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return frozenset(v.strip().lower() for v in value if v.strip())


def parse(data: Any, path: str) -> ManagedPolicy:
    """Validate a decoded managed file; raises ValueError on a bad schema."""
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be an object")
    env = data.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) for k in env):
        raise ValueError("'env' must be an object of strings")
    perms = data.get("permissions", {})
    if not isinstance(perms, dict):
        raise ValueError("'permissions' must be an object")
    mcp = data.get("mcpAllowlist")
    return ManagedPolicy(
        path=path,
        hooks=hooks,
        allow_managed_hooks_only=_flag(data.get("allowManagedHooksOnly")),
        allow_managed_plugins_only=_flag(data.get("allowManagedPluginsOnly")),
        allowed_plugins=_names(data.get("allowedPlugins"), "allowedPlugins"),
        mcp_allowlist=None if mcp is None else _names(mcp, "mcpAllowlist"),
        env={k: str(v) for k, v in env.items()},
        tools=ToolPolicy.from_config({
            "allow": list(_names(perms.get("allow"), "permissions.allow")),
            "deny": list(_names(perms.get("deny"), "permissions.deny")),
        }),
        disable_bypass_permissions=_flag(data.get("disableBypassPermissions")),
    )


def lockdown(path: str, error: str) -> ManagedPolicy:
    return ManagedPolicy(
        path=path,
        error=error,
        allow_managed_hooks_only=True,
        allow_managed_plugins_only=True,
        mcp_allowlist=frozenset(),
        tools=ToolPolicy.from_config({"deny": list(LOCKDOWN_DENY)}),
        disable_bypass_permissions=True,
    )


_CACHE: dict[str, Any] = {"key": None, "policy": NONE}


def load() -> ManagedPolicy:
    """The policy in force, cached by path and mtime."""
    path = find_file()
    if path is None:
        _CACHE.update(key=None, policy=NONE)
        return NONE
    try:
        stat = path.stat()
        key: tuple[Any, ...] = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        key = (str(path), "unreadable", str(exc))
    if _CACHE["key"] == key:
        return _CACHE["policy"]
    try:
        policy = parse(json.loads(path.read_text(encoding="utf-8")), str(path))
    except (OSError, ValueError) as exc:
        logger.error("Managed settings {} unusable, lockdown in force: {}", path, exc)
        policy = lockdown(str(path), str(exc))
    _CACHE.update(key=key, policy=policy)
    return policy


# -- helpers used by the wiring points -----------------------------------------

def tool_denied(tool_name: str) -> str | None:
    """Refusal reason when the organization forbids *tool_name*, else None."""
    policy = load()
    if not policy.active or policy.tools.is_allowed(tool_name):
        return None
    why = "managed settings unreadable, lockdown" if policy.locked_down else "permissions"
    return f"{tool_name} is blocked by your organization's policy ({why})."


def plugin_allowed(name: str) -> bool:
    policy = load()
    if not policy.allow_managed_plugins_only:
        return True
    return name.strip().lower() in policy.allowed_plugins


def mcp_server_allowed(name: str) -> bool:
    allow = load().mcp_allowlist
    return allow is None or name.strip().lower() in allow


def pinned_env() -> dict[str, str]:
    """Variables no lower tier may change (policy-source vars included)."""
    policy = load()
    if not policy.active:
        return {}
    out = dict(policy.env)
    out[ENV_PATH] = policy.path
    for var in POLICY_SOURCE_VARS[1:]:
        if var in os.environ:
            out.setdefault(var, os.environ[var])
    return out


def apply_env(env: dict[str, str]) -> dict[str, str]:
    """Overlay pinned variables onto *env* (in place) and return it."""
    env.update(pinned_env())
    return env


def summary() -> str:
    """One line for the system prompt, '' when no managed file exists."""
    policy = load()
    if not policy.active:
        return ""
    if policy.locked_down:
        return (f"- Organization policy: managed settings at {policy.path} are invalid ({policy.error}); "
                "lockdown in force: risky tools, plugins and MCP servers are refused.")
    parts = []
    if policy.hooks:
        parts.append("managed hooks")
    if policy.allow_managed_hooks_only:
        parts.append("managed hooks only")
    if policy.allow_managed_plugins_only:
        parts.append(f"{len(policy.allowed_plugins)} allowed plugin(s)")
    if policy.mcp_allowlist is not None:
        parts.append(f"{len(policy.mcp_allowlist)} allowed MCP server(s)")
    if policy.tools.deny:
        parts.append("denied tools: " + ", ".join(sorted(policy.tools.deny)))
    if policy.tools.allow:
        parts.append("tool allowlist")
    if policy.env:
        parts.append(f"{len(policy.env)} pinned env var(s)")
    if policy.disable_bypass_permissions:
        parts.append("no permission bypass")
    return "- Organization policy in force: " + ("; ".join(parts) or "no restrictions") + "."
