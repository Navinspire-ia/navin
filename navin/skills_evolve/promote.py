# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Promotion, rollback, retirement, publication (S2.4, S2.5).

* ``promote_draft``: copy the draft into ``.navin/skills/<name>`` when the
  gate says so (or when a human forces a *flat* draft), verify the live
  loader really sees a valid skill, and roll back on any doubt.
* ``rollback_promotion`` / ``retire_project_skill``: take a promoted skill
  out of the project layer again; the text is kept in the draft folder.
* ``publish_to_harness``: copy a promoted skill to ``~/.navin/skills`` so
  every project on the machine gets it. **Human actor mandatory.**
* ``force_promote`` / ``discard_draft``: the human's other two buttons.

Every step writes one journal line; promotions also leave an S1 episode
("skill X promoted, score A -> B") when the project's episodic memory is on,
so ``recall`` can answer "what changed the agent last week".
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from navin.skills_evolve.drafts import (
    DraftError,
    DraftRecord,
    journal,
    read_draft,
    read_draft_markdown,
    remove_draft_folder,
    save_draft,
)
from navin.skills_evolve.paths import (
    draft_skill_file,
    harness_skill_dir,
    harness_skill_file,
    project_skill_dir,
    project_skill_file,
)
from navin.skills_evolve.settings import read_settings

HUMAN = "human"


class PromotionError(ValueError):
    """The requested transition is not allowed in this state."""


class HumanRequiredError(PromotionError):
    """The action needs a human click; the engine may not do it alone."""


def _require(workspace: Path | str, name: str) -> DraftRecord:
    record = read_draft(workspace, name)
    if record is None:
        raise DraftError(f"no draft named {name}")
    return record


def _refresh_loader_caches() -> None:
    from navin.agent.skills import clear_skills_index_cache

    clear_skills_index_cache()


def post_copy_verify(workspace: Path | str, name: str) -> str | None:
    """Ask the *live* loader whether the promoted skill is a real skill.

    Returns the problem as a string, ``None`` when everything checks out:
    the catalogue lists it from the workspace, its content loads, and its
    frontmatter validates for this name.
    """
    from navin.agent.skills import SkillsLoader
    from navin.webui.skills_api import SkillsApiError, _validate_skill_markdown

    _refresh_loader_caches()
    try:
        loader = SkillsLoader(Path(workspace))
        entries = loader.list_skills(filter_unavailable=False)
    except Exception as exc:  # noqa: BLE001 - a loader crash is a failed verification
        return f"skills loader failed: {exc}"
    entry = next((item for item in entries if item.get("name") == name), None)
    if entry is None:
        return "promoted skill is not in the live catalogue"
    if entry.get("source") != "workspace":
        return f"skill {name} resolves to {entry.get('source')} instead of the project"
    content = loader.load_skill(name)
    if not content or not content.strip():
        return "promoted skill has no content"
    try:
        _validate_skill_markdown(content, expected_name=name)
    except SkillsApiError as exc:
        return f"promoted skill is invalid: {exc}"
    return None


def _note_s1(workspace: Path | str, name: str, before: int | None, after: int | None, actor: str) -> None:
    """One episode in the S1 journal, only when the project's memory is on."""
    try:
        from navin.cognition.episodes import append_episode, build_episode
        from navin.cognition.settings import cognition_enabled
    except Exception:  # noqa: BLE001 - the sidecar is optional
        return
    if not cognition_enabled(workspace, "episodes"):
        return
    record = build_episode(
        channel="skills-evolve",
        chat_id="system",
        session_key="skills-evolve",
        user_text=f"skill {name} promoted",
        reply=f"skill {name} promoted by {actor}, score {before if before is not None else '?'} -> {after if after is not None else '?'} /20",
        tools_used=[],
        stop_reason="promoted",
    )
    if record is not None:
        append_episode(workspace, record)


def promote_draft(
    workspace: Path | str,
    name: str,
    *,
    actor: str = "auto",
    force: bool = False,
    verify: bool = True,
) -> DraftRecord:
    """Copy the draft into the project layer, verify, roll back on failure.

    The engine (``actor="auto"``) may only promote an *eligible* draft.
    ``force=True`` promotes a *flat* draft and requires a human actor.
    """
    record = _require(workspace, name)
    if record.status == "promoted":
        return record
    if force:
        if actor != HUMAN:
            raise HumanRequiredError("forcing a flat draft needs a human")
        if record.status not in ("flat", "eligible", "retired"):
            raise PromotionError(f"only a flat draft can be forced (status: {record.status})")
    elif record.status != "eligible":
        raise PromotionError(f"draft {name} is not eligible (status: {record.status})")
    markdown = read_draft_markdown(workspace, name)
    if not markdown:
        raise DraftError(f"draft {name} has no SKILL.md")

    target = project_skill_file(workspace, name)
    previous = target.read_text(encoding="utf-8") if target.is_file() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    _refresh_loader_caches()

    problem = post_copy_verify(workspace, name) if verify else None
    if problem is not None:
        _restore(target, previous)
        _refresh_loader_caches()
        record.touch("rejected" if not force else "flat")
        record.note = f"rollback after promotion: {problem}"
        record.push_history({"event": "rollback", "reason": problem, "actor": actor})
        save_draft(workspace, record)
        journal(workspace, "rollback", name=name, reason=problem, actor=actor)
        raise PromotionError(problem)

    record.previous_markdown = previous
    record.forced_by = actor if force else None
    record.note = None
    record.touch("promoted")
    record.promoted_at = record.updated_at
    record.push_history({"event": "promoted", "actor": actor, "forced": force})
    save_draft(workspace, record)
    before, after = record.baseline_score, record.best_score
    journal(
        workspace,
        "forced" if force else "promoted",
        name=name,
        actor=actor,
        score_before=before,
        score_after=after,
    )
    _note_s1(workspace, name, before, after, actor)
    logger.info("skills-evolve: skill {} promoted ({} -> {} /20, actor={})", name, before, after, actor)
    return record


