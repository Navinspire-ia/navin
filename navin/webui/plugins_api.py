"""WebUI HTTP API helpers for plugin packs."""

from __future__ import annotations

from typing import Any

from loguru import logger

from navin.plugins import PluginError, PluginManager


class PluginsApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def webui_plugins_payload() -> dict[str, Any]:
    """List installed plugin packs."""
    return {"plugins": PluginManager().list()}


def install_plugin(*, source: str, location: str, name: str | None = None) -> dict[str, Any]:
    """Install a plugin from a git URL or a local directory path."""
    manager = PluginManager()
    try:
        if source == "git":
            plugin = manager.install_from_git(location, name=name or None)
        elif source == "path":
            plugin = manager.install_from_path(location, name=name or None)
        else:
            raise PluginsApiError("source must be 'git' or 'path'")
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    return {"ok": True, "plugin": plugin, "mcp_changed": changed, **webui_plugins_payload()}


def remove_plugin(name: str) -> dict[str, Any]:
    manager = PluginManager()
    try:
        manager.uninstall(name)
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    return {"ok": True, "mcp_changed": changed, **webui_plugins_payload()}


def set_plugin_enabled(name: str, enabled: bool) -> dict[str, Any]:
    manager = PluginManager()
    try:
        plugin = manager.set_enabled(name, enabled)
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    return {"ok": True, "plugin": plugin, "mcp_changed": changed, **webui_plugins_payload()}


async def maybe_reload_mcp(payload: dict[str, Any], reload_mcp: Any) -> dict[str, Any]:
    """Hot-reload MCP connections when the plugin mutation touched the config."""
    if not payload.get("mcp_changed") or reload_mcp is None:
        return payload
    try:
        payload["mcp_reload"] = await reload_mcp()
    except Exception as exc:  # reload failure must not mask the mutation result
        logger.warning("MCP hot reload after plugin change failed: {}", exc)
        payload["mcp_reload"] = {"ok": False, "error": str(exc)}
    return payload
