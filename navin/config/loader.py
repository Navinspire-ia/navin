# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Configuration loading utilities."""

import copy
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

import pydantic
from filelock import FileLock
from loguru import logger
from pydantic import BaseModel

from navin.config.schema import LOW_EFFORT_MIGRATION, Config, _resolve_tool_config_refs
from navin.config.secrets import (
    decrypt_config_data,
    encrypt_config_data,
    has_plaintext_secrets,
)

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None
_schema_refs_ready = False


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".navin" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    global _schema_refs_ready
    if not _schema_refs_ready:
        _resolve_tool_config_refs()
        _schema_refs_ready = True

    path = config_path or get_config_path()

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            rewrite_secrets = has_plaintext_secrets(data)
            data = decrypt_config_data(data, path)
            data = _migrate_config(data)
            config = _validate_readable(data, path)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            raise ValueError(f"Failed to load config from {path}: {e}") from e
        if rewrite_secrets:
            _remember_config_source(config, path)
            try:
                save_config(config, path)
            except OSError as exc:
                logger.warning("could not encrypt secrets in {}: {}", path, exc)

    _remember_config_source(config, path)
    _apply_ssrf_whitelist(config)
    return config


def _drop_setting(data: Any, loc: tuple[Any, ...]) -> bool:
    """Remove the deepest key of *loc* that exists in *data*; False when none does."""
    node = data
    parent: Any = None
    key: Any = None
    for part in loc:
        if isinstance(node, dict) and part in node:
            parent, key, node = node, part, node[part]
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            parent, key, node = node, part, node[part]
        else:
            break
    if parent is None:
        return False
    del parent[key]
    return True


def _validate_readable(data: Any, path: Path) -> Config:
    """Validate the saved settings, setting aside the ones this version cannot read.

    Another version of Navin (the CLI updated before the desktop app, a newer
    build then an older one) can leave a value this one rejects. Refusing the
    whole file stopped the engine at startup with no way to recover from the
    app. The rejected settings fall back to their defaults instead, and the
    file as it was is kept next to it as ``config.json.unreadable``.
    """
    try:
        return Config.model_validate(data)
    except pydantic.ValidationError as exc:
        error = exc
    if not isinstance(data, dict):
        raise error
    trimmed = copy.deepcopy(data)
    dropped: list[str] = []
    for _ in range(8):
        removed = [
            ".".join(str(part) for part in item.get("loc") or ())
            for item in error.errors()
            if _drop_setting(trimmed, tuple(item.get("loc") or ()))
        ]
        if not removed:
            raise error
        dropped.extend(removed)
        try:
            config = Config.model_validate(trimmed)
        except pydantic.ValidationError as exc:
            error = exc
            continue
        try:
            shutil.copy2(path, path.with_name(f"{path.name}.unreadable"))
        except OSError as copy_error:
            logger.warning("could not keep a copy of {}: {}", path, copy_error)
        logger.warning(
            "Settings this version cannot read were reset to their defaults ({}); "
            "the original file is kept as {}.unreadable",
            ", ".join(dict.fromkeys(dropped)),
            path.name,
        )
        return config
    raise error


_cache_lock = threading.RLock()
#: str(path) -> (file signature, loader used, shared Config). See load_config_cached.
_config_cache: dict[str, tuple[tuple[int, int, int, int] | None, Any, Config]] = {}


