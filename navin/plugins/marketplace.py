# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Plugin marketplaces: catalogs of installable plugins, as Claude Code defines them.

A marketplace is a git repository (or a local folder) holding a
``.claude-plugin/marketplace.json`` (or ``marketplace.json``) catalog::

    {"name": "acme-tools", "owner": {"name": "Acme"},
     "plugins": [{"name": "deploy", "source": "./plugins/deploy",
                  "description": "...", "version": "1.2.0"},
                 {"name": "lint", "source": {"source": "github", "repo": "acme/lint"}},
                 {"name": "docs", "source": "https://github.com/acme/docs.git"}]}

``/plugin marketplace add <owner/repo | git url | path>`` registers one (the
clone lives under ``~/.navin/plugins/.marketplaces/<name>``, a hidden folder
the plugin lister skips), ``/plugin install <plugin>@<marketplace>`` resolves
the plugin's ``source`` to something :class:`PluginManager` installs: a folder
of the marketplace clone, a git URL or a GitHub shorthand.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.plugins.manager import (
    PluginError,
    PluginManager,
    _normalize_name,
    _read_json,
    _rmtree_force,
    _safe_relpath,
    git_url_for,
    plugins_root,
)
from navin.utils.host_env import failure_detail, host_tool_env
from navin.utils.proc import no_window_kwargs

_REGISTRY_FILE = "marketplaces.json"
_CLONES_DIR = ".marketplaces"
_CATALOG_PATHS = (".claude-plugin/marketplace.json", ".navin-plugin/marketplace.json", "marketplace.json")
_GIT_TIMEOUT_S = 120
_SPEC_RE = re.compile(r"^(?P<plugin>[A-Za-z0-9][A-Za-z0-9._-]*)@(?P<market>[A-Za-z0-9][A-Za-z0-9._-]*)$")


@dataclass(frozen=True)
class MarketplacePlugin:
    """One entry of a catalog, with its resolved install source."""

    name: str
    marketplace: str
    description: str = ""
    version: str = ""
    #: ``path`` (a folder of the clone), ``git`` (clonable URL) or ``npx``.
    kind: str = "path"
    location: str = ""
    category: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Marketplace:
    name: str
    source: str
    path: Path
    description: str = ""
    owner: str = ""
    plugins: tuple[MarketplacePlugin, ...] = field(default_factory=tuple)


def parse_spec(spec: str) -> tuple[str, str] | None:
    """``plugin@marketplace`` -> (plugin, marketplace); None for anything else."""
    match = _SPEC_RE.match(spec.strip())
    if match is None:
        return None
    return match.group("plugin"), match.group("market")


def catalog_path(root: Path) -> Path | None:
    for rel in _CATALOG_PATHS:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def _resolve_source(raw: Any, clone_root: Path) -> tuple[str, str]:
    """(kind, location) for a catalog ``source`` value.

    Strings: a relative folder of the marketplace, ``owner/repo`` or a git
    URL. Objects: ``{"source": "github", "repo": "owner/repo"}``,
    ``{"source": "git", "url": ...}``, ``{"source": "npm", "package": ...}``
    and ``{"source": "path", "path": ...}``.
    """
    if isinstance(raw, dict):
        kind = str(raw.get("source") or "").lower()
        if kind == "github" and raw.get("repo"):
            url = git_url_for(str(raw["repo"]))
            if url is None:
                raise PluginError(f"invalid github repo in marketplace: {raw.get('repo')!r}")
            return "git", url
        if kind in ("git", "url") and raw.get("url"):
            url = git_url_for(str(raw["url"]))
            if url is None:
                raise PluginError(f"invalid git url in marketplace: {raw.get('url')!r}")
            return "git", url
        if kind in ("npm", "npx") and raw.get("package"):
            return "npx", str(raw["package"])
        if kind in ("path", "local", "") and raw.get("path"):
            raw = str(raw["path"])
        else:
            raise PluginError(f"unsupported plugin source in marketplace: {json.dumps(raw)[:120]}")
    text = str(raw or "").strip()
    if not text:
        raise PluginError("plugin entry has no source")
    url = git_url_for(text)
    if url is not None and not text.startswith("./"):
        return "git", url
    rel = _safe_relpath(text[2:] if text.startswith("./") else text)
    if rel is None:
        raise PluginError(f"invalid plugin path in marketplace: {text!r}")
    return "path", str(clone_root / rel)


