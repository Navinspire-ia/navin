# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebUI payloads for the per-application computer permission rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.computer.audit_view import AuditViewError, list_sessions, read_session
from navin.computer.permissions import LEVELS, AppPermissions


class ComputerPermissionsError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _payload(perms: AppPermissions) -> dict[str, Any]:
    rules = [{"app": app, "level": level} for app, level in sorted(perms.rules.items())]
    return {"levels": list(LEVELS), "rules": rules}


def list_rules() -> dict[str, Any]:
    return _payload(AppPermissions.load())


def upsert_rule(app: str, level: str) -> dict[str, Any]:
    perms = AppPermissions.load()
    try:
        perms.set(app, level)
    except ValueError as exc:
        raise ComputerPermissionsError(str(exc)) from exc
    perms.save()
    return _payload(perms)


def delete_rule(app: str) -> dict[str, Any]:
    perms = AppPermissions.load()
    if not perms.remove(app):
        raise ComputerPermissionsError(f"no rule for {app!r}")
    perms.save()
    return _payload(perms)


def audit_root() -> Path:
    """Folder holding one audit trail per desktop session (same as the CLI)."""
    from navin.config.loader import load_config
    from navin.utils.artifacts import artifact_directory

    cfg = load_config().tools.computer
    return artifact_directory(str(cfg.screenshot_dir or "computer"))


def audit_payload(session: str = "", last: int = 200, root: Path | None = None) -> dict[str, Any]:
    """Session list, or the steps of one session when ``session`` is given."""
    base = root if root is not None else audit_root()
    if not session:
        return {"root": str(base), "sessions": list_sessions(base, limit=50)}
    try:
        return {"root": str(base), "session": read_session(base, session, last=last)}
    except AuditViewError as exc:
        raise ComputerPermissionsError(str(exc)) from exc
