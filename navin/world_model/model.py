# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The local head: a small count model that predicts the class of the next
observation from (tool, normalized args, last calls). Pure Python, trains in
one pass, serializes to JSON, needs no network and no GPU (S3.2).

It is deliberately a statistics head, not the chat LLM: five levels of
counts, from the whole project down to the exact call, blended with
hierarchical Dirichlet smoothing so a rare exact call falls back on its
program, then its tool, then the project.

    level 0  everything                 "npm test fails 30% of the time here"
    level 1  tool                       "exec fails 12%"
    level 2  key (tool|program|sub)     "exec|npm|test fails 80%"
    level 3  key + previous outcome     "exec|npm|test after exec:error fails 95%"
    level 4  exact call (args hash)     "this very command, seen 6 times: 6 errors"

Two baselines share the interface so the exam can compare honestly:
``AlwaysOkModel`` ("everything works") and ``MajorityModel`` (class
frequencies of the training split).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from navin.world_model.trajectory import CLASSES

_INDEX = {name: i for i, name in enumerate(CLASSES)}
K = len(CLASSES)

LEVELS = ("global", "tool", "key", "key_prev", "call")
# Pseudo-count strength of the parent distribution at each level. Small
# numbers trust the deeper level quickly; larger ones stay cautious.
STRENGTH = {"global": 1.0, "tool": 4.0, "key": 3.0, "key_prev": 2.0, "call": 1.5}
MIN_PROB = 1e-6


@dataclass(frozen=True, slots=True)
class Prediction:
    cls: str
    confidence: float
    probs: tuple[float, ...]
    support: int
    level: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "cls": self.cls,
            "confidence": round(self.confidence, 4),
            "support": self.support,
            "level": self.level,
            "probs": {name: round(p, 4) for name, p in zip(CLASSES, self.probs, strict=True)},
        }


class Model(Protocol):
    name: str

    def predict(self, tool: str, key: str, args_hash: str, prev: list[str]) -> Prediction: ...


def _uniform() -> list[float]:
    return [1.0 / K] * K


def _blend(counts: list[int] | None, parent: list[float], strength: float) -> tuple[list[float], int]:
    if not counts:
        return parent, 0
    n = sum(counts)
    if n == 0:
        return parent, 0
    denominator = n + strength
    return [(counts[i] + strength * parent[i]) / denominator for i in range(K)], n


def _finish(probs: list[float], support: int, level: str) -> Prediction:
    total = sum(probs) or 1.0
    normalized = tuple(max(MIN_PROB, p / total) for p in probs)
    best = max(range(K), key=lambda i: normalized[i])
    return Prediction(cls=CLASSES[best], confidence=normalized[best], probs=normalized, support=support, level=level)