def read_catalog(clone_root: Path, *, marketplace_name: str | None = None) -> Marketplace:
    """Parse the catalog of a marketplace checkout."""
    path = catalog_path(clone_root)
    if path is None:
        raise PluginError(
            "not a plugin marketplace: no .claude-plugin/marketplace.json or marketplace.json found"
        )
    data = _read_json(path)
    name = _normalize_name(str(marketplace_name or data.get("name") or clone_root.name))
    owner = data.get("owner")
    owner_name = str(owner.get("name", "")) if isinstance(owner, dict) else str(owner or "")
    plugins: list[MarketplacePlugin] = []
    for entry in data.get("plugins") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        try:
            plugin_name = _normalize_name(str(entry["name"]))
            kind, location = _resolve_source(entry.get("source"), clone_root)
        except PluginError as exc:
            logger.warning("Marketplace {}: plugin {} skipped: {}", name, entry.get("name"), exc)
            continue
        keywords = entry.get("keywords") if isinstance(entry.get("keywords"), list) else []
        plugins.append(
            MarketplacePlugin(
                name=plugin_name,
                marketplace=name,
                description=str(entry.get("description") or ""),
                version=str(entry.get("version") or ""),
                kind=kind,
                location=location,
                category=str(entry.get("category") or ""),
                keywords=tuple(str(k) for k in keywords),
            )
        )
    return Marketplace(
        name=name,
        source="",
        path=clone_root,
        description=str(data.get("description") or ""),
        owner=owner_name,
        plugins=tuple(plugins),
    )


