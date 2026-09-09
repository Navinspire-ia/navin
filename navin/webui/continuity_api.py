# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP helpers for Resume seed and explicit leave-handoff."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.continuity.resume_seed import build_resume_seed, write_leave_handoff
from navin.security.workspace_access import WorkspaceScope


class ContinuityApiError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def resume_seed_payload(scope: WorkspaceScope) -> dict[str, Any]:
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise ContinuityApiError(f"project not found: {root}", status=404)
    return build_resume_seed(root, project_name=scope.project_name or root.name)


def leave_handoff_payload(
    scope: WorkspaceScope,
    *,
    body: str,
    session_key: str | None = None,
) -> dict[str, Any]:
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise ContinuityApiError(f"project not found: {root}", status=404)
    try:
        return write_leave_handoff(root, body=body, session_key=session_key)
    except ValueError as exc:
        raise ContinuityApiError(str(exc), status=400) from exc
