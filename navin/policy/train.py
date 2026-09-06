"""Training job, policy exam and scoreboard (S4.2, S4.3).

``train`` is the one job of policy learning. It runs in a separate process
(``navin.policy.train_job``) started by the job runner, or from
``navin agi policy train``, never inside a turn:

1. refuse unless the world model radar is ``up`` (S3.3);
2. run the frozen battery in sandboxes and append the steps to the
   trajectory file (``log`` corridor); freeze the held-out steps if no exam
   set exists yet;
3. fit a ``PolicyHead`` on the training split, under a CPU / RAM / time
   budget (past it: the run fails, nothing on disk moves);
4. score N+1 on the frozen set against the best baseline and against N;
5. save adapter N+1; make it active only when it beats its reference
   overall with **no suite down**. ``flat`` = N stays. ``down`` = N stays,
   the file is kept as evidence, steer is untouched;
6. one scoreboard line: reference -> N+1, verdict, per suite.

``exam`` re-scores the active adapter on the same frozen set (never a new
one), ``rollback`` goes back to N-1, ``freeze`` starts a new exam version
(human), ``force`` activates a flat adapter (human, traced, reversible).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.policy import checkpoints as ck
from navin.policy.battery import (
    BatteryInvalidError,
    BatteryTamperedError,
    PolicyBattery,
    load_battery,
)
from navin.policy.dataset import (
    Heldout,
    HeldoutTamperedError,
    freeze_heldout,
    heldout_candidates,
    load_heldout,
    training_rows,
)
from navin.policy.episodes import Episode, S3Predictor, run_cases
from navin.policy.journal import append_step, count_steps, flush, journal, read_steps
from navin.policy.model import (
    MajorityPolicy,
    Metrics,
    PolicyHead,
    UniformPolicy,
    Verdict,
    actions_in,
    compare,
    evaluate,
)
from navin.policy.paths import scoreboard_path, train_state_path
from navin.policy.radar import radar
from navin.policy.settings import read_settings
from navin.policy.trajectory import now_stamp

HUMAN = "human"


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


class TrainBudgetExceededError(RuntimeError):
    """Timeout or memory ceiling hit during training."""


@dataclass(frozen=True, slots=True)
class TrainBudget:
    timeout_s: float = 120.0
    max_rss_growth_mb: int = 512
    max_cases: int = 400
    max_rows: int = 200_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeout_s": self.timeout_s,
            "max_rss_growth_mb": self.max_rss_growth_mb,
            "max_cases": self.max_cases,
            "max_rows": self.max_rows,
        }


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
# Scoreboard and train state
# --------------------------------------------------------------------------


def _best_baseline(train_rows: list[dict[str, Any]], heldout_rows: list[dict[str, Any]]) -> tuple[str, Metrics]:
    """The stronger of the two baselines on the frozen set (higher accuracy, then lower log-loss)."""
    majority = evaluate(MajorityPolicy(train_rows), heldout_rows)
    uniform = evaluate(UniformPolicy(actions_in(train_rows)), heldout_rows)
    if (majority.accuracy, -majority.log_loss) >= (uniform.accuracy, -uniform.log_loss):
        return "majority", majority
    return "uniform", uniform


def read_scoreboard(workspace: Path | str, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = scoreboard_path(workspace).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
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
    for entry in reversed(read_scoreboard(workspace, limit=50)):
        if heldout_version is None or entry.get("heldout_version") == heldout_version:
            return entry
    return None


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


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TrainResult:
    status: str  # trained | not_enough_data | budget_exceeded | skipped | radar_down | tampered | contaminated | error
    checkpoint: int | None = None
    activated: bool = False
    verdict_vs_baseline: dict[str, Any] | None = None
    verdict_vs_active: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    battery_version: str | None = None
    heldout_version: str | None = None
    heldout_stale: bool = False
    episodes: list[dict[str, Any]] = field(default_factory=list)
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
            "battery_version": self.battery_version,
            "heldout_version": self.heldout_version,
            "heldout_stale": self.heldout_stale,
            "episodes": self.episodes,
            "rows_train": self.rows_train,
            "rows_heldout": self.rows_heldout,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainResult:
        result = cls(status=str(data.get("status") or "error"))
        for key in cls.__slots__:  # type: ignore[attr-defined]
            if key in data and key != "status":
                setattr(result, key, data[key])
        return result


# --------------------------------------------------------------------------
# Collect: the eval run that produces trajectories
# --------------------------------------------------------------------------


def s3_predictor(workspace: Path) -> S3Predictor | None:
    """What the project's world model expects of a call, when it has an active head."""
    from navin.world_model import checkpoints as world_ck
    from navin.world_model.journal import project_salt
    from navin.world_model.trajectory import normalize_call

    active = world_ck.active_checkpoint(workspace)
    if active is None:
        return None
    salt = project_salt(workspace)

    def predict(tool: str, arguments: Any) -> str | None:
        features = normalize_call(tool, arguments, salt=salt)
        prediction = active.model.predict(features.tool, features.key, features.args_hash, [])
        return prediction.cls if prediction.support >= 3 else None

    return predict


