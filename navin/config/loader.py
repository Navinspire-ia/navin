"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any

import pydantic
from loguru import logger
from pydantic import BaseModel

from navin.config.schema import Config, _resolve_tool_config_refs
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
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            raise ValueError(f"Failed to load config from {path}: {e}") from e
        if rewrite_secrets:
            try:
                save_config(config, path)
            except OSError as exc:
                logger.warning("could not encrypt secrets in {}: {}", path, exc)

    _apply_ssrf_whitelist(config)
    return config


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply the SSRF policy from config to the network security module."""
    from navin.security.network import configure_ssrf_protection, configure_ssrf_whitelist

    configure_ssrf_protection(config.tools.ssrf_protection)
    configure_ssrf_whitelist(config.tools.ssrf_whitelist)


# Written even when they still match the default, because following a changed
# default here would move or expose the user's data rather than merely adjust a
# preference: the workspace is where every file the agent owns lives, the ports
# are what external clients and the desktop launcher connect to, and the rest are
# security decisions where an operator's explicit "no" must not become implicit.
_PINNED_PATHS: tuple[tuple[str, ...], ...] = (
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


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _configured_values(config)
    if config.providers.openai_codex.proxy is not None:
        data.setdefault("providers", {})["openaiCodex"] = {
            "proxy": config.providers.openai_codex.proxy,
        }

    data = encrypt_config_data(data, path)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    if not payload.endswith("\n"):
        payload += "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
