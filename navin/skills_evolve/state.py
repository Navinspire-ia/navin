# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the AGI panel and ``navin agi`` read and press (S2.5).

``agi_state`` is one JSON-friendly snapshot: flag, drafts with scores and
verdicts, pending jobs, battery version, journal tail. ``agi_action`` maps
the buttons to the corridor. The two human-only buttons (``publish``,
``force``) refuse any actor other than ``"human"``; the panel and the CLI
pass it, the engine never does.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from navin.skills_evolve.author import DraftBrief, family_for_tool, slugify
from navin.skills_evolve.battery import BatteryInvalidError, load_battery
from navin.skills_evolve.drafts import (
    DraftError,
    list_drafts,
    read_draft,
    read_draft_markdown,
    read_journal,
)
from navin.skills_evolve.jobs import enqueue_draft_job, pending_jobs, runner_alive
from navin.skills_evolve.paths import drafts_dir, harness_skill_file, project_skill_file
from navin.skills_evolve.promote import (
    HUMAN,
    HumanRequiredError,
    PromotionError,
    discard_draft,
    force_promote,
    project_skill_exists,
    promote_draft,
    publish_to_harness,
    rollback_promotion,
)
from navin.skills_evolve.settings import (
    SETTINGS_NAME,
    read_settings,
    update_settings,
)

ACTIONS = (
    "draft",     # write a draft now from a brief (manual / post hoc trigger)
    "run",       # drain the pending jobs now
    "exam",      # rerun the same battery on a draft
    "promote",   # promote an eligible draft (when promote_project is off)
    "force",     # human: promote a flat draft anyway
    "publish",   # human: copy a promoted skill to ~/.navin/skills
    "rollback",  # take a promoted skill out of the project
    "discard",   # throw a draft away
    "guard",     # run the periodic guard now
)


class AgiActionError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _draft_payload(workspace: Path, record: Any) -> dict[str, Any]:
    data = record.summary()
    data["in_project"] = project_skill_exists(workspace, record.name)
    data["in_harness"] = harness_skill_file(record.name).is_file()
    data["paths"] = {
        "draft": os.path.relpath(drafts_dir(workspace) / record.name / "SKILL.md", workspace),
        "project": os.path.relpath(project_skill_file(workspace, record.name), workspace),
    }
    return data


def agi_state(workspace: Path | str, *, journal_limit: int = 30) -> dict[str, Any]:
    workspace = Path(workspace)
    settings = read_settings(workspace)
    try:
        battery = load_battery().describe()
    except BatteryInvalidError as exc:
        battery = {"version": None, "error": str(exc), "suites": []}
    drafts = [_draft_payload(workspace, record) for record in list_drafts(workspace)] if settings.enabled else []
    pending = pending_jobs(workspace) if settings.enabled else []
    return {
        **settings.as_dict(),
        "settings_file": ".navin/" + SETTINGS_NAME,
        "drafts_dir": ".navin/skills-draft",
        "harness_dir": str(harness_skill_file("x").parent.parent),
        "battery": battery,
        "drafts": drafts,
        "pending_jobs": [
            {"name": job.get("brief", {}).get("name"), "ts": job.get("ts"), "source": job.get("source")}
            for job in pending
        ],
        "runner_alive": runner_alive(),
        "journal": read_journal(workspace, limit=journal_limit) if settings.enabled else [],
    }


def agi_update(workspace: Path | str, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        update_settings(workspace, fields)
    except ValueError as exc:
        raise AgiActionError(str(exc)) from exc
    return agi_state(workspace)


def draft_text(workspace: Path | str, name: str) -> str | None:
    return read_draft_markdown(workspace, name)


def _manual_brief(name: str | None, brief: dict[str, Any] | None) -> DraftBrief:
    payload = dict(brief or {})
    clean_name = slugify(str(name or payload.get("name") or ""), limit=48)
    if not clean_name:
        raise AgiActionError("a draft needs a name")
    payload["name"] = clean_name
    payload.setdefault("description", f"Skill {clean_name} drafted on request")
    payload.setdefault("kind", "manual")
    tool = payload.get("tool")
    payload.setdefault("family", family_for_tool(tool if isinstance(tool, str) else None))
    return DraftBrief.from_dict(payload)


def agi_action(
    workspace: Path | str,
    action: str,
    *,
    name: str | None = None,
    actor: str = "auto",
    brief: dict[str, Any] | None = None,
    deps: Any | None = None,
) -> dict[str, Any]:
    """Press one button. Returns ``{"ok", "action", "result", "state"}``.

    Raises ``AgiActionError`` with an HTTP-ish status: 400 for a bad
    request, 403 when a human is required, 404 for a missing draft, 409 when
    the flag is off or the state forbids the transition.
    """
    workspace = Path(workspace)
    if action not in ACTIONS:
        raise AgiActionError(f"unknown action: {action}")
    settings = read_settings(workspace)
    if not settings.enabled:
        raise AgiActionError("skills evolution is off for this project", 409)
    result: Any
    try:
        if action == "draft":
            job = _manual_brief(name, brief)
            queued = enqueue_draft_job(workspace, job, source="manual")
            result = {"queued": queued, "name": job.name}
            if not queued:
                raise AgiActionError("draft stage is off or this draft is already queued", 409)
        elif action == "run":
            from navin.skills_evolve.jobs import drain_jobs

            result = drain_jobs(workspace, deps)
        elif action == "guard":
            from navin.skills_evolve.guard import run_guard

            result = run_guard(workspace, deps)
        else:
            if not name:
                raise AgiActionError("a draft name is required")
            if read_draft(workspace, name) is None:
                raise AgiActionError(f"no draft named {name}", 404)
            if action == "exam":
                from navin.skills_evolve.pipeline import reexamine_draft

                result = reexamine_draft(workspace, name, deps).as_dict()
            elif action == "promote":
                result = promote_draft(workspace, name, actor=actor).summary()
            elif action == "force":
                result = force_promote(workspace, name, actor=actor).summary()
            elif action == "publish":
                result = {"target": str(publish_to_harness(workspace, name, actor=actor))}
            elif action == "rollback":
                result = rollback_promotion(workspace, name, actor=actor or HUMAN).summary()
            elif action == "discard":
                result = {"removed": discard_draft(workspace, name, actor=actor, reason="requested")}
    except HumanRequiredError as exc:
        raise AgiActionError(str(exc), 403) from exc
    except PromotionError as exc:
        raise AgiActionError(str(exc), 409) from exc
    except DraftError as exc:
        raise AgiActionError(str(exc), 404) from exc
    return {"ok": True, "action": action, "result": result, "state": agi_state(workspace)}
