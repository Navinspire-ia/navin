# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Board tool: the agent side of the shared project task board.

Humans manage the same board from the Dev workbench (kanban + Evolutions
views); this tool lets agents and subagents list, claim, update, comment,
and create tasks or milestones. Every mutation is recorded in the activity
timeline with the acting agent's name, and connected WebUI clients are
notified so the board refreshes live.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_session_key
from navin.board import session_focus
from navin.board.autonomy import (
    should_auto_branch,
    should_open_pr,
    should_sync_issues,
    start_task_branch,
)
from navin.board.ledger import (
    LEDGER_STATUSES,
    MissionLedgerStore,
    action_fingerprint,
    parse_criteria_evidence,
    unproven_criteria,
)
from navin.board.notify import publish_board_update
from navin.board.plan import milestone_progress, plan_summary
from navin.board.store import (
    MILESTONE_STATUSES,
    TASK_PRIORITIES,
    TASK_STATUSES,
    TASK_VALIDATIONS,
    BoardError,
    BoardValidationPendingError,
    ProjectBoardStore,
)
from navin.bus.notify import publish_notification
from navin.security.workspace_access import current_tool_workspace

# Per (event loop, board directory): operations on one board stay serialized
# once they run in threads, while boards of other projects (other sessions)
# proceed in parallel. A single loop-wide lock made every board move in the
# gateway queue behind whichever session was pushing a branch.
_BOARD_DISPATCH_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _board_dispatch_lock(store: Any) -> asyncio.Lock:
    board = getattr(store, "dir", None) or getattr(store, "project_path", None) or ""
    key = (id(asyncio.get_running_loop()), os.path.normcase(os.path.abspath(str(board))))
    return _BOARD_DISPATCH_LOCKS.setdefault(key, asyncio.Lock())

_LIST_LIMIT = 60
_NEXT_LIMIT = 12
def _with_note(result: Any, note: str) -> Any:
    """Append *note* to a tool result, keeping a ToolResult's flags."""
    if isinstance(result, ToolResult):
        return ToolResult(
            f"{result}\n{note}",
            is_error=result.is_error,
            recovery_hint=result.recovery_hint,
            verification=result.verification,
            made_progress=result.made_progress,
        )
    if isinstance(result, str):
        return f"{result}\n{note}"
    return result


_MUTATING_ACTIONS = {
    "create",
    "update",
    "move",
    "claim",
    "comment",
    "delete",
    "sync_github",
    "milestone_create",
    "milestone_update",
    "log",
    "ledger_init",
    "ledger_update",
    "ledger_progress",
    "ledger_replan",
    "ledger_pause",
    "ledger_resume",
}
# A mission still owed an outcome. `done` and `failed` are finished, so opening
# the next mission over one of those loses nothing.
_LIVE_LEDGER_STATUSES = frozenset({"draft", "running", "paused", "blocked"})

_LEDGER_ACTIONS = frozenset(
    {
        "ledger_get",
        "ledger_timeline",
        "ledger_restore",
        "ledger_diff",
        "ledger_replay",
        "ledger_compare",
        "ledger_init",
        "ledger_update",
        "ledger_progress",
        "ledger_replan",
        "ledger_pause",
        "ledger_resume",
    }
)


def open_mission_refusal(ledger: dict[str, Any]) -> str:
    """Why ``ledger_init`` will not overwrite the mission already in progress.

    There is one ledger per project and ``create`` rewrites the file whole, so
    an unrelated request that opened a mission used to silently destroy the one
    in flight - and, because the ledger is replayed into context every turn, the
    new mission usually inherited the old goal and acceptance criteria. Saying
    no is what keeps a one-off errand from becoming the project's mission.
    """
    steps = ledger.get("steps") or []
    done = sum(1 for step in steps if str(step.get("status")) in ("completed", "done"))
    return (
        f"a mission is already open: {str(ledger.get('goal') or '')!r} "
        f"(v{ledger.get('version')}, {ledger.get('status')}, {done}/{len(steps)} steps done). "
        "ledger_init replaces it and loses its steps, criteria and history. "
        "If this request belongs to that mission, file the tasks with action=create "
        "and extend the plan with ledger_update. If it is a separate one-off, do the "
        "work without a ledger. If the mission is genuinely over, close it with "
        "ledger_update status=done. Pass replace=true only to abandon it."
    )


def _fmt_task(task: dict[str, Any], *, verbose: bool = False) -> str:
    assignee = task.get("assignee")
    who = f" @{assignee['name']}({assignee['type']})" if assignee else ""
    labels = f" [{', '.join(task['labels'])}]" if task.get("labels") else ""
    milestone = f" milestone={task['milestone_id']}" if task.get("milestone_id") else ""
    line = (
        f"{task['id']} [{task['status']}] ({task['priority']}) "
        f"{task['title']}{who}{labels}{milestone}"
    )
    if not verbose:
        return line
    parts = [line]
    if task.get("description"):
        parts.append(f"  description: {task['description']}")
    if task.get("depends_on"):
        parts.append(f"  depends_on: {', '.join(task['depends_on'])}")
    parts.append(f"  created_by: {task['created_by']['name']} ({task['created_by']['type']})")
    parts.append(f"  updated_at: {task['updated_at']}")
    for comment in task.get("comments", [])[-10:]:
        parts.append(f"  comment [{comment['ts']}] {comment['author']} ({comment['author_type']}): {comment['text']}")
    return "\n".join(parts)


