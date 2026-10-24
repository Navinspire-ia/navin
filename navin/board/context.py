# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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
import re
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


def board_digest(
    project_path: Path | str,
    session_key: str | None = None,
    *,
    whole_board: bool = False,
) -> list[str]:
    """Read the board (+ mission ledger + autonomy) and summarise. Never raises.

    With a ``session_key``, the mission ledger is only shown to the chat that
    works on it: a new chat must not be steered toward another chat's mission.
    """
    lines: list[str] = []
    try:
        tasks = ProjectBoardStore(project_path).read_tasks()
        if session_key and not whole_board:
            # A chat only sees the tasks it worked on: another chat's queue
            # must not show up here as "ready next".
            from navin.board import session_focus

            mine = set(session_focus.touched(session_key))
            tasks = [task for task in tasks if task.get("id") in mine]
        lines.extend(board_summary_lines(tasks))
    except Exception:
        pass
    try:
        ledger_store = MissionLedgerStore(project_path)
        if ledger_store.exists():
            mission_lines: list[str] = []
            if session_key is None:
                mission_lines = ledger_store.runtime_lines(max_lines=12)
            else:
                from navin.board import session_focus
                from navin.board.plan import mission_concerns

                ledger = ledger_store.load()
                if mission_concerns(ledger, session_focus.touched(session_key)):
                    mission_lines = ledger_store.runtime_lines(ledger, max_lines=12)
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
    try:
        from navin.reverse.repo import runtime_lines as reverse_runtime_lines

        touched: set[str] | None = None
        if session_key and not whole_board:
            from navin.board import session_focus

            touched = set(session_focus.touched(session_key))
        reverse_lines = reverse_runtime_lines(project_path, touched=touched)
        if reverse_lines:
            if lines:
                lines.append("")
            lines.extend(reverse_lines)
    except Exception:
        pass
    return lines


_HISTORY_ASK = re.compile(
    r"\b(continu\w*|reprend\w*|reprise|resume\w*|historique|history|mission|"
    r"o[uù] (on|en) (est|etait|était)|where (were|are) we|last (session|time)|derni[eè]re session)\b",
    re.IGNORECASE,
)


def wants_project_history(text: str | None) -> bool:
    """True when the user asks to see or continue earlier project work."""
    return bool(text and _HISTORY_ASK.search(text))


def session_is_fresh(request: "RequestContext") -> bool:
    """A chat that touched no board task and did not ask for the project history.

    A new chat starts clean: earlier missions, the board and the resume brief
    stay out of its context until the user asks for them or the chat works on
    a task itself.
    """
    key = getattr(request, "session_key", None) or ""
    if not key:
        return False
    from navin.board import session_focus

    if session_focus.touched(key):
        return False
    return not wants_project_history(getattr(request, "original_user_text", None))


async def board_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Remind the model, every turn, what its own plan says is next."""
    workspace = request.workspace
    if workspace is None:
        return None
    if await asyncio.to_thread(session_is_fresh, request):
        return None
    whole_board = wants_project_history(getattr(request, "original_user_text", None))
    lines = await asyncio.to_thread(
        lambda: board_digest(
            workspace,
            getattr(request, "session_key", None) or "",
            whole_board=whole_board,
        )
    )
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="board", content=content)