def _file_signature(path: Path) -> tuple[int, int, int, int] | None:
    """mtime_ns, size, inode and ctime_ns of *path*, or None when it is absent.

    save_config replaces the file (new inode), editors rewrite it (new mtime
    or size): any of them changing means the cached parse is stale.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino, st.st_ctime_ns)


def load_config_cached(config_path: Path | None = None) -> Config:
    """``load_config`` for hot read-only paths, re-read only when the file changes.

    The gateway used to parse, decrypt and validate the config file several
    times per turn (runtime refresh, presets, routes, billing, websocket role).
    This returns one shared ``Config`` per file version instead, invalidated by
    the file's mtime/size/inode and by ``save_config`` / ``invalidate_config_cache``.
    Secrets are decrypted once per change.

    The returned object is SHARED: callers must treat it as read-only. A caller
    that needs to edit (and save) must use ``load_config`` or take
    ``config.model_copy(deep=True)`` first. Thread-safe.
    """
    path = config_path or get_config_path()
    key = str(path)
    # Signed before reading: a write racing the parse leaves a signature that
    # no longer matches, so the next call re-reads instead of keeping stale data.
    signature = _file_signature(path)
    with _cache_lock:
        hit = _config_cache.get(key)
    # The loader identity is part of the key so a patched ``load_config``
    # (tests, embedding hosts) is never bypassed by an entry it did not make.
    loader = load_config
    if hit is not None and hit[0] == signature and hit[1] is loader:
        config = hit[2]
        # load_config re-applies the SSRF policy on every load; keep that.
        _apply_ssrf_whitelist(config)
        return config
    # Parsed outside the lock: load_config may save (plaintext secrets
    # migration), and save_config takes the file lock then invalidates.
    # Same call shape as the callers used to make (patched loaders may take none).
    config = loader(config_path) if config_path is not None else loader()
    with _cache_lock:
        _config_cache[key] = (signature, loader, config)
    return config


def invalidate_config_cache(config_path: Path | None = None) -> None:
    """Drop cached configs (one path, or all when *config_path* is None)."""
    with _cache_lock:
        if config_path is None:
            _config_cache.clear()
        else:
            _config_cache.pop(str(config_path), None)


def _remember_config_source(config: Config, path: Path) -> None:
    config._loaded_path = str(path.resolve())
    config._loaded_values = config.model_dump(mode="json")


_ABSENT = object()


def _merge_config_edits(before: Any, edited: Any, latest: Any) -> Any:
    """Overlay this caller's edits; leave concurrently changed fields intact."""
    if edited == before:
        return latest
    if isinstance(before, dict) and isinstance(edited, dict):
        merged = dict(latest) if isinstance(latest, dict) else {}
        for key in before.keys() | edited.keys():
            value = _merge_config_edits(before.get(key, _ABSENT), edited.get(key, _ABSENT), merged.get(key, _ABSENT))
            if value is _ABSENT:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged
    return edited


