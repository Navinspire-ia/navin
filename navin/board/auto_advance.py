# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep a run's plan panel moving without waiting for the model to say so.

The chat plan panel (``session_plan``) only changes when a task status changes,
and statuses only changed when the model called ``board``. Models forget, and
the panel then sat on step one for the whole run. These rules help the model
move the focused tasks of one session forward. The model stays responsible
for closing steps; the runtime only fills obvious gaps, and when in doubt it
leaves a step open rather than closing it wrongly.

Only tasks this session's agent *owns* are ever moved: tasks it created, or
claimed itself, or that R1/R2 claimed for it. A task the session merely
commented on, a human's task, or a task assigned to someone else is never
claimed or closed here. Ownership is persisted next to the session focus file
so a restart does not turn an unknown step into a closable one.

"Work" below means a successful tool call other than ``board``, a few
bookkeeping tools and read-only lookups (read_file, grep, ...); an ``exec``
(or a test runner) that exited non-zero is a failure, not work. Work is attributed to a step only when it ran *after* the
step became active, using the turn's ordered tool events.

R1 plan filed     - after the model creates a task in a working turn (composer
                    mode not plan/ask), if none of the session's focused tasks
                    is active, claim the first ready owned one ("auto-claimed").
R2 step closed    - after the model closes a focused task as ``done``, if none
                    is still active, claim the next ready owned task
                    ("auto-claimed").
R3 moved on       - when the model claims (or moves to ``in_progress``) focused
                    task X while an earlier owned step Y is still
                    ``in_progress``, Y is marked as superseded. At the end of
                    the turn, Y is closed as ``done`` if work ran while it was
                    active (in this turn or an earlier one); an auto-claimed Y
                    that saw no work returns to ``planned``; a Y the model
                    claimed itself with no recorded work stays open.
R4 turn finished  - at the end of a working turn that stopped ``completed``,
                    whose answer neither asks the user something nor reports
                    a failure, whose last tool call did not fail, and with no
                    pending code validation: close the current step only if
                    the model claimed it itself (never an auto-claimed one),
                    it is exactly ``in_progress``, and work ran after it became
                    active in this turn. A turn that ends on a question returns
                    unworked auto-claimed steps to ``planned`` instead.
                    Cron, heartbeat and subagent-result turns are skipped.

Gates every auto-close respects:

- only ``in_progress`` steps are closed: ``review`` / ``audit`` are someone
  else's call;
- pending code validation (the same gate the runner applies to an explicit
  ``board move done``) leaves the task open;
- the store's own gates still run. A task that requires evidence (acceptance
  criteria or validation=test|lint|verify) is closed only when a fresh green
  verification is recorded for the project, and that recorded run becomes the
  evidence. Otherwise it stays open;
- the status is re-checked inside the store lock, so a concurrent edit wins.

Subagents are ignored: they share the parent's session focus and claim in
parallel, so "started X" says nothing about the parent's step.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from loguru import logger

from navin.board import session_focus
from navin.board.plan import (
    ACTIVE_STATUSES,
    PARKED_STATUSES,
    blocking_dependencies,
    plan_members,
)
from navin.board.store import BoardError, ProjectBoardStore

# Statuses a step can be claimed from. ``fix``/``audit``/``review`` are left to
# the model: they mean someone already looked at the step.
_CLAIMABLE = frozenset({"backlog", "planned"})
# The only status the runtime ever closes from.
_CLOSABLE = "in_progress"
_NON_WORKING_MODES = frozenset({"plan", "ask"})
_ACTOR = "agent"

# Tool calls that do not advance a step by themselves.
_NON_WORK_TOOLS = frozenset({
    "board", "message", "set_composer_mode", "ask_user", "cron", "create_goal",
    "update_goal", "notify", "recall", "skill", "policy_next", "open_file_preview",
    "open_in_editor", "open_preview", "open_terminal", "present_artifact",
})
# Looking around is not finishing a step: a turn that only read files (often
# while filing the plan) must not close or "work" the step it landed on.
_READ_ONLY_TOOLS = frozenset({
    "read_file", "list_dir", "find_files", "grep", "code_index", "lsp",
    "list_exec_sessions", "web_search", "web_fetch", "scrape",
})
_EXIT_CODE_TOOLS = frozenset({"exec", "write_stdin", "run_cli_app"})
_VERIFY_TOOLS = frozenset({"test_run", "verify", "lint"})
_VERIFY_FAILURE = ("FAIL", "VERDICT_TEST_FAILURES", "VERDICT_LINT_ERRORS")

