"""Navin plugin packs: self-contained bundles of skills, MCP servers, and commands.

A plugin is a directory under ``~/.navin/plugins/<name>/`` containing any of:

- ``plugin.json``          - manifest (name, version, description, author, homepage)
- ``skills/<skill>/SKILL.md`` - skills loaded next to builtin/workspace skills
- ``mcp.json``             - ``{"mcpServers": {...}}`` merged into the tools config
                             (commands can use npx, uvx, docker, any binary)

Install from a local directory or a git repository, enable/disable without
uninstalling, and everything hot-reloads: skills are rescanned on every listing
and MCP servers reconnect through the runtime reload channel.
"""

from navin.plugins.manager import (
    PluginError,
    PluginManager,
    enabled_plugin_skill_dirs,
    plugins_root,
)

__all__ = [
    "PluginError",
    "PluginManager",
    "enabled_plugin_skill_dirs",
    "plugins_root",
]
