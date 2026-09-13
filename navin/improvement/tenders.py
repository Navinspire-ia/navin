# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tenders learns query-term ordering while retaining the complete fetch brief."""

from __future__ import annotations

import time

from loguru import logger

from navin.improvement.engine import ImprovementEngine, Observation
from navin.improvement.search import tender_brief


def begin(store, profile, brief):
    engine = ImprovementEngine(store.root / "improvement", "tenders")
    try:
        # Qualification also uses certifications, references and financial
        # capacity. Hash the whole profile so changed eligibility never shares
        # an experiment with the old company configuration.
        trial = engine.choose({"version": 1, "profile": profile})
        if trial:
            return (engine, trial, time.monotonic()), tender_brief(brief, trial.policy)
    except Exception as exc:  # noqa: BLE001 - use the configured collection path
        logger.debug("Tenders improvement unavailable: {}", type(exc).__name__)
    return None, brief


def finish(experiment, incoming, reports, *, failed=False):
    if not experiment:
        return
    engine, trial, started = experiment
    try:
        # Counts come from parsed, normalized notices scored with the original
        # company profile. A query hint or a catalogue entry is not evidence.
        unique = {row["id"]: row for row in incoming if row.get("id")}
        relevant = [row for row in unique.values() if row.get("go") and row.get("score", 0) >= 60]
        score = min(1, sum(row["score"] / 100 for row in relevant) / 5)
        executed = sum(report.get("kind") in {"fetch", "search", "custom"} for report in reports)
        if not executed and not failed:
            engine.abandon(trial)
            return
        engine.observe(trial, Observation(score, bool(relevant) and not failed,
                                         (time.monotonic() - started) * 1000, max(1, executed)))
    except Exception as exc:  # noqa: BLE001 - keep the collection result
        logger.debug("Tenders improvement observation unavailable: {}", type(exc).__name__)