def _s5_leak(workspace: Path, battery: Any) -> list[str]:
    """S5 secret items found in this battery. Empty when S5 is off (the
    common case): the import is lazy and S4 never depends on S5 otherwise."""
    try:
        from navin.transfer.settings import transfer_enabled

        if not transfer_enabled(workspace):
            return []
        from navin.transfer.suites import s4_training_leak

        cases = battery.cases
        cases = cases() if callable(cases) else cases
        return s4_training_leak(workspace, (case.prompt for case in cases))
    except Exception as exc:  # noqa: BLE001 - a broken S5 must not break S4
        logger.warning("S5 leak scan skipped: {}", exc)
        return []


def collect(
    workspace: Path | str,
    battery: PolicyBattery,
    *,
    actor: str = "auto",
    clock: _BudgetClock | None = None,
    max_cases: int | None = None,
) -> list[Episode]:
    """Run every battery case in a sandbox and append the steps (``log`` corridor).

    The chat is not involved: this runs in the training process. The writer
    thread is flushed before returning so the trainer reads what it wrote.
    """
    workspace = Path(workspace)
    settings = read_settings(workspace)
    cases = battery.cases if max_cases is None else battery.cases[:max_cases]
    episodes: list[Episode] = []

    def on_episode(episode: Episode) -> None:
        if clock is not None:
            clock.check(f"eval {episode.case_id}")
        if settings.feature("log"):
            for step in episode.steps:
                append_step(workspace, step.as_record())
        episodes.append(episode)

    run_cases(cases, battery_version=battery.version, s3_predict=s3_predictor(workspace), on_episode=on_episode)
    flush(10.0)
    passed = sum(1 for e in episodes if e.passed)
    journal(
        workspace,
        "eval_run",
        battery=battery.version,
        episodes=len(episodes),
        passed=passed,
        steps=sum(len(e.steps) for e in episodes),
        logged=settings.feature("log"),
        actor=actor,
    )
    return episodes


# --------------------------------------------------------------------------
# Train
# --------------------------------------------------------------------------


def _ensure_heldout(workspace: Path, rows: list[dict[str, Any]], battery: PolicyBattery) -> Heldout | None:
    heldout = load_heldout(workspace)
    if heldout is not None:
        return heldout
    if len(heldout_candidates(rows)) < 4:
        return None
    heldout = freeze_heldout(workspace, rows, battery_version=battery.version)
    journal(workspace, "heldout_frozen", version=heldout.version, rows=len(heldout.rows), battery=battery.version)
    return heldout


