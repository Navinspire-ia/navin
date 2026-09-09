# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI HTTP API helpers for plugin packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.skills import clear_skills_index_cache
from navin.plugins import PluginError, PluginManager


class PluginsApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def webui_plugins_payload() -> dict[str, Any]:
    """List installed plugin packs."""
    return {"plugins": PluginManager().list()}


def install_plugin(
    *,
    source: str,
    location: str = "",
    name: str | None = None,
    archive: bytes | None = None,
    filename: str | None = None,
    files: list[tuple[str, bytes]] | None = None,
    scope: str = "workspace",
    workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Install a skill pack from git, a local path, npm, or an uploaded folder."""
    from navin.utils.host import normalize_host_path
    from navin.webui.skills_api import publish_plugin_skills, resolve_skill_apply_root

    manager = PluginManager()
    try:
        if source == "git":
            plugin = manager.install_from_git(location, name=name or None)
        elif source == "path":
            try:
                resolved = normalize_host_path(location)
            except ValueError as exc:
                raise PluginsApiError("folder path is invalid") from exc
            plugin = manager.install_from_path(resolved, name=name or None)
        elif source == "npx":
            plugin = manager.install_from_npx(location, name=name or None)
        elif source == "upload":
            if files:
                plugin = manager.install_from_files(files, name=name or None)
            elif archive:
                plugin = manager.install_from_archive(
                    archive,
                    filename=filename or "pack.zip",
                    name=name or None,
                )
            else:
                raise PluginsApiError("upload a folder or a .zip / .tgz archive")
        else:
            raise PluginsApiError("source must be 'git', 'path', 'npx' or 'upload'")
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    apply_root = resolve_skill_apply_root(
        scope=scope,
        workspace=workspace_path,
        fallback=Path.home(),
    )
    copied = publish_plugin_skills(plugin["name"], apply_root, plugins_dir=manager.root)
    clear_skills_index_cache()
    from navin import workspace_layout
    from navin.webui.skills_api import skill_previews_from_dest

    dest = workspace_layout.coalesce_owned_skills(apply_root)
    preview_names = copied or list(plugin.get("skills") or [])
    return {
        "ok": True,
        "plugin": plugin,
        "mcp_changed": changed,
        "copied": copied,
        "dest": str(dest),
        "previews": skill_previews_from_dest(dest, preview_names),
        **webui_plugins_payload(),
    }


def remove_plugin(name: str) -> dict[str, Any]:
    manager = PluginManager()
    try:
        manager.uninstall(name)
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    clear_skills_index_cache()
    return {"ok": True, "mcp_changed": changed, **webui_plugins_payload()}


def set_plugin_enabled(name: str, enabled: bool) -> dict[str, Any]:
    manager = PluginManager()
    try:
        plugin = manager.set_enabled(name, enabled)
        changed = manager.sync_mcp_servers()
    except PluginError as exc:
        raise PluginsApiError(str(exc)) from exc
    clear_skills_index_cache()
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