class BoardTool(Tool):
    """Read and update the shared project task board."""

    _scopes = {"core", "subagent"}

    def __init__(self, workspace: Path | None = None, bus: Any = None) -> None:
        self._workspace = workspace
        self._bus = bus

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=Path(ctx.workspace), bus=ctx.bus)

    @property
    def name(self) -> str:
        return "board"

    @property
    def description(self) -> str:
        return (
            "Shared project task board (kanban + dependency-aware plan + "
            "milestones + mission ledger), also edited by humans in the Dev "
            "workbench. 'next' returns the ready queue (pick work there), "
            "'plan' shows milestones/critical path/blockers, 'claim' each step "
            "before starting it and 'move' it done as soon as it is finished "
            "(the chat plan panel advances on these; send them in the same reply "
            "as the step's real work, never alone), 'update'/'comment' to "
            "track, 'create' files new "
            "work (depends_on/acceptance/validation). Marking done with "
            "validation=test|lint|verify requires evidence AND a real passing "
            "verify/test_run recorded in the last 30 minutes - run "
            "`verify action=check` right before the move; prose alone is "
            "rejected. Always pass 'actor'. With board autonomy on, claim "
            "creates a task branch and done opens a PR. 'sync_github' imports "
            "open issues (no task_id) or exports one task (with task_id); it "
            "works on GitHub, GitLab and Forgejo through the forge API."
        )

    def call_read_only(self, arguments: Any) -> bool:
        """list/next/plan/get/ledger_get read; everything else records."""
        if not isinstance(arguments, dict):
            return False
        return arguments.get("action") not in _MUTATING_ACTIONS

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list",
                        "next",
                        "plan",
                        "get",
                        "create",
                        "update",
                        "move",
                        "claim",
                        "comment",
                        "delete",
                        "sync_github",
                        "milestone_create",
                        "milestone_update",
                        "log",
                        "ledger_get",
                        "ledger_init",
                        "ledger_update",
                        "ledger_progress",
                        "ledger_replan",
                        "ledger_pause",
                        "ledger_resume",
                        "ledger_timeline",
                        "ledger_restore",
                        "ledger_diff",
                        "ledger_replay",
                        "ledger_compare",
                    ],
                    "description": "Board or mission-ledger operation to perform",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task id",
                },
                "milestone_id": {
                    "type": "string",
                    "description": (
                        "Milestone id (milestone_update, or to attach a task on "
                        "create/update)"
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Title (create / milestone_create)",
                },
                "description": {
                    "type": "string",
                    "description": "Longer description (create / milestone_create)",
                },
                "status": {
                    "type": "string",
                    # Union of both vocabularies: this one field carries the task
                    # status and the milestone status, so the enum can only catch
                    # typos, and the description says which set applies where.
                    "enum": list(
                        dict.fromkeys([*TASK_STATUSES, *MILESTONE_STATUSES])
                    ),
                    "description": (
                        "Task status for create/move/update: "
                        + ", ".join(TASK_STATUSES)
                        + ". Milestone status for milestone_*: "
                        + ", ".join(MILESTONE_STATUSES)
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": list(TASK_PRIORITIES),
                    "description": "Task priority (for create/update)",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task labels/tags (for create/update)",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Task ids this task waits for; a task is ready only "
                        "once every dependency is done"
                    ),
                },
                "acceptance": {
                    "type": "string",
                    "description": (
                        "Per-step success criterion; once set, done requires "
                        "evidence"
                    ),
                },
                "validation": {
                    "type": "string",
                    "enum": list(TASK_VALIDATIONS),
                    "description": (
                        "How this step is verified before done; test/lint/verify "
                        "require a real recorded run"
                    ),
                },
                "agent_hint": {
                    "type": "string",
                    "description": (
                        "Preferred subagent label for this step (stored as "
                        "task.agent)"
                    ),
                },
                "evidence": {
                    "type": "string",
                    "description": (
                        "How the acceptance/validation was verified (required "
                        "for done; also used by ledger_progress). ledger_update: "
                        "one line per mission criterion, 'N: command -> result'; "
                        "the mission cannot be closed until every criterion has one"
                    ),
                },
                "tasks": {
                    "type": "array",
                    "description": (
                        "create: file the whole plan in ONE call. Each item: title "
                        "(required), description, priority, depends_on (ids, or "
                        "titles of items earlier in this list), acceptance, validation"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "priority": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "acceptance": {"type": "string"},
                            "validation": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
                "claim_next": {
                    "type": "boolean",
                    "description": (
                        "move to done: also claim the best ready task and return it, "
                        "so closing one step and starting the next is one call"
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": "Mission goal (ledger_init / ledger_update)",
                },
                "replace": {
                    "type": "boolean",
                    "description": (
                        "Abandon the mission already open and start a new one "
                        "(ledger_init). Without it, ledger_init refuses rather "
                        "than overwrite a mission that is still unfinished."
                    ),
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Mission constraints (ledger_init / ledger_update)",
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Verified facts (ledger_init / ledger_update / replan)",
                },
                "missing_info": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Missing information (ledger_init / ledger_update)",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Global acceptance criteria (ledger_init / ledger_update)",
                },
                "step_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Board task ids that form the mission steps (ledger_init)",
                },
                "ok": {
                    "type": "boolean",
                    "description": "Whether the step succeeded (ledger_progress)",
                },
                "progress": {
                    "type": "boolean",
                    "description": "Whether real progress was made (ledger_progress)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the plan changed (ledger_replan / ledger_pause)",
                },
                "fingerprint": {
                    "type": "string",
                    "description": "Optional action fingerprint for loop detection",
                },
                "tokens": {
                    "type": "integer",
                    "description": "Tokens to debit from mission budget (ledger_progress)",
                },
                "assignee": {
                    "type": "string",
                    "description": (
                        "'type:name', type in human|agent|subagent "
                        "(e.g. 'subagent:audit-worker')"
                    ),
                },
                "text": {
                    "type": "string",
                    "description": (
                        "Comment body (comment) or timeline note (log); "
                        "REQUIRED for both. ledger_restore: version N or 'coherent' "
                        "(reason= why); ledger_diff: 'A..B'; ledger_replay: optional "
                        "version N to take the step (task_id=) from; ledger_compare: "
                        "'A..B' to rank what two plans achieved"
                    ),
                },
                "target_date": {
                    "type": "string",
                    "description": "Milestone target date YYYY-MM-DD",
                },
                "filter_status": {
                    "type": "string",
                    "enum": list(TASK_STATUSES),
                    "description": "Only list tasks with this status",
                },
                "actor": {
                    "type": "string",
                    "description": (
                        "Your agent/subagent name, shown in the timeline"
                    ),
                },
                "actor_type": {
                    "type": "string",
                    "enum": ["agent", "subagent"],
                    "description": "Whether you are the main agent or a spawned subagent",
                },
            },
            "required": ["action"],
        }

    def _store(self) -> ProjectBoardStore:
        project = current_tool_workspace(self._workspace).project_path
        if project is None:
            raise BoardError("no workspace configured")
        root = Path(project).expanduser().resolve(strict=False)
        if not root.is_dir():
            raise BoardError(f"project root not found: {root}", status=404)
        return ProjectBoardStore(root)

    @staticmethod
    def _graph_defects(summary: dict[str, Any]) -> list[str]:
        """Human-actionable warnings about a broken dependency graph."""
        lines: list[str] = []
        for ring in summary.get("cycles", [])[:5]:
            lines.append(f"Dependency cycle (nothing in it can start): {' -> '.join(ring)}")
        for task_id, missing in list(summary.get("dangling", {}).items())[:5]:
            lines.append(
                f"Task {task_id} depends on unknown task(s): {', '.join(missing)}. "
                "Fix or drop the reference."
            )
        return lines

    def _no_work_explanation(
        self,
        summary: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
    ) -> str:
        """Explain an empty ready queue instead of just reporting nothing to do."""
        if not by_id:
            return "Board is empty. Use action=create to file the first task."
        defects = self._graph_defects(summary)
        if summary["blocked"]:
            lines = ["No task is ready. Everything open is blocked:"]
            for entry in summary["blocked"][:_NEXT_LIMIT]:
                if entry["blocked_by"]:
                    why = "waiting on " + ", ".join(entry["blocked_by"])
                else:
                    why = "parked in blocked status - needs a human"
                lines.append(f"  {_fmt_task(by_id[entry['id']])}  <- {why}")
            if defects:
                lines.extend(defects)
            return "\n".join(lines)
        if summary["in_progress"]:
            return (
                "Nothing new is ready; "
                f"{len(summary['in_progress'])} task(s) are already in flight: "
                + ", ".join(summary["in_progress"][:10])
            )
        return "\n".join(["All tasks are done. Nothing ready to pick up.", *defects])

    @staticmethod
    def _parse_assignee(raw: str | None) -> dict[str, str] | None:
        if not raw or not raw.strip():
            return None
        cleaned = raw.strip()
        if ":" in cleaned:
            kind, _, name = cleaned.partition(":")
            kind = kind.strip().lower()
            name = name.strip()
            if kind in ("human", "agent", "subagent") and name:
                return {"type": kind, "name": name}
        return {"type": "agent", "name": cleaned}

    async def execute(
        self,
        action: str,
        task_id: str | None = None,
        milestone_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        depends_on: list[str] | None = None,
        assignee: str | None = None,
        text: str | None = None,
        target_date: str | None = None,
        filter_status: str | None = None,
        actor: str | None = None,
        actor_type: str | None = None,
        acceptance: str | None = None,
        validation: str | None = None,
        agent_hint: str | None = None,
        evidence: str | None = None,
        goal: str | None = None,
        constraints: list[str] | None = None,
        facts: list[str] | None = None,
        missing_info: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        step_ids: list[str] | None = None,
        ok: bool | None = None,
        progress: bool | None = None,
        reason: str | None = None,
        fingerprint: str | None = None,
        tokens: int | None = None,
        replace: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        who = (actor or "agent").strip() or "agent"
        who_type = actor_type if actor_type in ("agent", "subagent") else "agent"
        if not (isinstance(text, str) and text.strip()):
            for key in ("detail", "message", "note", "content", "body"):
                alt = kwargs.get(key)
                if isinstance(alt, str) and alt.strip():
                    text = alt.strip()
                    break
        touched: list[str] = []
        call_index, in_subagent = self._begin_auto_advance_call()
        try:
            store = self._store()
            verified = ""
            if action == "move" and status == "done" and task_id:
                try:
                    verified = await self._verify_before_done(store, store.get_task(task_id))
                except BoardError:
                    verified = ""  # unknown task: let _dispatch report it
                if verified.startswith(("Error", "Validation pending")):
                    return ToolResult.error(verified)
            # The board lives on disk (tasks, activity, ledger): its reads and
            # writes run off the event loop so the UI and the reply stream
            # keep moving; notifications are handed back to the loop.
            from navin.bus.notify import run_off_loop

            # One operation per board at a time, as when it ran on the loop:
            # parallel tool calls must not interleave read-modify-write cycles.
            # Forge side effects (git push, PR/MR, issue close; up to minutes)
            # are queued in ``deferred`` and run after the lock is released.
            deferred: list[Any] = []
            async with _board_dispatch_lock(store):
                result = await run_off_loop(
                    self._dispatch,
                    store,
                    touched=touched,
                    deferred=deferred,
                    action=action,
                    task_id=task_id,
                    milestone_id=milestone_id,
                    title=title,
                    description=description,
                    status=status,
                    priority=priority,
                    labels=labels,
                    depends_on=depends_on,
                    assignee=assignee,
                    text=text,
                    target_date=target_date,
                    filter_status=filter_status,
                    who=who,
                    who_type=who_type,
                    acceptance=acceptance,
                    validation=validation,
                    agent_hint=agent_hint,
                    evidence=evidence,
                    goal=goal,
                    constraints=constraints,
                    facts=facts,
                    missing_info=missing_info,
                    acceptance_criteria=acceptance_criteria,
                    step_ids=step_ids,
                    ok=ok,
                    progress=progress,
                    reason=reason,
                    fingerprint=fingerprint,
                    tokens=tokens,
                    replace=replace,
                    tasks=kwargs.get("tasks") if isinstance(kwargs.get("tasks"), list) else None,
                    claim_next=bool(kwargs.get("claim_next")),
                )
            for job in deferred:
                # Each store write inside is atomic under the store's own
                # per-board lock, so no dispatch lock is needed here.
                extra = await run_off_loop(job)
                if extra:
                    result = _with_note(result, extra.lstrip("\n"))
        except BoardValidationPendingError as e:
            return ToolResult.error(
                "Validation pending: task remains open.\n"
                f"{e.message}\n"
                "Continue the required validation automatically. Retry closing only after it passes. "
                "This is a validation prerequisite, not a request for user permission.",
                recovery_hint="",
            )
        except BoardError as e:
            return ToolResult.error(f"Error: {e.message}")
        if action in _MUTATING_ACTIONS and getattr(result, "made_progress", None) is not False:
            session_key = current_request_session_key()
            session_focus.remember(session_key, touched)
            notes = self._auto_advance(
                store, session_key, action=action, task_id=task_id,
                status=status, who_type=who_type, touched=touched,
                call_index=call_index, in_subagent=in_subagent,
            )
            if notes:
                result = _with_note(result, "Plan auto-advance: " + "; ".join(notes) + ".")
            publish_board_update(self._bus, str(store.project_path))
        if verified:
            result = _with_note(result, verified)
        return result

    @staticmethod
    def _begin_auto_advance_call() -> tuple[int | None, bool]:
        """Count this call for :mod:`navin.board.auto_advance` (parent turns only)."""
        try:
            from navin.agent.tools.context import current_request_context
            from navin.board.auto_advance import begin_board_call, is_subagent_context

            ctx = current_request_context()
            if ctx is None:
                return None, False
            if is_subagent_context(ctx.metadata, ctx.sender_id):
                return None, True
            return begin_board_call(ctx.session_key, turn_id=ctx.turn_id), False
        except Exception:
            logger.debug("board auto-advance call tracking failed", exc_info=True)
            return None, False

    @staticmethod
    def _auto_advance(
        store: ProjectBoardStore,
        session_key: str | None,
        *,
        action: str,
        task_id: str | None,
        status: str | None,
        who_type: str,
        touched: list[str] | None = None,
        call_index: int | None = None,
        in_subagent: bool = False,
    ) -> list[str]:
        """Rules R1-R3 of :mod:`navin.board.auto_advance`; never fails the call."""
        if action not in ("create", "claim", "move", "update", "delete"):
            return []
        try:
            from navin.agent.tools.context import current_request_context
            from navin.board.auto_advance import after_tool_mutation

            ctx = current_request_context()
            meta = ctx.metadata if ctx is not None and isinstance(ctx.metadata, dict) else {}
            return after_tool_mutation(
                store,
                session_key,
                action=action,
                task_id=task_id,
                status=status,
                actor_type=who_type,
                composer_mode=meta.get("composer_mode"),
                touched=touched,
                call_index=call_index,
                subagent=in_subagent,
            )
        except Exception:
            logger.debug("board auto-advance failed", exc_info=True)
            return []

    def _dispatch(
        self,
        store: ProjectBoardStore,
        *,
        touched: list[str],
        action: str,
        task_id: str | None,
        milestone_id: str | None,
        title: str | None,
        description: str | None,
        status: str | None,
        priority: str | None,
        labels: list[str] | None,
        depends_on: list[str] | None,
        assignee: str | None,
        text: str | None,
        target_date: str | None,
        filter_status: str | None,
        who: str,
        who_type: str,
        acceptance: str | None = None,
        validation: str | None = None,
        agent_hint: str | None = None,
        evidence: str | None = None,
        goal: str | None = None,
        constraints: list[str] | None = None,
        facts: list[str] | None = None,
        missing_info: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        step_ids: list[str] | None = None,
        ok: bool | None = None,
        progress: bool | None = None,
        reason: str | None = None,
        fingerprint: str | None = None,
        tokens: int | None = None,
        replace: bool | None = None,
        tasks: list[dict[str, Any]] | None = None,
        claim_next: bool | None = None,
        deferred: list[Any] | None = None,
    ) -> str:
        if action in _LEDGER_ACTIONS:
            return self._dispatch_ledger(
                store,
                action=action,
                replace=replace,
                status=status,
                task_id=task_id,
                who=who,
                goal=goal,
                constraints=constraints,
                facts=facts,
                missing_info=missing_info,
                acceptance_criteria=acceptance_criteria,
                step_ids=step_ids,
                ok=ok,
                progress_flag=progress,
                reason=reason,
                fingerprint=fingerprint,
                tokens=tokens,
                evidence=evidence,
                text=text,
            )

        if action == "list":
            tasks = store.read_tasks()
            if filter_status:
                if filter_status not in TASK_STATUSES:
                    raise BoardError(f"invalid filter_status: {filter_status}")
                tasks = [t for t in tasks if t["status"] == filter_status]
            milestones = store.read_milestones()
            if not tasks and not milestones:
                return "Board is empty. Use action=create to file the first task."
            lines: list[str] = []
            if milestones:
                lines.append(f"Milestones ({len(milestones)}):")
                for m in milestones:
                    date = f" due {m['target_date']}" if m.get("target_date") else ""
                    lines.append(f"  {m['id']} [{m['status']}] {m['title']}{date}")
                lines.append("")
            lines.append(f"Tasks ({len(tasks)}):")
            for task in tasks[:_LIST_LIMIT]:
                lines.append(f"  {_fmt_task(task)}")
            if len(tasks) > _LIST_LIMIT:
                lines.append(f"  … {len(tasks) - _LIST_LIMIT} more (use filter_status)")
            return "\n".join(lines)

        if action == "next":
            tasks = store.read_tasks()
            summary = plan_summary(tasks)
            by_id = {task["id"]: task for task in tasks}
            queue = summary["ready_queue"]
            if not queue:
                return self._no_work_explanation(summary, by_id)
            lines = [
                "Ready tasks, best first "
                "(critical path first, then most dependents, then priority):"
            ]
            for ready_id in queue[:_NEXT_LIMIT]:
                task = by_id[ready_id]
                note = summary["tasks"][ready_id]
                marks = []
                if note["on_critical_path"]:
                    marks.append("critical-path")
                if note["dependents"]:
                    marks.append(f"unblocks {note['dependents']}")
                suffix = f"  <- {', '.join(marks)}" if marks else ""
                lines.append(f"  {_fmt_task(task)}{suffix}")
            if len(queue) > _NEXT_LIMIT:
                lines.append(f"  … {len(queue) - _NEXT_LIMIT} more ready")
            active = summary["in_progress"]
            if active:
                lines.append(
                    f"Already in flight ({len(active)}): {', '.join(active[:10])}. "
                    "Finish or hand these off before claiming more."
                )
            return "\n".join(lines)

        if action == "plan":
            tasks = store.read_tasks()
            milestones = store.read_milestones()
            summary = plan_summary(tasks)
            by_id = {task["id"]: task for task in tasks}
            progress_map = milestone_progress(tasks, milestones)
            lines = [
                f"Plan: {summary['open_count']} open, {summary['done_count']} done, "
                f"{len(summary['ready_queue'])} ready, {len(summary['blocked'])} blocked."
            ]
            ledger = MissionLedgerStore(store.project_path).load()
            if ledger:
                lines.append(
                    f"Mission ledger v{ledger.get('version')} "
                    f"status={ledger.get('status')} goal={str(ledger.get('goal') or '')[:120]}"
                )
            if milestones:
                lines.append("")
                lines.append("Milestones:")
                for milestone in milestones:
                    counts = progress_map.get(milestone["id"], {})
                    date = f" due {milestone['target_date']}" if milestone.get("target_date") else ""
                    lines.append(
                        f"  {milestone['id']} [{milestone['status']}] {milestone['title']}{date}"
                        f" - {counts.get('done', 0)}/{counts.get('total', 0)} done"
                        f", {counts.get('blocked', 0)} blocked"
                    )
            if summary["critical_path"]:
                lines.append("")
                lines.append(
                    "Critical path (longest chain of open work, "
                    f"{len(summary['critical_path'])} tasks, dependencies first):"
                )
                for path_id in summary["critical_path"]:
                    lines.append(f"  {_fmt_task(by_id[path_id])}")
            if summary["ready_queue"]:
                lines.append("")
                lines.append("Ready now:")
                for ready_id in summary["ready_queue"][:_NEXT_LIMIT]:
                    lines.append(f"  {_fmt_task(by_id[ready_id])}")
            if summary["blocked"]:
                lines.append("")
                lines.append("Blocked:")
                for entry in summary["blocked"][:_NEXT_LIMIT]:
                    task = by_id[entry["id"]]
                    if entry["blocked_by"]:
                        why = "waiting on " + ", ".join(entry["blocked_by"])
                    else:
                        why = "parked in blocked status"
                    lines.append(f"  {_fmt_task(task)}  <- {why}")
            defects = self._graph_defects(summary)
            if defects:
                lines.append("")
                lines.extend(defects)
            return "\n".join(lines)

        if action == "get":
            if not task_id:
                raise BoardError("get requires task_id")
            task = store.get_task(task_id)
            return _fmt_task(task, verbose=True)

        if action == "create" and tasks:
            # A plan filed one task per call cost one model round trip each
            # (measured: 6 creates, 6 steps). One call files them all.
            created: list[dict[str, Any]] = []
            by_title: dict[str, str] = {}
            for item in tasks:
                if not isinstance(item, dict) or not str(item.get("title") or "").strip():
                    raise BoardError("each item in tasks needs a title")
                deps = [
                    by_title.get(str(dep).strip().lower(), str(dep))
                    for dep in (item.get("depends_on") or [])
                ]
                task = store.create_task(
                    title=str(item["title"]),
                    description=item.get("description"),
                    status=status,
                    priority=item.get("priority") or priority,
                    labels=labels,
                    depends_on=deps or None,
                    assignee=self._parse_assignee(assignee),
                    milestone_id=milestone_id,
                    acceptance=item.get("acceptance"),
                    validation=item.get("validation"),
                    agent=agent_hint,
                    evidence=None,
                    actor=who,
                    actor_type=who_type,
                )
                by_title[str(item["title"]).strip().lower()] = task["id"]
                touched.append(task["id"])
                created.append(task)
            return f"{len(created)} tasks created:\n" + "\n".join(
                f"  {_fmt_task(task)}" for task in created
            )

        if action == "create":
            if not title:
                raise BoardError("create requires title")
            task = store.create_task(
                title=title,
                description=description,
                status=status,
                priority=priority,
                labels=labels,
                depends_on=depends_on,
                assignee=self._parse_assignee(assignee),
                milestone_id=milestone_id,
                acceptance=acceptance,
                validation=validation,
                agent=agent_hint,
                evidence=evidence,
                actor=who,
                actor_type=who_type,
            )
            touched.append(task["id"])
            return f"Task created: {_fmt_task(task)}"

        if action == "update":
            if not task_id:
                raise BoardError("update requires task_id")
            fields: dict[str, Any] = {}
            if title is not None:
                fields["title"] = title
            if description is not None:
                fields["description"] = description
            if status is not None:
                fields["status"] = status
            if priority is not None:
                fields["priority"] = priority
            if labels is not None:
                fields["labels"] = labels
            if depends_on is not None:
                fields["depends_on"] = depends_on
            if assignee is not None:
                fields["assignee"] = self._parse_assignee(assignee)
            if milestone_id is not None:
                fields["milestone_id"] = milestone_id
            if acceptance is not None:
                fields["acceptance"] = acceptance
            if validation is not None:
                fields["validation"] = validation
            if agent_hint is not None:
                fields["agent"] = agent_hint
            if evidence is not None:
                fields["evidence"] = evidence
            if not fields:
                raise BoardError("update requires at least one field")
            task = store.update_task(task_id, fields=fields, actor=who, actor_type=who_type)
            touched.append(task["id"])
            extra = ""
            if "status" in fields:
                extra = self._autonomy_after_status(
                    store, task, who=who, who_type=who_type, deferred=deferred,
                )
            return f"Task updated: {_fmt_task(task)}{extra}"

        if action == "move":
            if not task_id or not status:
                raise BoardError("move requires task_id and status")
            current = store.get_task(task_id)
            if current.get("status") == status and (
                evidence is None or current.get("evidence")
            ):
                # Closing the same step twice was measured at one extra model
                # step per repeat; a quiet rewrite would invite the next one.
                return (
                    f"Task {current['id']} is already {status}; nothing changed. "
                    "Report each step once and move on."
                )
            move_fields: dict[str, Any] = {"status": status}
            if evidence is not None:
                move_fields["evidence"] = evidence
            task = store.update_task(
                task_id, fields=move_fields, actor=who, actor_type=who_type,
            )
            touched.append(task["id"])
            extra = self._autonomy_after_status(
                store, task, who=who, who_type=who_type, deferred=deferred,
            )
            if status == "done":
                extra += self._record_ledger_done(store.project_path, task["id"], evidence, who)
                if claim_next:
                    extra += self._claim_next_ready(
                        store, touched, who=who, who_type=who_type, closed_id=task["id"],
                    )
            return f"Task moved: {_fmt_task(task)}{extra}"

        if action == "claim":
            if not task_id:
                raise BoardError("claim requires task_id")
            task = store.claim_task(task_id, actor=who, actor_type=who_type)
            touched.append(task["id"])
            extra = self._autonomy_after_claim(store, task, who=who, who_type=who_type)
            return f"Task claimed: {_fmt_task(task)}{extra}"

        if action == "comment":
            if not task_id or not text:
                raise BoardError(
                    'comment requires task_id and text. '
                    'Example: action="comment", task_id="t1", text="Found the root cause"'
                )
            task = store.comment_task(task_id, text=text, actor=who, actor_type=who_type)
            touched.append(task["id"])
            return f"Comment added to {task['id']} ({len(task['comments'])} comments)."

        if action == "delete":
            if not task_id:
                raise BoardError("delete requires task_id")
            store.delete_task(task_id, actor=who, actor_type=who_type)
            return f"Task deleted: {task_id}"

        if action == "sync_github":
            from navin.board.github_sync import import_issues_to_board, push_task_to_issue

            project = store.project_path
            if task_id:
                task = store.get_task(task_id)
                if task.get("issue_url"):
                    return f"Task {task_id} already has an issue: {task['issue_url']}"
                result = push_task_to_issue(project, task)
                if not result["ok"]:
                    return f"Issue not created: {result['detail']}"
                store.update_task(
                    task_id,
                    fields={"issue_url": result["issue_url"]},
                    actor=who,
                    actor_type=who_type,
                )
                touched.append(task_id)
                self._notify(
                    title=f"Issue created for {task_id}",
                    detail=result["issue_url"],
                    key=f"board:{task_id}",
                )
                return f"Issue created: {result['issue_url']}"
            result = import_issues_to_board(project, store, actor=who)
            if not result["ok"]:
                return f"Issue sync failed: {result['detail']}"
            if result["imported"]:
                self._notify(
                    title="Forge issues imported to board",
                    detail=result["detail"],
                    key="board:sync-github",
                )
            return f"Issue sync: {result['detail']}"

        if action == "milestone_create":
            if not title:
                raise BoardError("milestone_create requires title")
            milestone = store.create_milestone(
                title=title,
                description=description,
                target_date=target_date,
                status=status if status in ("planned", "active", "done") else None,
                actor=who,
                actor_type=who_type,
            )
            return (
                f"Milestone created: {milestone['id']} [{milestone['status']}] "
                f"{milestone['title']}"
            )

        if action == "milestone_update":
            if not milestone_id:
                raise BoardError("milestone_update requires milestone_id")
            mfields: dict[str, Any] = {}
            if title is not None:
                mfields["title"] = title
            if description is not None:
                mfields["description"] = description
            if target_date is not None:
                mfields["target_date"] = target_date
            if status is not None:
                mfields["status"] = status
            if not mfields:
                raise BoardError("milestone_update requires at least one field")
            milestone = store.update_milestone(
                milestone_id, fields=mfields, actor=who, actor_type=who_type,
            )
            return (
                f"Milestone updated: {milestone['id']} [{milestone['status']}] "
                f"{milestone['title']}"
            )

        if action == "log":
            if not text:
                raise BoardError(
                    'log requires text. '
                    'Example: action="log", text="Started Android bootstrap", actor="navin"'
                )
            store.log_activity(
                kind="note",
                actor=who,
                actor_type=who_type,
                detail=text,
                task_id=task_id,
            )
            return "Timeline entry recorded."

        return self.unknown_action(action)

    # -- Autonomy hooks --------------------------------------------------------

    def _notify(
        self,
        *,
        title: str,
        detail: str | None = None,
        level: str = "info",
        key: str | None = None,
    ) -> None:
        publish_notification(
            self._bus,
            title=title,
            detail=detail,
            level=level,
            source="board",
            key=key,
        )

    async def _verify_before_done(self, store: Any, task: dict[str, Any]) -> str:
        """Run verify for a done that still lacks a fresh green run.

        The store refuses an agent's done on a test/lint/verify step without a
        recent passing run, so the model used to call lint, test_run and verify
        (three round trips, 5-15s each) before every move. Running the check
        here makes closing a step one call. Returns "" when no run was needed,
        a short verdict when it passed, or "Error..." when it failed.
        """
        validation = task.get("validation")
        if validation not in ("test", "lint", "verify"):
            return ""
        from navin.quality.verification_log import refusal_to_close_without_proof

        if not refusal_to_close_without_proof(store.project_path, require_tests=(validation == "test")):
            return ""
        from navin.agent.tools.quality import VerifyTool

        verdict = await VerifyTool(workspace=store.project_path).execute(
            action="check", with_tests=True
        )
        text = verdict if isinstance(verdict, str) else str(getattr(verdict, "content", verdict))
        problem = refusal_to_close_without_proof(store.project_path, require_tests=(validation == "test"))
        if problem:
            # The standard prerequisite wording, so the runner treats it as
            # "validation pending" (no progress, retry allowed once fixed)
            # rather than a hard tool failure.
            return (
                "Validation pending: task remains open.\n"
                f"{problem}\n"
                f"verify ran before closing {task.get('id')} and it is not green. Fix what it "
                "reports, then move again. If the failure is unrelated to this step and "
                "pre-existing, do not loop on it: record that in the evidence, lower the "
                "step's validation (board update validation=lint) or report the blocker.\n"
                f"{text[:2000]}"
            )
        return "Verified before closing: " + text.splitlines()[0][:200] if text else ""

    @staticmethod
    def _record_ledger_done(
        project_path: Any, task_id: str, evidence: str | None, who: str | None
    ) -> str:
        """Record a finished step in the mission ledger, when there is one.

        The model used to follow every move-to-done with its own
        ledger_progress call: one model round trip (5-8s) for a write that
        takes milliseconds. Done is the progress record.
        """
        from navin.board.ledger import action_fingerprint

        try:
            store = MissionLedgerStore(project_path)
            ledger = store.load()
            if not ledger:
                return ""
            from navin.board.plan import mission_step_ids

            steps = mission_step_ids(ledger)
            if steps and str(task_id) not in steps:
                # A task from another chat's work is not this mission's
                # progress: recording it moved the mission's current step
                # onto unrelated tasks and kept the mission alive everywhere.
                return ""
            ledger = store.record_step_result(
                ledger,
                task_id,
                ok=True,
                evidence=evidence,
                progress=True,
                fingerprint=action_fingerprint(task_id, True, (evidence or "")[:80]),
                actor=who or "agent",
            )
            store.save(ledger)
        except Exception:
            return ""
        return " Ledger progress recorded."

    def _claim_next_ready(
        self,
        store: Any,
        touched: list[str],
        *,
        who: str | None,
        who_type: str | None,
        closed_id: str | None = None,
    ) -> str:
        """Claim the next ready step of the plan just worked on (claim_next=true).

        The plan is the closed task's dependency group plus the tasks filed
        with it. A ready task outside it (another mission, an old card) is
        named but not claimed, so the turn stays on the work in hand.
        """
        from navin.board.plan import plan_members

        tasks = store.read_tasks()
        queue = plan_summary(tasks)["ready_queue"]
        if not queue:
            return " No ready task left."
        if closed_id:
            plan = plan_members(tasks, closed_id)
            in_plan = [task_id for task_id in queue if task_id in plan]
            if not in_plan:
                return (
                    " No ready step left in this plan. Next ready task on the board: "
                    f"{queue[0]} (not claimed: it belongs to other work)."
                )
            queue = in_plan
        task = store.claim_task(queue[0], actor=who, actor_type=who_type)
        touched.append(task["id"])
        extra = self._autonomy_after_claim(store, task, who=who, who_type=who_type)
        return f"\nNext task claimed: {_fmt_task(task)}{extra}"

    def _autonomy_after_claim(
        self,
        store: ProjectBoardStore,
        task: dict[str, Any],
        *,
        who: str,
        who_type: str,
    ) -> str:
        """Create the task's isolated branch when board autonomy allows it."""
        try:
            project = store.project_path
            if not should_auto_branch(project):
                return ""
            result = start_task_branch(project, task)
            if not result["ok"]:
                store.comment_task(
                    task["id"],
                    text=f"Auto-branch skipped: {result['detail']}",
                    actor=who,
                    actor_type=who_type,
                )
                return f"\nAuto-branch skipped: {result['detail']}"
            branch = result["branch"]
            base = result.get("base")
            store.update_task(
                task["id"],
                fields={"branch": branch},
                actor=who,
                actor_type=who_type,
            )
            moved = f"Working on isolated branch {branch}"
            if base and base != branch:
                moved += f" (left {base})"
            store.comment_task(
                task["id"], text=moved, actor=who, actor_type=who_type,
            )
            # A branch switch the user did not ask for has to be loud: they
            # find out on their next commit otherwise. Warning level, both
            # branch names, and where the switch is turned off.
            self._notify(
                title=(
                    f"Git branch changed: {base} -> {branch}"
                    if base and base != branch
                    else f"Task {task['id']} claimed on {branch}"
                ),
                detail=(
                    f"Task {task['id']} ({task.get('title') or 'untitled'}). "
                    "Autonomy auto-branch is on for this project; turn it off "
                    "in the Guardrails panel to commit on your own branch."
                ),
                level="warning",
                key=f"board:{task['id']}",
            )
            left = f" (you were on {base})" if base and base != branch else ""
            return (
                f"\nIsolated branch ready: {branch}{left} ({result['detail']}). "
                "Commit your work here; the PR opens automatically on done. "
                "Tell the user you switched branches and name both branches."
            )
        except Exception:
            logger.exception("board autonomy claim hook failed")
            return ""

    def _sync_ledger_with_status(
        self,
        store: ProjectBoardStore,
        task: dict[str, Any],
        *,
        who: str,
    ) -> str:
        """Mirror a done/blocked status change into the mission ledger.

        The board and the ledger described the same work but only spoke when
        the model remembered to call ledger_progress after a move: a task
        moved to done left the ledger step pending, and every later replan
        reasoned from stale progress. A status change carries its own
        verdict, so it records itself. Best-effort by design: no ledger, or a
        step the ledger does not track, changes nothing.
        """
        status = task.get("status")
        if status not in ("done", "blocked"):
            return ""
        try:
            ledger_store = MissionLedgerStore(store.project_path)
            ledger = ledger_store.load()
            if not ledger:
                return ""
            task_id = str(task.get("id") or "")
            ok = status == "done"
            prog = ledger.get("progress") or {}
            # Idempotence: a move that repeats what the ledger already says
            # (usually because the model did call ledger_progress) must not
            # inflate stall counters or loop fingerprints.
            if (
                prog.get("current_step_id") == task_id
                and bool(prog.get("last_result_ok")) is ok
            ):
                return ""
            evidence = str(task.get("evidence") or "").strip()
            ledger = ledger_store.record_step_result(
                ledger,
                task_id,
                ok=ok,
                evidence=evidence or f"board status -> {status}",
                progress=ok,
                actor=who,
            )
            ledger_store.save(ledger)
            return "\nLedger synced with the status change."
        except Exception:
            logger.exception("board -> ledger status sync failed")
            return ""

    def _autonomy_after_status(
        self,
        store: ProjectBoardStore,
        task: dict[str, Any],
        *,
        who: str,
        who_type: str,
        deferred: list[Any] | None = None,
    ) -> str:
        """PR on done, issue close on done, warning on blocked; ledger sync.

        With *deferred* the forge work (push, PR, issue close) is queued there
        instead of running inline, so the caller can run it outside the board
        dispatch lock.
        """
        self._sync_ledger_with_status(store, task, who=who)
        try:
            status = task.get("status")
            if status == "blocked":
                self._notify(
                    title=f"Task {task['id']} is blocked",
                    detail=task.get("title"),
                    level="warning",
                    key=f"board:{task['id']}",
                )
                return ""
            if status != "done":
                return ""
        except Exception:
            logger.exception("board autonomy status hook failed")
            return ""
        if deferred is not None:
            snapshot = dict(task)
            deferred.append(
                lambda: self._forge_after_done(
                    store, snapshot, who=who, who_type=who_type,
                )
            )
            return ""
        return self._forge_after_done(store, task, who=who, who_type=who_type)

    def _forge_after_done(
        self,
        store: ProjectBoardStore,
        task: dict[str, Any],
        *,
        who: str,
        who_type: str,
    ) -> str:
        """Open the task's PR/MR and close its linked issue; never raises."""
        try:
            project = store.project_path
            lines: list[str] = []
            # Without auto-branch the task has no branch of its own, and the
            # only thing a PR attempt can do is fail on a ref that was never
            # created. Staying quiet beats a misleading error on every done.
            has_branch = bool(str(task.get("branch") or "").strip())
            if should_open_pr(project) and has_branch:
                from navin.board.github_sync import open_task_pr

                result = open_task_pr(project, task)
                # GitLab calls it a merge request; the board says what the
                # user will actually see on their forge.
                label = str(result.get("request_label") or "pull request")
                label = label[:1].upper() + label[1:]
                if result["ok"]:
                    fields: dict[str, str] = {"pr_url": result["pr_url"]}
                    if result.get("head_sha"):
                        fields["head_sha"] = str(result["head_sha"])
                    # Keep the refreshed task: the issue closed just below cites
                    # the pull request that fixed it.
                    task = store.update_task(
                        task["id"],
                        fields=fields,
                        actor=who,
                        actor_type=who_type,
                    )
                    sha_note = (
                        f" @ {result['head_sha']}" if result.get("head_sha") else ""
                    )
                    store.comment_task(
                        task["id"],
                        text=f"{label}: {result['pr_url']}{sha_note}",
                        actor=who,
                        actor_type=who_type,
                    )
                    self._notify(
                        title=f"{label} opened for {task['id']}",
                        detail=result["pr_url"],
                        level="success",
                        key=f"board:{task['id']}",
                    )
                    lines.append(f"{label} opened: {result['pr_url']}")
                else:
                    store.comment_task(
                        task["id"],
                        text=f"Auto-PR skipped: {result['detail']}",
                        actor=who,
                        actor_type=who_type,
                    )
                    lines.append(f"Auto-PR skipped: {result['detail']}")
            if should_sync_issues(project) and task.get("issue_url"):
                from navin.board.github_sync import close_task_issue

                closed = close_task_issue(project, task)
                if closed["ok"]:
                    lines.append(f"Linked issue closed: {task['issue_url']}")
                else:
                    lines.append(f"Issue close skipped: {closed['detail']}")
            return ("\n" + "\n".join(lines)) if lines else ""
        except Exception:
            logger.exception("board autonomy done hook failed")
            return ""

    def _dispatch_ledger(
        self,
        store: ProjectBoardStore,
        *,
        action: str,
        task_id: str | None,
        who: str,
        goal: str | None,
        constraints: list[str] | None,
        facts: list[str] | None,
        missing_info: list[str] | None,
        acceptance_criteria: list[str] | None,
        step_ids: list[str] | None,
        ok: bool | None,
        progress_flag: bool | None,
        reason: str | None,
        fingerprint: str | None,
        tokens: int | None,
        evidence: str | None,
        text: str | None,
        replace: bool | None = None,
        status: str | None = None,
    ) -> str:
        ledger_store = MissionLedgerStore(store.project_path)

        if action == "ledger_timeline":
            from navin.board.timeline import build_timeline

            return json.dumps(build_timeline(store.project_path), ensure_ascii=False)

        if action in ("ledger_restore", "ledger_diff"):
            # text carries the target: "N" or "coherent" for restore, "A..B" for diff.
            from navin.board.timeline import diff_versions, restore_mission

            target = (text or "").strip()
            try:
                if action == "ledger_restore":
                    if not target:
                        return "Error: ledger_restore needs text=<version> or text=coherent"
                    out = restore_mission(
                        store.project_path, target, reason=reason or "", actor=who or "agent"
                    )
                else:
                    left, sep, right = target.partition("..")
                    if not sep:
                        return "Error: ledger_diff needs text=<A>..<B> (two versions)"
                    out = diff_versions(store.project_path, int(left), int(right))
            except (BoardError, ValueError) as exc:
                return f"Error: {exc}"
            return json.dumps(out, ensure_ascii=False)

        if action in ("ledger_replay", "ledger_compare"):
            # ledger_replay: task_id = step, text = optional source version.
            # ledger_compare: text = "A..B".
            from navin.board.timeline import compare_strategies, replay_step

            target = (text or "").strip()
            try:
                if action == "ledger_replay":
                    if not task_id:
                        return "Error: ledger_replay needs task_id=<step id> (text=<version> optional)"
                    out = replay_step(
                        store.project_path,
                        task_id,
                        from_version=int(target.lstrip("v")) if target else None,
                        reason=reason or "",
                        actor=who or "agent",
                    )
                else:
                    left, sep, right = target.partition("..")
                    if not sep:
                        return "Error: ledger_compare needs text=<A>..<B> (two versions)"
                    out = compare_strategies(store.project_path, int(left), int(right))
            except (BoardError, ValueError) as exc:
                return f"Error: {exc}"
            return json.dumps(out, ensure_ascii=False)

        if action == "ledger_get":
            ledger = ledger_store.load()
            if not ledger:
                return "No mission ledger yet. Use action=ledger_init with a goal."
            lines = ledger_store.runtime_lines(ledger)
            steps = ledger.get("steps") or []
            if steps:
                lines.append(f"Steps ({len(steps)}):")
                for step in steps[:20]:
                    lines.append(
                        f"  {step.get('id')} [{step.get('status')}] {step.get('title')}"
                        f" validation={step.get('validation')}"
                    )
            return "\n".join(lines)

        if action == "ledger_init":
            if not goal:
                raise BoardError("ledger_init requires goal")
            open_mission = ledger_store.load()
            if (
                open_mission
                and not replace
                and str(open_mission.get("status")) in _LIVE_LEDGER_STATUSES
            ):
                raise BoardError(open_mission_refusal(open_mission))
            steps: list[dict[str, Any]] = []
            if step_ids:
                by_id = {t["id"]: t for t in store.read_tasks()}
                for sid in step_ids:
                    task = by_id.get(sid)
                    if not task:
                        continue
                    steps.append(
                        {
                            "id": task["id"],
                            "title": task["title"],
                            "status": task["status"],
                            "depends_on": list(task.get("depends_on") or []),
                            "agent": task.get("agent"),
                            "acceptance": task.get("acceptance") or "",
                            "validation": task.get("validation") or "none",
                            "retry_count": task.get("retry_count") or 0,
                            "max_retries": task.get("max_retries") or 3,
                            "evidence": task.get("evidence") or "",
                        }
                    )
            ledger = ledger_store.create(
                goal=goal,
                constraints=constraints,
                facts=facts,
                missing_info=missing_info,
                acceptance_criteria=acceptance_criteria,
                steps=steps,
                status="draft",
                actor=who,
            )
            return (
                f"Mission ledger created v{ledger['version']} "
                f"status={ledger['status']} steps={len(ledger.get('steps') or [])}"
            )

        if action == "ledger_update":
            ledger = ledger_store.load()
            if not ledger:
                raise BoardError("no mission ledger; call ledger_init first")
            proof_note = ""
            if evidence:
                proofs = parse_criteria_evidence(evidence)
                if not proofs:
                    raise BoardError(
                        "ledger_update evidence must name the criterion it proves, "
                        "one per line: '1: pytest tests/test_x.py -> 12 passed'"
                    )
                refused = ledger_store.record_criteria_evidence(ledger, proofs, actor=who)
                if refused:
                    raise BoardError("evidence refused: " + "; ".join(refused))
                ledger_store.save(ledger)
                proof_note = f" Proof recorded for criteria {', '.join(map(str, sorted(proofs)))}."
            if status == "done":
                by_id = {task["id"]: task for task in store.read_tasks()}
                unfinished = [
                    by_id.get(step["id"], step)
                    for step in ledger.get("steps") or []
                    if by_id.get(step["id"], step).get("status") not in {"done", "completed"}
                ]
                if unfinished:
                    pending = ", ".join(
                        f"{step['id']} [{step.get('status')}]" for step in unfinished[:10]
                    )
                    raise BoardError(
                        f"cannot mark mission done: unfinished steps: {pending}. "
                        "Continue the unfinished work and validate each step before closing it. "
                        "Repeating ledger_update status=done does not complete a step."
                    )
                missing = unproven_criteria(ledger)
                if missing:
                    pending = "; ".join(f"{n}. {text[:120]}" for n, text in missing[:10])
                    raise BoardError(
                        f"cannot mark mission done: acceptance criteria without proof: {pending}. "
                        "Verify each one for real, then pass evidence='N: command -> result' "
                        "(one line per criterion) with status=done."
                    )
            fields: dict[str, Any] = {}
            # How a mission is closed, which is what frees the project for the
            # next one without abandoning this one.
            if status is not None:
                if status not in LEDGER_STATUSES:
                    raise BoardError(
                        f"unknown mission status: {status} "
                        f"(use one of {', '.join(sorted(LEDGER_STATUSES))})"
                    )
                fields["status"] = status
            if goal is not None:
                fields["goal"] = goal
            if constraints is not None:
                fields["constraints"] = constraints
            if facts is not None:
                fields["facts"] = facts
            if missing_info is not None:
                fields["missing_info"] = missing_info
            if acceptance_criteria is not None:
                fields["acceptance_criteria"] = acceptance_criteria
            if not fields:
                if proof_note:
                    return f"Mission ledger v{ledger['version']}.{proof_note}"
                raise BoardError("ledger_update requires at least one field")
            previous_version = ledger["version"]
            ledger = ledger_store.apply_manual_edit(ledger, fields, actor=who)
            if ledger["version"] == previous_version:
                return ToolResult(
                    f"Mission ledger unchanged at v{previous_version}; no new progress. "
                    "The requested values are already saved. Do not repeat this update. "
                    "Continue the unfinished implementation or validation; if all work is "
                    "complete, report the result directly in chat.",
                    made_progress=False,
                )
            ledger_store.save(ledger)
            return f"Mission ledger updated to v{ledger['version']}{proof_note}"

        if action == "ledger_progress":
            if not task_id:
                raise BoardError("ledger_progress requires task_id")
            if ok is None:
                raise BoardError("ledger_progress requires ok=true|false")
            ledger = ledger_store.load()
            if not ledger:
                raise BoardError("no mission ledger; call ledger_init first")
            fp = fingerprint or action_fingerprint(
                task_id, ok, (evidence or text or "")[:80]
            )
            ledger = ledger_store.record_step_result(
                ledger,
                task_id,
                ok=bool(ok),
                evidence=evidence or text,
                progress=bool(progress_flag) if progress_flag is not None else bool(ok),
                fingerprint=fp,
                actor=who,
            )
            if tokens:
                ledger = ledger_store.consume_budget(
                    ledger, tokens=int(tokens), actor=who
                )
            pause_reason = ledger_store.should_pause(ledger)
            if pause_reason and ledger.get("status") != "paused":
                ledger = ledger_store.pause(ledger, reason=pause_reason, actor=who)
            ledger_store.save(ledger)
            prog = ledger.get("progress") or {}
            return (
                f"Progress recorded for {task_id}: ok={ok} "
                f"stall={prog.get('stall_count')} loop={prog.get('loop_detected')} "
                f"replan_needed={prog.get('replan_needed')} status={ledger.get('status')}"
            )

        if action == "ledger_replan":
            if not task_id:
                raise BoardError("ledger_replan requires task_id (failed step)")
            ledger = ledger_store.load()
            if not ledger:
                raise BoardError("no mission ledger; call ledger_init first")
            ledger = ledger_store.local_replan(
                ledger,
                task_id,
                reason=reason or f"local replan around {task_id}",
                facts=facts,
                missing_info=missing_info,
                actor=who,
            )
            ledger_store.save(ledger)
            invalidated = list(
                (ledger.get("progress") or {}).get("last_invalidated") or []
            )
            for step_id in invalidated:
                try:
                    task = store.get_task(step_id)
                except BoardError:
                    continue
                if task.get("status") in ("done", "cancelled"):
                    continue
                if task.get("status") != "planned":
                    store.update_task(
                        step_id,
                        fields={"status": "planned"},
                        actor=who,
                        actor_type="agent",
                    )
            return (
                f"Local replan applied around {task_id}; "
                f"ledger now v{ledger['version']}"
            )

        if action == "ledger_pause":
            ledger = ledger_store.load()
            if not ledger:
                raise BoardError("no mission ledger; call ledger_init first")
            ledger = ledger_store.pause(
                ledger, reason=reason or "manual", actor=who
            )
            ledger_store.save(ledger)
            return f"Mission paused ({ledger.get('pause_reason')}) v{ledger['version']}"

        if action == "ledger_resume":
            ledger = ledger_store.load()
            if not ledger:
                raise BoardError("no mission ledger; call ledger_init first")
            ledger = ledger_store.resume(ledger, actor=who)
            ledger_store.save(ledger)
            return f"Mission resumed v{ledger['version']} status={ledger['status']}"

        raise BoardError(f"unknown ledger action: {action}")
