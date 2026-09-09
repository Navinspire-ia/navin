# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Training job, frozen exam and scoreboard (S3.2, S3.3).

``train`` is the one job of the world model. It runs on the job runner
thread or from ``navin agi world train``, never inside a turn:

1. read the trajectories, freeze the held-out set if none exists yet;
2. fit a ``CountModel`` on the training bucket, under a CPU / RAM / time
   budget (past it: the run fails, nothing on disk moves);
3. score the new head on the frozen set against the best baseline
   ("always ok" or "majority") and against the head that serves today;
4. save checkpoint N; make it active only when it learned (``up`` vs the
   baseline) and did not regress vs the active one. ``down`` = the file is
   kept as evidence, the pointer does not move;
5. one scoreboard line: baseline -> N, verdict.

``exam`` re-scores the active head on the same frozen set (never a new set),
``rollback`` goes back to N-1. Both are journaled.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.world_model import checkpoints as ck
from navin.world_model.dataset import (
    Heldout,
    HeldoutTamperedError,
    freeze_heldout,
    heldout_candidates,
    load_heldout,
    training_rows,
)
from navin.world_model.journal import (
    count_trajectories,
    journal,
    read_trajectories,
)
from navin.world_model.model import (
    AlwaysOkModel,
    CountModel,
    MajorityModel,
    Metrics,
    compare,
    evaluate,
)
from navin.world_model.paths import scoreboard_path, train_state_path
from navin.world_model.settings import read_settings
from navin.world_model.trajectory import now_stamp

HUMAN = "human"


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


class TrainBudgetExceededError(RuntimeError):
    """Timeout or memory ceiling hit during training."""


@dataclass(frozen=True, slots=True)
class TrainBudget:
    timeout_s: float = 60.0
    max_rss_growth_mb: int = 512
    max_rows: int = 200_000

    def as_dict(self) -> dict[str, Any]:
        return {"timeout_s": self.timeout_s, "max_rss_growth_mb": self.max_rss_growth_mb, "max_rows": self.max_rows}


def _rss_bytes() -> int | None:
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            fields = handle.read().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class _BudgetClock:
    def __init__(self, budget: TrainBudget) -> None:
        self._budget = budget
        self._deadline = time.monotonic() + budget.timeout_s
        self._rss0 = _rss_bytes()

    def check(self, where: str) -> None:
        if time.monotonic() > self._deadline:
            raise TrainBudgetExceededError(f"training timeout after {self._budget.timeout_s:.0f}s ({where})")
        if self._rss0 is not None:
            now = _rss_bytes()
            if now is not None and (now - self._rss0) / (1024 * 1024) > self._budget.max_rss_growth_mb:
                raise TrainBudgetExceededError(
                    f"training memory ceiling {self._budget.max_rss_growth_mb} MB exceeded ({where})"
                )


# --------------------------------------------------------------------------
# Scoreboard
# --------------------------------------------------------------------------


def _best_baseline(train_rows: list[dict[str, Any]], heldout_rows: list[dict[str, Any]]) -> tuple[str, Metrics]:
    """The stronger of the two baselines on the frozen set (lower log-loss)."""
    always = evaluate(AlwaysOkModel(), heldout_rows)
    majority = evaluate(MajorityModel(train_rows), heldout_rows)
    if majority.log_loss <= always.log_loss:
        return "majority", majority
    return "always_ok", always


