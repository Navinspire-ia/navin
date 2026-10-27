# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Infrastructure Orchestrator: multi-step changes with dependencies.

A run is the plan of one mission (``.navin/infra/runs/<id>.json``): ordered
steps, each a single command with its owner (the lead or a subagent), its
dependencies, its policy decision and its outcome. The phases are always the
same: Plan -> Discover -> Analyze -> Validate -> Execute -> Verify -> Recover.
Long operations survive restarts because the run is on disk; a step only
starts when every step it depends on is done.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from navin.infra.policy import Decision, infra_dir

PHASES = ("plan", "discover", "analyze", "validate", "execute", "verify", "recover")
STEP_STATES = ("pending", "running", "done", "failed", "skipped", "refused", "needs_approval")


class PlanError(ValueError):
    """A plan or a step that cannot run as written (said to the agent as is)."""


def runs_dir(workspace: str | Path) -> Path:
    return infra_dir(workspace) / "runs"


def _order(steps: list[dict[str, Any]]) -> list[str]:
    """Topological order (Kahn); raises on unknown dependencies and cycles."""
    ids = [step["id"] for step in steps]
    known = set(ids)
    for step in steps:
        for dep in step["depends_on"]:
            if dep not in known:
                raise PlanError(f"step {step['id']} depends on unknown step {dep}")
    incoming = {step["id"]: set(step["depends_on"]) for step in steps}
    order, ready = [], [sid for sid in ids if not incoming[sid]]
    while ready:
        current = ready.pop(0)
        order.append(current)
        for sid in ids:
            if current in incoming[sid]:
                incoming[sid].discard(current)
                if not incoming[sid] and sid not in order and sid not in ready:
                    ready.append(sid)
    if len(order) != len(ids):
        cycle = sorted(sid for sid, deps in incoming.items() if deps)
        raise PlanError(f"dependency cycle between steps {', '.join(cycle)}")
    return order


def create_run(workspace: str | Path, goal: str, steps: list[dict[str, Any]],
               judge: Callable[[str], tuple[Decision | None, str]]) -> dict[str, Any]:
    """Validate and save a plan. ``judge(command)`` gives the policy decision (or an error)."""
    if not goal.strip():
        raise PlanError("a plan needs a goal")
    if not steps:
        raise PlanError("a plan needs at least one step")
    clean: list[dict[str, Any]] = []
    titles: dict[str, str] = {}
    for index, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise PlanError(f"step {index} must be an object with title and command")
        sid = str(raw.get("id") or f"s{index}").strip()
        if not re.match(r"^[\w.-]{1,40}$", sid):
            raise PlanError(f"step id {sid!r} must be short (letters, digits, - _ .)")
        if sid in titles:
            raise PlanError(f"duplicate step id {sid}")
        title = str(raw.get("title") or raw.get("command") or "").strip()
        titles[sid] = title
        clean.append({
            "id": sid, "title": title, "command": str(raw.get("command") or "").strip(),
            "depends_on": [str(dep) for dep in (raw.get("depends_on") or [])],
            "owner": str(raw.get("owner") or "lead"), "phase": str(raw.get("phase") or "execute"),
            "verify": str(raw.get("verify") or ""), "status": "pending",
        })
    # Dependencies may name a step by its title too.
    by_title = {title.lower(): sid for sid, title in titles.items() if title}
    for step in clean:
        step["depends_on"] = [dep if dep in titles else by_title.get(dep.lower(), dep) for dep in step["depends_on"]]
    order = _order(clean)
    approvals, denied = [], []
    for step in clean:
        if step["phase"] not in PHASES:
            raise PlanError(f"step {step['id']}: phase must be one of {', '.join(PHASES)}")
        if not step["command"]:
            step.update({"risk": "read", "decision": "allow", "reasons": ["manual step (no command)"]})
            continue
        decision, error = judge(step["command"])
        if decision is None:
            step.update({"risk": "change", "decision": "deny", "reasons": [error]})
            denied.append(step["id"])
            continue
        step.update({"risk": decision.risk, "decision": decision.action, "reasons": decision.reasons, "categories": decision.categories})
        if decision.action == "ask":
            approvals.append(step["id"])
        elif decision.action == "deny":
            denied.append(step["id"])
    run = {
        "id": f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}", "goal": goal.strip(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "planned", "order": order,
        "steps": sorted(clean, key=lambda step: order.index(step["id"])),
        "approvals_needed": approvals, "denied": denied, "phases": list(PHASES),
    }
    save_run(workspace, run)
    return run


def save_run(workspace: str | Path, run: dict[str, Any]) -> None:
    directory = runs_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f"{run['id']}.json.tmp"
    temp.write_text(json.dumps(run, indent=1), encoding="utf-8")
    temp.replace(directory / f"{run['id']}.json")


def load_run(workspace: str | Path, run_id: str) -> dict[str, Any]:
    if not re.match(r"^[\w.-]+$", run_id or ""):
        raise PlanError("invalid run id")
    try:
        return json.loads((runs_dir(workspace) / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanError(f"run {run_id} not found") from exc


def list_runs(workspace: str | Path, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for path in sorted(runs_dir(workspace).glob("run-*.json"), reverse=True)[:limit]:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning("infra: run file {} is unreadable and is left out: {}", path, exc)
            continue
    return out


def step_of(run: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in run["steps"]:
        if step["id"] == step_id:
            return step
    raise PlanError(f"run {run['id']} has no step {step_id}")


def blockers(run: dict[str, Any], step_id: str) -> list[str]:
    step = step_of(run, step_id)
    done = {other["id"] for other in run["steps"] if other["status"] in {"done", "skipped"}}
    return [dep for dep in step["depends_on"] if dep not in done]


def ready_steps(run: dict[str, Any]) -> list[str]:
    return [step["id"] for step in run["steps"] if step["status"] in {"pending", "needs_approval"} and not blockers(run, step["id"])]


def record(workspace: str | Path, run: dict[str, Any], step_id: str, **fields: Any) -> dict[str, Any]:
    step = step_of(run, step_id)
    step.update(fields)
    step["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    states = {other["status"] for other in run["steps"]}
    if states <= {"done", "skipped"}:
        run["status"] = "done"
    elif "failed" in states or "refused" in states:
        run["status"] = "failed"
    elif "running" in states or "done" in states:
        run["status"] = "running"
    save_run(workspace, run)
    return run


def summary(run: dict[str, Any]) -> dict[str, Any]:
    """The run as the Desktop and the model see it."""
    return {
        "id": run["id"], "goal": run["goal"], "created_at": run["created_at"], "status": run["status"],
        "ready": ready_steps(run), "approvals_needed": run.get("approvals_needed", []), "denied": run.get("denied", []),
        "steps": [{key: step.get(key, "") for key in ("id", "title", "command", "risk", "decision", "status", "owner", "depends_on", "verified", "snapshot")} for step in run["steps"]],
    }
