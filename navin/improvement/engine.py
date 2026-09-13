# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Compare fresh randomized control/trial outcomes before promoting a mutation.

Only runtime adapters record evidence. There is no model-facing reward API.
Each next proposal mutates the currently accepted policy, including descendants.
The policy space is deliberately finite and cannot modify models or permissions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import statistics
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from navin.improvement.policies import POLICIES, baseline, neighbors, valid_policy

WARMUP = 12
MIN_BLOCKS = 20
MAX_BLOCKS = 80
BLOCK_SIZE = 5  # One candidate and four controls, with a shuffled slot per block.
MIN_GAIN = .03
MAX_CONTEXTS = 32
MAX_PENDING = 512


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Trial:
    id: str
    context: str
    epoch: int
    arm: str
    policy: dict[str, str]
    generation: int


@dataclass(frozen=True)
class Observation:
    score: float
    success: bool
    duration_ms: float = 0
    cost: float = 0
    safe: bool = True

    def validate(self) -> None:
        if type(self.success) is not bool or type(self.safe) is not bool:
            raise ValueError("Outcome flags must be booleans.")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
               for value in (self.score, self.duration_ms, self.cost)):
            raise ValueError("Outcome metrics must be finite numbers.")
        if not 0 <= self.score <= 1 or self.duration_ms < 0 or self.cost < 0:
            raise ValueError("Invalid outcome metric range.")