def _restore(target: Path, previous: str | None) -> None:
    if previous is None:
        shutil.rmtree(target.parent, ignore_errors=True)
    else:
        target.write_text(previous, encoding="utf-8")


def retire_project_skill(
    workspace: Path | str,
    name: str,
    *,
    reason: str,
    actor: str = "guard",
) -> DraftRecord:
    """Take a promoted skill out of ``.navin/skills``; the draft keeps the text."""
    record = _require(workspace, name)
    target = project_skill_file(workspace, name)
    if target.is_file():
        current = target.read_text(encoding="utf-8")
        draft_file = draft_skill_file(workspace, name)
        draft_file.parent.mkdir(parents=True, exist_ok=True)
        draft_file.write_text(current, encoding="utf-8")
        _restore(target, record.previous_markdown)
        _refresh_loader_caches()
    record.touch("retired")
    record.note = reason
    record.push_history({"event": "retired", "reason": reason, "actor": actor})
    save_draft(workspace, record)
    journal(workspace, "retired", name=name, reason=reason, actor=actor)
    return record


def rollback_promotion(workspace: Path | str, name: str, *, actor: str = HUMAN) -> DraftRecord:
    record = _require(workspace, name)
    if record.status != "promoted":
        raise PromotionError(f"draft {name} is not promoted (status: {record.status})")
    return retire_project_skill(workspace, name, reason="rolled back", actor=actor)


def force_promote(workspace: Path | str, name: str, *, actor: str) -> DraftRecord:
    """Human button: promote a flat draft anyway. Traced, reversible."""
    return promote_draft(workspace, name, actor=actor, force=True)


def publish_to_harness(workspace: Path | str, name: str, *, actor: str) -> Path:
    """Copy a promoted project skill to ``~/.navin/skills`` (every project).

    Refused unless a human asks and the project turned ``publish_harness``
    on. The engine never calls this.
    """
    if actor != HUMAN:
        raise HumanRequiredError("publishing to the harness needs a human")
    settings = read_settings(workspace)
    if not settings.feature("publish_harness"):
        raise PromotionError("publish_harness is off for this project")
    record = _require(workspace, name)
    if record.status != "promoted":
        raise PromotionError(f"only a promoted skill can be published (status: {record.status})")
    source = project_skill_file(workspace, name)
    if not source.is_file():
        raise PromotionError(f"project skill {name} is missing on disk")
    target = harness_skill_file(name)
    harness_skill_dir(name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    _refresh_loader_caches()
    try:
        from navin.agent.skills import clear_home_skill_dir_cache

        clear_home_skill_dir_cache()
    except Exception:  # noqa: BLE001 - cache helper is best-effort
        pass
    record.touch()
    record.published_at = record.updated_at
    record.push_history({"event": "published", "actor": actor})
    save_draft(workspace, record)
    journal(workspace, "published", name=name, actor=actor, target=str(target))
    return target


def discard_draft(workspace: Path | str, name: str, *, actor: str = "auto", reason: str | None = None) -> bool:
    """Throw the draft away. A promoted skill must be retired first."""
    record = read_draft(workspace, name)
    if record is not None and record.status == "promoted":
        raise PromotionError("retire or roll back the promoted skill before discarding its draft")
    removed = remove_draft_folder(workspace, name)
    if removed or record is not None:
        journal(workspace, "discarded", name=name, actor=actor, reason=reason)
    return removed


def project_skill_exists(workspace: Path | str, name: str) -> bool:
    return project_skill_file(workspace, name).is_file()


def project_skill_folder(workspace: Path | str, name: str) -> Path:
    return project_skill_dir(workspace, name)


def describe_targets(workspace: Path | str, name: str) -> dict[str, Any]:
    return {
        "draft": str(draft_skill_file(workspace, name)),
        "project": str(project_skill_file(workspace, name)),
        "harness": str(harness_skill_file(name)),
    }