def features_of(record: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    prev = record.get("prev")
    return (
        str(record.get("tool") or ""),
        str(record.get("key") or ""),
        str(record.get("args") or ""),
        [str(p) for p in prev] if isinstance(prev, list) else [],
    )


def _prev_class(prev: list[str]) -> str:
    if not prev:
        return "start"
    last = prev[-1]
    return last.rsplit(":", 1)[-1] if ":" in last else last


class CountModel:
    """Hierarchical counts over the observation classes."""

    name = "counts"
    version = 1

    def __init__(self) -> None:
        self.global_counts: list[int] = [0] * K
        self.tables: dict[str, dict[str, list[int]]] = {level: {} for level in LEVELS[1:]}
        self.rows = 0

    # -- training ---------------------------------------------------------

    @staticmethod
    def _keys(tool: str, key: str, args_hash: str, prev: list[str]) -> dict[str, str]:
        return {
            "tool": tool,
            "key": key,
            "key_prev": f"{key}|{_prev_class(prev)}",
            "call": args_hash,
        }

    def observe(self, tool: str, key: str, args_hash: str, prev: list[str], cls: str) -> None:
        index = _INDEX.get(cls)
        if index is None:
            return
        self.global_counts[index] += 1
        for level, value in self._keys(tool, key, args_hash, prev).items():
            table = self.tables[level]
            counts = table.get(value)
            if counts is None:
                counts = [0] * K
                table[value] = counts
            counts[index] += 1
        self.rows += 1

    def fit(self, rows: Iterable[dict[str, Any]], *, on_progress: Any | None = None) -> CountModel:
        for n, record in enumerate(rows, start=1):
            tool, key, args_hash, prev = features_of(record)
            self.observe(tool, key, args_hash, prev, str(record.get("cls") or ""))
            if on_progress is not None and n % 1000 == 0:
                on_progress(n)
        return self

    # -- inference --------------------------------------------------------

    def predict(self, tool: str, key: str, args_hash: str, prev: list[str]) -> Prediction:
        probs, support = _blend(self.global_counts, _uniform(), STRENGTH["global"])
        deepest = "global" if support else "prior"
        for level, value in self._keys(tool, key, args_hash, prev).items():
            counts = self.tables[level].get(value)
            probs, n = _blend(counts, probs, STRENGTH[level])
            if n:
                support, deepest = n, level
        return _finish(probs, support, deepest)

    def key_support(self, key: str) -> tuple[list[int], int]:
        counts = self.tables["key"].get(key) or [0] * K
        return counts, sum(counts)

    def predict_key(self, key: str) -> Prediction:
        """What the head expects of a key on its own (the beliefs summary)."""
        probs, _ = _blend(self.global_counts, _uniform(), STRENGTH["global"])
        counts, support = self.key_support(key)
        probs, _ = _blend(counts, probs, STRENGTH["key"])
        return _finish(probs, support, "key")

    def keys(self) -> list[str]:
        return list(self.tables["key"])

    # -- persistence ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "classes": list(CLASSES),
            "rows": self.rows,
            "global": self.global_counts,
            "tables": self.tables,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CountModel:
        model = cls()
        if not isinstance(data, dict) or data.get("classes") != list(CLASSES):
            return model
        raw_global = data.get("global")
        if isinstance(raw_global, list) and len(raw_global) == K:
            model.global_counts = [int(v) for v in raw_global]
        tables = data.get("tables")
        if isinstance(tables, dict):
            for level in LEVELS[1:]:
                table = tables.get(level)
                if not isinstance(table, dict):
                    continue
                clean: dict[str, list[int]] = {}
                for value, counts in table.items():
                    if isinstance(counts, list) and len(counts) == K:
                        clean[str(value)] = [int(v) for v in counts]
                model.tables[level] = clean
        rows = data.get("rows")
        model.rows = int(rows) if isinstance(rows, int) else sum(model.global_counts)
        return model


class AlwaysOkModel:
    """Baseline: every call works. What Navin implicitly assumes today."""

    name = "always_ok"

    def __init__(self, epsilon: float = 0.02) -> None:
        rest = epsilon / (K - 1)
        probs = [rest] * K
        probs[_INDEX["ok"]] = 1.0 - epsilon
        self._prediction = _finish(probs, 0, "baseline")

    def predict(self, tool: str, key: str, args_hash: str, prev: list[str]) -> Prediction:
        return self._prediction


class MajorityModel:
    """Baseline: the class frequencies of the training split, smoothed."""

    name = "majority"

    def __init__(self, rows: Iterable[dict[str, Any]] = ()) -> None:
        counts = [1] * K
        for record in rows:
            index = _INDEX.get(str(record.get("cls") or ""))
            if index is not None:
                counts[index] += 1
        total = sum(counts)
        self._prediction = _finish([c / total for c in counts], total - K, "baseline")

    def predict(self, tool: str, key: str, args_hash: str, prev: list[str]) -> Prediction:
        return self._prediction


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Metrics:
    n: int
    log_loss: float
    error_rate: float
    ece: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "log_loss": round(self.log_loss, 4),
            "error_rate": round(self.error_rate, 4),
            "ece": round(self.ece, 4),
        }


def evaluate(model: Model, rows: list[dict[str, Any]], *, bins: int = 10) -> Metrics:
    """Prediction error on ``rows``: log-loss, wrong-class rate, calibration (ECE)."""
    if not rows:
        return Metrics(0, 0.0, 0.0, 0.0)
    loss = 0.0
    wrong = 0
    bin_conf = [0.0] * bins
    bin_hit = [0] * bins
    bin_n = [0] * bins
    for record in rows:
        tool, key, args_hash, prev = features_of(record)
        actual = _INDEX.get(str(record.get("cls") or ""))
        if actual is None:
            continue
        prediction = model.predict(tool, key, args_hash, prev)
        p = max(MIN_PROB, prediction.probs[actual])
        loss -= math.log(p)
        hit = prediction.cls == CLASSES[actual]
        if not hit:
            wrong += 1
        slot = min(bins - 1, int(prediction.confidence * bins))
        bin_conf[slot] += prediction.confidence
        bin_hit[slot] += 1 if hit else 0
        bin_n[slot] += 1
    n = sum(bin_n)
    if n == 0:
        return Metrics(0, 0.0, 0.0, 0.0)
    ece = sum(abs(bin_hit[i] / bin_n[i] - bin_conf[i] / bin_n[i]) * (bin_n[i] / n) for i in range(bins) if bin_n[i])
    return Metrics(n=n, log_loss=loss / n, error_rate=wrong / n, ece=ece)


# Relative log-loss change below which two heads are "the same".
FLAT_BAND = 0.02
# Extra wrong-class rate tolerated before calling a regression.
ERROR_TOLERANCE = 0.01


def compare(candidate: Metrics, reference: Metrics) -> str:
    """``up`` (learned), ``flat`` (same), ``down`` (worse) for candidate vs reference."""
    if candidate.n == 0 or reference.n == 0:
        return "flat"
    relative = (reference.log_loss - candidate.log_loss) / max(reference.log_loss, 1e-9)
    worse_errors = candidate.error_rate > reference.error_rate + ERROR_TOLERANCE
    if relative <= -FLAT_BAND or candidate.error_rate > reference.error_rate + 2 * ERROR_TOLERANCE:
        return "down"
    if relative >= FLAT_BAND and not worse_errors:
        return "up"
    return "flat"