# Bounds for the persisted ownership registry (same as the session focus).
_MAX_TASKS_PER_SESSION = 40
_MAX_SESSIONS = 64
_REGISTRY_FILE = "session-plan-owners.json"

# Turn metadata / senders that mark a turn as unrelated to the user's plan.
_SKIP_SENDERS = frozenset({"cron", "subagent", "heartbeat"})
_SKIP_META_KEYS = ("_cron_trigger", "_cron_turn", "heartbeat", "subagent")


@dataclass
class _Owned:
    auto: bool = False  # claimed by R1/R2, never claimed by the model itself
    worked: bool = False  # work was recorded while it was active


@dataclass
class _TurnLog:
    turn_id: str | None
    calls: int = 0  # board calls executed in this turn, in order
    active_at: dict[str, int] = field(default_factory=dict)  # task -> board call index
    superseded: dict[str, int] = field(default_factory=dict)  # task -> board call index


_owned: OrderedDict[str, dict[str, _Owned]] = OrderedDict()
_owned_loaded = False
_turns: dict[str, _TurnLog] = {}
_auto_guard = threading.RLock()

# Bound per turn by the agent loop: returns True while code validation is
# pending. Absent (None) outside a loop turn, which counts as "not pending".
_validation_probe: ContextVar[Callable[[], bool] | None] = ContextVar(
    "navin_board_validation_probe", default=None,
)


@contextmanager
def validation_probe(probe: Callable[[], bool] | None) -> Iterator[None]:
    token = _validation_probe.set(probe)
    try:
        yield
    finally:
        _validation_probe.reset(token)


def validation_pending() -> bool:
    probe = _validation_probe.get()
    if probe is None:
        return False
    try:
        return bool(probe())
    except Exception:
        # Unknown means pending: never close a step on a failed probe.
        return True


def working_mode(composer_mode: Any) -> bool:
    mode = str(composer_mode or "").strip().lower()
    return mode not in _NON_WORKING_MODES


def is_subagent_context(metadata: Mapping[str, Any] | None, sender_id: Any = None) -> bool:
    if str(sender_id or "").strip().lower() == "subagent":
        return True
    return bool(isinstance(metadata, Mapping) and metadata.get("subagent"))


def unrelated_turn(
    metadata: Mapping[str, Any] | None, sender_id: Any = None, session_key: str | None = None,
) -> bool:
    """Cron, heartbeat and subagent-result turns never move the user's plan."""
    if str(sender_id or "").strip().lower() in _SKIP_SENDERS:
        return True
    if str(session_key or "").strip().lower() == "heartbeat":
        return True
    if isinstance(metadata, Mapping):
        if any(metadata.get(name) for name in _SKIP_META_KEYS):
            return True
        if str(metadata.get("session_key") or "").strip().lower() == "heartbeat":
            return True
    return False


# -- Ownership registry (persisted) -------------------------------------------


def _registry_path() -> Path:
    from navin.config.paths import get_webui_dir

    return get_webui_dir() / _REGISTRY_FILE


def _ensure_loaded() -> None:
    global _owned_loaded
    if _owned_loaded:
        return
    _owned_loaded = True
    path = _registry_path()
    try:
        if not path.is_file():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        for key, entries in raw.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(entries, dict):
                continue
            owned: dict[str, _Owned] = {}
            for tid, flags in entries.items():
                if isinstance(tid, str) and tid.strip() and isinstance(flags, dict):
                    owned[tid.strip()] = _Owned(
                        auto=bool(flags.get("auto")), worked=bool(flags.get("worked")),
                    )
            if owned:
                _owned[key.strip()] = owned
        while len(_owned) > _MAX_SESSIONS:
            _owned.popitem(last=False)
    except Exception:
        logger.exception("Failed to load plan ownership from {}", path)


def _persist() -> None:
    path = _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {tid: {"auto": o.auto, "worked": o.worked} for tid, o in entries.items()}
            for key, entries in _owned.items()
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.exception("Failed to persist plan ownership to {}", path)


