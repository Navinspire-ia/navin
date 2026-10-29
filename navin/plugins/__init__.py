# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin plugin packs: self-contained bundles of skills, MCP servers, agents, commands and hooks.

A plugin is a directory under ``~/.navin/plugins/<name>/`` containing any of:

- ``plugin.json`` (or ``.claude-plugin/plugin.json``) - manifest (name, version,
  description, author, homepage, optional agents/commands/hooks/mcpServers)
- ``skills/<skill>/SKILL.md`` - skills loaded next to builtin/workspace skills
- ``agents/*.md``, ``commands/*.md``, ``hooks/hooks.json`` - subagents, slash
  commands (``/plugin:name``) and lifecycle hooks
- ``mcp.json`` (or ``.mcp.json``) - ``{"mcpServers": {...}}`` merged into the
  tools config (commands can use npx, uvx, docker, any binary)

Plugins written for Claude Code install unchanged. Install from a local
directory, a git repository (``owner/repo`` works), an npm package or a
marketplace (``plugin@marketplace``, see :mod:`navin.plugins.marketplace`);
enable/disable without uninstalling. Everything hot-reloads: skills are
rescanned on every listing and MCP servers reconnect through the runtime
reload channel.
"""

from navin.plugins.manager import (
    PluginError,
    PluginManager,
    enabled_plugin_skill_dirs,
    plugins_root,
)
from navin.plugins.marketplace import MarketplaceRegistry, install_from_marketplace

__all__ = [
    "MarketplaceRegistry",
    "PluginError",
    "PluginManager",
    "enabled_plugin_skill_dirs",
    "install_from_marketplace",
    "plugins_root",
]