def _remote_channel_enabled(config: Config) -> bool:
    """True when a messaging channel reachable by remote users is enabled.

    Telegram, Slack, Teams, Discord and friends accept prompts from an
    untrusted chat, so the agent must not fetch private/internal addresses on
    their behalf (SSRF to 169.254.169.254, RFC1918, loopback). The local
    WebSocket/WebUI channel is excluded: it only serves the operator's own
    machine and commonly needs to reach the dev server it just started.
    """
    channels = getattr(config.channels, "model_extra", None) or {}
    local_names = {"websocket", "webui", "local"}
    for name, value in channels.items():
        if name in local_names:
            continue
        if isinstance(value, dict):
            if value.get("enabled"):
                return True
        elif getattr(value, "enabled", False):
            return True
    return False


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply the SSRF policy from config to the network security module."""
    from navin.security.network import configure_ssrf_protection, configure_ssrf_whitelist

    enabled = bool(config.tools.ssrf_protection) or _remote_channel_enabled(config)
    configure_ssrf_protection(enabled)
    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


# Written even when they still match the default, because following a changed
# default here would move or expose the user's data rather than merely adjust a
# preference: the workspace is where every file the agent owns lives, the ports
# are what external clients and the desktop launcher connect to, and the rest are
# security decisions where an operator's explicit "no" must not become implicit.
_PINNED_PATHS: tuple[tuple[str, ...], ...] = (
    # Without it on disk the one-shot migrations would run again at every load.
    ("appliedMigrations",),
    ("agents", "defaults", "workspace"),
    ("api", "host"),
    ("api", "port"),
    ("gateway", "host"),
    ("gateway", "port"),
    ("tools", "webuiAllowRemotePackageInstall"),
    ("tools", "exec", "allowPatterns"),
    ("tools", "exec", "denyPatterns"),
    # A declared connection is a statement about someone else's database; its
    # engine and write permission have to survive verbatim.
    ("tools", "database", "connections"),
)


def _pin(source: dict[str, Any], target: dict[str, Any], path: tuple[str, ...]) -> None:
    """Copy one path from the full dump into the pruned one, if it exists."""
    value: Any = source
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return
        value = value[key]
    if isinstance(value, (dict, list)) and not value:
        # An empty list or mapping states nothing worth protecting, and writing
        # it back would only clutter the file.
        return
    cursor = target
    for key in path[:-1]:
        nested = cursor.get(key)
        if not isinstance(nested, dict):
            nested = {}
            cursor[key] = nested
        cursor = nested
    cursor[path[-1]] = value


def _configured_values(config: Config) -> dict[str, Any]:
    """Dump only what differs from the schema defaults, plus the pinned paths.

    Writing every default froze them: an installation created today kept its
    defaults forever, so improving one in the schema reached nobody who had
    already run navin. Omitting them instead lets the file say what the user
    chose and leaves the rest to follow navin's own defaults.

    Note this cannot prune ``channels.*`` or custom providers, which are extra
    fields with no declared default and are therefore always written in full.
    """
    full = config.model_dump(mode="json", by_alias=True)
    pruned = config.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    for path in _PINNED_PATHS:
        _pin(full, pruned, path)
    return pruned


def save_config(
    config: Config,
    config_path: Path | None = None,
    *,
    preserve_concurrent: bool = False,
) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
        preserve_concurrent: Let newer on-disk edits win conflicts with this
            caller's changes. Use for background refreshes of derived settings.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(path) + ".lock", timeout=15, mode=0o600):
        current = config
        if (config._loaded_path == str(path.resolve()) and config._loaded_values is not None and path.is_file()):
            # Read directly: load_config may migrate plaintext secrets by saving,
            # which would try to acquire this same interprocess lock again.
            raw = json.loads(path.read_text(encoding="utf-8"))
            latest = Config.model_validate(_migrate_config(decrypt_config_data(raw, path)))
            edited_values = config.model_dump(mode="json")
            latest_values = latest.model_dump(mode="json")
            if preserve_concurrent:
                merged = _merge_config_edits(config._loaded_values, latest_values, edited_values)
            else:
                merged = _merge_config_edits(config._loaded_values, edited_values, latest_values)
            current = Config.model_validate(merged)
        data = _configured_values(current)
        if current.providers.openai_codex.proxy is not None:
            data.setdefault("providers", {})["openaiCodex"] = {
                "proxy": current.providers.openai_codex.proxy,
            }
        data = encrypt_config_data(data, path)
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
        invalidate_config_cache(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if current is not config:
            config.__dict__.update(current.__dict__)
        _remember_config_source(config, path)


def merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively add missing defaults without replacing configured values."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = merge_missing_defaults(merged[key], value)
    return merged


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` survive;
    returns the same instance when no references are present.
    Raises ``ValueError`` if a referenced variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _resolve_env_vars(obj: object) -> object:
    """Recursively resolve ``${VAR}`` patterns in plain strings/dicts/lists."""
    if isinstance(obj, str):
        return _ENV_REF_PATTERN.sub(_env_replace, obj)
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(v) for v in obj]
    return obj


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value


def _reset_high_reasoning_effort(data: dict) -> None:
    """Once per install: a saved ``high`` reasoning effort goes back to the default.

    ``high`` used to be the default, so files written back then carry it as if
    chosen, and the new default (low) never reached them. Only ``high`` moves,
    in the agent defaults and in every model preset; any other value is kept.
    The marker makes it one-shot: a ``high`` picked afterwards stays.
    """
    done = data.get("appliedMigrations")
    if not isinstance(done, list):
        done = []
    if LOW_EFFORT_MIGRATION in done:
        return

    def reset(section: Any) -> None:
        if not isinstance(section, dict):
            return
        for key in ("reasoningEffort", "reasoning_effort"):
            if str(section.get(key) or "").strip().lower() == "high":
                section.pop(key)

    agents = data.get("agents")
    reset(agents.get("defaults") if isinstance(agents, dict) else None)
    presets = data.get("modelPresets", data.get("model_presets"))
    if isinstance(presets, dict):
        for preset in presets.values():
            reset(preset)
    data["appliedMigrations"] = [*done, LOW_EFFORT_MIGRATION]


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    agents = data.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    if isinstance(defaults, dict):
        had_legacy_max_messages = (
            "maxMessages" in defaults or "max_messages" in defaults
        )
        defaults.pop("maxMessages", None)
        defaults.pop("max_messages", None)
        if had_legacy_max_messages:
            # TODO(next version): Remove this legacy cleanup branch; the schema
            # will silently ignore this field once the warning grace period ends.
            logger.warning(
                "agents.defaults.maxMessages/max_messages is legacy and ignored; "
                "replay max messages is now an internal safety cap. Remove it from "
                "config. This compatibility warning will be removed in the next version."
            )

    _reset_high_reasoning_effort(data)

    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # No migration for restrictToWorkspace. The default is off, and a value
    # already on disk is left exactly as written: the boundary was briefly on by
    # default and pinned, so some installs carry a "true" nobody chose, but
    # nothing distinguishes those from a "true" an operator meant. Rewriting
    # someone's security setting on a guess is worse than leaving a fence they
    # can drop from the permissions panel in one click.

    # Move tools.myEnabled / tools.mySet → tools.my.{enable, allowSet}.
    # The old flat keys shipped in the initial MyTool landing; wrapping them in a
    # sub-config keeps `web` / `exec` / `my` symmetric and gives room to grow.
    if "myEnabled" in tools or "mySet" in tools:
        my_cfg = tools.setdefault("my", {})
        if "myEnabled" in tools and "enable" not in my_cfg:
            my_cfg["enable"] = tools.pop("myEnabled")
        else:
            tools.pop("myEnabled", None)
        if "mySet" in tools and "allowSet" not in my_cfg:
            my_cfg["allowSet"] = tools.pop("mySet")
        else:
            tools.pop("mySet", None)

    return data
