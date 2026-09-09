# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Periodic guard: a promoted skill that now regresses is retired (S2.4).

Run from a cron or ``navin agi guard``, never from a chat turn. For every
skill this corridor promoted, the same frozen battery is run twice: the
project layer with the skill and without it. A ``down`` verdict retires the
skill from ``.navin/skills`` (its text stays in the draft folder) and the
journal says why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.skills_evolve.drafts import journal, list_drafts, save_draft
from navin.skills_evolve.exam import grade, run_exam
from navin.skills_evolve.pipeline import PipelineDeps, default_deps
from navin.skills_evolve.promote import project_skill_exists, retire_project_skill
from navin.skills_evolve.sandbox import project_skill_texts
from navin.skills_evolve.settings import read_settings


def run_guard(workspace: Path | str, deps: PipelineDeps | None = None) -> dict[str, Any]:
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "skills-evolve flag off", "checked": [], "retired": []}
    deps = deps or default_deps(settings)
    checked: list[dict[str, Any]] = []
    retired: list[str] = []
    for record in list_drafts(workspace):
        if record.status != "promoted":
            continue
        name = record.name
        if not project_skill_exists(workspace, name):
            retire_project_skill(workspace, name, reason="project skill file disappeared", actor="guard")
            retired.append(name)
            checked.append({"name": name, "verdict": "missing"})
            continue
        without = run_exam(
            deps.battery,
            project_skill_texts(workspace, exclude=name),
            model_factory=deps.model_factory,
            budget=deps.budget,
        )
        with_skill = run_exam(
            deps.battery,
            project_skill_texts(workspace),
            model_factory=deps.model_factory,
            budget=deps.budget,
        )
        verdict = grade(with_skill, without)
        entry = {
            "name": name,
            "verdict": verdict.overall,
            "suites": verdict.suites,
            "score": with_skill.score,
            "without": without.score,
            "battery": deps.battery.version,
        }
        checked.append(entry)
        record.push_history({"event": "guard", **entry})
        journal(workspace, "guard", **entry)
        if verdict.regressed:
            reason = f"periodic guard: {verdict.overall} ({with_skill.score} vs {without.score} /20)"
            retire_project_skill(workspace, name, reason=reason, actor="guard")
            retired.append(name)
        else:
            save_draft(workspace, record)
    return {"status": "ok", "checked": checked, "retired": retired, "battery": deps.battery.version}
