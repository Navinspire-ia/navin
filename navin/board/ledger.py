# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Mission Task / Progress Ledger (Magentic-One style) over the project board.

Persisted at ``<project>/.navin/board/mission.json``. The board tasks remain the
executable steps; this file holds goal, facts, acceptance, versioned history,
progress, budgets, and pause/resume state.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from loguru import logger

from navin.board.store import BoardError, board_dir_for_project

MISSION_SCHEMA_VERSION = 1
MISSION_FILENAME = "mission.json"

LEDGER_STATUSES = frozenset(
    {"draft", "running", "paused", "blocked", "done", "failed"}
)
PAUSE_REASONS = frozenset({"human", "budget", "stall", "loop", "manual", ""})
VALIDATION_KINDS = frozenset({"test", "lint", "verify", "manual", "none"})
EVIDENCE_REQUIRED = frozenset({"test", "lint", "verify"})

_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_STR = 4000
_MAX_SHORT = 400
_MAX_LIST = 40
_MAX_HISTORY = 80
_MAX_FINGERPRINTS = 24
_MAX_STEPS = 200

_DEFAULT_BUDGET = {
    "max_retries": 3,
    "max_replans": 5,
    "max_stalls": 3,
    "token_budget": 0,
    "cost_budget": 0.0,
    "tokens_used": 0,
    "cost_used": 0.0,
}

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _clean_str(value: Any, *, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_str_list(raw: Any, *, max_len: int = _MAX_SHORT) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:_MAX_LIST]:
        cleaned = _clean_str(item, max_len=max_len)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        raise BoardError("mission file too large", status=413)
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
            logger.warning("mission file too large, ignoring: {}", path)
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("mission read failed {}: {}", path, e)
        return None


def mission_path_for_project(project_path: Path | str) -> Path:
    return board_dir_for_project(project_path) / MISSION_FILENAME


def action_fingerprint(*parts: Any) -> str:
    """Stable short hash for loop detection across tool/file actions."""
    blob = "|".join(str(p).strip() for p in parts if p is not None)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def empty_progress() -> dict[str, Any]:
    return {
        "current_step_id": None,
        "last_result_ok": None,
        "last_evidence": "",
        "real_progress": False,
        "loop_detected": False,
        "stall_count": 0,
        "replan_needed": False,
        "recent_action_fingerprints": [],
        "last_invalidated": [],
        "budget": dict(_DEFAULT_BUDGET),
        "next_actor": "main",
    }


def empty_ledger(*, goal: str = "", status: str = "draft") -> dict[str, Any]:
    return {
        "schema_version": MISSION_SCHEMA_VERSION,
        "goal": goal[:_MAX_STR] if goal else "",
        "status": status if status in LEDGER_STATUSES else "draft",
        "version": 1,
        "constraints": [],
        "facts": [],
        "missing_info": [],
        "acceptance_criteria": [],
        "steps": [],
        "history": [
            {
                "version": 1,
                "reason": "ledger created",
                "at": _now_iso(),
                "actor": "system",
                "changes": ["init"],
            }
        ],
        "paused_at": None,
        "pause_reason": "",
        "progress": empty_progress(),
        "updated_at": _now_iso(),
    }


