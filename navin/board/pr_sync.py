"""PR merge suggestions for board tasks (suggestion only, no auto-close).

For each task that already has a ``pr_url``, ask the forge whether the pull
request (merge request, on GitLab) merged. The URL alone says which server,
which repository and which number to read, so a board can track tasks whose
requests live on GitHub, GitLab and Forgejo at the same time. ``gh`` stays a
fallback on github.com. Callers can surface suggestions in the board UI; this
module never mutates task status.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navin.board.github_sync import _config, _forge_api, _gh, gh_available

_MAX_TASKS = 40


def _check_via_forge(url: str) -> dict[str, Any] | None:
    """Merge state through the forge REST API, or None when unavailable."""
    api = _forge_api()
    if api is None:
        return None
    config = _config()
    try:
        ref = api.resolve_ref(url, config=config)
    except Exception:  # noqa: BLE001 - resolution is best-effort
        return None
    if ref is None or ref.remote.kind == api.UNKNOWN:
        return None
    token, _source = api.resolve_token(ref.remote, config=config)
    if not token:
        if ref.remote.kind == "github" and gh_available():
            return None
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": api.token_missing_error(ref.remote).message[:300],
        }
    try:
        request = api.get_request(ref.remote, token, ref.number)
    except api.ForgeError as exc:
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": exc.message[:300],
        }
    merged = bool(request.get("merged"))
    return {
        "ok": True,
        "merged": merged,
        "pr_url": str(request.get("url") or url),
        "state": str(request.get("state") or "").lower() or None,
        "merged_at": request.get("merged_at"),
        "branch": request.get("head") or "",
        "base": request.get("base") or "",
        "forge": ref.remote.kind,
        "detail": "merged" if merged else "not merged",
    }


def check_pr_merged(
    project_path: Path | str,
    pr_url: str,
) -> dict[str, Any]:
    """Return merge state for one PR/MR URL. Best-effort; never raises."""
    root = Path(project_path).expanduser().resolve(strict=False)
    url = (pr_url or "").strip()
    if not url:
        return {"ok": False, "merged": False, "pr_url": url, "detail": "missing pr_url"}

    via_forge = _check_via_forge(url)
    if via_forge is not None:
        return via_forge

    if not gh_available():
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": (
                "no forge token for this pull request and gh CLI not installed - "
                "add a token in Settings > Git"
            ),
        }
    code, out, err = _gh(
        root,
        "pr",
        "view",
        url,
        "--json",
        "state,mergedAt,url",
    )
    if code != 0:
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": (err or out or "gh pr view failed").strip()[:300],
        }
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": "invalid gh pr view JSON",
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "merged": False,
            "pr_url": url,
            "detail": "unexpected gh payload",
        }
    state = str(data.get("state") or "").upper()
    merged_at = data.get("mergedAt")
    merged = bool(merged_at) or state == "MERGED"
    return {
        "ok": True,
        "merged": merged,
        "pr_url": str(data.get("url") or url),
        "state": state.lower() or None,
        "merged_at": merged_at,
        "detail": "merged" if merged else "not merged",
    }


def suggest_merged_task_prs(
    project_path: Path | str,
    tasks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return ``{task_id, pr_url, merged: true}`` suggestions for merged PRs.

    Does not close or update board tasks - suggestion only.
    """
    root = Path(project_path).expanduser().resolve(strict=False)
    suggestions: list[dict[str, Any]] = []
    if not tasks:
        return suggestions
    scanned = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        pr_url = str(task.get("pr_url") or "").strip()
        task_id = str(task.get("id") or "").strip()
        if not pr_url or not task_id:
            continue
        # Skip tasks already done - nothing to suggest.
        status = str(task.get("status") or "").strip().lower()
        if status in {"done", "cancelled", "canceled"}:
            continue
        scanned += 1
        if scanned > _MAX_TASKS:
            break
        result = check_pr_merged(root, pr_url)
        if result.get("ok") and result.get("merged"):
            suggestions.append(
                {
                    "task_id": task_id,
                    "pr_url": result.get("pr_url") or pr_url,
                    "merged": True,
                    "merged_at": result.get("merged_at"),
                    "forge": result.get("forge") or "",
                    "detail": "PR merged - consider marking the board task done",
                }
            )
    return suggestions


def _sync_available(project_path: Path | str) -> bool:
    """True when at least one road (token or ``gh``) can read a request."""
    if gh_available():
        return True
    api = _forge_api()
    if api is None:
        return False
    try:
        remote = api.detect_forge(Path(project_path), config=_config())
    except Exception:  # noqa: BLE001 - detection is best-effort
        return False
    if remote is None or remote.kind == api.UNKNOWN:
        # The project's own remote says nothing about where the stored PR
        # URLs point; a task may still carry a URL this host has a token for.
        return False
    token, _source = api.resolve_token(remote, config=_config())
    return bool(token)


def pr_sync_payload(
    project_path: Path | str,
    store: Any,
) -> dict[str, Any]:
    """HTTP-facing payload: list of merge suggestions for the project board."""
    root = Path(project_path).expanduser().resolve(strict=False)
    if not _sync_available(root):
        return {
            "ok": False,
            "available": False,
            "suggestions": [],
            "detail": (
                "no forge token for this project and gh CLI not installed - "
                "add a token in Settings > Git"
            ),
        }
    try:
        tasks = store.read_tasks() if store is not None else []
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "suggestions": [],
            "detail": f"could not read board tasks: {exc}"[:300],
        }
    suggestions = suggest_merged_task_prs(root, tasks)
    return {
        "ok": True,
        "available": True,
        "suggestions": suggestions,
        "detail": (
            f"{len(suggestions)} merged PR suggestion(s)"
            if suggestions
            else "no merged PRs pending board update"
        ),
    }
