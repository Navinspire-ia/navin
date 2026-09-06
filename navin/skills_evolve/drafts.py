"""Draft records and the evolution journal (S2.1).

A draft is a folder ``.navin/skills-draft/<name>/`` holding ``SKILL.md``
(the current best version) and ``draft.json`` (status, scores, attempts,
history). The journal ``.navin/skills-draft/journal.jsonl`` keeps one line
per event: created, examined, revised, kept, discarded, promoted, rollback,
retired, published, forced, tampered.

Nothing here is read by the agent loop. The live skills loader ignores the
``skills-draft`` folder by name, so writing a draft changes no turn.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.skills_evolve.paths import (
    SKILL_FILE,
    draft_dir,
    draft_record_file,
    draft_skill_file,
    drafts_dir,
    journal_path,
)

STATUSES = (
    "drafting",   # written, not examined yet
    "examining",  # exam in progress
    "eligible",   # passed the gate, waiting for promotion (promote_project off)
    "flat",       # no regression, no progress: a human may force it
    "rejected",   # best version still regresses or exam failed; kept for inspection
    "promoted",   # copied into .navin/skills
    "retired",    # removed from .navin/skills by the periodic guard or a rollback
    "discarded",  # thrown away (folder gone, record stays in the journal)
)

_MAX_HISTORY = 12
_MAX_JOURNAL_BYTES = 4 * 1024 * 1024


def now_stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


class DraftError(ValueError):
    """A draft operation that cannot proceed (missing, wrong status...)."""


@dataclass(slots=True)
class DraftRecord:
    name: str
    status: str = "drafting"
    origin: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_stamp)
    updated_at: str = field(default_factory=now_stamp)
    attempts: int = 0
    battery_version: str | None = None
    baseline: dict[str, Any] | None = None
    best: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    previous_markdown: str | None = None
    promoted_at: str | None = None
    published_at: str | None = None
    forced_by: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DraftRecord:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        record = cls(**clean)
        if record.status not in STATUSES:
            record.status = "drafting"
        return record

    def touch(self, status: str | None = None) -> None:
        if status is not None:
            if status not in STATUSES:
                raise DraftError(f"unknown draft status: {status}")
            self.status = status
        self.updated_at = now_stamp()

    def push_history(self, entry: dict[str, Any]) -> None:
        self.history.append({"ts": now_stamp(), **entry})
        del self.history[:-_MAX_HISTORY]

    @property
    def best_score(self) -> int | None:
        if isinstance(self.best, dict) and isinstance(self.best.get("score"), int):
            return self.best["score"]
        return None

    @property
    def baseline_score(self) -> int | None:
        if isinstance(self.baseline, dict) and isinstance(self.baseline.get("score"), int):
            return self.baseline["score"]
        return None

    def summary(self) -> dict[str, Any]:
        """The compact shape the AGI panel and the CLI list."""
        verdict = self.verdict if isinstance(self.verdict, dict) else {}
        return {
            "name": self.name,
            "status": self.status,
            "score": self.best_score,
            "baseline_score": self.baseline_score,
            "verdict": verdict.get("overall"),
            "suites": verdict.get("suites") or {},
            "eligible": bool(verdict.get("eligible")),
            "attempts": self.attempts,
            "battery_version": self.battery_version,
            "origin": self.origin.get("kind") if isinstance(self.origin, dict) else None,
            "trigger": self.origin.get("summary") if isinstance(self.origin, dict) else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "promoted_at": self.promoted_at,
            "published_at": self.published_at,
            "forced_by": self.forced_by,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# Atomic files
# --------------------------------------------------------------------------


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


def read_draft(workspace: Path | str, name: str) -> DraftRecord | None:
    path = draft_record_file(workspace, name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    raw.setdefault("name", name)
    return DraftRecord.from_dict(raw)


def save_draft(workspace: Path | str, record: DraftRecord) -> Path:
    path = draft_record_file(workspace, record.name)
    _write_atomic(path, json.dumps(record.as_dict(), ensure_ascii=False, indent=2) + "\n")
    return path


def read_draft_markdown(workspace: Path | str, name: str) -> str | None:
    try:
        return draft_skill_file(workspace, name).read_text(encoding="utf-8")
    except OSError:
        return None


def write_draft(
    workspace: Path | str,
    name: str,
    markdown: str,
    *,
    origin: dict[str, Any] | None = None,
) -> DraftRecord:
    """Create or replace the draft ``SKILL.md`` and its record.

    Only the draft folder is written: the project skill, the loader caches
    and the agent stay exactly as they were.
    """
    from navin.webui.skills_api import (
        SkillsApiError,
        _validate_skill_markdown,
        normalize_skill_name,
    )

    try:
        clean = normalize_skill_name(name)
        _validate_skill_markdown(markdown, expected_name=clean)
    except SkillsApiError as exc:
        raise DraftError(str(exc)) from exc
    record = read_draft(workspace, clean)
    if record is None:
        record = DraftRecord(name=clean, origin=dict(origin or {}))
        event = "created"
    else:
        if origin:
            record.origin = dict(origin)
        record.touch("drafting")
        event = "rewritten"
    _write_atomic(draft_skill_file(workspace, clean), markdown)
    save_draft(workspace, record)
    journal(workspace, event, name=clean, origin=record.origin.get("kind"))
    return record


def list_drafts(workspace: Path | str) -> list[DraftRecord]:
    root = drafts_dir(workspace)
    if not root.is_dir():
        return []
    records: list[DraftRecord] = []
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        record = read_draft(workspace, child.name)
        if record is None and (child / SKILL_FILE).is_file():
            record = DraftRecord(name=child.name)
        if record is not None:
            records.append(record)
    return records


def remove_draft_folder(workspace: Path | str, name: str) -> bool:
    folder = draft_dir(workspace, name)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------


def journal(workspace: Path | str, event: str, **fields: Any) -> None:
    """Append one line. Best-effort: a journal problem never stops the corridor."""
    record = {"ts": now_stamp(), "event": event, **fields}
    path = journal_path(workspace)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if os.stat(path).st_size >= _MAX_JOURNAL_BYTES:
                os.replace(path, path.with_name(f"{path.stem}.1{path.suffix}"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    except OSError as exc:
        logger.debug("skills-evolve journal skipped {}: {}", path, exc)


def read_journal(workspace: Path | str, *, limit: int = 50) -> list[dict[str, Any]]:
    path = journal_path(workspace)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out