def _owned_for(key: str) -> dict[str, _Owned]:
    """This session's owned tasks, pruned to what its focus still holds.

    A session whose focus was forgotten (chat deleted) loses its entries here,
    so ownership never outlives the focus it describes.
    """
    focus = set(session_focus.touched(key))
    with _auto_guard:
        _ensure_loaded()
        entries = _owned.get(key)
        if entries is None:
            return {}
        stale = [tid for tid in entries if tid not in focus]
        if stale:
            for tid in stale:
                entries.pop(tid, None)
            if not entries:
                _owned.pop(key, None)
            _persist()
        return {tid: _Owned(o.auto, o.worked) for tid, o in entries.items()}


def _set_owned(key: str, task_id: str, *, auto: bool | None = None, worked: bool | None = None) -> None:
    with _auto_guard:
        _ensure_loaded()
        entries = _owned.pop(key, None) or {}
        entry = entries.get(task_id) or _Owned()
        if auto is not None:
            entry.auto = auto
        if worked is not None:
            entry.worked = worked
        entries[task_id] = entry
        while len(entries) > _MAX_TASKS_PER_SESSION:
            entries.pop(next(iter(entries)))
        _owned[key] = entries
        while len(_owned) > _MAX_SESSIONS:
            _owned.popitem(last=False)
        _persist()


def _drop_owned(key: str, task_id: str) -> None:
    with _auto_guard:
        _ensure_loaded()
        entries = _owned.get(key)
        if not entries or task_id not in entries:
            return
        entries.pop(task_id, None)
        if not entries:
            _owned.pop(key, None)
        _persist()


def owned_tasks(session_key: str | None) -> dict[str, dict[str, bool]]:
    """Owned task ids with their flags, for inspection and tests."""
    key = (session_key or "").strip()
    if not key:
        return {}
    return {tid: {"auto": o.auto, "worked": o.worked} for tid, o in _owned_for(key).items()}


# -- Per-turn log (memory only) ------------------------------------------------


def begin_board_call(session_key: str | None, *, turn_id: str | None = None) -> int | None:
    """Count one ``board`` call of the current turn; returns its index.

    The index maps the call onto the turn's ordered tool events at turn end,
    which is how work is attributed to the step that was active at the time.
    """
    key = (session_key or "").strip()
    if not key:
        return None
    with _auto_guard:
        log = _turns.get(key)
        if log is None or log.turn_id != turn_id:
            log = _TurnLog(turn_id=turn_id)
            _turns[key] = log
        index = log.calls
        log.calls += 1
        return index


def open_plan_steps(workspace: Path | str | None, session_key: str | None) -> list[dict[str, str]]:
    """Steps of this conversation's plan still to do and workable right now.

    Only while the current turn drives the plan (it called the board): a turn
    about something else must not be pushed back onto an old plan. Counted:
    the session's steps in progress, and the planned or backlog steps it owns
    whose dependencies are done. Blocked steps, steps held by someone else and
    steps still waiting on a dependency are not the agent's to finish now.
    """
    key = (session_key or "").strip()
    if not key or workspace is None:
        return []
    with _auto_guard:
        log = _turns.get(key)
        drives_plan = log is not None and log.calls > 0
    if not drives_plan:
        return []
    try:
        store = ProjectBoardStore(Path(workspace).expanduser())
        focus, by_id, tasks = _focused(store, key)
    except Exception:
        logger.debug("open plan steps: board read failed", exc_info=True)
        return []
    if not focus:
        return []
    owned = owned_tasks(key)
    blocking = blocking_dependencies(tasks)
    steps: list[dict[str, str]] = []
    for task_id in focus:
        task = by_id[task_id]
        status = str(task.get("status") or "")
        if _assigned_elsewhere(task):
            continue
        workable = status == _CLOSABLE or (
            status in _CLAIMABLE and task_id in owned and not blocking.get(task_id)
        )
        if workable:
            steps.append({
                "id": task_id,
                "title": str(task.get("title") or "").strip(),
                "status": status,
            })
    return steps


_OPEN_PLAN_SHOWN = 12