def read_scoreboard(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = scoreboard_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _append_scoreboard(workspace: Path | str, entry: dict[str, Any]) -> None:
    path = scoreboard_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def latest_score(workspace: Path | str, *, heldout_version: str | None = None) -> dict[str, Any] | None:
    """Most recent scoreboard line, optionally for one held-out version only."""
    for entry in reversed(read_scoreboard(workspace, limit=50)):
        if heldout_version is None or entry.get("heldout_version") == heldout_version:
            return entry
    return None


# --------------------------------------------------------------------------
# Train state (rows seen at the last run)
# --------------------------------------------------------------------------


def read_train_state(workspace: Path | str) -> dict[str, Any]:
    try:
        with open(train_state_path(workspace), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_train_state(workspace: Path | str, **fields: Any) -> None:
    path = train_state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**read_train_state(workspace), **fields}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def training_due(workspace: Path | str) -> bool:
    """Enough new lines since the last run (or no run yet and enough lines)."""
    settings = read_settings(workspace)
    if not settings.feature("train"):
        return False
    total = count_trajectories(workspace)
    if total < settings.min_rows:
        return False
    state = read_train_state(workspace)
    seen = state.get("rows_total")
    if not isinstance(seen, int):
        return True
    return total - seen >= settings.train_every


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TrainResult:
    status: str  # trained | not_enough_data | budget_exceeded | skipped | error | tampered
    checkpoint: int | None = None
    activated: bool = False
    verdict_vs_baseline: str | None = None
    verdict_vs_active: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    heldout_version: str | None = None
    rows_train: int = 0
    rows_heldout: int = 0
    reason: str | None = None
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checkpoint": self.checkpoint,
            "activated": self.activated,
            "verdict_vs_baseline": self.verdict_vs_baseline,
            "verdict_vs_active": self.verdict_vs_active,
            "metrics": self.metrics,
            "baseline": self.baseline,
            "heldout_version": self.heldout_version,
            "rows_train": self.rows_train,
            "rows_heldout": self.rows_heldout,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def _ensure_heldout(workspace: Path, rows: list[dict[str, Any]]) -> Heldout | None:
    heldout = load_heldout(workspace)
    if heldout is not None:
        return heldout
    if len(heldout_candidates(rows)) < 8:
        return None
    heldout = freeze_heldout(workspace, rows)
    journal(workspace, "heldout_frozen", version=heldout.version, rows=len(heldout.rows))
    return heldout


def train(
    workspace: Path | str,
    *,
    actor: str = "auto",
    budget: TrainBudget | None = None,
    force: bool = False,
) -> TrainResult:
    """One training run. ``force`` (a human) ignores the ``train`` sub-switch
    but never the master flag."""
    workspace = Path(workspace)
    started = time.monotonic()
    settings = read_settings(workspace)
    if not settings.enabled:
        return TrainResult(status="skipped", reason="world model is off for this project")
    if not settings.train and not force:
        return TrainResult(status="skipped", reason="train corridor is off")
    budget = budget or TrainBudget()
    clock = _BudgetClock(budget)
    try:
        rows = read_trajectories(workspace, limit=budget.max_rows)
        clock.check("read")
        total_rows = count_trajectories(workspace)
        try:
            heldout = _ensure_heldout(workspace, rows)
        except HeldoutTamperedError as exc:
            journal(workspace, "train_failed", reason=str(exc), actor=actor)
            return TrainResult(status="tampered", reason=str(exc))
        if heldout is None:
            return TrainResult(status="not_enough_data", reason=f"{len(rows)} calls logged, need more to freeze an exam")
        train_rows = training_rows(rows, heldout)
        if len(train_rows) < settings.min_rows:
            return TrainResult(
                status="not_enough_data",
                reason=f"{len(train_rows)} training calls, need {settings.min_rows}",
                heldout_version=heldout.version,
                rows_train=len(train_rows),
                rows_heldout=len(heldout.rows),
            )
        model = CountModel()
        model.fit(train_rows, on_progress=lambda n: clock.check(f"fit {n}"))
        clock.check("fit")
        metrics = evaluate(model, heldout.rows)
        baseline_kind, baseline = _best_baseline(train_rows, heldout.rows)
        clock.check("exam")
        verdict_vs_baseline = compare(metrics, baseline)
        active = ck.active_checkpoint(workspace)
        verdict_vs_active: str | None = None
        if active is not None:
            active_metrics = evaluate(active.model, heldout.rows)
            verdict_vs_active = compare(metrics, active_metrics)
        number = ck.next_checkpoint_number(workspace)
        checkpoint = ck.Checkpoint(
            number=number,
            trained_at=now_stamp(),
            rows=len(train_rows),
            heldout_version=heldout.version,
            metrics=metrics.as_dict(),
            baseline={"kind": baseline_kind, **baseline.as_dict()},
            verdict_vs_baseline=verdict_vs_baseline,
            verdict_vs_active=verdict_vs_active,
            actor=actor,
            model=model,
            budget=budget.as_dict(),
        )
        ck.save_checkpoint(workspace, checkpoint)
        learned = verdict_vs_baseline == "up"
        activated = learned and verdict_vs_active != "down"
        if activated:
            ck.activate(workspace, number, heldout_version=heldout.version, actor=actor)
        _write_train_state(workspace, rows_total=total_rows, last_train_at=now_stamp(), last_checkpoint=number)
        ck.prune_checkpoints(workspace)
        _append_scoreboard(
            workspace,
            {
                "ts": now_stamp(),
                "checkpoint": number,
                "heldout_version": heldout.version,
                "n": metrics.n,
                "metrics": metrics.as_dict(),
                "baseline": {"kind": baseline_kind, **baseline.as_dict()},
                "verdict_vs_baseline": verdict_vs_baseline,
                "verdict_vs_active": verdict_vs_active,
                "activated": activated,
                "actor": actor,
            },
        )
        journal(
            workspace,
            "trained",
            checkpoint=number,
            rows=len(train_rows),
            heldout=heldout.version,
            log_loss=metrics.as_dict()["log_loss"],
            baseline_log_loss=baseline.as_dict()["log_loss"],
            verdict=verdict_vs_baseline,
            vs_active=verdict_vs_active,
            activated=activated,
            actor=actor,
        )
        if settings.beliefs:
            from navin.world_model.beliefs import render_beliefs

            try:
                render_beliefs(workspace, model=model)
            except OSError as exc:
                logger.debug("BELIEFS.md not written: {}", exc)
        return TrainResult(
            status="trained",
            checkpoint=number,
            activated=activated,
            verdict_vs_baseline=verdict_vs_baseline,
            verdict_vs_active=verdict_vs_active,
            metrics=metrics.as_dict(),
            baseline={"kind": baseline_kind, **baseline.as_dict()},
            heldout_version=heldout.version,
            rows_train=len(train_rows),
            rows_heldout=len(heldout.rows),
            reason=None if activated else ("kept as evidence, not activated" if learned else "did not beat the baseline"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except TrainBudgetExceededError as exc:
        journal(workspace, "train_failed", reason=str(exc), actor=actor)
        return TrainResult(status="budget_exceeded", reason=str(exc), duration_ms=int((time.monotonic() - started) * 1000))


def exam(workspace: Path | str, *, actor: str = "auto") -> dict[str, Any]:
    """Re-score the active head on the same frozen set. Never a new set."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "world model is off for this project"}
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        journal(workspace, "exam_failed", reason=str(exc), actor=actor)
        return {"status": "tampered", "reason": str(exc)}
    if heldout is None:
        return {"status": "no_heldout", "reason": "nothing frozen yet; train once first"}
    active = ck.active_checkpoint(workspace)
    if active is None:
        return {"status": "no_active", "reason": "no active checkpoint", "heldout_version": heldout.version}
    rows = read_trajectories(workspace)
    metrics = evaluate(active.model, heldout.rows)
    baseline_kind, baseline = _best_baseline(training_rows(rows, heldout), heldout.rows)
    verdict = compare(metrics, baseline)
    entry = {
        "ts": now_stamp(),
        "checkpoint": active.number,
        "heldout_version": heldout.version,
        "n": metrics.n,
        "metrics": metrics.as_dict(),
        "baseline": {"kind": baseline_kind, **baseline.as_dict()},
        "verdict_vs_baseline": verdict,
        "verdict_vs_active": None,
        "activated": True,
        "actor": actor,
        "exam": True,
    }
    _append_scoreboard(workspace, entry)
    journal(workspace, "examined", checkpoint=active.number, verdict=verdict, log_loss=entry["metrics"]["log_loss"], actor=actor)
    return {"status": "scored", **entry}


def rollback(workspace: Path | str, *, actor: str = HUMAN, reason: str = "requested") -> dict[str, Any]:
    workspace = Path(workspace)
    before = ck.read_active(workspace)
    if before is None:
        return {"status": "nothing_active"}
    after = ck.rollback_active(workspace, actor=actor)
    journal(
        workspace,
        "rollback",
        from_checkpoint=before.get("checkpoint"),
        to_checkpoint=after.get("checkpoint") if after else None,
        actor=actor,
        reason=reason,
    )
    return {"status": "rolled_back", "from": before.get("checkpoint"), "to": after.get("checkpoint") if after else None}


def freeze(workspace: Path | str, *, actor: str = HUMAN) -> dict[str, Any]:
    """Freeze a new held-out version from today's held-out bucket (human action)."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "world model is off for this project"}
    rows = read_trajectories(workspace)
    try:
        heldout = freeze_heldout(workspace, rows)
    except ValueError as exc:
        return {"status": "not_enough_data", "reason": str(exc)}
    journal(workspace, "heldout_frozen", version=heldout.version, rows=len(heldout.rows), actor=actor)
    return {"status": "frozen", **heldout.describe()}
