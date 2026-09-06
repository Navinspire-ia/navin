"""The local policy head: state -> distribution over actions (S4.2).

A small count model, deliberately not the chat LLM and not its weights:
it answers "given what the user asked and what the last calls answered,
which tool did the successful eval runs call next?" from reward-weighted
counts at five levels, blended with hierarchical Dirichlet smoothing so a
state never seen falls back on its intent, then on the project::

    level 0  everything                 "after a read, runs edit 40% of the time"
    level 1  intent                     "fix -> read_file first"
    level 2  intent + last call         "fix after read_file:ok -> edit_file"
    level 3  intent + last three        "fix after read, edit:changed -> stop"
    level 4  intent + last + S3 class   "...and the world model expects ok"

Only steps of **passed** episodes count as examples. Steps of failed
episodes feed a separate table, ``avoid``: actions that, in that state,
only ever appeared in runs that failed the eval. The steerer may say
"successful runs did X here" and "Y failed every time here"; it never
executes anything.

Two baselines share the interface so the exam compares honestly:
``UniformPolicy`` (no opinion) and ``MajorityPolicy`` (the most frequent
action of the training split, whatever the state).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from navin.policy.trajectory import STOP, StateKey

LEVELS = ("global", "intent", "intent_last", "intent_prev", "intent_last_s3")
STRENGTH = {"global": 1.0, "intent": 3.0, "intent_last": 2.0, "intent_prev": 1.5, "intent_last_s3": 1.5}
MIN_PROB = 1e-6
# An action is "to avoid" in a state when failed runs took it at least this
# often there and no successful run ever did.
AVOID_MIN_FAILS = 2


@dataclass(frozen=True, slots=True)
class ActionDistribution:
    action: str
    confidence: float
    probs: dict[str, float]
    support: int
    level: str
    avoid: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        top = sorted(self.probs.items(), key=lambda kv: -kv[1])[:5]
        return {
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "support": self.support,
            "level": self.level,
            "avoid": list(self.avoid),
            "top": [{"action": name, "p": round(p, 4)} for name, p in top],
        }


class Policy(Protocol):
    name: str

    def predict(self, state: StateKey) -> ActionDistribution: ...


def _blend(counts: dict[str, float] | None, parent: dict[str, float], strength: float) -> tuple[dict[str, float], int]:
    if not counts:
        return parent, 0
    n = sum(counts.values())
    if n <= 0:
        return parent, 0
    denominator = n + strength
    return {name: (counts.get(name, 0.0) + strength * parent.get(name, 0.0)) / denominator for name in parent}, int(round(n))


def _finish(probs: dict[str, float], support: int, level: str, avoid: tuple[str, ...] = ()) -> ActionDistribution:
    total = sum(probs.values()) or 1.0
    normalized = {name: max(MIN_PROB, p / total) for name, p in probs.items()}
    best = max(normalized, key=lambda name: normalized[name])
    return ActionDistribution(action=best, confidence=normalized[best], probs=normalized, support=support, level=level, avoid=avoid)


def _keys(state: StateKey) -> dict[str, str]:
    last = state.last
    return {
        "intent": state.intent,
        "intent_last": f"{state.intent}|{last}",
        "intent_prev": f"{state.intent}|{'>'.join(state.prev) or 'start'}",
        "intent_last_s3": f"{state.intent}|{last}|{state.s3 or '-'}",
    }


class PolicyHead:
    """Reward-weighted counts over actions, per state level, plus an avoid table."""

    name = "policy-counts"
    version = 1

    def __init__(self) -> None:
        self.actions: dict[str, float] = {STOP: 0.0}
        self.tables: dict[str, dict[str, dict[str, float]]] = {level: {} for level in LEVELS[1:]}
        self.failed: dict[str, dict[str, int]] = {}
        # Successful runs per intent: what a suggestion is really learned from.
        self.runs: dict[str, int] = {}
        self._run_ids: dict[str, set[str]] = {}
        self.rows = 0
        self.episodes: set[str] = set()

    # -- training ---------------------------------------------------------

    def observe(self, state: StateKey, action: str, reward: float, *, episode: str | None = None) -> None:
        if not action:
            return
        if episode:
            self.episodes.add(episode)
        self.actions.setdefault(action, 0.0)
        key_last = _keys(state)["intent_last"]
        if reward <= 0:
            table = self.failed.setdefault(key_last, {})
            table[action] = table.get(action, 0) + 1
            return
        self.actions[action] += reward
        for level, value in _keys(state).items():
            table = self.tables[level].setdefault(value, {})
            table[action] = table.get(action, 0.0) + reward
        self.rows += 1
        seen = self._run_ids.setdefault(state.intent, set())
        marker = episode or f"row-{self.rows}"
        if marker not in seen:
            seen.add(marker)
            self.runs[state.intent] = self.runs.get(state.intent, 0) + 1

    def fit(self, rows: Iterable[dict[str, Any]], *, on_progress: Any | None = None) -> PolicyHead:
        for n, record in enumerate(rows, start=1):
            reward = record.get("reward")
            self.observe(
                StateKey.from_record(record),
                str(record.get("action") or ""),
                float(reward) if isinstance(reward, (int, float)) else 0.0,
                episode=str(record.get("episode") or "") or None,
            )
            if on_progress is not None and n % 500 == 0:
                on_progress(n)
        return self

    # -- inference --------------------------------------------------------

    def _uniform(self) -> dict[str, float]:
        names = list(self.actions) or [STOP]
        return {name: 1.0 / len(names) for name in names}

    def avoid_for(self, state: StateKey) -> tuple[str, ...]:
        key_last = _keys(state)["intent_last"]
        fails = self.failed.get(key_last) or {}
        wins = self.tables["intent_last"].get(key_last) or {}
        return tuple(sorted(a for a, n in fails.items() if n >= AVOID_MIN_FAILS and wins.get(a, 0.0) <= 0))

    def predict(self, state: StateKey) -> ActionDistribution:
        """Blend every level; ``level`` is the deepest one that had data.

        ``support`` is the number of successful runs (episodes) of this kind
        of request (the intent level), which is what the suggestion is really
        learned from: deeper levels only refine it and rarely hold more than
        a run or two each.
        """
        probs, n_global = _blend(self.actions, self._uniform(), STRENGTH["global"])
        deepest = "global" if n_global else "prior"
        for level, value in _keys(state).items():
            counts = self.tables[level].get(value)
            probs, n = _blend(counts, probs, STRENGTH[level])
            if n:
                deepest = level
        support = self.runs.get(state.intent, 0) if deepest != "global" else 0
        return _finish(probs, support, deepest, self.avoid_for(state))

    def known_actions(self) -> list[str]:
        return sorted(self.actions)

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "rows": self.rows,
            "episodes": len(self.episodes),
            "actions": self.actions,
            "tables": self.tables,
            "failed": self.failed,
            "runs": self.runs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyHead:
        head = cls()
        if not isinstance(data, dict):
            return head
        actions = data.get("actions")
        if isinstance(actions, dict):
            head.actions = {str(k): float(v) for k, v in actions.items() if isinstance(v, (int, float))}
            head.actions.setdefault(STOP, 0.0)
        tables = data.get("tables")
        if isinstance(tables, dict):
            for level in LEVELS[1:]:
                table = tables.get(level)
                if not isinstance(table, dict):
                    continue
                clean: dict[str, dict[str, float]] = {}
                for value, counts in table.items():
                    if isinstance(counts, dict):
                        clean[str(value)] = {str(a): float(n) for a, n in counts.items() if isinstance(n, (int, float))}
                head.tables[level] = clean
        failed = data.get("failed")
        if isinstance(failed, dict):
            head.failed = {
                str(k): {str(a): int(n) for a, n in v.items() if isinstance(n, (int, float))}
                for k, v in failed.items()
                if isinstance(v, dict)
            }
        runs = data.get("runs")
        if isinstance(runs, dict):
            head.runs = {str(k): int(v) for k, v in runs.items() if isinstance(v, (int, float)) and v > 0}
        rows = data.get("rows")
        head.rows = int(rows) if isinstance(rows, int) else int(sum(head.actions.values()))
        return head


class UniformPolicy:
    """Baseline: no opinion, every known action equally likely."""

    name = "uniform"

    def __init__(self, actions: Iterable[str] = ()) -> None:
        names = sorted({*actions, STOP})
        self._prediction = _finish({name: 1.0 / len(names) for name in names}, 0, "baseline")

    def predict(self, state: StateKey) -> ActionDistribution:
        return self._prediction


class MajorityPolicy:
    """Baseline: the action frequencies of the successful training steps, smoothed."""

    name = "majority"

    def __init__(self, rows: Iterable[dict[str, Any]] = ()) -> None:
        counts: dict[str, float] = {STOP: 1.0}
        for record in rows:
            action = str(record.get("action") or "")
            reward = record.get("reward")
            if not action or not isinstance(reward, (int, float)) or reward <= 0:
                continue
            counts[action] = counts.get(action, 1.0) + 1.0
        total = sum(counts.values())
        self._prediction = _finish({a: c / total for a, c in counts.items()}, int(total) - len(counts), "baseline")

    def predict(self, state: StateKey) -> ActionDistribution:
        return self._prediction


def actions_in(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {str(r.get("action")) for r in rows if isinstance(r.get("action"), str)}


# --------------------------------------------------------------------------
# Metrics and verdicts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuiteMetrics:
    n: int
    accuracy: float
    log_loss: float

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "accuracy": round(self.accuracy, 4), "log_loss": round(self.log_loss, 4)}


@dataclass(frozen=True, slots=True)
class Metrics:
    n: int
    accuracy: float
    log_loss: float
    suites: dict[str, SuiteMetrics] = field(default_factory=dict)

    @property
    def score(self) -> int:
        """Accuracy out of 20, the note the panel shows."""
        return round(20 * self.accuracy) if self.n else 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "accuracy": round(self.accuracy, 4),
            "log_loss": round(self.log_loss, 4),
            "score": self.score,
            "suites": {suite: m.as_dict() for suite, m in sorted(self.suites.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Metrics:
        if not isinstance(data, dict):
            return cls(0, 0.0, 0.0)
        suites: dict[str, SuiteMetrics] = {}
        raw = data.get("suites")
        if isinstance(raw, dict):
            for suite, m in raw.items():
                if isinstance(m, dict):
                    suites[str(suite)] = SuiteMetrics(int(m.get("n") or 0), float(m.get("accuracy") or 0.0), float(m.get("log_loss") or 0.0))
        return cls(int(data.get("n") or 0), float(data.get("accuracy") or 0.0), float(data.get("log_loss") or 0.0), suites)


def evaluate(policy: Policy, rows: list[dict[str, Any]]) -> Metrics:
    """Next-action agreement with the successful steps of ``rows``, overall and per suite.

    Failed episodes are not an exam of what to do, so their steps are skipped.
    """
    hits = 0
    loss = 0.0
    n = 0
    per_suite: dict[str, list[float]] = {}
    for record in rows:
        reward = record.get("reward")
        if not isinstance(reward, (int, float)) or reward <= 0:
            continue
        actual = str(record.get("action") or "")
        if not actual:
            continue
        prediction = policy.predict(StateKey.from_record(record))
        p = max(MIN_PROB, prediction.probs.get(actual, MIN_PROB))
        hit = prediction.action == actual
        loss -= math.log(p)
        hits += 1 if hit else 0
        n += 1
        bucket = per_suite.setdefault(str(record.get("suite") or "uncategorized"), [0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += 1 if hit else 0
        bucket[2] -= math.log(p)
    if n == 0:
        return Metrics(0, 0.0, 0.0)
    suites = {
        suite: SuiteMetrics(n=int(b[0]), accuracy=b[1] / b[0], log_loss=b[2] / b[0])
        for suite, b in per_suite.items()
        if b[0]
    }
    return Metrics(n=n, accuracy=hits / n, log_loss=loss / n, suites=suites)


# Accuracy difference below which two heads are "the same" on a suite.
FLAT_BAND = 0.05
# Relative log-loss change that counts as a move when the accuracy is flat.
LOSS_BAND = 0.05


def _compare_one(candidate: SuiteMetrics | Metrics, reference: SuiteMetrics | Metrics) -> str:
    if candidate.n == 0 or reference.n == 0:
        return "flat"
    delta = candidate.accuracy - reference.accuracy
    if delta <= -FLAT_BAND:
        return "down"
    if delta >= FLAT_BAND:
        return "up"
    relative = (reference.log_loss - candidate.log_loss) / max(reference.log_loss, 1e-9)
    if relative <= -LOSS_BAND:
        return "down"
    if relative >= LOSS_BAND:
        return "up"
    return "flat"


@dataclass(frozen=True, slots=True)
class Verdict:
    overall: str
    suites: dict[str, str] = field(default_factory=dict)
    reference_score: int = 0
    candidate_score: int = 0

    @property
    def eligible(self) -> bool:
        """The live gate: better overall and no suite down."""
        return self.overall == "up" and "down" not in self.suites.values()

    @property
    def regressed(self) -> bool:
        return self.overall == "down" or "down" in self.suites.values()

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "suites": dict(sorted(self.suites.items())),
            "reference_score": self.reference_score,
            "candidate_score": self.candidate_score,
            "eligible": self.eligible,
            "regressed": self.regressed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Verdict | None:
        if not isinstance(data, dict) or not isinstance(data.get("overall"), str):
            return None
        suites = data.get("suites")
        return cls(
            overall=str(data["overall"]),
            suites={str(k): str(v) for k, v in suites.items()} if isinstance(suites, dict) else {},
            reference_score=int(data.get("reference_score") or 0),
            candidate_score=int(data.get("candidate_score") or 0),
        )


def compare(candidate: Metrics, reference: Metrics) -> Verdict:
    """``up`` / ``flat`` / ``down`` overall and per suite for candidate vs reference."""
    suites: dict[str, str] = {}
    for suite, metrics in candidate.suites.items():
        ref = reference.suites.get(suite)
        suites[suite] = _compare_one(metrics, ref) if ref is not None else "flat"
    return Verdict(
        overall=_compare_one(candidate, reference),
        suites=suites,
        reference_score=reference.score,
        candidate_score=candidate.score,
    )