def train(
    workspace: Path | str,
    *,
    actor: str = "auto",
    budget: TrainBudget | None = None,
    force: bool = False,
    collect_first: bool = True,
) -> TrainResult:
    """One training run. ``force`` (a human) ignores the ``train`` sub-switch
    but never the master flag and never the radar."""
    workspace = Path(workspace)
    started = time.monotonic()
    settings = read_settings(workspace)
    if not settings.enabled:
        return TrainResult(status="skipped", reason="policy learning is off for this project")
    if not settings.train and not force:
        return TrainResult(status="skipped", reason="train corridor is off")
    signal = radar(workspace)
    if not signal.up:
        reason = "world model radar is not up: " + "; ".join(signal.reasons)
        journal(workspace, "train_refused", reason=reason, actor=actor)
        return TrainResult(status="radar_down", reason=reason)
    budget = budget or TrainBudget()
    clock = _BudgetClock(budget)
    try:
        try:
            battery = load_battery(workspace=workspace)
        except BatteryInvalidError as exc:
            journal(workspace, "train_failed", reason=str(exc), actor=actor)
            return TrainResult(status="error", reason=str(exc))
        leaked = _s5_leak(workspace, battery)
        if leaked:
            reason = f"S5 secret items sit in the policy battery ({', '.join(leaked[:3])}): training on them is forbidden"
            journal(workspace, "train_refused", reason=reason, actor=actor)
            return TrainResult(status="contaminated", reason=reason, battery_version=battery.version)
        episodes: list[Episode] = []
        if collect_first and settings.log:
            episodes = collect(workspace, battery, actor=actor, clock=clock, max_cases=budget.max_cases)
        clock.check("collect")
        rows = read_steps(workspace, limit=budget.max_rows)
        if not rows:
            reason = (
                "log corridor is off and no eval trajectory exists yet"
                if not settings.log
                else "no eval trajectory on disk"
            )
            return TrainResult(status="not_enough_data", reason=reason, battery_version=battery.version)
        try:
            heldout = _ensure_heldout(workspace, rows, battery)
        except HeldoutTamperedError as exc:
            journal(workspace, "train_failed", reason=str(exc), actor=actor)
            return TrainResult(status="tampered", reason=str(exc), battery_version=battery.version)
        if heldout is None:
            return TrainResult(
                status="not_enough_data",
                reason=f"{len(rows)} steps logged, not enough held-out steps to freeze an exam",
                battery_version=battery.version,
            )
        train_rows = training_rows(rows, heldout)
        successful = sum(1 for r in train_rows if float(r.get("reward") or 0.0) > 0)
        if successful < settings.min_rows:
            return TrainResult(
                status="not_enough_data",
                reason=f"{successful} successful training steps, need {settings.min_rows}",
                battery_version=battery.version,
                heldout_version=heldout.version,
                rows_train=len(train_rows),
                rows_heldout=len(heldout.rows),
            )
        model = PolicyHead()
        model.fit(train_rows, on_progress=lambda n: clock.check(f"fit {n}"))
        clock.check("fit")
        metrics = evaluate(model, heldout.rows)
        baseline_kind, baseline = _best_baseline(train_rows, heldout.rows)
        verdict_vs_baseline = compare(metrics, baseline)
        active = ck.active_checkpoint(workspace)
        verdict_vs_active: Verdict | None = None
        if active is not None:
            verdict_vs_active = compare(metrics, evaluate(active.model, heldout.rows))
        clock.check("exam")
        try:
            battery.verify_unchanged()
        except BatteryTamperedError as exc:
            journal(workspace, "train_failed", reason=str(exc), actor=actor)
            return TrainResult(status="tampered", reason=str(exc), battery_version=battery.version)
        number = ck.next_checkpoint_number(workspace)
        checkpoint = ck.Checkpoint(
            number=number,
            trained_at=now_stamp(),
            rows=len(train_rows),
            episodes=len(model.episodes),
            battery_version=battery.version,
            heldout_version=heldout.version,
            metrics=metrics.as_dict(),
            baseline={"kind": baseline_kind, **baseline.as_dict()},
            verdict_vs_baseline=verdict_vs_baseline.as_dict(),
            verdict_vs_active=verdict_vs_active.as_dict() if verdict_vs_active else None,
            actor=actor,
            model=model,
            budget=budget.as_dict(),
        )
        ck.save_checkpoint(workspace, checkpoint)
        reference = verdict_vs_active if verdict_vs_active is not None else verdict_vs_baseline
        activated = reference.eligible
        if activated:
            ck.activate(workspace, number, heldout_version=heldout.version, actor=actor)
        _write_train_state(
            workspace,
            rows_total=count_steps(workspace),
            last_train_at=now_stamp(),
            last_checkpoint=number,
            battery_version=battery.version,
        )
        ck.prune_checkpoints(workspace)
        _append_scoreboard(
            workspace,
            {
                "ts": now_stamp(),
                "checkpoint": number,
                "battery_version": battery.version,
                "heldout_version": heldout.version,
                "n": metrics.n,
                "metrics": metrics.as_dict(),
                "baseline": {"kind": baseline_kind, **baseline.as_dict()},
                "verdict_vs_baseline": verdict_vs_baseline.as_dict(),
                "verdict_vs_active": verdict_vs_active.as_dict() if verdict_vs_active else None,
                "reference": "active" if verdict_vs_active is not None else "baseline",
                "activated": activated,
                "actor": actor,
            },
        )
        if not activated and reference.regressed:
            journal(workspace, "rejected", checkpoint=number, verdict=reference.overall, suites=reference.suites, actor=actor)
        journal(
            workspace,
            "trained",
            checkpoint=number,
            rows=len(train_rows),
            episodes=len(model.episodes),
            battery=battery.version,
            heldout=heldout.version,
            accuracy=metrics.as_dict()["accuracy"],
            score=metrics.score,
            baseline_score=baseline.score,
            verdict=verdict_vs_baseline.overall,
            vs_active=verdict_vs_active.overall if verdict_vs_active else None,
            suites=reference.suites,
            activated=activated,
            actor=actor,
        )
        if activated:
            journal(workspace, "activated", checkpoint=number, previous=active.number if active else None, actor=actor)
        if activated:
            reason = None
        elif reference.regressed:
            reason = "down: N stays, N+1 kept as evidence"
        elif reference.overall == "flat":
            reason = "flat: N stays (a human may force it)"
        else:
            reason = "did not beat the reference"
        return TrainResult(
            status="trained",
            checkpoint=number,
            activated=activated,
            verdict_vs_baseline=verdict_vs_baseline.as_dict(),
            verdict_vs_active=verdict_vs_active.as_dict() if verdict_vs_active else None,
            metrics=metrics.as_dict(),
            baseline={"kind": baseline_kind, **baseline.as_dict()},
            battery_version=battery.version,
            heldout_version=heldout.version,
            heldout_stale=heldout.battery_version != battery.version,
            episodes=[e.as_dict() for e in episodes],
            rows_train=len(train_rows),
            rows_heldout=len(heldout.rows),
            reason=reason,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except TrainBudgetExceededError as exc:
        journal(workspace, "train_failed", reason=str(exc), actor=actor)
        return TrainResult(status="budget_exceeded", reason=str(exc), duration_ms=int((time.monotonic() - started) * 1000))
    except Exception as exc:  # noqa: BLE001 - a crashed job leaves N in place
        logger.warning("policy training crashed for {}: {}", workspace, exc)
        journal(workspace, "train_failed", reason=f"{type(exc).__name__}: {exc}", actor=actor)
        return TrainResult(status="error", reason=f"{type(exc).__name__}: {exc}", duration_ms=int((time.monotonic() - started) * 1000))


# --------------------------------------------------------------------------
# Exam, rollback, freeze, force
# --------------------------------------------------------------------------


def exam(workspace: Path | str, *, actor: str = "auto") -> dict[str, Any]:
    """Re-score the active adapter on the same frozen set. Never a new set."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        journal(workspace, "exam_failed", reason=str(exc), actor=actor)
        return {"status": "tampered", "reason": str(exc)}
    if heldout is None:
        return {"status": "no_heldout", "reason": "nothing frozen yet; train once first"}
    active = ck.active_checkpoint(workspace)
    if active is None:
        return {"status": "no_active", "reason": "no active adapter", "heldout_version": heldout.version}
    rows = read_steps(workspace)
    train_rows = training_rows(rows, heldout)
    metrics = evaluate(active.model, heldout.rows)
    baseline_kind, baseline = _best_baseline(train_rows, heldout.rows)
    verdict = compare(metrics, baseline)
    entry = {
        "ts": now_stamp(),
        "checkpoint": active.number,
        "battery_version": active.battery_version,
        "heldout_version": heldout.version,
        "n": metrics.n,
        "metrics": metrics.as_dict(),
        "baseline": {"kind": baseline_kind, **baseline.as_dict()},
        "verdict_vs_baseline": verdict.as_dict(),
        "verdict_vs_active": None,
        "reference": "baseline",
        "activated": True,
        "actor": actor,
        "exam": True,
    }
    _append_scoreboard(workspace, entry)
    journal(workspace, "examined", checkpoint=active.number, verdict=verdict.overall, score=metrics.score, actor=actor)
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
    """Freeze a new held-out version from the latest held-out episodes (human action)."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    try:
        battery = load_battery(workspace=workspace)
    except BatteryInvalidError as exc:
        return {"status": "error", "reason": str(exc)}
    rows = read_steps(workspace)
    try:
        heldout = freeze_heldout(workspace, rows, battery_version=battery.version)
    except ValueError as exc:
        return {"status": "not_enough_data", "reason": str(exc)}
    journal(workspace, "heldout_frozen", version=heldout.version, rows=len(heldout.rows), battery=battery.version, actor=actor)
    return {"status": "frozen", **heldout.describe()}


def force(workspace: Path | str, *, actor: str = HUMAN, number: int | None = None) -> dict[str, Any]:
    """Activate a flat adapter on purpose. Refused for a regressed one. Traced, reversible."""
    workspace = Path(workspace)
    if not read_settings(workspace).enabled:
        return {"status": "skipped", "reason": "policy learning is off for this project"}
    active = ck.read_active(workspace)
    candidates = [n for n in ck.list_checkpoint_numbers(workspace) if active is None or n != active.get("checkpoint")]
    if number is not None:
        candidates = [n for n in candidates if n == number]
    if not candidates:
        return {"status": "nothing_to_force", "reason": "no other adapter on disk"}
    target = ck.load_checkpoint(workspace, candidates[-1])
    if target is None:
        return {"status": "nothing_to_force", "reason": "adapter file unreadable"}
    reference = Verdict.from_dict(target.verdict_vs_active) or Verdict.from_dict(target.verdict_vs_baseline)
    if reference is not None and reference.regressed:
        journal(workspace, "force_refused", checkpoint=target.number, verdict=reference.overall, suites=reference.suites, actor=actor)
        return {"status": "refused", "reason": f"adapter {target.number} regressed ({reference.overall}); forcing a down adapter is not allowed"}
    ck.activate(workspace, target.number, heldout_version=target.heldout_version, actor=actor, forced=True)
    journal(
        workspace,
        "forced",
        checkpoint=target.number,
        previous=active.get("checkpoint") if active else None,
        verdict=reference.overall if reference else None,
        actor=actor,
    )
    return {"status": "forced", "checkpoint": target.number, "previous": active.get("checkpoint") if active else None}