def build_open_plan_message(steps: list[dict[str, str]], *, repeat: bool = False) -> str:
    """The reminder sent when a build turn ends with plan steps still open.

    ``repeat`` is the second reminder for the same unchanged state: the first
    one was answered with text only, so it says so and asks for an action.
    """
    lines = [f"- {step['id']} [{step['status']}] {step['title']}" for step in steps[:_OPEN_PLAN_SHOWN]]
    if len(steps) > _OPEN_PLAN_SHOWN:
        lines.append(f"- ... and {len(steps) - _OPEN_PLAN_SHOWN} more")
    head = (
        "Your last answer changed nothing on the plan, and these steps are still open. "
        "Call a tool now (claim, implement, test or block with a reason); a text "
        "answer does not close a step.\n"
        if repeat
        else "The plan of this conversation still has open steps:\n"
    )
    return (
        head
        + "\n".join(lines)
        + "\n\nDo not hand back yet. If the step in progress is finished, close it with its "
        "evidence and claim the next in one board call (move status=done claim_next=true), "
        "then implement it, test it and close it. Keep going until every step is done. "
        "Stop only when a step is truly blocked (move it to blocked with the reason and go on "
        "with the others) or needs a decision only the user can make (ask it in one precise "
        "question)."
    )


def _pop_turn(key: str, turn_id: str | None) -> _TurnLog | None:
    with _auto_guard:
        log = _turns.pop(key, None)
    if log is None or log.turn_id != turn_id:
        return None
    return log


# -- Store helpers --------------------------------------------------------------