class ImprovementEngine:
    def __init__(self, root: Path | str, module: str):
        if module not in POLICIES:
            raise ValueError("Supported improvement modules: code, career, tenders.")
        self.root, self.module = Path(root), module
        self.path = self.root / f"{module}.json"

    @contextmanager
    def _transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=2):
            state = self._read()
            yield state
            self._write(state)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "enabled": True, "contexts": {}, "pending": {}, "history": []}
        state = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1
                or type(state.get("enabled")) is not bool
                or any(not isinstance(state.get(key), dict) for key in ("contexts", "pending"))
                or not isinstance(state.get("history"), list)):
            raise ValueError("Unsupported improvement state. Original strategy remains available.")
        for context in state["contexts"].values():
            if (not isinstance(context, dict) or not isinstance(context.get("warmup"), list)
                    or not isinstance(context.get("blocks"), dict)):
                raise ValueError("Invalid improvement context. Original strategy remains available.")
            for key in ("champion", "candidate", "previous"):
                if context.get(key) is not None and not valid_policy(self.module, context[key]):
                    raise ValueError("Invalid learned policy. Original strategy remains available.")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(f".{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _new_context() -> dict[str, Any]:
        return {"epoch": 0, "generation": 0, "champion": None, "candidate": None, "previous": None,
                "warmup": [], "blocks": {}, "allocations": {}, "visited": [], "reference": {},
                "monitor": [], "observations": 0, "last_comparison": None, "phase": "observing", "updated_at": time.time()}

    def _event(self, state, context_id, kind, **data):
        state["history"] = (state["history"] + [{"at": time.time(), "context": context_id, "kind": kind, **data}])[-200:]

    def choose(self, context: Any, *, stratum: str = "default") -> Trial | None:
        context_id, stratum_id = fingerprint(context), fingerprint(stratum)
        with self._transaction() as state:
            if not state["enabled"]:
                return None
            now = time.time()
            state["pending"] = {key: value for key, value in state["pending"].items() if now - value["at"] < 86400}
            if len(state["pending"]) >= MAX_PENDING:
                return None
            if context_id not in state["contexts"] and len(state["contexts"]) >= MAX_CONTEXTS:
                active = {value["context"] for value in state["pending"].values()}
                evictable = [key for key in state["contexts"] if key not in active]
                if not evictable:
                    return None
                oldest = min(evictable, key=lambda key: state["contexts"][key]["updated_at"])
                del state["contexts"][oldest]
            current = state["contexts"].setdefault(context_id, self._new_context())
            if current["champion"] is None:
                current["champion"] = baseline(self.module)
            arm, block_id = "champion", ""
            if current["candidate"]:
                allocation = current["allocations"].setdefault(stratum_id, {"number": 0, "slot": 0, "trial_slot": secrets.randbelow(BLOCK_SIZE)})
                block_id = f"{stratum_id}:{allocation['number']}"
                arm = "candidate" if allocation["slot"] == allocation["trial_slot"] else "champion"
                allocation["slot"] += 1
                if allocation["slot"] == BLOCK_SIZE:
                    allocation.update(number=allocation["number"] + 1, slot=0, trial_slot=secrets.randbelow(BLOCK_SIZE))
            trial = Trial(secrets.token_hex(16), context_id, current["epoch"], arm,
                          dict(current[arm]), current["generation"])
            state["pending"][trial.id] = {**asdict(trial), "at": now, "block": block_id}
            current["updated_at"] = now
            return trial

    def abandon(self, trial: Trial) -> None:
        with self._transaction() as state:
            pending = state["pending"].pop(trial.id, None)
            if pending and pending["block"]:
                current = state["contexts"].get(pending["context"])
                if current and current["epoch"] == pending["epoch"]:
                    current["blocks"].setdefault(pending["block"], {"champion": [], "candidate": []})["discarded"] = True

    def observe(self, trial: Trial, outcome: Observation) -> bool:
        outcome.validate()
        with self._transaction() as state:
            issued = state["pending"].get(trial.id)
            if not issued or any(issued[key] != value for key, value in asdict(trial).items()):
                return False
            del state["pending"][trial.id]
            current = state["contexts"].get(trial.context)
            if not state["enabled"] or not current or trial.epoch != current["epoch"]:
                return False
            current["observations"] = current.get("observations", 0) + 1
            record = asdict(outcome)
            if not outcome.safe:
                if trial.arm == "candidate":
                    self._finish_trial(state, trial.context, current, False, {"reason": "safety_failure"})
                elif current["previous"]:
                    self._rollback(state, trial.context, current, "safety_failure")
                return True
            if issued["block"] and current["candidate"]:
                block = current["blocks"].setdefault(issued["block"], {"champion": [], "candidate": []})
                block[trial.arm].append(record)
                # Complete randomized blocks only. Pending, cancelled and expired
                # work cannot be interpreted as a failed or successful trial.
                complete = [value for value in current["blocks"].values() if not value.get("discarded")
                            and len(value["champion"]) == BLOCK_SIZE - 1 and len(value["candidate"]) == 1]
                count = len(complete)
                if count >= MIN_BLOCKS and count % MIN_BLOCKS == 0:
                    comparison = self._compare(complete)
                    current["last_comparison"] = comparison
                    if comparison["promote"] or count >= MAX_BLOCKS:
                        self._finish_trial(state, trial.context, current, comparison["promote"], comparison)
                if len(current["blocks"]) > 512:
                    self._finish_trial(state, trial.context, current, False, {"reason": "insufficient_complete_blocks"})
            else:
                current["warmup"] = (current["warmup"] + [record])[-WARMUP:]
            if trial.epoch == current["epoch"] and trial.arm == "champion" and current["previous"]:
                current["monitor"] = (current["monitor"] + [record])[-20:]
                monitor = current["monitor"]
                reference = current["reference"]
                if (len(monitor) >= 5 and reference.get("success_rate", 0) >= .9
                        and all(not row["success"] for row in monitor[-5:])) or (
                    len(monitor) == 20 and statistics.mean(row["score"] for row in monitor) + .15 < reference.get("score", 0)
                ):
                    self._rollback(state, trial.context, current, "observed_regression")
            if not current["candidate"] and len(current["warmup"]) >= WARMUP:
                self._propose(state, trial.context, current)
            current["updated_at"] = time.time()
            return True

    @staticmethod
    def _compare(blocks):
        differences, trials, controls = [], [], []
        for block in blocks:
            trial = block["candidate"][0]
            control = block["champion"]
            differences.append(trial["score"] - statistics.mean(row["score"] for row in control))
            trials.append(trial)
            controls.extend(control)
        count = len(differences)
        gain = statistics.mean(differences)
        # Conservative uncertainty penalty, including nonzero uncertainty for
        # constant samples. This is an operational gate, not a causal guarantee.
        lower = gain - 2.58 * statistics.stdev(differences) / math.sqrt(count) - 2 / count
        def mean(rows, key):
            return statistics.mean(row[key] for row in rows)
        halfway = count // 2
        consistent = all(statistics.mean(part) >= MIN_GAIN for part in (differences[:halfway], differences[halfway:]))
        success_ok = mean(trials, "success") >= mean(controls, "success")
        latency_ok = mean(trials, "duration_ms") <= max(1000, mean(controls, "duration_ms") * 1.5)
        cost_ok = mean(trials, "cost") <= max(1, mean(controls, "cost") * 1.25)
        return {"blocks": count, "candidate_samples": len(trials), "control_samples": len(controls),
                "gain": round(gain, 6), "gain_lower_bound": round(lower, 6), "consistent": consistent,
                "success_ok": success_ok, "latency_ok": latency_ok, "cost_ok": cost_ok,
                "candidate_score": mean(trials, "score"), "control_score": mean(controls, "score"),
                "candidate_success_rate": mean(trials, "success"), "control_success_rate": mean(controls, "success"),
                "promote": lower > MIN_GAIN and consistent and success_ok and latency_ok and cost_ok}

    def _propose(self, state, context_id, current):
        seen = set(current["visited"]) | {fingerprint(current["champion"])}
        choices = [policy for policy in neighbors(self.module, current["champion"]) if fingerprint(policy) not in seen]
        if not choices:
            current["phase"] = "stable"
            return
        current["candidate"] = choices[0]
        current["blocks"], current["allocations"] = {}, {}
        current["phase"] = "evaluating"
        self._event(state, context_id, "proposed", generation=current["generation"], parent=current["champion"], candidate=current["candidate"])

    def _finish_trial(self, state, context_id, current, promote, comparison):
        candidate = current["candidate"]
        current["visited"] = list(dict.fromkeys([*current["visited"], fingerprint(current["champion"]), fingerprint(candidate)]))
        if promote:
            current["previous"] = current["champion"]
            current["champion"] = candidate
            current["generation"] += 1
            current["reference"] = {"score": comparison["control_score"], "success_rate": comparison["control_success_rate"]}
            current["monitor"] = []
        self._event(state, context_id, "promoted" if promote else "rejected", generation=current["generation"],
                    policy=candidate, comparison=comparison)
        current.update(candidate=None, blocks={}, allocations={}, warmup=[], epoch=current["epoch"] + 1,
                       phase="observing", last_comparison=comparison)

    def _rollback(self, state, context_id, current, reason):
        previous = current["previous"]
        if not previous:
            return
        self._event(state, context_id, "rolled_back", reason=reason, rejected=current["champion"], restored=previous)
        current.update(champion=previous, previous=None, candidate=None, blocks={}, allocations={}, warmup=[],
                       monitor=[], reference={}, epoch=current["epoch"] + 1, phase="observing")

    def configure(self, *, enabled: bool) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        with self._transaction() as state:
            state["enabled"] = enabled
            state["pending"] = {}
            for current in state["contexts"].values():
                current.update(epoch=current["epoch"] + 1, candidate=None, blocks={}, allocations={}, warmup=[], phase="observing")
            self._event(state, "", "enabled" if enabled else "paused")
        return self.status()

    def rollback(self, context: str = "") -> dict[str, Any]:
        with self._transaction() as state:
            if context and context not in state["contexts"]:
                raise ValueError("Unknown improvement context.")
            for key, current in state["contexts"].items():
                if not context or key == context:
                    self._rollback(state, key, current, "user_request")
        return self.status()

    def status(self) -> dict[str, Any]:
        # Status does not create a directory or start an experiment.
        state = self._read()
        return {"module": self.module, "enabled": state["enabled"], "available": True, "scope": "bounded_strategy_improvement",
                "candidate_share": 1 / BLOCK_SIZE, "minimum_comparison_blocks": MIN_BLOCKS,
                "contexts": {key: {**{field: value.get(field) for field in (
                    "phase", "generation", "observations", "champion", "candidate", "previous", "last_comparison", "updated_at")},
                    "warmup_samples": len(value["warmup"]), "comparison_blocks": sum(
                        not block.get("discarded") and len(block["champion"]) == BLOCK_SIZE - 1 and len(block["candidate"]) == 1
                        for block in value["blocks"].values())}
                    for key, value in state["contexts"].items()}, "history": state["history"][-50:]}