def _clean_history_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    reason = _clean_str(raw.get("reason"), max_len=_MAX_SHORT) or "update"
    try:
        version = int(raw.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    actor = _clean_str(raw.get("actor"), max_len=80) or "unknown"
    changes = _clean_str_list(raw.get("changes"), max_len=_MAX_SHORT)
    at = raw.get("at") if isinstance(raw.get("at"), str) else _now_iso()
    return {
        "version": max(0, version),
        "reason": reason,
        "at": at,
        "actor": actor,
        "changes": changes,
    }


def _clean_budget(raw: Any) -> dict[str, Any]:
    out = dict(_DEFAULT_BUDGET)
    if not isinstance(raw, dict):
        return out
    for key in ("max_retries", "max_replans", "max_stalls", "token_budget", "tokens_used"):
        try:
            out[key] = max(0, int(raw.get(key, out[key])))
        except (TypeError, ValueError):
            pass
    for key in ("cost_budget", "cost_used"):
        try:
            out[key] = max(0.0, float(raw.get(key, out[key])))
        except (TypeError, ValueError):
            pass
    return out


def _clean_progress(raw: Any) -> dict[str, Any]:
    base = empty_progress()
    if not isinstance(raw, dict):
        return base
    step = _clean_str(raw.get("current_step_id"), max_len=32)
    base["current_step_id"] = step
    if raw.get("last_result_ok") is None:
        base["last_result_ok"] = None
    else:
        base["last_result_ok"] = bool(raw.get("last_result_ok"))
    base["last_evidence"] = _clean_str(raw.get("last_evidence"), max_len=_MAX_STR) or ""
    base["real_progress"] = bool(raw.get("real_progress"))
    base["loop_detected"] = bool(raw.get("loop_detected"))
    try:
        base["stall_count"] = max(0, int(raw.get("stall_count") or 0))
    except (TypeError, ValueError):
        base["stall_count"] = 0
    base["replan_needed"] = bool(raw.get("replan_needed"))
    fps = raw.get("recent_action_fingerprints")
    if isinstance(fps, list):
        cleaned_fps: list[str] = []
        for item in fps[-_MAX_FINGERPRINTS:]:
            s = _clean_str(item, max_len=32)
            if s:
                cleaned_fps.append(s)
        base["recent_action_fingerprints"] = cleaned_fps
    base["budget"] = _clean_budget(raw.get("budget"))
    invalidated = raw.get("last_invalidated")
    if isinstance(invalidated, list):
        cleaned_ids: list[str] = []
        for item in invalidated[:_MAX_STEPS]:
            step_id = _clean_str(item, max_len=32)
            if step_id and step_id not in cleaned_ids:
                cleaned_ids.append(step_id)
        base["last_invalidated"] = cleaned_ids
    next_actor = _clean_str(raw.get("next_actor"), max_len=80) or "main"
    base["next_actor"] = next_actor
    return base


def _clean_step(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    step_id = _clean_str(raw.get("id"), max_len=32)
    if step_id is None:
        return None
    title = _clean_str(raw.get("title"), max_len=200) or step_id
    status = _clean_str(raw.get("status"), max_len=32) or "planned"
    depends_on = _clean_str_list(raw.get("depends_on"), max_len=32)[:20]
    agent = _clean_str(raw.get("agent"), max_len=80)
    acceptance = _clean_str(raw.get("acceptance"), max_len=_MAX_SHORT) or ""
    validation = raw.get("validation")
    if validation not in VALIDATION_KINDS:
        # Allow free-text validation notes from plans; gate uses task field.
        validation = _clean_str(validation, max_len=_MAX_SHORT) or "none"
    try:
        retry_count = max(0, int(raw.get("retry_count") or 0))
    except (TypeError, ValueError):
        retry_count = 0
    try:
        max_retries = max(0, int(raw.get("max_retries") or _DEFAULT_BUDGET["max_retries"]))
    except (TypeError, ValueError):
        max_retries = _DEFAULT_BUDGET["max_retries"]
    evidence = _clean_str(raw.get("evidence"), max_len=_MAX_STR) or ""
    return {
        "id": step_id,
        "title": title,
        "status": status,
        "depends_on": depends_on,
        "agent": agent,
        "acceptance": acceptance,
        "validation": validation,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "evidence": evidence,
    }


def normalize_ledger(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    goal = _clean_str(raw.get("goal"), max_len=_MAX_STR) or ""
    status = raw.get("status")
    if status not in LEDGER_STATUSES:
        status = "draft"
    try:
        version = max(1, int(raw.get("version") or 1))
    except (TypeError, ValueError):
        version = 1
    history: list[dict[str, Any]] = []
    if isinstance(raw.get("history"), list):
        for item in raw["history"][-_MAX_HISTORY:]:
            entry = _clean_history_entry(item)
            if entry is not None:
                history.append(entry)
    if not history:
        history = [
            {
                "version": version,
                "reason": "ledger normalized",
                "at": _now_iso(),
                "actor": "system",
                "changes": ["normalize"],
            }
        ]
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw.get("steps"), list):
        for item in raw["steps"][:_MAX_STEPS]:
            step = _clean_step(item)
            if step is None or step["id"] in seen:
                continue
            seen.add(step["id"])
            steps.append(step)
    pause_reason = raw.get("pause_reason")
    if pause_reason not in PAUSE_REASONS and not isinstance(pause_reason, str):
        pause_reason = ""
    elif isinstance(pause_reason, str):
        pause_reason = pause_reason.strip()[:80]
    return {
        "schema_version": MISSION_SCHEMA_VERSION,
        "goal": goal,
        "status": status,
        "version": version,
        "constraints": _clean_str_list(raw.get("constraints")),
        "facts": _clean_str_list(raw.get("facts")),
        "missing_info": _clean_str_list(raw.get("missing_info")),
        "acceptance_criteria": _clean_str_list(raw.get("acceptance_criteria")),
        "steps": steps,
        "history": history,
        "paused_at": raw.get("paused_at") if isinstance(raw.get("paused_at"), str) else None,
        "pause_reason": pause_reason or "",
        "progress": _clean_progress(raw.get("progress")),
        "updated_at": raw.get("updated_at")
        if isinstance(raw.get("updated_at"), str)
        else _now_iso(),
    }


def validation_requires_evidence(validation: Any) -> bool:
    if validation in EVIDENCE_REQUIRED:
        return True
    if isinstance(validation, str) and validation.strip().lower() in EVIDENCE_REQUIRED:
        return True
    return False


class MissionLedgerStore:
    """Load / mutate / persist one project's mission ledger."""

    def __init__(self, project_path: Path | str) -> None:
        self.project_path = Path(project_path).expanduser()
        self.path = mission_path_for_project(self.project_path)
        self._lock = _lock_for(self.path.parent)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, Any] | None:
        raw = _read_json(self.path)
        if raw is None:
            return None
        return normalize_ledger(raw)

    def save(self, ledger: Mapping[str, Any]) -> dict[str, Any]:
        cleaned = normalize_ledger(dict(ledger))
        if cleaned is None:
            raise BoardError("invalid mission ledger")
        cleaned["updated_at"] = _now_iso()
        with self._lock:
            _atomic_write_json(self.path, cleaned)
        return cleaned

    def create(
        self,
        *,
        goal: str,
        constraints: list[str] | None = None,
        facts: list[str] | None = None,
        missing_info: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        status: str = "draft",
        actor: str = "agent",
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cleaned_goal = _clean_str(goal, max_len=_MAX_STR)
        if cleaned_goal is None:
            raise BoardError("ledger goal is required")
        ledger = empty_ledger(goal=cleaned_goal, status=status)
        ledger["constraints"] = _clean_str_list(constraints or [])
        ledger["facts"] = _clean_str_list(facts or [])
        ledger["missing_info"] = _clean_str_list(missing_info or [])
        ledger["acceptance_criteria"] = _clean_str_list(acceptance_criteria or [])
        if steps:
            seen: set[str] = set()
            cleaned_steps: list[dict[str, Any]] = []
            for item in steps[:_MAX_STEPS]:
                step = _clean_step(item)
                if step is None or step["id"] in seen:
                    continue
                seen.add(step["id"])
                cleaned_steps.append(step)
            ledger["steps"] = cleaned_steps
        if budget:
            ledger["progress"]["budget"] = _clean_budget(
                {**ledger["progress"]["budget"], **budget}
            )
        ledger["history"] = [
            {
                "version": 1,
                "reason": "ledger created",
                "at": _now_iso(),
                "actor": _clean_str(actor, max_len=80) or "agent",
                "changes": ["init"],
            }
        ]
        return self.save(ledger)

    def bump_version(
        self,
        ledger: dict[str, Any],
        *,
        reason: str,
        changes: list[str] | None = None,
        actor: str = "agent",
    ) -> dict[str, Any]:
        version = int(ledger.get("version") or 1) + 1
        ledger["version"] = version
        entry = {
            "version": version,
            "reason": _clean_str(reason, max_len=_MAX_SHORT) or "replan",
            "at": _now_iso(),
            "actor": _clean_str(actor, max_len=80) or "agent",
            "changes": _clean_str_list(changes or []),
        }
        history = list(ledger.get("history") or [])
        history.append(entry)
        ledger["history"] = history[-_MAX_HISTORY:]
        return ledger

    def record_step_result(
        self,
        ledger: dict[str, Any],
        step_id: str,
        *,
        ok: bool,
        evidence: str | None = None,
        progress: bool = False,
        fingerprint: str | None = None,
        actor: str = "agent",
    ) -> dict[str, Any]:
        prog = _clean_progress(ledger.get("progress"))
        prog["current_step_id"] = _clean_str(step_id, max_len=32)
        prog["last_result_ok"] = bool(ok)
        prog["last_evidence"] = _clean_str(evidence, max_len=_MAX_STR) or ""
        prog["real_progress"] = bool(progress) or bool(ok)
        if fingerprint:
            fps = list(prog.get("recent_action_fingerprints") or [])
            fps.append(fingerprint[:32])
            prog["recent_action_fingerprints"] = fps[-_MAX_FINGERPRINTS:]
            if self.detect_loop(prog["recent_action_fingerprints"]):
                prog["loop_detected"] = True
                prog["stall_count"] = int(prog.get("stall_count") or 0) + 1
                prog["replan_needed"] = True
        if ok and progress:
            prog["stall_count"] = 0
            prog["loop_detected"] = False
            prog["replan_needed"] = False
        elif not ok:
            prog["stall_count"] = int(prog.get("stall_count") or 0) + 1
            if prog["stall_count"] >= int(prog["budget"].get("max_stalls") or 3):
                prog["replan_needed"] = True
        # Mirror evidence onto matching step.
        for step in ledger.get("steps") or []:
            if step.get("id") == step_id:
                step["evidence"] = prog["last_evidence"]
                if ok:
                    step["status"] = "completed"
                else:
                    step["retry_count"] = int(step.get("retry_count") or 0) + 1
                break
        ledger["progress"] = prog
        if ledger.get("status") == "draft":
            ledger["status"] = "running"
        _ = actor  # reserved for activity callers
        return ledger

    @staticmethod
    def detect_loop(fingerprints: list[str], *, window: int = 6, repeats: int = 3) -> bool:
        if not fingerprints:
            return False
        recent = fingerprints[-window:]
        if len(recent) < repeats:
            return False
        counts: dict[str, int] = {}
        for fp in recent:
            counts[fp] = counts.get(fp, 0) + 1
            if counts[fp] >= repeats:
                return True
        return False

    def should_replan(self, ledger: Mapping[str, Any]) -> bool:
        prog = ledger.get("progress") or {}
        if prog.get("replan_needed") or prog.get("loop_detected"):
            return True
        stall = int(prog.get("stall_count") or 0)
        max_stalls = int((prog.get("budget") or {}).get("max_stalls") or 3)
        return stall >= max_stalls

    def should_pause(self, ledger: Mapping[str, Any]) -> str | None:
        """Return pause reason if the ledger should stop, else None."""
        if ledger.get("status") == "paused":
            return str(ledger.get("pause_reason") or "manual")
        prog = ledger.get("progress") or {}
        budget = prog.get("budget") or {}
        token_budget = int(budget.get("token_budget") or 0)
        tokens_used = int(budget.get("tokens_used") or 0)
        if token_budget > 0 and tokens_used >= token_budget:
            return "budget"
        cost_budget = float(budget.get("cost_budget") or 0)
        cost_used = float(budget.get("cost_used") or 0)
        if cost_budget > 0 and cost_used >= cost_budget:
            return "budget"
        if prog.get("loop_detected") and self.should_replan(ledger):
            max_replans = int(budget.get("max_replans") or 5)
            # Count replan history entries.
            replans = sum(
                1
                for h in (ledger.get("history") or [])
                if "replan" in str(h.get("reason") or "").lower()
            )
            if replans >= max_replans:
                return "loop"
        stall = int(prog.get("stall_count") or 0)
        max_stalls = int(budget.get("max_stalls") or 3)
        if stall >= max_stalls * 2:
            return "stall"
        if (ledger.get("progress") or {}).get("next_actor") == "human":
            return "human"
        if ledger.get("missing_info") and prog.get("next_actor") == "human":
            return "human"
        return None

    def pause(
        self,
        ledger: dict[str, Any],
        *,
        reason: str = "manual",
        actor: str = "agent",
    ) -> dict[str, Any]:
        ledger["status"] = "paused"
        ledger["paused_at"] = _now_iso()
        ledger["pause_reason"] = reason if reason in PAUSE_REASONS else reason[:80]
        prog = _clean_progress(ledger.get("progress"))
        if reason == "human":
            prog["next_actor"] = "human"
        ledger["progress"] = prog
        return self.bump_version(
            ledger,
            reason=f"paused: {ledger['pause_reason']}",
            changes=["pause"],
            actor=actor,
        )

    def resume(self, ledger: dict[str, Any], *, actor: str = "agent") -> dict[str, Any]:
        ledger["status"] = "running"
        ledger["paused_at"] = None
        ledger["pause_reason"] = ""
        prog = _clean_progress(ledger.get("progress"))
        if prog.get("next_actor") == "human":
            prog["next_actor"] = "main"
        ledger["progress"] = prog
        return self.bump_version(
            ledger, reason="resumed", changes=["resume"], actor=actor
        )

    def consume_budget(
        self,
        ledger: dict[str, Any],
        *,
        tokens: int = 0,
        cost: float = 0.0,
        actor: str = "system",
    ) -> dict[str, Any]:
        prog = _clean_progress(ledger.get("progress"))
        budget = prog["budget"]
        budget["tokens_used"] = int(budget.get("tokens_used") or 0) + max(0, int(tokens))
        budget["cost_used"] = float(budget.get("cost_used") or 0) + max(0.0, float(cost))
        prog["budget"] = budget
        ledger["progress"] = prog
        reason = self.should_pause(ledger)
        if reason == "budget":
            ledger = self.pause(ledger, reason="budget", actor=actor)
            return ledger
        return ledger

    def local_replan(
        self,
        ledger: dict[str, Any],
        failed_step_id: str,
        *,
        reason: str,
        new_steps: list[dict[str, Any]] | None = None,
        facts: list[str] | None = None,
        missing_info: list[str] | None = None,
        actor: str = "agent",
    ) -> dict[str, Any]:
        """Invalidate the failed step and dependents; keep completed upstream steps."""
        steps = list(ledger.get("steps") or [])
        by_id = {s["id"]: s for s in steps}
        if failed_step_id not in by_id:
            raise BoardError(f"unknown step for replan: {failed_step_id}")

        # Collect downstream: anything that transitively depends on failed step.
        invalidated = {failed_step_id}
        changed = True
        while changed:
            changed = False
            for step in steps:
                deps = set(step.get("depends_on") or [])
                if step["id"] not in invalidated and deps & invalidated:
                    invalidated.add(step["id"])
                    changed = True

        for step in steps:
            if step["id"] not in invalidated:
                continue
            # Aval (failed step + dependents) is reset; completed upstream stays.
            step["status"] = "planned"
            step["retry_count"] = 0
            step["evidence"] = ""

        if new_steps:
            for item in new_steps:
                step = _clean_step(item)
                if step is None:
                    continue
                if step["id"] in by_id:
                    # Replace invalidated / matching step in place.
                    idx = next(i for i, s in enumerate(steps) if s["id"] == step["id"])
                    steps[idx] = step
                else:
                    steps.append(step)
                    by_id[step["id"]] = step

        ledger["steps"] = steps
        if facts is not None:
            ledger["facts"] = _clean_str_list(facts)
        if missing_info is not None:
            ledger["missing_info"] = _clean_str_list(missing_info)
        prog = _clean_progress(ledger.get("progress"))
        prog["replan_needed"] = False
        prog["loop_detected"] = False
        prog["stall_count"] = 0
        prog["current_step_id"] = failed_step_id
        prog["last_invalidated"] = sorted(invalidated)
        ledger["progress"] = prog
        if ledger.get("status") in ("draft", "paused", "blocked"):
            ledger["status"] = "running"
        return self.bump_version(
            ledger,
            reason=reason or f"local replan around {failed_step_id}",
            changes=[f"invalidate:{','.join(sorted(invalidated))}"],
            actor=actor,
        )

    def apply_manual_edit(
        self,
        ledger: dict[str, Any],
        fields: Mapping[str, Any],
        *,
        actor: str = "human",
    ) -> dict[str, Any]:
        allowed = {
            "goal",
            "constraints",
            "facts",
            "missing_info",
            "acceptance_criteria",
            "steps",
            "status",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise BoardError(f"unknown ledger fields: {', '.join(sorted(unknown))}")
        changes: list[str] = []
        for key, value in fields.items():
            if key == "goal":
                cleaned = _clean_str(value, max_len=_MAX_STR)
                if cleaned:
                    ledger["goal"] = cleaned
                    changes.append("goal")
            elif key in ("constraints", "facts", "missing_info", "acceptance_criteria"):
                ledger[key] = _clean_str_list(value)
                changes.append(key)
            elif key == "steps" and isinstance(value, list):
                cleaned_steps: list[dict[str, Any]] = []
                seen: set[str] = set()
                for item in value[:_MAX_STEPS]:
                    step = _clean_step(item)
                    if step and step["id"] not in seen:
                        seen.add(step["id"])
                        cleaned_steps.append(step)
                ledger["steps"] = cleaned_steps
                changes.append("steps")
            elif key == "status" and value in LEDGER_STATUSES:
                ledger["status"] = value
                changes.append("status")
        if not changes:
            raise BoardError("manual edit requires at least one field")
        return self.bump_version(
            ledger,
            reason="manual edit",
            changes=changes,
            actor=actor,
        )

    def progress_snapshot(self, ledger: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not ledger:
            return None
        prog = ledger.get("progress") or {}
        return {
            "goal": ledger.get("goal") or "",
            "status": ledger.get("status"),
            "version": ledger.get("version"),
            "acceptance_criteria": list(ledger.get("acceptance_criteria") or []),
            "constraints": list(ledger.get("constraints") or [])[:10],
            "facts": list(ledger.get("facts") or [])[:10],
            "missing_info": list(ledger.get("missing_info") or [])[:10],
            "current_step_id": prog.get("current_step_id"),
            "stall_count": prog.get("stall_count", 0),
            "loop_detected": bool(prog.get("loop_detected")),
            "replan_needed": bool(prog.get("replan_needed")),
            "next_actor": prog.get("next_actor"),
            "pause_reason": ledger.get("pause_reason") or "",
            "budget": dict(prog.get("budget") or {}),
            "history_tail": list(ledger.get("history") or [])[-3:],
            "step_count": len(ledger.get("steps") or []),
        }

    def runtime_lines(
        self,
        ledger: Mapping[str, Any] | None = None,
        *,
        max_lines: int = 24,
    ) -> list[str]:
        data = ledger if ledger is not None else self.load()
        if not data:
            return []
        snap = self.progress_snapshot(data)
        if not snap:
            return []
        lines = [
            f"Mission ledger v{snap['version']} status={snap['status']}",
            f"Goal: {snap['goal'][:300]}",
        ]
        if snap.get("pause_reason"):
            lines.append(f"Paused: {snap['pause_reason']} (resume before continuing work)")
        if snap.get("current_step_id"):
            lines.append(f"Current step: {snap['current_step_id']}")
        lines.append(
            f"Progress: stall={snap['stall_count']} loop={snap['loop_detected']} "
            f"replan_needed={snap['replan_needed']} next_actor={snap['next_actor']}"
        )
        budget = snap.get("budget") or {}
        if int(budget.get("token_budget") or 0) > 0:
            lines.append(
                f"Budget tokens: {budget.get('tokens_used', 0)}/{budget.get('token_budget')}"
            )
        if snap.get("constraints"):
            lines.append("Constraints: " + "; ".join(snap["constraints"][:5]))
        if snap.get("facts"):
            lines.append("Facts: " + "; ".join(snap["facts"][:5]))
        if snap.get("missing_info"):
            lines.append("Missing info: " + "; ".join(snap["missing_info"][:5]))
        if snap.get("acceptance_criteria"):
            lines.append(
                "Acceptance: " + "; ".join(snap["acceptance_criteria"][:5])
            )
        hist = snap.get("history_tail") or []
        if hist:
            last = hist[-1]
            lines.append(
                f"Last plan change v{last.get('version')}: {last.get('reason')}"
            )
        lines.append(
            "Orchestrator protocol: read ledger + board next → one ready step → "
            "act → validate with evidence → ledger_progress → next|replan|pause."
        )
        return lines[:max_lines]


def mission_runtime_lines_for_project(project_path: Path | str | None) -> list[str]:
    """Helper for AgentLoop Runtime Context injection."""
    if not project_path:
        return []
    try:
        store = MissionLedgerStore(project_path)
        if not store.exists():
            return []
        return store.runtime_lines()
    except Exception:
        logger.debug("mission runtime lines skipped", exc_info=True)
        return []
