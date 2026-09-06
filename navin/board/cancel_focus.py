"""Mark in-flight session plan tasks as cancelled after /stop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from navin.board import session_focus
from navin.board.plan import ACTIVE_STATUSES
from navin.board.store import BoardError, ProjectBoardStore


def cancel_focused_active_tasks(
    workspace: Path,
    session_key: str | None,
    *,
    actor: str = "user",
) -> int:
    """Set active (in_progress/review/audit) focused tasks to ``cancelled``.

    Returns how many tasks were updated. Best-effort: board errors are logged
    and swallowed so /stop never fails because of the plan panel.
    """
    return _set_focused_active_status(
        workspace,
        session_key,
        status="cancelled",
        actor=actor,
        actor_type="human",
    )


def park_focused_active_tasks_to_todo(
    workspace: Path,
    session_key: str | None,
    *,
    actor: str = "system",
) -> int:
    """Return in-flight focused tasks to ``planned`` after a budget stop.

    Keeps the plan panel from spinning forever when the turn hits max
    iterations without an automatic continuation, and leaves the step
    reclaimable by the next Continue / forge slice.
    """
    return _set_focused_active_status(
        workspace,
        session_key,
        status="planned",
        actor=actor,
        actor_type="agent",
    )


def _set_focused_active_status(
    workspace: Path,
    session_key: str | None,
    *,
    status: str,
    actor: str,
    actor_type: str,
) -> int:
    focus_ids = session_focus.touched(session_key)
    if not focus_ids:
        return 0
    try:
        store = ProjectBoardStore(workspace)
        tasks = store.read_tasks()
    except Exception:
        logger.debug("focused task status update: board read failed", exc_info=True)
        return 0

    by_id = {task["id"]: task for task in tasks}
    updated = 0
    for task_id in focus_ids:
        task = by_id.get(task_id)
        if not task:
            continue
        if task.get("status") not in ACTIVE_STATUSES:
            continue
        try:
            store.update_task(
                task_id,
                actor=actor,
                actor_type=actor_type,
                fields={"status": status},
            )
            updated += 1
        except BoardError as exc:
            logger.debug("update task {} → {} failed: {}", task_id, status, exc)
        except Exception:
            logger.debug("update task {} → {} failed", task_id, status, exc_info=True)
    return updated


def cancel_focused_active_tasks_safe(
    workspace: Path | None,
    session_key: str | None,
    **kwargs: Any,
) -> int:
    if workspace is None:
        return 0
    try:
        return cancel_focused_active_tasks(workspace, session_key, **kwargs)
    except Exception:
        logger.debug("cancel_focused_active_tasks_safe failed", exc_info=True)
        return 0


def park_focused_active_tasks_to_todo_safe(
    workspace: Path | None,
    session_key: str | None,
    **kwargs: Any,
) -> int:
    if workspace is None:
        return 0
    try:
        return park_focused_active_tasks_to_todo(workspace, session_key, **kwargs)
    except Exception:
        logger.debug("park_focused_active_tasks_to_todo_safe failed", exc_info=True)
        return 0
