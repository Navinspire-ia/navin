"""WebUI payloads for reviewing (accepting/rejecting) pending agent edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.hunks import HunkConflictError
from navin.agent.review import PendingReviewStore
from navin.security.workspace_access import WorkspaceScope


class ReviewApiError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _display_path(path: str, project_path: Path) -> str:
    try:
        return str(Path(path).relative_to(project_path))
    except ValueError:
        return path


def review_changes_payload(
    store: PendingReviewStore,
    session_key: str,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    rows = store.list_changes(session_key)
    for row in rows:
        row["display_path"] = _display_path(row["path"], scope.project_path)
    return {"changes": rows}


def review_file_payload(
    store: PendingReviewStore,
    session_key: str,
    raw_path: str | None,
) -> dict[str, Any]:
    if not raw_path or not raw_path.strip():
        raise ReviewApiError(400, "path is required")
    payload = store.file_payload(session_key, raw_path.strip())
    if payload is None:
        raise ReviewApiError(404, "no pending change for this file")
    return payload


def review_action_payload(
    store: PendingReviewStore,
    session_key: str,
    action: str | None,
    raw_path: str | None,
    raw_hunk: str | None = None,
) -> dict[str, Any]:
    path = raw_path.strip() if raw_path and raw_path.strip() else None
    hunk = raw_hunk.strip() if raw_hunk and raw_hunk.strip() else None

    if hunk is not None:
        if path is None:
            raise ReviewApiError(400, "path is required when a hunk is given")
        if action not in {"accept", "reject"}:
            raise ReviewApiError(400, "action must be 'accept' or 'reject'")
        try:
            if action == "accept":
                result = store.accept_hunk(session_key, path, hunk)
            else:
                result = store.reject_hunk(session_key, path, hunk)
        except HunkConflictError as exc:
            # 409, not 400: the request was well formed and simply lost a race
            # with the agent or another click. The client refreshes and retries.
            raise ReviewApiError(409, str(exc)) from exc
        return {"action": action, "hunk": hunk, "count": 1, **result}

    if action == "accept":
        accepted = store.accept(session_key, path)
        return {"action": "accept", "count": accepted}
    if action == "reject":
        restored, deleted = store.reject(session_key, path)
        return {"action": "reject", "count": restored + deleted,
                "restored": restored, "deleted": deleted}
    raise ReviewApiError(400, "action must be 'accept' or 'reject'")
