# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project task board store: tasks, milestones, and an activity timeline.

The board is the shared workspace between humans (Dev workbench UI) and
agents (the ``board`` tool). It lives inside the project at
``<project>/.navin/board/`` so it travels with the repository:

- ``board.json``      tasks (kanban items)
- ``milestones.json`` roadmap milestones/epics
- ``activity.jsonl``  append-only timeline of every mutation (Evolutions view)

Storage follows the existing JSON-on-disk conventions (atomic tmp+fsync+
replace writes, strict normalization on read) used by the team roster and
cron stores.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from navin.board.plan import milestone_progress, plan_summary

BOARD_SCHEMA_VERSION = 1

TASK_STATUSES = (
    "backlog",
    "planned",
    "in_progress",
    "review",
    "audit",
    "fix",
    "blocked",
    "cancelled",
    "done",
)
TASK_PRIORITIES = ("low", "medium", "high", "critical")
MILESTONE_STATUSES = ("planned", "active", "done")
ACTOR_TYPES = ("human", "agent", "subagent")
TASK_VALIDATIONS = ("test", "lint", "verify", "manual", "none")

_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TASKS = 500
_MAX_MILESTONES = 60
_MAX_COMMENTS = 100
_MAX_LABELS = 10
_MAX_DEPENDS = 20
_MAX_TITLE_LEN = 200
_MAX_DESC_LEN = 4000
_MAX_COMMENT_LEN = 2000
_MAX_NAME_LEN = 80
_MAX_LABEL_LEN = 40
_MAX_ACCEPTANCE_LEN = 400
_MAX_EVIDENCE_LEN = 2000
_MAX_AGENT_HINT_LEN = 80
_MAX_BRANCH_LEN = 120
_MAX_URL_LEN = 300
_ACTIVITY_KEEP_LINES = 2000
_DEFAULT_MAX_RETRIES = 3