def _focused(store: ProjectBoardStore, session_key: str) -> tuple[list[str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    focus = session_focus.touched(session_key)
    if not focus:
        return [], {}, []
    tasks = store.read_tasks()
    by_id = {task["id"]: task for task in tasks}
    return [tid for tid in focus if tid in by_id], by_id, tasks


def _active(focus: list[str], by_id: dict[str, dict[str, Any]]) -> list[str]:
    return [tid for tid in focus if by_id[tid].get("status") in ACTIVE_STATUSES]


def _assigned_elsewhere(task: dict[str, Any]) -> bool:
    """True when someone other than the agent holds the task."""
    assignee = task.get("assignee")
    if not isinstance(assignee, dict) or not assignee:
        return False
    return assignee.get("type") != "agent"


def _next_ready(
    focus: list[str],
    by_id: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
    owned: Mapping[str, _Owned],
) -> str | None:
    blocking = blocking_dependencies(tasks)
    for tid in focus:
        if tid not in owned:
            continue
        task = by_id[tid]
        if (
            task.get("status") in _CLAIMABLE
            and task.get("status") not in PARKED_STATUSES
            and not blocking.get(tid)
            and not _assigned_elsewhere(task)
        ):
            return tid
    return None


def _claim(store: ProjectBoardStore, session_key: str, task: dict[str, Any], call_index: int | None) -> bool:
    try:
        store.claim_task(
            task["id"], actor=_ACTOR, actor_type="agent", expected_status=task.get("status"),
        )
    except BoardError as exc:
        logger.debug("auto-advance claim {} failed: {}", task["id"], exc)
        return False
    _set_owned(session_key, task["id"], auto=True, worked=False)
    _note_active(session_key, task["id"], call_index)
    return True


def _note_active(key: str, task_id: str, call_index: int | None) -> None:
    if call_index is None:
        return
    with _auto_guard:
        log = _turns.get(key)
        if log is not None:
            log.active_at[task_id] = call_index
            log.superseded.pop(task_id, None)


def _close(store: ProjectBoardStore, task: dict[str, Any], *, pending: bool = False) -> bool:
    """Close one ``in_progress`` step through the store gates. False leaves it open."""
    if pending or validation_pending():
        return False
    if task.get("status") != _CLOSABLE:
        return False
    fields: dict[str, Any] = {"status": "done"}
    evidence = str(task.get("evidence") or "").strip()
    needs_evidence = bool(str(task.get("acceptance") or "").strip()) or task.get(
        "validation"
    ) in ("test", "lint", "verify")
    if needs_evidence and not evidence:
        from navin.quality.verification_log import (
            last_verification_summary,
            refusal_to_close_without_proof,
        )

        if refusal_to_close_without_proof(
            store.project_path, require_tests=task.get("validation") == "test",
        ):
            return False
        summary = last_verification_summary(store.project_path, limit=1)
        fields["evidence"] = f"Auto-closed after a green recorded run: {summary}".strip()
    try:
        store.update_task(
            task["id"], fields=fields, actor=_ACTOR, actor_type="agent", expected_status=_CLOSABLE,
        )
    except BoardError as exc:
        logger.debug("auto-advance close {} refused: {}", task["id"], exc)
        return False
    return True


def _park(store: ProjectBoardStore, task_id: str) -> bool:
    try:
        store.update_task(
            task_id, fields={"status": "planned"}, actor=_ACTOR, actor_type="agent",
            expected_status=_CLOSABLE,
        )
    except BoardError:
        return False
    return True


# -- R1-R3 (during the turn) ----------------------------------------------------


def after_tool_mutation(
    store: ProjectBoardStore,
    session_key: str | None,
    *,
    action: str,
    task_id: str | None,
    status: str | None,
    actor_type: str,
    composer_mode: Any = None,
    touched: list[str] | None = None,
    call_index: int | None = None,
    subagent: bool = False,
) -> list[str]:
    """Apply R1-R3 after a successful ``board`` mutation.

    Returns short human-readable notes (empty when nothing moved) so the tool
    result can tell the model what changed under it.
    """
    key = (session_key or "").strip()
    if not key:
        return []
    if subagent or actor_type == "subagent":
        if action == "claim" or (action in ("move", "update") and status == _CLOSABLE):
            # A step a subagent took is the subagent's: the parent's rules
            # never close it.
            for tid in [*(touched or []), task_id]:
                if tid:
                    _drop_owned(key, tid)
        return []
    focus, by_id, tasks = _focused(store, key)
    if not focus:
        return []
    ids = [tid for tid in (touched or []) if tid in by_id] or (
        [task_id] if task_id in by_id else []
    )
    target = ids[0] if ids else None
    target_status = by_id[target].get("status") if target else None

    # Ownership is recorded in every mode: a plan filed in Plan mode is still
    # this session's plan when the user switches to Agent.
    if action == "create" and target:
        _set_owned(key, target, auto=False)
    elif action == "delete" and task_id:
        _drop_owned(key, task_id)
    if target and target_status in ("done", "cancelled"):
        _drop_owned(key, target)
    if not working_mode(composer_mode):
        return []
    notes: list[str] = []
    if action == "create" and target and target_status == _CLOSABLE:
        _note_active(key, target, call_index)

    owned = _owned_for(key)
    started = target is not None and target_status == _CLOSABLE and (
        action == "claim" or (action in ("move", "update") and status == _CLOSABLE)
    )
    if started:
        if action == "claim" or target in owned:
            # The model chose this step itself; it is no longer auto-claimed.
            _set_owned(key, target, auto=False)
            owned = _owned_for(key)
        _note_active(key, target, call_index)
        if target in focus:
            position = focus.index(target)
            for earlier in focus[:position]:
                task = by_id[earlier]
                if (
                    earlier not in owned
                    or task.get("status") != _CLOSABLE
                    or _assigned_elsewhere(task)
                ):
                    continue
                with _auto_guard:
                    log = _turns.get(key)
                    if log is not None and call_index is not None:
                        log.superseded[earlier] = call_index
                notes.append(
                    f"{earlier} is still in_progress: move it to done if it is finished"
                )
        return notes

    closed = action in ("move", "update") and status == "done"
    if action == "create" or closed:
        focus, by_id, tasks = _focused(store, key)
        if _active(focus, by_id):
            return notes
        # Stay on the plan this call filed or advanced: an older card the
        # session also touched is other work, even when it ranks first.
        if ids:
            plan = plan_members(tasks, ids[0])
            focus = [tid for tid in focus if tid in plan]
        nxt = _next_ready(focus, by_id, tasks, _owned_for(key))
        if nxt and _claim(store, key, by_id[nxt], call_index):
            notes.append(f"{nxt} claimed as the current step")
    return notes


# -- Turn end: R3 resolution and R4 ---------------------------------------------


_QUESTION_END = re.compile(r"[?؟](?=[\s*_`)\"'»”]*(?:\s|$))")
_ASK_PHRASES = re.compile(
    r"\blet me know\b|\btell me\b|\bwould you like\b|\bdo you want\b|\bshall i\b"
    r"|\bshould i\b|\bdis[- ]moi\b|\bdites[- ]moi\b|\bfais[- ]moi savoir\b"
    r"|\bfaites[- ]moi savoir\b|\bveux[- ]tu\b|\bvoulez[- ]vous\b|\bsouhaitez[- ]vous\b"
    r"|\bsouhaites[- ]tu\b",
    re.IGNORECASE,
)
_FAILURE_REPORT = re.compile(
    r"\bcould(?:n['’]?t| not)\b|\bcan(?:['’]?t|not)\b|\bunable to\b|\bfailed\b"
    r"|\bfailing\b|\bstill fails?\b|\bn['’]ai pas pu\b|\bpas pu\b|\bpas r[ée]ussi\b"
    r"|\b[ée]checs?\b|\b[ée]chou\w*|\bimpossible\b",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d+[.)]|[a-zA-Z][.)])\s+")


