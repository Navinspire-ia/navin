# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Planning layer over the raw board: dependency graph, ready queue, critical path.

The store keeps tasks flat and each task may declare ``depends_on``. Everything
derived from those edges lives here as pure functions so both the HTTP payload
and the agent tool compute the same view of "what can be worked on right now".

Vocabulary:

- **satisfied** dependency: the referenced task exists and is ``done``.
- **ready**: an open task whose every dependency is satisfied. These are the
  only tasks an agent should pick up.
- **blocked**: an open task with at least one unsatisfied dependency, or one
  explicitly parked in the ``blocked`` status by a human or an agent.
- **critical path**: the longest chain of not-yet-done tasks following
  dependency edges. Its length bounds how fast the remaining work can finish
  even with unlimited parallelism, so it is where attention pays off most.
"""

from __future__ import annotations

from typing import Any

# Finished successfully - the only status that satisfies a dependency.
DONE_STATUSES = frozenset({"done"})
# Explicitly stopped by the user (/stop) mid-run. Closed for picking, but must
# NOT unlock dependents the way ``done`` does.
CANCELLED_STATUSES = frozenset({"cancelled"})
# Statuses that mean "this work is no longer pending" (not in the ready queue).
CLOSED_STATUSES = DONE_STATUSES | CANCELLED_STATUSES
# Statuses that mean "a human or agent parked this on purpose".
PARKED_STATUSES = frozenset({"blocked"})
# Statuses that mean "someone is on it", used for work-in-progress accounting.
ACTIVE_STATUSES = frozenset({"in_progress", "review", "audit"})

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _is_done(task: dict[str, Any]) -> bool:
    return task.get("status") in DONE_STATUSES


def _is_closed(task: dict[str, Any]) -> bool:
    return task.get("status") in CLOSED_STATUSES


def dependency_issues(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map task id to dependency ids that point at nothing.

    Dangling references are kept in storage rather than silently dropped so the
    board can surface them instead of quietly treating a task as ready.
    """
    known = {task["id"] for task in tasks}
    issues: dict[str, list[str]] = {}
    for task in tasks:
        missing = [dep for dep in task.get("depends_on", []) if dep not in known]
        if missing:
            issues[task["id"]] = missing
    return issues