# One lock per board directory: the HTTP API and the agent tool live in the
# same process and may mutate the same files concurrently.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class BoardError(Exception):
    """Board validation or persistence failure with an HTTP-ish status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def board_dir_for_project(project_path: Path | str) -> Path:
    return Path(project_path).expanduser() / ".navin" / "board"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _clean_str(value: Any, *, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_actor(actor: Any, actor_type: Any) -> dict[str, str]:
    name = _clean_str(actor, max_len=_MAX_NAME_LEN) or "unknown"
    kind = actor_type if actor_type in ACTOR_TYPES else "human"
    return {"type": kind, "name": name}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        raise BoardError("board file too large", status=413)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            logger.warning("board file too large, ignoring: {}", path)
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("board read failed {}: {}", path, e)
        return None


def _clean_comment(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    text = _clean_str(raw.get("text"), max_len=_MAX_COMMENT_LEN)
    if text is None:
        return None
    author = _clean_actor(raw.get("author"), raw.get("author_type"))
    ts = raw.get("ts")
    return {
        "author": author["name"],
        "author_type": author["type"],
        "text": text,
        "ts": ts if isinstance(ts, str) else _now_iso(),
    }


def _clean_assignee(raw: Any) -> dict[str, str] | None:
    """Normalize assignee from a dict or ``human:<email>`` / ``agent:<name>`` string."""
    if isinstance(raw, str):
        cleaned = raw.strip()
        if not cleaned:
            return None
        if ":" in cleaned:
            kind, _, name = cleaned.partition(":")
            kind = kind.strip().lower()
            name = name.strip()
            if kind in ACTOR_TYPES and name:
                return {"type": kind, "name": name[:_MAX_NAME_LEN]}
            return None
        # Bare email defaults to a human assignee.
        if "@" in cleaned:
            return {"type": "human", "name": cleaned[:_MAX_NAME_LEN]}
        return None
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"), max_len=_MAX_NAME_LEN)
    if name is None:
        return None
    kind = raw.get("type")
    return {"type": kind if kind in ACTOR_TYPES else "human", "name": name}


def _invalid_update_message(label: str, merged: dict[str, Any]) -> str:
    """Name the field that made an update invalid.

    The cleaners coerce almost everything to a default or truncate it, and return
    None for only a handful of causes, so the caller can point at the culprit
    instead of saying "invalid update" and leaving the agent to guess.
    """
    title = merged.get("title")
    if not isinstance(title, str) or not title.strip():
        return f"invalid {label} update: title must be a non-empty string"
    return f"invalid {label} update: the resulting {label} failed validation"


def _clean_task(raw: Any, seen_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    task_id = _clean_str(raw.get("id"), max_len=32)
    if task_id is None or task_id in seen_ids:
        return None
    title = _clean_str(raw.get("title"), max_len=_MAX_TITLE_LEN)
    if title is None:
        return None
    status = raw.get("status")
    if status not in TASK_STATUSES:
        status = "backlog"
    priority = raw.get("priority")
    if priority not in TASK_PRIORITIES:
        priority = "medium"
    labels: list[str] = []
    if isinstance(raw.get("labels"), list):
        for item in raw["labels"][:_MAX_LABELS]:
            cleaned = _clean_str(item, max_len=_MAX_LABEL_LEN)
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
    depends_on: list[str] = []
    if isinstance(raw.get("depends_on"), list):
        for item in raw["depends_on"][:_MAX_DEPENDS]:
            cleaned = _clean_str(item, max_len=32)
            if cleaned and cleaned not in depends_on and cleaned != task_id:
                depends_on.append(cleaned)
    comments: list[dict[str, Any]] = []
    if isinstance(raw.get("comments"), list):
        for item in raw["comments"][-_MAX_COMMENTS:]:
            cleaned_comment = _clean_comment(item)
            if cleaned_comment is not None:
                comments.append(cleaned_comment)
    created_by = _clean_actor(
        (raw.get("created_by") or {}).get("name") if isinstance(raw.get("created_by"), dict) else None,
        (raw.get("created_by") or {}).get("type") if isinstance(raw.get("created_by"), dict) else None,
    )
    validation = raw.get("validation")
    if validation not in TASK_VALIDATIONS:
        validation = "none"
    try:
        retry_count = max(0, int(raw.get("retry_count") or 0))
    except (TypeError, ValueError):
        retry_count = 0
    try:
        max_retries = max(0, int(raw.get("max_retries") or _DEFAULT_MAX_RETRIES))
    except (TypeError, ValueError):
        max_retries = _DEFAULT_MAX_RETRIES
    seen_ids.add(task_id)
    return {
        "id": task_id,
        "title": title,
        "description": _clean_str(raw.get("description"), max_len=_MAX_DESC_LEN) or "",
        "status": status,
        "priority": priority,
        "labels": labels,
        "assignee": _clean_assignee(raw.get("assignee")),
        "milestone_id": _clean_str(raw.get("milestone_id"), max_len=32),
        "depends_on": depends_on,
        "acceptance": _clean_str(raw.get("acceptance"), max_len=_MAX_ACCEPTANCE_LEN) or "",
        "validation": validation,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "agent": _clean_str(raw.get("agent"), max_len=_MAX_AGENT_HINT_LEN),
        "evidence": _clean_str(raw.get("evidence"), max_len=_MAX_EVIDENCE_LEN) or "",
        # Git / GitHub trail written by the autonomy hooks (board tool).
        "branch": _clean_str(raw.get("branch"), max_len=_MAX_BRANCH_LEN),
        "pr_url": _clean_str(raw.get("pr_url"), max_len=_MAX_URL_LEN),
        "head_sha": _clean_str(raw.get("head_sha"), max_len=40),
        "issue_url": _clean_str(raw.get("issue_url"), max_len=_MAX_URL_LEN),
        "created_by": created_by,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else _now_iso(),
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else _now_iso(),
        "comments": comments,
    }


def _clean_milestone(raw: Any, seen_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    milestone_id = _clean_str(raw.get("id"), max_len=32)
    if milestone_id is None or milestone_id in seen_ids:
        return None
    title = _clean_str(raw.get("title"), max_len=_MAX_TITLE_LEN)
    if title is None:
        return None
    status = raw.get("status")
    if status not in MILESTONE_STATUSES:
        status = "planned"
    seen_ids.add(milestone_id)
    return {
        "id": milestone_id,
        "title": title,
        "description": _clean_str(raw.get("description"), max_len=_MAX_DESC_LEN) or "",
        "target_date": _clean_str(raw.get("target_date"), max_len=32),
        "status": status,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else _now_iso(),
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else _now_iso(),
    }


class ProjectBoardStore:
    """CRUD facade over one project's board files."""

    def __init__(self, project_path: Path | str) -> None:
        self.project_path = Path(project_path).expanduser()
        self.dir = board_dir_for_project(self.project_path)
        self.board_path = self.dir / "board.json"
        self.milestones_path = self.dir / "milestones.json"
        self.activity_path = self.dir / "activity.jsonl"
        self._lock = _lock_for(self.dir)

    # -- Reads ---------------------------------------------------------------

    def read_tasks(self) -> list[dict[str, Any]]:
        raw = _read_json(self.board_path)
        items = raw.get("tasks") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        seen: set[str] = set()
        tasks = []
        for item in items[:_MAX_TASKS]:
            cleaned = _clean_task(item, seen)
            if cleaned is not None:
                tasks.append(cleaned)
        return tasks

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self._find(self.read_tasks(), task_id)

    def read_milestones(self) -> list[dict[str, Any]]:
        raw = _read_json(self.milestones_path)
        items = raw.get("milestones") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        seen: set[str] = set()
        milestones = []
        for item in items[:_MAX_MILESTONES]:
            cleaned = _clean_milestone(item, seen)
            if cleaned is not None:
                milestones.append(cleaned)
        return milestones

    def read_activity(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.activity_path.is_file():
            return []
        try:
            with open(self.activity_path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("board activity read failed {}: {}", self.activity_path, e)
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-max(1, limit):]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
        entries.reverse()  # newest first
        return entries

    def payload(self, *, activity_limit: int = 200) -> dict[str, Any]:
        tasks = self.read_tasks()
        milestones = self.read_milestones()
        return {
            "schema_version": BOARD_SCHEMA_VERSION,
            "project_path": str(self.project_path),
            "statuses": list(TASK_STATUSES),
            "priorities": list(TASK_PRIORITIES),
            "tasks": tasks,
            "milestones": milestones,
            "activity": self.read_activity(activity_limit),
            "plan": plan_summary(tasks),
            "milestone_progress": milestone_progress(tasks, milestones),
        }

    # -- Task mutations --------------------------------------------------------

    def create_task(
        self,
        *,
        title: str,
        actor: str,
        actor_type: str,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        assignee: dict[str, Any] | None = None,
        milestone_id: str | None = None,
        depends_on: list[str] | None = None,
        acceptance: str | None = None,
        validation: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        agent: str | None = None,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        cleaned_title = _clean_str(title, max_len=_MAX_TITLE_LEN)
        if cleaned_title is None:
            raise BoardError("task title is required")
        if status is not None and status not in TASK_STATUSES:
            raise BoardError(f"invalid status: {status}")
        if priority is not None and priority not in TASK_PRIORITIES:
            raise BoardError(f"invalid priority: {priority}")
        if validation is not None and validation not in TASK_VALIDATIONS:
            raise BoardError(f"invalid validation: {validation}")
        who = _clean_actor(actor, actor_type)
        now = _now_iso()
        task = _clean_task(
            {
                "id": _new_id("t"),
                "title": cleaned_title,
                "description": description or "",
                "status": status or "backlog",
                "priority": priority or "medium",
                "labels": labels or [],
                "assignee": assignee,
                "milestone_id": milestone_id,
                "depends_on": depends_on or [],
                "acceptance": acceptance or "",
                "validation": validation or "none",
                "retry_count": retry_count if retry_count is not None else 0,
                "max_retries": max_retries if max_retries is not None else _DEFAULT_MAX_RETRIES,
                "agent": agent,
                "evidence": evidence or "",
                "created_by": who,
                "created_at": now,
                "updated_at": now,
                "comments": [],
            },
            set(),
        )
        assert task is not None
        with self._lock:
            tasks = self.read_tasks()
            if len(tasks) >= _MAX_TASKS:
                raise BoardError("board is full", status=409)
            tasks.append(task)
            self._write_tasks(tasks)
            self._append_activity(
                kind="task_created",
                actor=who,
                task_id=task["id"],
                detail=task["title"],
            )
        return task

    def update_task(
        self,
        task_id: str,
        *,
        actor: str,
        actor_type: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "title",
            "description",
            "status",
            "priority",
            "labels",
            "assignee",
            "milestone_id",
            "depends_on",
            "acceptance",
            "validation",
            "retry_count",
            "max_retries",
            "agent",
            "evidence",
            "branch",
            "pr_url",
            "head_sha",
            "issue_url",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise BoardError(f"unknown fields: {', '.join(sorted(unknown))}")
        if "status" in fields and fields["status"] not in TASK_STATUSES:
            raise BoardError(f"invalid status: {fields['status']}")
        if "priority" in fields and fields["priority"] not in TASK_PRIORITIES:
            raise BoardError(f"invalid priority: {fields['priority']}")
        if "validation" in fields and fields["validation"] not in TASK_VALIDATIONS:
            raise BoardError(f"invalid validation: {fields['validation']}")
        who = _clean_actor(actor, actor_type)
        # Hard gate: done requires evidence when validation is test/lint/verify,
        # or when the task declares acceptance criteria (whatever the validation
        # mode): stated criteria without recorded proof is exactly how long
        # missions drift, so the store refuses the close instead of trusting it.
        if fields.get("status") == "done":
            # Peek current task to merge validation/evidence for the gate.
            current = self.get_task(task_id)
            merged_validation = fields.get("validation", current.get("validation"))
            merged_evidence = fields.get("evidence", current.get("evidence"))
            merged_acceptance = fields.get("acceptance", current.get("acceptance"))
            acceptance_text = (
                merged_acceptance if isinstance(merged_acceptance, str) else ""
            ).strip()
            evidence_text = (
                merged_evidence if isinstance(merged_evidence, str) else ""
            ).strip()
            if merged_validation in ("test", "lint", "verify") and not evidence_text:
                raise BoardError(
                    f"cannot mark {task_id} done: validation={merged_validation} "
                    "requires evidence (test_run / verify / lint output)"
                )
            if acceptance_text and not evidence_text:
                raise BoardError(
                    f"cannot mark {task_id} done: the task has acceptance criteria "
                    "but no evidence; record how each criterion was verified in "
                    "the evidence field first"
                )
            # Second gate, for agents only: the evidence string above is prose
            # the model wrote, which it can write without ever running
            # anything. A real run recorded by the quality tools (verify /
            # test_run) must exist, be green, and be fresh. Humans closing a
            # card in the Dev workbench are not subject to it - they are the
            # authority the gate protects, not the risk.
            if (
                who["type"] != "human"
                and merged_validation in ("test", "lint", "verify")
            ):
                from navin.quality.verification_log import (
                    refusal_to_close_without_proof,
                )

                problem = refusal_to_close_without_proof(
                    self.project_path,
                    require_tests=(merged_validation == "test"),
                )
                if problem:
                    raise BoardError(f"cannot mark {task_id} done: {problem}")
        with self._lock:
            tasks = self.read_tasks()
            task = self._find(tasks, task_id)
            old_status = task["status"]
            merged = {**task, **fields, "updated_at": _now_iso()}
            cleaned = _clean_task(merged, set())
            if cleaned is None:
                raise BoardError(_invalid_update_message("task", merged))
            tasks[tasks.index(task)] = cleaned
            self._write_tasks(tasks)
            if "status" in fields and fields["status"] != old_status:
                self._append_activity(
                    kind="task_moved",
                    actor=who,
                    task_id=task_id,
                    detail=f"{old_status} -> {cleaned['status']}",
                )
            else:
                self._append_activity(
                    kind="task_updated",
                    actor=who,
                    task_id=task_id,
                    detail=", ".join(sorted(fields)),
                )
        return cleaned

    def claim_task(
        self,
        task_id: str,
        *,
        actor: str,
        actor_type: str,
    ) -> dict[str, Any]:
        """Assign the task to *actor* and move it to in_progress."""
        who = _clean_actor(actor, actor_type)
        with self._lock:
            tasks = self.read_tasks()
            task = self._find(tasks, task_id)
            task["assignee"] = who
            if task["status"] in ("backlog", "planned", "fix", "audit", "review"):
                task["status"] = "in_progress"
            task["updated_at"] = _now_iso()
            self._write_tasks(tasks)
            self._append_activity(
                kind="task_claimed",
                actor=who,
                task_id=task_id,
                detail=task["status"],
            )
        return task

    def comment_task(
        self,
        task_id: str,
        *,
        actor: str,
        actor_type: str,
        text: str,
    ) -> dict[str, Any]:
        cleaned_text = _clean_str(text, max_len=_MAX_COMMENT_LEN)
        if cleaned_text is None:
            raise BoardError("comment text is required")
        who = _clean_actor(actor, actor_type)
        with self._lock:
            tasks = self.read_tasks()
            task = self._find(tasks, task_id)
            task["comments"] = (task["comments"] + [
                {
                    "author": who["name"],
                    "author_type": who["type"],
                    "text": cleaned_text,
                    "ts": _now_iso(),
                }
            ])[-_MAX_COMMENTS:]
            task["updated_at"] = _now_iso()
            self._write_tasks(tasks)
            self._append_activity(
                kind="task_commented",
                actor=who,
                task_id=task_id,
                detail=cleaned_text[:120],
            )
        return task

    def delete_task(self, task_id: str, *, actor: str, actor_type: str) -> None:
        who = _clean_actor(actor, actor_type)
        with self._lock:
            tasks = self.read_tasks()
            task = self._find(tasks, task_id)
            tasks.remove(task)
            for other in tasks:
                if task_id in other["depends_on"]:
                    other["depends_on"].remove(task_id)
            self._write_tasks(tasks)
            self._append_activity(
                kind="task_deleted",
                actor=who,
                task_id=task_id,
                detail=task["title"],
            )

    # -- Milestone mutations ----------------------------------------------------

    def create_milestone(
        self,
        *,
        title: str,
        actor: str,
        actor_type: str,
        description: str | None = None,
        target_date: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        cleaned_title = _clean_str(title, max_len=_MAX_TITLE_LEN)
        if cleaned_title is None:
            raise BoardError("milestone title is required")
        if status is not None and status not in MILESTONE_STATUSES:
            raise BoardError(f"invalid milestone status: {status}")
        who = _clean_actor(actor, actor_type)
        now = _now_iso()
        milestone = _clean_milestone(
            {
                "id": _new_id("m"),
                "title": cleaned_title,
                "description": description or "",
                "target_date": target_date,
                "status": status or "planned",
                "created_at": now,
                "updated_at": now,
            },
            set(),
        )
        assert milestone is not None
        with self._lock:
            milestones = self.read_milestones()
            if len(milestones) >= _MAX_MILESTONES:
                raise BoardError("too many milestones", status=409)
            milestones.append(milestone)
            self._write_milestones(milestones)
            self._append_activity(
                kind="milestone_created",
                actor=who,
                milestone_id=milestone["id"],
                detail=milestone["title"],
            )
        return milestone

    def update_milestone(
        self,
        milestone_id: str,
        *,
        actor: str,
        actor_type: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {"title", "description", "target_date", "status"}
        unknown = set(fields) - allowed
        if unknown:
            raise BoardError(f"unknown fields: {', '.join(sorted(unknown))}")
        if "status" in fields and fields["status"] not in MILESTONE_STATUSES:
            raise BoardError(f"invalid milestone status: {fields['status']}")
        who = _clean_actor(actor, actor_type)
        with self._lock:
            milestones = self.read_milestones()
            milestone = self._find(milestones, milestone_id, label="milestone")
            merged = {**milestone, **fields, "updated_at": _now_iso()}
            cleaned = _clean_milestone(merged, set())
            if cleaned is None:
                raise BoardError(_invalid_update_message("milestone", merged))
            milestones[milestones.index(milestone)] = cleaned
            self._write_milestones(milestones)
            self._append_activity(
                kind="milestone_updated",
                actor=who,
                milestone_id=milestone_id,
                detail=", ".join(sorted(fields)),
            )
        return cleaned

    def delete_milestone(self, milestone_id: str, *, actor: str, actor_type: str) -> None:
        who = _clean_actor(actor, actor_type)
        with self._lock:
            milestones = self.read_milestones()
            milestone = self._find(milestones, milestone_id, label="milestone")
            milestones.remove(milestone)
            self._write_milestones(milestones)
            tasks = self.read_tasks()
            changed = False
            for task in tasks:
                if task["milestone_id"] == milestone_id:
                    task["milestone_id"] = None
                    changed = True
            if changed:
                self._write_tasks(tasks)
            self._append_activity(
                kind="milestone_deleted",
                actor=who,
                milestone_id=milestone_id,
                detail=milestone["title"],
            )

    # -- Activity ----------------------------------------------------------------

    def log_activity(
        self,
        *,
        kind: str,
        actor: str,
        actor_type: str,
        detail: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Free-form activity entry (e.g. an agent run summary)."""
        cleaned_kind = _clean_str(kind, max_len=40) or "note"
        who = _clean_actor(actor, actor_type)
        with self._lock:
            self._append_activity(
                kind=cleaned_kind,
                actor=who,
                task_id=task_id,
                detail=_clean_str(detail, max_len=500),
            )

    # -- Internals ------------------------------------------------------------

    @staticmethod
    def _find(
        items: list[dict[str, Any]],
        item_id: str,
        *,
        label: str = "task",
    ) -> dict[str, Any]:
        for item in items:
            if item["id"] == item_id:
                return item
        raise BoardError(
            f"{label} not found: {item_id}. Use action='list' to see the "
            "current ids.",
            status=404,
        )

    def _write_tasks(self, tasks: list[dict[str, Any]]) -> None:
        _atomic_write_json(
            self.board_path,
            {
                "schema_version": BOARD_SCHEMA_VERSION,
                "tasks": tasks,
                "updated_at": _now_iso(),
            },
        )

    def _write_milestones(self, milestones: list[dict[str, Any]]) -> None:
        _atomic_write_json(
            self.milestones_path,
            {
                "schema_version": BOARD_SCHEMA_VERSION,
                "milestones": milestones,
                "updated_at": _now_iso(),
            },
        )

    def _append_activity(
        self,
        *,
        kind: str,
        actor: dict[str, str],
        detail: str | None = None,
        task_id: str | None = None,
        milestone_id: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "ts": _now_iso(),
            "kind": kind,
            "actor": actor["name"],
            "actor_type": actor["type"],
        }
        if task_id:
            entry["task_id"] = task_id
        if milestone_id:
            entry["milestone_id"] = milestone_id
        if detail:
            entry["detail"] = detail
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.activity_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._maybe_rotate_activity()
        except OSError as e:
            logger.warning("board activity append failed {}: {}", self.activity_path, e)

    def _maybe_rotate_activity(self) -> None:
        try:
            if self.activity_path.stat().st_size <= _MAX_FILE_BYTES:
                return
            with open(self.activity_path, encoding="utf-8") as f:
                lines = f.readlines()
            tmp = self.activity_path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(lines[-_ACTIVITY_KEEP_LINES:])
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.activity_path)
        except OSError as e:
            logger.warning("board activity rotation failed: {}", e)
