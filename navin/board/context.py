"""Put the agent's own plan back in front of it on every turn.

The board persists to disk and survives a compaction, but nothing re-read it:
the agent saw its plan only on the turn it called ``board``, and by the next one
the queue was whatever it happened to remember. Long tasks drifted for exactly
that reason - work restarted, finished items got redone, and the blocked ones
were never revisited.

What goes into the prompt is deliberately a digest, not the board. Injecting
every task would cost more context than the plan is worth and would bury the
one line that matters, so this names what is running, what is next, and any
graph defect a human has to settle, then stops.

Every line here is a statement of fact, never an instruction. The block ships
under a marker that tells the model this is metadata it may act on or ignore,
so an imperative placed here would be one the model is explicitly allowed to
skip. What to do about a stalled queue or a dependency cycle belongs in the
tool contract, which is always in the system prompt.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navin.board.ledger import MissionLedgerStore
from navin.board.plan import plan_summary
from navin.board.store import ProjectBoardStore
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

# Enough to orient the agent without turning the digest into the board itself.
_MAX_NAMED = 3
_MAX_TITLE = 70


def _title(task: dict[str, Any]) -> str:
    title = str(task.get("title") or task.get("id") or "").strip()
    if len(title) > _MAX_TITLE:
        title = title[: _MAX_TITLE - 1].rstrip() + "…"
    return title


def _named(ids: list[str], titles: dict[str, dict[str, Any]]) -> str:
    """Render up to a few tasks as ``id (title)``, counting the rest."""
    shown = [
        f"{task_id} ({_title(titles[task_id])})"
        for task_id in ids[:_MAX_NAMED]
        if task_id in titles
    ]
    if not shown:
        return ""
    rest = len(ids) - len(shown)
    return ", ".join(shown) + (f", and {rest} more" if rest > 0 else "")


def board_summary_lines(tasks: list[dict[str, Any]]) -> list[str]:
    """A few lines describing where the plan stands, for the model.

    Returns nothing when there is no open work: an empty board has no advice to
    give, and a line that says so every turn only teaches the model to skip it.
    """
    if not tasks:
        return []

    plan = plan_summary(tasks)
    if not plan["open_count"]:
        return []

    by_id = {task["id"]: task for task in tasks}
    lines = [
        f"Board: {plan['open_count']} open, {plan['done_count']} done."
    ]

    running = _named(plan["in_progress"], by_id)
    if running:
        lines.append(f"In progress: {running}")

    ready = _named(plan["ready_queue"], by_id)
    if ready:
        lines.append(f"Ready next: {ready}")
    elif not running:
        # Open work that is neither running nor startable is the one state the
        # agent cannot resolve by picking up the next task.
        lines.append(
            "Nothing is ready to start: every open task is blocked or parked."
        )

    blocked = plan["blocked"]
    if blocked:
        lines.append(f"Blocked: {len(blocked)}.")

    defects = []
    if plan["cycles"]:
        defects.append(f"{len(plan['cycles'])} dependency cycle(s)")
    if plan["dangling"]:
        defects.append(f"{len(plan['dangling'])} dependency on a missing task")
    if defects:
        lines.append(
            f"The plan has {' and '.join(defects)}, so the tasks involved can "
            "never become ready."
        )

    return lines


def board_digest(project_path: Path | str) -> list[str]:
    """Read the board (+ mission ledger + autonomy) and summarise. Never raises."""
    lines: list[str] = []
    try:
        tasks = ProjectBoardStore(project_path).read_tasks()
        lines.extend(board_summary_lines(tasks))
    except Exception:
        pass
    try:
        ledger_store = MissionLedgerStore(project_path)
        if ledger_store.exists():
            mission_lines = ledger_store.runtime_lines(max_lines=12)
            if mission_lines:
                if lines:
                    lines.append("")
                lines.extend(mission_lines)
    except Exception:
        pass
    try:
        from navin.board.autonomy import runtime_lines as autonomy_runtime_lines

        autonomy_lines = autonomy_runtime_lines(project_path)
        if autonomy_lines:
            if lines:
                lines.append("")
            lines.extend(autonomy_lines)
    except Exception:
        pass
    return lines


async def board_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Remind the model, every turn, what its own plan says is next."""
    workspace = request.workspace
    if workspace is None:
        return None
    lines = await asyncio.to_thread(board_digest, workspace)
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="board", content=content)