class MarketplaceRegistry:
    """The marketplaces this machine knows, under the plugins root."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or plugins_root()

    # -- storage -------------------------------------------------------------

    def _registry_path(self) -> Path:
        return self.root / _REGISTRY_FILE

    def _clones_root(self) -> Path:
        return self.root / _CLONES_DIR

    def _load(self) -> dict[str, dict[str, Any]]:
        path = self._registry_path()
        if not path.is_file():
            return {}
        try:
            data = _read_json(path)
        except PluginError:
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._registry_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # -- reading ---------------------------------------------------------------

    def list(self) -> list[Marketplace]:
        out: list[Marketplace] = []
        for name, entry in sorted(self._load().items()):
            clone = Path(str(entry.get("path") or self._clones_root() / name))
            try:
                market = read_catalog(clone, marketplace_name=name)
            except PluginError as exc:
                logger.warning("Marketplace {} unreadable: {}", name, exc)
                market = Marketplace(name=name, source=str(entry.get("source") or ""), path=clone)
            out.append(
                Marketplace(
                    name=market.name,
                    source=str(entry.get("source") or ""),
                    path=clone,
                    description=market.description,
                    owner=market.owner,
                    plugins=market.plugins,
                )
            )
        return out

    def get(self, name: str) -> Marketplace:
        wanted = _normalize_name(name)
        for market in self.list():
            if market.name == wanted:
                return market
        raise PluginError(f"marketplace not found: {wanted} (add it with `/plugin marketplace add <owner/repo|url|path>`)")

    def find_plugin(self, spec: str) -> MarketplacePlugin:
        """``plugin@marketplace`` or ``plugin`` (searched in every marketplace)."""
        parsed = parse_spec(spec)
        if parsed is not None:
            plugin_name, market_name = parsed
            market = self.get(market_name)
            wanted = _normalize_name(plugin_name)
            for plugin in market.plugins:
                if plugin.name == wanted:
                    return plugin
            known = ", ".join(p.name for p in market.plugins) or "none"
            raise PluginError(f"plugin {wanted} not in marketplace {market.name} (available: {known})")
        wanted = _normalize_name(spec)
        hits = [p for m in self.list() for p in m.plugins if p.name == wanted]
        if not hits:
            raise PluginError(f"no marketplace offers a plugin named {wanted}")
        if len(hits) > 1:
            choices = ", ".join(f"{p.name}@{p.marketplace}" for p in hits)
            raise PluginError(f"{wanted} exists in several marketplaces, choose one: {choices}")
        return hits[0]

    # -- mutations ------------------------------------------------------------

    def add(self, source: str, *, name: str | None = None) -> Marketplace:
        """Register a marketplace from ``owner/repo``, a git URL or a local folder."""
        text = source.strip()
        if not text:
            raise PluginError("marketplace source is required: owner/repo, a git url or a folder")
        clone_url = git_url_for(text)
        registry = self._load()
        if clone_url is None:
            local = Path(text).expanduser().resolve()
            if not local.is_dir():
                raise PluginError(f"not a folder and not a git source: {text}")
            market = read_catalog(local, marketplace_name=name)
            if market.name in registry:
                raise PluginError(f"marketplace {market.name} is already registered")
            registry[market.name] = {"source": str(local), "path": str(local), "kind": "path"}
            self._save(registry)
            logger.info("Marketplace registered: {} (folder {})", market.name, local)
            return self.get(market.name)
        tmp = self._clones_root() / f".adding-{_normalize_name(name or text.rsplit('/', 1)[-1])}"
        if tmp.exists():
            _rmtree_force(tmp)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            _git(["clone", "--depth", "1", clone_url, str(tmp)])
            market = read_catalog(tmp, marketplace_name=name)
            if market.name in registry:
                raise PluginError(f"marketplace {market.name} is already registered")
            final = self._clones_root() / market.name
            if final.exists():
                _rmtree_force(final)
            tmp.rename(final)
        finally:
            if tmp.exists():
                _rmtree_force(tmp)
        registry[market.name] = {"source": text, "path": str(final), "kind": "git", "url": clone_url}
        self._save(registry)
        logger.info("Marketplace registered: {} ({})", market.name, clone_url)
        return self.get(market.name)

    def remove(self, name: str) -> None:
        wanted = _normalize_name(name)
        registry = self._load()
        entry = registry.pop(wanted, None)
        if entry is None:
            raise PluginError(f"marketplace not found: {wanted}")
        self._save(registry)
        if entry.get("kind") == "git":
            _rmtree_force(Path(str(entry.get("path") or self._clones_root() / wanted)))
        logger.info("Marketplace removed: {}", wanted)

    def update(self, name: str | None = None) -> list[str]:
        """Refresh the git clones (all, or one); returns the names refreshed."""
        registry = self._load()
        names = [_normalize_name(name)] if name else sorted(registry)
        refreshed: list[str] = []
        for market_name in names:
            entry = registry.get(market_name)
            if entry is None:
                raise PluginError(f"marketplace not found: {market_name}")
            if entry.get("kind") != "git":
                refreshed.append(market_name)  # a folder is always current
                continue
            clone = Path(str(entry.get("path") or self._clones_root() / market_name))
            if (clone / ".git").is_dir():
                _git(["-C", str(clone), "pull", "--ff-only", "--depth", "1"])
            else:
                _rmtree_force(clone)
                clone.parent.mkdir(parents=True, exist_ok=True)
                _git(["clone", "--depth", "1", str(entry.get("url") or entry.get("source")), str(clone)])
            refreshed.append(market_name)
        return refreshed


def _git(args: list[str]) -> None:
    # A fixed argv (no shell), the first element is always the git binary.
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env=host_tool_env(GIT_TERMINAL_PROMPT="0"),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PluginError(f"git {args[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        detail = failure_detail(proc.stderr or proc.stdout)
        raise PluginError(f"git {args[0]} failed: {detail}")


def install_from_marketplace(
    spec: str, *, manager: PluginManager | None = None, registry: MarketplaceRegistry | None = None
) -> dict[str, Any]:
    """Install ``plugin@marketplace`` (or ``plugin`` when only one marketplace has it)."""
    manager = manager or PluginManager()
    registry = registry or MarketplaceRegistry(manager.root)
    plugin = registry.find_plugin(spec)
    if plugin.kind == "git":
        row = manager.install_from_git(plugin.location, name=plugin.name)
    elif plugin.kind == "npx":
        row = manager.install_from_npx(plugin.location, name=plugin.name)
    else:
        if not Path(plugin.location).is_dir():
            raise PluginError(
                f"plugin folder missing in marketplace checkout: {plugin.location} "
                f"(run `/plugin marketplace update {plugin.marketplace}`)"
            )
        row = manager.install_from_path(plugin.location, name=plugin.name)
    manager._set_state(row["name"], enabled=True, source=f"{plugin.name}@{plugin.marketplace}")
    return manager.get(row["name"])