def find_cycles(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """Return dependency cycles as id chains, e.g. ``[["a", "b", "a"]]``.

    A cycle makes every task in it permanently unready, so callers surface it
    as a planning error the humans must break.
    """
    edges = {
        task["id"]: [dep for dep in task.get("depends_on", []) if dep in {t["id"] for t in tasks}]
        for task in tasks
    }
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    visiting: set[str] = set()
    done: set[str] = set()

    def walk(node: str, trail: list[str]) -> None:
        if node in visiting:
            start = trail.index(node)
            ring = trail[start:] + [node]
            signature = frozenset(ring)
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                cycles.append(ring)
            return
        if node in done:
            return
        visiting.add(node)
        trail.append(node)
        for dep in edges.get(node, ()):
            walk(dep, trail)
        trail.pop()
        visiting.discard(node)
        done.add(node)

    for task_id in edges:
        walk(task_id, [])
    return cycles


def blocking_dependencies(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map each open task id to the dependency ids still standing in its way.

    A dependency blocks while it is missing or not yet ``done``. ``cancelled``
    does not satisfy a dependency - dependents stay blocked. An empty list
    means the task is unblocked.
    """
    by_id = {task["id"]: task for task in tasks}
    blocking: dict[str, list[str]] = {}
    for task in tasks:
        if _is_closed(task):
            blocking[task["id"]] = []
            continue
        unmet = [
            dep
            for dep in task.get("depends_on", [])
            if dep not in by_id or not _is_done(by_id[dep])
        ]
        blocking[task["id"]] = unmet
    return blocking


def critical_path(tasks: list[dict[str, Any]]) -> list[str]:
    """Longest chain of open tasks along dependency edges, dependencies first.

    Returns ``[]`` when no chain reaches two tasks: a board of independent
    tasks has no critical path, because nothing constrains the order.

    Cycles are cut rather than raised: a task already on the current trail is
    skipped so the walk always terminates.
    """
    by_id = {task["id"]: task for task in tasks}
    # Board order, not set order, so ties resolve identically on every call.
    open_order = [task["id"] for task in tasks if not _is_closed(task)]
    open_ids = set(open_order)
    memo: dict[str, list[str]] = {}

    def longest_from(task_id: str, trail: frozenset[str]) -> list[str]:
        if task_id not in open_ids or task_id in trail:
            return []
        if task_id in memo:
            return memo[task_id]
        best: list[str] = []
        for dep in by_id[task_id].get("depends_on", []):
            chain = longest_from(dep, trail | {task_id})
            if len(chain) > len(best):
                best = chain
        result = [*best, task_id]
        # Only memoize trail-independent results to stay correct around cycles.
        if not trail:
            memo[task_id] = result
        return result

    longest: list[str] = []
    for task_id in open_order:
        chain = longest_from(task_id, frozenset())
        if len(chain) > len(longest):
            longest = chain
    return longest if len(longest) > 1 else []


def _sort_key(
    task: dict[str, Any],
    depth: dict[str, int],
    on_critical_path: set[str],
) -> tuple[int, int, int, str]:
    """Order the ready queue so the agent's pick is deterministic.

    Critical-path membership wins over priority: unblocking the longest chain
    shortens the whole project, while a high-priority leaf only helps itself.
    Deeper dependents next, then declared priority, then creation order.
    """
    return (
        0 if task["id"] in on_critical_path else 1,
        -depth.get(task["id"], 0),
        _PRIORITY_RANK.get(task.get("priority"), 2),
        str(task.get("created_at") or ""),
    )


def _dependents_depth(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """How many open tasks transitively wait on each task."""
    open_tasks = [task for task in tasks if not _is_closed(task)]
    waiters: dict[str, set[str]] = {task["id"]: set() for task in open_tasks}
    for task in open_tasks:
        for dep in task.get("depends_on", []):
            if dep in waiters:
                waiters[dep].add(task["id"])

    resolved: dict[str, set[str]] = {}

    def collect(task_id: str, trail: frozenset[str]) -> set[str]:
        if task_id in resolved:
            return resolved[task_id]
        if task_id in trail:
            return set()
        total: set[str] = set()
        for waiter in waiters.get(task_id, ()):
            total.add(waiter)
            total |= collect(waiter, trail | {task_id})
        if not trail:
            resolved[task_id] = total
        return total

    return {task_id: len(collect(task_id, frozenset())) for task_id in waiters}


def plan_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Derived planning view of the board.

    Returns per-task annotations (``blocked_by``, ``ready``, ``dependents``)
    plus board-level aggregates: the ordered ready queue, blocked tasks with
    reasons, the critical path, work in progress, and any graph defect
    (dangling reference or cycle) that needs a human decision.
    """
    blocking = blocking_dependencies(tasks)
    depth = _dependents_depth(tasks)
    path = critical_path(tasks)
    on_path = set(path)
    cycles = find_cycles(tasks)
    cycle_members = {task_id for ring in cycles for task_id in ring}

    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for task in tasks:
        if _is_closed(task):
            continue
        unmet = blocking.get(task["id"], [])
        if task.get("status") in PARKED_STATUSES:
            blocked.append(task)
            continue
        if unmet or task["id"] in cycle_members:
            blocked.append(task)
            continue
        # Already claimed or awaiting a check: reported under in_progress so the
        # queue only ever offers work nobody has started.
        if task.get("status") in ACTIVE_STATUSES:
            continue
        ready.append(task)

    ready.sort(key=lambda task: _sort_key(task, depth, on_path))

    annotations = {
        task["id"]: {
            "blocked_by": blocking.get(task["id"], []),
            "ready": any(candidate["id"] == task["id"] for candidate in ready),
            "dependents": depth.get(task["id"], 0),
            "on_critical_path": task["id"] in on_path,
        }
        for task in tasks
    }

    return {
        "tasks": annotations,
        "ready_queue": [task["id"] for task in ready],
        "blocked": [
            {"id": task["id"], "blocked_by": blocking.get(task["id"], []), "status": task["status"]}
            for task in blocked
        ],
        "critical_path": path,
        "in_progress": [
            task["id"] for task in tasks if task.get("status") in ACTIVE_STATUSES
        ],
        "open_count": sum(1 for task in tasks if not _is_closed(task)),
        "done_count": sum(1 for task in tasks if _is_done(task)),
        "cycles": cycles,
        "dangling": dependency_issues(tasks),
    }


_PLAN_ITEM_DESCRIPTION_MAX_CHARS = 280


def _plan_item_description(task: dict[str, Any]) -> str:
    """First lines of the task description, sized for the chat plan panel."""
    text = str(task.get("description") or "").strip()
    if not text:
        return ""
    if len(text) > _PLAN_ITEM_DESCRIPTION_MAX_CHARS:
        text = text[:_PLAN_ITEM_DESCRIPTION_MAX_CHARS].rstrip() + "…"
    return text


def plan_quality(
    entries: list[dict[str, Any]],
    mission: dict[str, Any] | None,
) -> dict[str, Any]:
    """P2-7: deterministic pre-Build checklist over the plan the agent filed.

    /blueprint asks a human to approve the plan, but nothing scored it first:
    a plan whose steps carry no acceptance or validation, or whose ledger
    still lists missing_info, reads exactly like a solid one in the panel.
    This judge is a read-only checklist (no LLM call, so it can run on every
    board fetch): the UI shows "plan ready" or the concrete gaps before the
    Build button is clicked. It warns; it never blocks the human.
    """
    gaps: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("done") or entry.get("cancelled"):
            continue
        task_id = str(entry.get("id") or "")
        title = str(entry.get("title") or task_id)
        if not str(entry.get("acceptance") or "").strip():
            gaps.append({
                "kind": "task_acceptance_missing",
                "task_id": task_id,
                "message": f"Task '{title}' has no acceptance criteria.",
            })
        if (entry.get("validation") or "none") == "none":
            gaps.append({
                "kind": "task_validation_missing",
                "task_id": task_id,
                "message": f"Task '{title}' has no validation (test/lint/verify/manual).",
            })
    if not isinstance(mission, dict) or not mission.get("goal"):
        gaps.append({
            "kind": "ledger_missing",
            "task_id": None,
            "message": "No mission ledger was initialized for this plan (board action=ledger_init).",
        })
    else:
        if not list(mission.get("acceptance_criteria") or []):
            gaps.append({
                "kind": "acceptance_criteria_missing",
                "task_id": None,
                "message": "The mission ledger has no acceptance_criteria.",
            })
        missing = [str(item) for item in (mission.get("missing_info") or []) if str(item).strip()]
        if missing:
            gaps.append({
                "kind": "missing_info_open",
                "task_id": None,
                "message": "Open questions remain: " + "; ".join(missing[:4]),
            })
    return {
        "status": "gaps" if gaps else "ready",
        "gaps": gaps[:12],
    }


def session_plan(
    tasks: list[dict[str, Any]],
    focus_ids: list[str],
    mission: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The narrow plan for one run: only the tasks it touched, in touch order.

    This is what the chat shows while the agent works, so it answers a different
    question from ``plan_summary``: not "what could be worked on" but "what did
    this run set out to do, and where is it". Returns None when the run has not
    touched the board, which is the signal to show no panel at all rather than an
    empty one.

    Tasks deleted since they were touched are dropped silently - the panel
    describes the board as it stands now, not as the run remembers it.

    When a mission ledger is present, goal/version/stall fields are attached so
    the chat panel can show Magentic-style progress without a second fetch.
    """

    by_id = {task["id"]: task for task in tasks}
    items = [by_id[task_id] for task_id in focus_ids if task_id in by_id]
    if not items:
        return None

    # Over the whole board, not just the focused subset: a task can depend on work
    # filed in an earlier session, and judging that against the subset alone would
    # read the missing dependency as unmet and call a ready task blocked.
    blocking = blocking_dependencies(tasks)
    entries = [
        {
            "id": task["id"],
            "title": task["title"],
            # The chat panel shows this under the title so a one-line task
            # name is not the only window into what the step really covers.
            "description": _plan_item_description(task),
            "status": task["status"],
            "priority": task["priority"],
            "done": _is_done(task),
            "active": task.get("status") in ACTIVE_STATUSES,
            "blocked": bool(blocking.get(task["id"]))
            or task.get("status") in PARKED_STATUSES,
            "cancelled": task.get("status") in CANCELLED_STATUSES,
            "acceptance": task.get("acceptance") or "",
            "validation": task.get("validation") or "none",
            "retry_count": int(task.get("retry_count") or 0),
        }
        for task in items
    ]
    done_count = sum(1 for entry in entries if entry["done"])
    # The heading names what is being worked on, so an in-flight task wins over
    # the next one waiting; with none in flight the first unfinished item is the
    # honest answer, and once everything is done the last one is.
    current = next(
        (entry for entry in entries if entry["active"]),
        next(
            (entry for entry in entries if not entry["done"] and not entry["cancelled"]),
            entries[-1],
        ),
    )
    plan: dict[str, Any] = {
        "items": entries,
        "current_id": current["id"],
        "title": current["title"],
        "done_count": done_count,
        "total_count": len(entries),
        # Only real completions - a /stop that cancels steps must not look like
        # "Plan complete".
        "complete": done_count == len(entries) and len(entries) > 0,
        # Pre-Build quality judge (P2-7): "ready" or the concrete gaps, so the
        # panel can warn before the human clicks Build.
        "quality": plan_quality(entries, mission),
    }
    if isinstance(mission, dict) and mission.get("goal"):
        prog = mission.get("progress") or {}
        history = mission.get("history") or []
        last = history[-1] if history else None
        plan["goal"] = mission.get("goal") or ""
        plan["version"] = int(mission.get("version") or 1)
        plan["ledger_status"] = mission.get("status") or ""
        plan["stall_count"] = int(prog.get("stall_count") or 0)
        plan["loop_detected"] = bool(prog.get("loop_detected"))
        plan["replan_needed"] = bool(prog.get("replan_needed"))
        plan["pause_reason"] = mission.get("pause_reason") or ""
        plan["acceptance_criteria"] = list(mission.get("acceptance_criteria") or [])[:8]
        budget = prog.get("budget") or {}
        try:
            plan["token_budget"] = int(budget.get("token_budget") or 0)
            plan["tokens_used"] = int(budget.get("tokens_used") or 0)
        except (TypeError, ValueError):
            plan["token_budget"] = 0
            plan["tokens_used"] = 0
        if isinstance(last, dict):
            plan["last_change"] = {
                "version": last.get("version"),
                "reason": last.get("reason") or "",
            }
    return plan


def milestone_progress(
    tasks: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-milestone task counts: total, done, open, blocked."""
    blocking = blocking_dependencies(tasks)
    progress: dict[str, dict[str, int]] = {
        milestone["id"]: {"total": 0, "done": 0, "open": 0, "blocked": 0}
        for milestone in milestones
    }
    for task in tasks:
        bucket = progress.get(task.get("milestone_id") or "")
        if bucket is None:
            continue
        bucket["total"] += 1
        if _is_done(task):
            bucket["done"] += 1
            continue
        if task.get("status") in CANCELLED_STATUSES:
            # Cancelled work is neither open nor successfully done.
            continue
        bucket["open"] += 1
        if blocking.get(task["id"]) or task.get("status") in PARKED_STATUSES:
            bucket["blocked"] += 1
    return progress
