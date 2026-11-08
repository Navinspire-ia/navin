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
# Handed over for a check: finished on the worker's side, not done for the
# board (a dependency still waits for ``done``).
REVIEW_STATUSES = frozenset({"review", "audit"})

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
    as a planning error the humans must break. One depth-first walk with an
    explicit stack: every task is finished once, and a chain of thousands of
    tasks does not reach Python's recursion limit.
    """
    known = {task["id"] for task in tasks}
    edges = {
        task["id"]: [dep for dep in task.get("depends_on") or [] if dep in known]
        for task in tasks
    }
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    done: set[str] = set()

    for root in edges:
        if root in done:
            continue
        trail = [root]
        # Where each task of the current walk sits in ``trail``.
        on_trail = {root: 0}
        positions = [0]
        while trail:
            node = trail[-1]
            deps = edges[node]
            position = positions[-1]
            if position < len(deps):
                positions[-1] = position + 1
                dep = deps[position]
                if dep in on_trail:
                    ring = trail[on_trail[dep]:] + [dep]
                    signature = frozenset(ring)
                    if signature not in seen_signatures:
                        seen_signatures.add(signature)
                        cycles.append(ring)
                elif dep not in done:
                    on_trail[dep] = len(trail)
                    trail.append(dep)
                    positions.append(0)
                continue
            trail.pop()
            positions.pop()
            del on_trail[node]
            done.add(node)
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


def _open_graph(tasks: list[dict[str, Any]]) -> tuple[list[str], list[list[int]]]:
    """Open task ids in board order, and for each the open tasks it depends on.

    Dependencies are positions in the first list, in declared order, each once.
    """
    order: list[str] = []
    index: dict[str, int] = {}
    for task in tasks:
        if not _is_closed(task) and task["id"] not in index:
            index[task["id"]] = len(order)
            order.append(task["id"])
    deps: list[list[int]] = [[] for _ in order]
    for task in tasks:
        if _is_closed(task):
            continue
        wanted = deps[index[task["id"]]]
        for dep in task.get("depends_on") or []:
            target = index.get(dep)
            if target is not None and target not in wanted:
                wanted.append(target)
    return order, deps


def _without_cycles(deps: list[list[int]]) -> tuple[list[list[int]], list[int]]:
    """Cut the dependency edges that close a cycle; return the rest in a usable order.

    A depth-first walk in board order drops each edge leading back to a task
    still on the walk. What remains has no cycle, and the second value lists
    every task after its dependencies. A board without a cycle keeps all its
    edges.
    """
    state = [0] * len(deps)  # 0 not reached, 1 on the walk, 2 finished
    kept: list[list[int]] = [[] for _ in deps]
    finished: list[int] = []
    for root in range(len(deps)):
        if state[root]:
            continue
        state[root] = 1
        walk = [(root, 0)]
        while walk:
            node, position = walk[-1]
            if position < len(deps[node]):
                walk[-1] = (node, position + 1)
                dep = deps[node][position]
                if state[dep] == 1:
                    continue  # back to a task of this walk: the cycle is cut here
                kept[node].append(dep)
                if state[dep] == 0:
                    state[dep] = 1
                    walk.append((dep, 0))
                continue
            walk.pop()
            state[node] = 2
            finished.append(node)
    return kept, finished


def _components(edges: list[list[int]]) -> list[list[int]]:
    """Strongly connected components (Tarjan), each after every one it reaches.

    An explicit stack, so a chain of thousands of tasks needs no recursion.
    """
    count = len(edges)
    index = [-1] * count
    low = [0] * count
    on_stack = [False] * count
    stack: list[int] = []
    components: list[list[int]] = []
    counter = 0
    for root in range(count):
        if index[root] != -1:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        walk = [(root, 0)]
        while walk:
            node, position = walk[-1]
            if position < len(edges[node]):
                walk[-1] = (node, position + 1)
                target = edges[node][position]
                if index[target] == -1:
                    index[target] = low[target] = counter
                    counter += 1
                    stack.append(target)
                    on_stack[target] = True
                    walk.append((target, 0))
                elif on_stack[target]:
                    low[node] = min(low[node], index[target])
                continue
            walk.pop()
            if walk:
                parent = walk[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[int] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
    return components


def critical_path(tasks: list[dict[str, Any]]) -> list[str]:
    """Longest chain of open tasks along dependency edges, dependencies first.

    Returns ``[]`` when no chain reaches two tasks: a board of independent
    tasks has no critical path, because nothing constrains the order.

    Cycles are cut rather than raised (see ``_without_cycles``), so the chain
    never repeats a task. Each task's longest chain is computed once, after
    its dependencies'. The walk this replaces kept only its top-level results,
    so a task filed before its dependencies sent it down every path again, a
    number that grows exponentially with the depth of the plan. Ties go to the
    first dependency declared, then to board order.
    """
    order, deps = _open_graph(tasks)
    kept, finished = _without_cycles(deps)
    length = [0] * len(order)
    previous = [-1] * len(order)
    # ``finished`` lists every task after the dependencies it kept.
    for task in finished:
        best = -1
        for dep in kept[task]:
            if best == -1 or length[dep] > length[best]:
                best = dep
        previous[task] = best
        length[task] = (length[best] if best != -1 else 0) + 1
    end = -1
    for task in range(len(order)):
        if end == -1 or length[task] > length[end]:
            end = task
    if end == -1 or length[end] < 2:
        return []
    chain: list[str] = []
    while end != -1:
        chain.append(order[end])
        end = previous[end]
    chain.reverse()
    return chain


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
    """How many open tasks transitively wait on each task.

    A task in a cycle counts the tasks of its cycle, itself included. One
    pass over the components of the open graph, each carrying the tasks it
    reaches as one integer used as a bit set. Collecting the waiters again
    along every path (memoized only per starting task) was exponential: on a
    board of 902 tasks (266 open, 1036 links), board calls computed for the
    whole hour of their timeout in two CLI sessions, and the worker thread,
    which a timeout cannot stop, kept computing after it (2026-11-03).
    """
    order, deps = _open_graph(tasks)
    waiters: list[list[int]] = [[] for _ in order]
    for task, wanted in enumerate(deps):
        for dep in wanted:
            waiters[dep].append(task)
    component_of = [-1] * len(order)
    # Per component: its tasks and every task waiting on them.
    below: list[int] = []
    counts = [0] * len(order)
    # Each component comes after every component it reaches.
    for number, component in enumerate(_components(waiters)):
        members = 0
        for task in component:
            component_of[task] = number
            members |= 1 << task
        waiting = 0
        looped = False
        for task in component:
            for waiter in waiters[task]:
                if component_of[waiter] == number:
                    looped = True
                else:
                    waiting |= below[component_of[waiter]]
        below.append(members | waiting)
        count = (waiting | members).bit_count() if looped else waiting.bit_count()
        for task in component:
            counts[task] = count
    return {task_id: counts[position] for position, task_id in enumerate(order)}


# A plan filed in one create call, or in several calls of one request, has
# creation times close together; a card filed in another request is farther
# apart. Gaps up to this many seconds chain tasks into one filing.
_SAME_FILING_SECONDS = 600


def _created_ts(task: dict[str, Any]) -> float | None:
    from datetime import datetime

    raw = task.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def plan_members(tasks: list[dict[str, Any]], task_id: str) -> set[str]:
    """The plan a task belongs to: the tasks filed with it, plus everything
    linked to them through depends_on, in either direction.

    Used to keep "close and claim the next" on the work in hand instead of
    jumping to an unrelated card that happens to rank first on the board.
    """
    by_id = {task["id"]: task for task in tasks if task.get("id")}
    if task_id not in by_id:
        return {task_id}
    neighbours: dict[str, set[str]] = {tid: set() for tid in by_id}
    for task in by_id.values():
        for dep in task.get("depends_on") or []:
            if dep in by_id:
                neighbours[task["id"]].add(dep)
                neighbours[dep].add(task["id"])
    seeds = {task_id}
    stamp = _created_ts(by_id[task_id])
    if stamp is not None:
        timeline = sorted(
            (ts, tid) for tid, task in by_id.items() if (ts := _created_ts(task)) is not None
        )
        at = next(i for i, (_, tid) in enumerate(timeline) if tid == task_id)
        low = high = at
        while low > 0 and timeline[low][0] - timeline[low - 1][0] <= _SAME_FILING_SECONDS:
            low -= 1
        while (
            high + 1 < len(timeline)
            and timeline[high + 1][0] - timeline[high][0] <= _SAME_FILING_SECONDS
        ):
            high += 1
        seeds.update(tid for _, tid in timeline[low:high + 1])
    members: set[str] = set()
    stack = list(seeds)
    while stack:
        tid = stack.pop()
        if tid in members:
            continue
        members.add(tid)
        stack.extend(neighbours.get(tid, ()))
    return members


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
    ready_ids = {task["id"] for task in ready}

    annotations = {
        task["id"]: {
            "blocked_by": blocking.get(task["id"], []),
            "ready": task["id"] in ready_ids,
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


def mission_step_ids(mission: dict[str, Any] | None) -> set[str]:
    if not isinstance(mission, dict):
        return set()
    return {
        str(step.get("id"))
        for step in mission.get("steps") or []
        if isinstance(step, dict) and step.get("id")
    }


def mission_concerns(mission: dict[str, Any] | None, focus_ids: list[str]) -> bool:
    """True when this conversation worked on at least one step of the mission.

    A ledger that lists no steps cannot be attributed, so it is kept.
    """
    steps = mission_step_ids(mission)
    if not steps:
        return isinstance(mission, dict)
    return bool(steps & {str(i) for i in focus_ids})


def _current_entry(entries: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """The step the plan's heading names: the one worked on, else the next to do.

    A step in progress wins, the most recently updated one when there are
    several. A step handed over for review is no longer in flight: naming the
    first one kept a step finished at dawn "in progress" for a whole day while
    the run went on with the next ones. With nothing in progress, the first
    step still to do leads, one that can start before a blocked one. Once only
    finished or handed-over steps remain, the last one does.
    """
    updated = {task["id"]: str(task.get("updated_at") or "") for task in tasks}
    working = [entry for entry in entries if entry["status"] == "in_progress"]
    if working:
        # max() keeps the first of equal stamps: touch order breaks ties.
        return max(working, key=lambda entry: updated.get(entry["id"], ""))
    to_do = [
        entry for entry in entries
        if not entry["done"] and not entry["cancelled"] and entry["status"] not in REVIEW_STATUSES
    ]
    return next((entry for entry in to_do if not entry["blocked"]), to_do[0] if to_do else entries[-1])


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
    # The ledger is project-wide. A chat that only touched its own tasks must
    # not be titled with another chat's mission goal.
    if not mission_concerns(mission, focus_ids):
        mission = None

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
    # Handed over for a check: the agent's part is finished, the board's is
    # not. Counted apart, so a run that ends its steps in review still shows
    # how far it went instead of a plan stuck at 0 done for a whole day.
    review_count = sum(1 for entry in entries if entry["status"] in REVIEW_STATUSES)
    # A step someone is on is in progress, even while a dependency is open.
    blocked_count = sum(
        1 for entry in entries
        if entry["blocked"] and not entry["done"] and not entry["cancelled"]
        and entry["status"] not in REVIEW_STATUSES and entry["status"] != "in_progress"
    )
    current = _current_entry(entries, items)
    plan: dict[str, Any] = {
        "items": entries,
        "current_id": current["id"],
        "title": current["title"],
        "done_count": done_count,
        "review_count": review_count,
        "blocked_count": blocked_count,
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