def asks_user(final_content: str | None) -> bool:
    """True when the end of the answer asks the user something.

    Looks at the whole last paragraph (and the one before it when the last is
    a list of options), not only the last line: "Which DB?" followed by a
    numbered list is still a question.
    """
    if not isinstance(final_content, str):
        return False
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", final_content.strip()) if p.strip()]
    if not paragraphs:
        return False
    tail = [paragraphs[-1]]
    lines = [line for line in paragraphs[-1].splitlines() if line.strip()]
    if len(paragraphs) > 1 and lines and all(_LIST_ITEM.match(line) for line in lines):
        tail.insert(0, paragraphs[-2])
    text = "\n".join(tail)
    if _QUESTION_END.search(text):
        return True
    return bool(_ASK_PHRASES.search(text))


# "71 passes, 0 echec", "aucun echec", "0 failed", "no failures": a green
# report that names the failure count. Read as a failure, it kept the plan
# from advancing after a fully passing run.
_NO_FAILURE = re.compile(
    r"\b(?:0|z[ée]ro|aucune?|sans|pas d'|no|zero)\s*(?:[ée]checs?|[ée]chou\w*|failed|failures?|failing)\b"
    r"|\b(?:[ée]checs?|failed|failures?)\s*:\s*0\b",
    re.IGNORECASE,
)


def reports_failure(final_content: str | None) -> bool:
    if not isinstance(final_content, str):
        return False
    return bool(_FAILURE_REPORT.search(_NO_FAILURE.sub(" ", final_content)))


def _event_failed(event: Mapping[str, Any]) -> bool:
    if event.get("status") == "error":
        return True
    name = str(event.get("name") or "")
    if name in _EXIT_CODE_TOOLS and event.get("exit_code") not in ("", "0", None):
        return True
    if name in _VERIFY_TOOLS:
        detail = str(event.get("detail") or "").upper()
        if any(marker in detail for marker in _VERIFY_FAILURE):
            return True
    return False


def _is_work(event: Mapping[str, Any]) -> bool:
    name = str(event.get("name") or "")
    if name in _NON_WORK_TOOLS or name in _READ_ONLY_TOOLS:
        return False
    if event.get("status") != "ok" or event.get("progress") == "none":
        return False
    return not _event_failed(event)


def _board_positions(events: list[dict[str, Any]], log: _TurnLog) -> list[int] | None:
    """Index of each board call in the turn's tool events, or None if unknown.

    When the board calls this module saw do not line up one-to-one with the
    board events of the turn, positions are unknown and nothing is closed.
    """
    positions = [i for i, event in enumerate(events) if event.get("name") == "board"]
    if len(positions) != log.calls:
        return None
    return positions


def _worked_between(events: list[dict[str, Any]], start: int, end: int) -> bool:
    return any(_is_work(events[i]) for i in range(max(start + 1, 0), min(end, len(events))))


def finish_turn(
    workspace: Path | str | None,
    session_key: str | None,
    *,
    stop_reason: str,
    final_content: str | None,
    tool_events: list[dict[str, Any]] | None,
    composer_mode: Any = None,
    read_only: bool = False,
    validation_is_pending: bool = False,
    metadata: Mapping[str, Any] | None = None,
    sender_id: str | None = None,
    turn_id: str | None = None,
    bus: Any = None,
) -> str | None:
    """Resolve R3 and apply R4. Returns the task R4 closed, or None. Never raises."""
    closed, changed = _finish_turn_safe(
        workspace,
        session_key,
        stop_reason=stop_reason,
        final_content=final_content,
        tool_events=tool_events or [],
        composer_mode=composer_mode,
        read_only=read_only,
        validation_is_pending=validation_is_pending,
        metadata=metadata,
        sender_id=sender_id,
        turn_id=turn_id,
    )
    if changed and workspace is not None:
        _publish(bus, workspace)
    return closed


