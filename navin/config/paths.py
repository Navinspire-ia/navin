# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from navin.utils.helpers import ensure_dir


def get_config_path() -> Path:
    """Get the configuration file path (lazy import to break circular dependency).

    Delegates to ``navin.config.loader.get_config_path`` at call time so
    that importing this module never triggers a circular import during startup.
    """
    from navin.config.loader import get_config_path as _loader_get_config_path
    return _loader_get_config_path()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display threads (JSON)."""
    return get_runtime_subdir("webui")


def get_default_workspace_path() -> Path:
    """Default agent workspace: a visible, build-friendly folder in the home dir.

    ``~/.navin`` stays reserved for Navin's own storage (config, sessions,
    history). Projects must never live in a hidden dot-folder: some toolchains
    skip or misbehave inside them, and the path is confusing when shown as a
    "project" in the UI. ``Path.home()`` keeps this correct on Windows, macOS
    and Linux alike.
    """
    return Path.home() / "NavinProjects"


def _legacy_default_workspace_path() -> Path:
    """The pre-rename default (``~/.navin/workspace``), kept for detection only."""
    return Path.home() / ".navin" / "workspace"


def resolve_workspace_setting(workspace: str | None = None) -> Path:
    """Resolve a configured workspace value without creating it.

    A configured workspace still pointing at the legacy ``~/.navin/workspace``
    default is redirected to the new default: the internal folder must never be
    used (or shown) as a project workspace again.
    """
    path = Path(workspace).expanduser() if workspace else get_default_workspace_path()
    if path.resolve(strict=False) == _legacy_default_workspace_path().resolve(strict=False):
        return get_default_workspace_path()
    return path


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    return ensure_dir(resolve_workspace_setting(workspace))


def is_navin_internal_path(path: str | Path | None) -> bool:
    """True when a path lives inside a ``.navin`` folder (Navin system storage).

    Such a path must never be offered or displayed as a project workspace:
    ``.navin`` belongs to the system (config, sessions, history), not to the
    user's projects.
    """
    if not path:
        return False
    normalized = str(path).replace("\\", "/")
    return any(part == ".navin" for part in normalized.split("/"))


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to navin's default workspace path."""
    if workspace is None:
        return True
    current = Path(workspace).expanduser().resolve(strict=False)
    return current in (
        get_default_workspace_path().resolve(strict=False),
        _legacy_default_workspace_path().resolve(strict=False),
    )


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return Path.home() / ".navin" / "history" / "cli_history"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return Path.home() / ".navin" / "sessions"
