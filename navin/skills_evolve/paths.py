"""Where skills evolution keeps its files.

Three tiers, three folders:

* draft:   ``<project>/.navin/skills-draft/<name>/SKILL.md`` (+ ``draft.json``)
* project: ``<project>/.navin/skills/<name>/SKILL.md`` (read by the live loader)
* harness: ``~/.navin/skills/<name>/SKILL.md`` (every project on this machine)

The draft folder is deliberately **not** one of the folders the skills
loader scans (``navin.agent.skills._NAVIN_NON_SKILL_CHILDREN`` lists it), so
a draft can never reach a turn before it passed the exam.
"""

from __future__ import annotations

from pathlib import Path

from navin.workspace_layout import navin_dir, skills_dir, user_skills_dir

DRAFTS_DIR_NAME = "skills-draft"
DRAFT_RECORD_NAME = "draft.json"
JOURNAL_NAME = "journal.jsonl"
QUEUE_NAME = "queue.jsonl"
BASELINE_PREFIX = "baseline-"
SKILL_FILE = "SKILL.md"


def drafts_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / DRAFTS_DIR_NAME


def draft_dir(workspace: Path | str, name: str) -> Path:
    return drafts_dir(workspace) / name


def draft_skill_file(workspace: Path | str, name: str) -> Path:
    return draft_dir(workspace, name) / SKILL_FILE


def draft_record_file(workspace: Path | str, name: str) -> Path:
    return draft_dir(workspace, name) / DRAFT_RECORD_NAME


def journal_path(workspace: Path | str) -> Path:
    return drafts_dir(workspace) / JOURNAL_NAME


def queue_path(workspace: Path | str) -> Path:
    return drafts_dir(workspace) / QUEUE_NAME


def baseline_cache_file(workspace: Path | str, battery_version: str) -> Path:
    return drafts_dir(workspace) / f"{BASELINE_PREFIX}{battery_version}.json"


def project_skill_dir(workspace: Path | str, name: str) -> Path:
    return skills_dir(workspace) / name


def project_skill_file(workspace: Path | str, name: str) -> Path:
    return project_skill_dir(workspace, name) / SKILL_FILE


def harness_skill_dir(name: str) -> Path:
    return user_skills_dir() / name


def harness_skill_file(name: str) -> Path:
    return harness_skill_dir(name) / SKILL_FILE
