"""Plugin pack manager: install, list, enable/disable, uninstall."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from loguru import logger

from navin.utils.proc import no_window_kwargs

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_NPX_SPEC_RE = re.compile(
    r"^(@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?$"
)
_STATE_FILE = "state.json"
_GIT_CLONE_TIMEOUT_S = 120
_NPX_PACK_TIMEOUT_S = 180
_MAX_SKILLS_PER_PLUGIN = 200
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_SKIP_UPLOAD_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist"}


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


def _normalize_npx_spec(value: str) -> str:
    spec = value.strip()
    if spec.lower().startswith("npx "):
        spec = spec[4:].strip()
    if spec.lower().startswith("npm "):
        raise PluginError("use a package name such as @org/my-skill, not an npm command")
    if not spec or not _NPX_SPEC_RE.match(spec):
        raise PluginError("npx package must look like name, @scope/name or name@version")
    return spec


def _safe_relpath(value: str) -> str | None:
    cleaned = value.replace("\\", "/").strip().lstrip("/")
    if not cleaned:
        return None
    parts = Path(cleaned).parts
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _bundle_root(extracted: Path) -> Path:
    """If the archive has a single top-level folder, use that as the pack root."""
    visible = [p for p in extracted.iterdir() if not p.name.startswith(".")]
    if (
        len(visible) == 1
        and visible[0].is_dir()
        and visible[0].name not in {"skills"}
    ):
        return visible[0]
    return extracted


def _extract_archive(archive: Path, dest: Path) -> None:
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                rel = _safe_relpath(info.filename)
                if rel is None or info.is_dir():
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
        return
    if name.endswith(".tgz") or name.endswith(".tar.gz") or name.endswith(".tar"):
        mode = "r:gz" if name.endswith((".tgz", ".tar.gz")) else "r"
        with tarfile.open(archive, mode) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                rel = _safe_relpath(member.name)
                if rel is None:
                    continue
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(extracted.read())
        return
    raise PluginError("upload a .zip, .tgz or a folder of skills")


def _rmtree_force(path: Path, *, ignore_errors: bool = True) -> None:
    """Delete a tree that may contain read-only files.

    Git for Windows marks everything under ``.git/objects`` read-only, and
    ``os.unlink`` refuses those, so a plugin cloned from a repository could be
    installed but never removed.
    """

    def _clear_readonly(func: Any, target: str, _exc: BaseException) -> None:
        with suppress(OSError):
            os.chmod(target, stat.S_IWRITE)
            func(target)

    try:
        shutil.rmtree(path, onexc=_clear_readonly)
    except OSError:
        if not ignore_errors:
            raise


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

    def _reuse_if_installed(self, plugin_name: str) -> dict[str, Any] | None:
        """Same pack, other project: skip a second clone and just republish."""
        target = self.root / plugin_name
        if target.is_dir():
            return self.get(plugin_name)
        return None

    # -- install / uninstall ---------------------------------------------------

    def install_from_path(self, source: str | Path, *, name: str | None = None) -> dict[str, Any]:
        """Install a plugin by copying a local directory into the plugins root."""
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise PluginError(f"not a directory: {src}")
        manifest = self._manifest(src)
        plugin_name = _normalize_name(name or str(manifest.get("name") or src.name))
        reused = self._reuse_if_installed(plugin_name)
        if reused is not None:
            return reused
        target = self.root / plugin_name
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        self._coerce_skill_bundle(target)
        self._validate_bundle(target)
        self._set_state(plugin_name, enabled=True, source=str(src))
        logger.info("Plugin installed: {} (from {})", plugin_name, src)
        return self.get(plugin_name)

    def install_from_git(self, url: str, *, name: str | None = None) -> dict[str, Any]:
        """Install a plugin by shallow-cloning a git repository."""
        url = url.strip()
        if not re.match(r"^(https://|git@|ssh://)", url):
            raise PluginError("git url must start with https://, git@ or ssh://")
        plugin_name = _normalize_name(name or url.rstrip("/").rsplit("/", 1)[-1])
        reused = self._reuse_if_installed(plugin_name)
        if reused is not None:
            return reused
        target = self.root / plugin_name
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".clone-{plugin_name}"
        if tmp.exists():
            shutil.rmtree(tmp)
        try:
            proc = subprocess.run(  # noqa: S603
                ["git", "clone", "--depth", "1", url, str(tmp)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_GIT_CLONE_TIMEOUT_S,
                check=False,
                **no_window_kwargs(),
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-400:]
                raise PluginError(f"git clone failed: {detail}")
            self._coerce_skill_bundle(tmp)
            self._validate_bundle(tmp)
            _rmtree_force(tmp / ".git")
            tmp.rename(target)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PluginError(f"install failed: {exc}") from exc
        finally:
            if tmp.exists():
                _rmtree_force(tmp)
        self._set_state(plugin_name, enabled=True, source=url)
        logger.info("Plugin installed: {} (from {})", plugin_name, url)
        return self.get(plugin_name)

    def install_from_npx(self, spec: str, *, name: str | None = None) -> dict[str, Any]:
        """Install a skill pack published as an npm package (``npm pack``, no exec)."""
        package = _normalize_npx_spec(spec)
        plugin_name = _normalize_name(name or package.split("/")[-1].split("@")[0] or package)
        reused = self._reuse_if_installed(plugin_name)
        if reused is not None:
            return reused
        target = self.root / plugin_name
        tmp = Path(tempfile.mkdtemp(prefix="navin-npx-", dir=str(self.root) if self.root.is_dir() else None))
        try:
            proc = subprocess.run(  # noqa: S603
                ["npm", "pack", package, "--pack-destination", str(tmp)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_NPX_PACK_TIMEOUT_S,
                check=False,
                **no_window_kwargs(),
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()[-400:]
                raise PluginError(f"npm pack failed: {detail or package}")
            archives = sorted(tmp.glob("*.tgz")) + sorted(tmp.glob("*.tar.gz"))
            if not archives:
                raise PluginError(f"npm pack produced no archive for {package}")
            extracted = tmp / "extracted"
            extracted.mkdir()
            _extract_archive(archives[0], extracted)
            inner = _bundle_root(extracted)
            return self.install_from_path(inner, name=plugin_name)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PluginError(f"npx install failed: {exc}") from exc
        finally:
            _rmtree_force(tmp)

    def install_from_archive(
        self,
        data: bytes,
        *,
        filename: str = "pack.zip",
        name: str | None = None,
    ) -> dict[str, Any]:
        """Install a skill pack from a zip or tarball uploaded by the user."""
        if not data:
            raise PluginError("uploaded archive is empty")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise PluginError("uploaded archive is too large (max 8 MB)")
        tmp = Path(tempfile.mkdtemp(prefix="navin-upload-"))
        try:
            archive = tmp / Path(filename or "pack.zip").name
            archive.write_bytes(data)
            extracted = tmp / "extracted"
            extracted.mkdir()
            _extract_archive(archive, extracted)
            inner = _bundle_root(extracted)
            return self.install_from_path(inner, name=name)
        finally:
            _rmtree_force(tmp)

    def install_from_files(
        self,
        files: list[tuple[str, bytes]],
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Install a skill pack from a folder picker (relative paths + bytes)."""
        if not files:
            raise PluginError("no files in the uploaded folder")
        total = 0
        tmp = Path(tempfile.mkdtemp(prefix="navin-folder-"))
        try:
            for raw_path, payload in files:
                rel = _safe_relpath(raw_path)
                if rel is None:
                    continue
                if any(part in _SKIP_UPLOAD_DIRS for part in Path(rel).parts):
                    continue
                total += len(payload)
                if total > _MAX_UPLOAD_BYTES:
                    raise PluginError("uploaded folder is too large (max 8 MB)")
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(payload)
            inner = _bundle_root(tmp)
            return self.install_from_path(inner, name=name)
        finally:
            _rmtree_force(tmp)

    @staticmethod
    def _coerce_skill_bundle(plugin_dir: Path) -> None:
        """A folder with SKILL.md at the root is a single skill, wrap it."""
        skill_md = plugin_dir / "SKILL.md"
        skills_dir = plugin_dir / "skills"
        if not skill_md.is_file() or skills_dir.is_dir():
            return
        dest = plugin_dir / "skills" / _normalize_name(plugin_dir.name)
        dest.mkdir(parents=True, exist_ok=True)
        skill_md.rename(dest / "SKILL.md")

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
        try:
            _rmtree_force(target, ignore_errors=False)
        except OSError as exc:
            raise PluginError(f"uninstall failed: {exc}") from exc
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
