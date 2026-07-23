"""Plugin pack manager: install, list, enable/disable, uninstall."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_STATE_FILE = "state.json"
_GIT_CLONE_TIMEOUT_S = 120
_MAX_SKILLS_PER_PLUGIN = 200


class PluginError(ValueError):
    pass


def plugins_root() -> Path:
    """Directory holding installed plugin packs."""
    from navin.config.loader import get_config_path

    return get_config_path().parent / "plugins"


def _normalize_name(value: str) -> str:
    name = value.strip().lower()
    if name.endswith(".git"):
        name = name[:-4]
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-.")
    if not name or not _NAME_RE.match(name):
        raise PluginError(f"invalid plugin name: {value!r}")
    return name


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise PluginError(f"unreadable JSON: {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise PluginError(f"{path.name} must contain a JSON object")
    return data


def enabled_plugin_skill_dirs() -> list[tuple[str, Path]]:
    """Return ``(plugin_name, skills_dir)`` for every enabled plugin.

    Called by SkillsLoader on each listing, so newly installed plugins are
    picked up without restarting the gateway.
    """
    root = plugins_root()
    if not root.is_dir():
        return []
    try:
        manager = PluginManager(root)
        rows = manager.list()
    except Exception:  # never break skill listing on a corrupt plugin
        return []
    dirs: list[tuple[str, Path]] = []
    for row in rows:
        if not row["enabled"]:
            continue
        skills_dir = root / row["name"] / "skills"
        if skills_dir.is_dir():
            dirs.append((row["name"], skills_dir))
    return dirs


class PluginManager:
    """File-backed manager for plugin packs."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or plugins_root()

    # -- state ---------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.root / _STATE_FILE

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path.is_file():
            return {}
        try:
            return _read_json(path)
        except PluginError:
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    # -- introspection -------------------------------------------------------

    def _plugin_dirs(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(
            p for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    @staticmethod
    def _manifest(plugin_dir: Path) -> dict[str, Any]:
        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.is_file():
            return {}
        try:
            return _read_json(manifest_path)
        except PluginError:
            return {}

    @staticmethod
    def _skill_names(plugin_dir: Path) -> list[str]:
        skills_dir = plugin_dir / "skills"
        if not skills_dir.is_dir():
            return []
        names = [
            entry.name
            for entry in sorted(skills_dir.iterdir())
            if entry.is_dir() and (entry / "SKILL.md").is_file()
        ]
        return names[:_MAX_SKILLS_PER_PLUGIN]

    @staticmethod
    def _mcp_servers(plugin_dir: Path) -> dict[str, Any]:
        mcp_path = plugin_dir / "mcp.json"
        if not mcp_path.is_file():
            return {}
        data = _read_json(mcp_path)
        servers = data.get("mcpServers", data)
        if not isinstance(servers, dict):
            raise PluginError("mcp.json must contain an 'mcpServers' object")
        return servers

    def list(self) -> list[dict[str, Any]]:
        """List installed plugins with manifest info and component counts."""
        state = self._load_state()
        rows: list[dict[str, Any]] = []
        for plugin_dir in self._plugin_dirs():
            name = plugin_dir.name
            manifest = self._manifest(plugin_dir)
            skills = self._skill_names(plugin_dir)
            try:
                mcp = self._mcp_servers(plugin_dir)
            except PluginError:
                mcp = {}
            rows.append({
                "name": name,
                "display_name": str(manifest.get("displayName") or manifest.get("name") or name),
                "version": str(manifest.get("version") or ""),
                "description": str(manifest.get("description") or ""),
                "author": str(
                    (manifest.get("author") or {}).get("name", "")
                    if isinstance(manifest.get("author"), dict)
                    else manifest.get("author") or ""
                ),
                "homepage": str(manifest.get("homepage") or ""),
                "source": str(state.get(name, {}).get("source") or ""),
                "enabled": bool(state.get(name, {}).get("enabled", True)),
                "skills": skills,
                "mcp_servers": sorted(mcp.keys()),
            })
        return rows

    def get(self, name: str) -> dict[str, Any]:
        name = _normalize_name(name)
        for row in self.list():
            if row["name"] == name:
                return row
        raise PluginError(f"plugin not found: {name}")

    # -- install / uninstall ---------------------------------------------------

    def install_from_path(self, source: str | Path, *, name: str | None = None) -> dict[str, Any]:
        """Install a plugin by copying a local directory into the plugins root."""
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise PluginError(f"not a directory: {src}")
        manifest = self._manifest(src)
        plugin_name = _normalize_name(name or str(manifest.get("name") or src.name))
        self._validate_bundle(src)
        target = self.root / plugin_name
        if target.exists():
            raise PluginError(f"plugin already installed: {plugin_name}")
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        self._set_state(plugin_name, enabled=True, source=str(src))
        logger.info("Plugin installed: {} (from {})", plugin_name, src)
        return self.get(plugin_name)

    def install_from_git(self, url: str, *, name: str | None = None) -> dict[str, Any]:
        """Install a plugin by shallow-cloning a git repository."""
        url = url.strip()
        if not re.match(r"^(https://|git@|ssh://)", url):
            raise PluginError("git url must start with https://, git@ or ssh://")
        plugin_name = _normalize_name(name or url.rstrip("/").rsplit("/", 1)[-1])
        target = self.root / plugin_name
        if target.exists():
            raise PluginError(f"plugin already installed: {plugin_name}")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".clone-{plugin_name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        try:
            proc = subprocess.run(  # noqa: S603
                ["git", "clone", "--depth", "1", url, str(tmp)],
                capture_output=True,
                text=True,
                timeout=_GIT_CLONE_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-400:]
                raise PluginError(f"git clone failed: {detail}")
            self._validate_bundle(tmp)
            shutil.rmtree(tmp / ".git", ignore_errors=True)
            tmp.rename(target)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PluginError(f"install failed: {exc}") from exc
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        self._set_state(plugin_name, enabled=True, source=url)
        logger.info("Plugin installed: {} (from {})", plugin_name, url)
        return self.get(plugin_name)

    def _validate_bundle(self, plugin_dir: Path) -> None:
        """A plugin must ship at least one component and parse cleanly."""
        skills = self._skill_names(plugin_dir)
        mcp = self._mcp_servers(plugin_dir)  # raises on malformed mcp.json
        if not skills and not mcp:
            raise PluginError(
                "plugin has no components: expected skills/<name>/SKILL.md or mcp.json"
            )

    def uninstall(self, name: str) -> None:
        name = _normalize_name(name)
        target = self.root / name
        if not target.is_dir():
            raise PluginError(f"plugin not found: {name}")
        shutil.rmtree(target)
        state = self._load_state()
        state.pop(name, None)
        self._save_state(state)
        logger.info("Plugin uninstalled: {}", name)

    # -- enable / disable -------------------------------------------------------

    def _set_state(self, name: str, *, enabled: bool, source: str | None = None) -> None:
        state = self._load_state()
        entry = state.get(name) or {}
        entry["enabled"] = enabled
        if source is not None:
            entry["source"] = source
        state[name] = entry
        self._save_state(state)

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        name = _normalize_name(name)
        if not (self.root / name).is_dir():
            raise PluginError(f"plugin not found: {name}")
        self._set_state(name, enabled=enabled)
        return self.get(name)

    # -- MCP integration --------------------------------------------------------

    def mcp_server_names(self, name: str) -> list[str]:
        """Prefixed MCP server names contributed by a plugin."""
        plugin_dir = self.root / _normalize_name(name)
        return [f"{plugin_dir.name}-{server}" for server in self._mcp_servers(plugin_dir)]

    def sync_mcp_servers(self) -> bool:
        """Merge MCP servers from enabled plugins into the tools config.

        Plugin servers are namespaced ``<plugin>-<server>``. Servers from
        disabled or removed plugins are dropped. Returns True when the
        config changed (caller should hot-reload MCP connections).
        """
        from navin.config.loader import load_config, save_config
        from navin.config.schema import MCPServerConfig

        config = load_config()
        existing = config.tools.mcp_servers
        # Server keys previously written by the plugin system (survives uninstall).
        state = self._load_state()
        managed = set(state.get("__managed_mcp__") or [])

        desired: dict[str, MCPServerConfig] = {}
        for row in self.list():
            if not row["enabled"]:
                continue
            plugin_dir = self.root / row["name"]
            try:
                servers = self._mcp_servers(plugin_dir)
            except PluginError:
                continue
            for server_name, server_conf in servers.items():
                if not isinstance(server_conf, dict):
                    continue
                key = f"{row['name']}-{server_name}"
                try:
                    desired[key] = MCPServerConfig.model_validate(server_conf)
                except ValueError as exc:
                    logger.warning("Plugin {} MCP server {} invalid: {}", row["name"], server_name, exc)

        changed = False
        # Remove stale plugin-managed servers (disabled or uninstalled packs).
        for key in list(existing):
            if key in managed and key not in desired:
                del existing[key]
                changed = True
        # Add / update desired servers.
        for key, conf in desired.items():
            if key not in existing or existing[key].model_dump() != conf.model_dump():
                existing[key] = conf
                changed = True

        if changed:
            save_config(config)
        if managed != set(desired):
            state["__managed_mcp__"] = sorted(desired)
            self._save_state(state)
        return changed