async def finish_turn_async(
    workspace: Path | str | None,
    session_key: str | None,
    *,
    bus: Any = None,
    **kwargs: Any,
) -> str | None:
    """:func:`finish_turn` with its file I/O off the event loop."""
    try:
        closed, changed = await asyncio.to_thread(
            _finish_turn_safe, workspace, session_key,
            **{**kwargs, "tool_events": kwargs.get("tool_events") or []},
        )
    except Exception:
        logger.debug("board auto-advance at turn end failed", exc_info=True)
        return None
    if changed and workspace is not None:
        _publish(bus, workspace)
    return closed


def _publish(bus: Any, workspace: Path | str) -> None:
    from navin.board.notify import publish_board_update

    publish_board_update(bus, str(ProjectBoardStore(Path(workspace).expanduser()).project_path))


def _finish_turn_safe(
    workspace: Path | str | None, session_key: str | None, **kwargs: Any,
) -> tuple[str | None, bool]:
    key = (session_key or "").strip()
    log = _pop_turn(key, kwargs.get("turn_id")) if key else None
    if log is None:
        # No board call this turn (or a stale log): zero calls, so any board
        # event in the turn makes the positions unknown.
        log = _TurnLog(turn_id=kwargs.get("turn_id"))
    try:
        return _finish_turn(workspace, key, log=log, **kwargs)
    except Exception:
        logger.debug("board auto-advance at turn end failed", exc_info=True)
        return None, False


def _finish_turn(
    workspace: Path | str | None,
    key: str,
    *,
    log: _TurnLog,
    stop_reason: str,
    final_content: str | None,
    tool_events: list[dict[str, Any]],
    composer_mode: Any = None,
    read_only: bool = False,
    validation_is_pending: bool = False,
    metadata: Mapping[str, Any] | None = None,
    sender_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[str | None, bool]:
    if not key or workspace is None or not working_mode(composer_mode):
        return None, False
    if unrelated_turn(metadata, sender_id, key):
        return None, False
    root = Path(workspace).expanduser()
    if not (root / ".navin" / "board").is_dir():
        return None, False
    store = ProjectBoardStore(root)
    focus, by_id, _tasks = _focused(store, key)
    owned = _owned_for(key)
    running = [
        tid for tid in focus
        if tid in owned
        and by_id[tid].get("status") == _CLOSABLE
        and not _assigned_elsewhere(by_id[tid])
    ]
    if not running:
        return None, False

    events = [event for event in tool_events if isinstance(event, dict)]
    positions = _board_positions(events, log)
    pending = validation_is_pending or validation_pending()
    changed = False

    def window(tid: str) -> tuple[int, int]:
        assert positions is not None
        start = positions[log.active_at[tid]] if tid in log.active_at else -1
        end = positions[log.superseded[tid]] if tid in log.superseded else len(events)
        return start, end

    # Record work per running step while the event positions are known.
    if positions is not None:
        for tid in running:
            if not owned[tid].worked and _worked_between(events, *window(tid)):
                owned[tid].worked = True
                _set_owned(key, tid, worked=True)

    # R3: steps the model moved on from during this turn.
    superseded = [tid for tid in running if tid in log.superseded]
    for tid in superseded:
        entry = owned[tid]
        if entry.worked:
            if _close(store, by_id[tid], pending=pending):
                _drop_owned(key, tid)
                changed = True
        elif entry.auto and positions is not None:
            if _park(store, tid):
                changed = True

    current = [tid for tid in running if tid not in superseded]
    if asks_user(final_content):
        # Waiting on the user: no spinner on a step nobody started.
        for tid in current:
            if owned[tid].auto and not owned[tid].worked and positions is not None:
                if _park(store, tid):
                    changed = True
        return None, changed

    # R4.
    if stop_reason != "completed" or read_only or pending or positions is None:
        return None, changed
    if reports_failure(final_content):
        return None, changed
    if not events or _event_failed(events[-1]):
        return None, changed
    candidates = [tid for tid in current if not owned[tid].auto]
    if not candidates:
        return None, changed
    this_turn = [tid for tid in candidates if tid in log.active_at]
    if this_turn:
        target = max(this_turn, key=lambda tid: log.active_at[tid])
    elif len(candidates) == 1:
        target = candidates[0]
    else:
        return None, changed
    if not _worked_between(events, *window(target)):
        return None, changed
    if not _close(store, by_id[target], pending=pending):
        return None, changed
    _drop_owned(key, target)
    return target, True


def reset_for_tests() -> None:
    global _owned_loaded
    with _auto_guard:
        _owned.clear()
        _turns.clear()
        _owned_loaded = True
        _persist()
