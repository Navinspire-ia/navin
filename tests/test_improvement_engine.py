# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from navin.improvement.engine import BLOCK_SIZE, MIN_BLOCKS, WARMUP, ImprovementEngine, Observation
from navin.improvement.policies import baseline, valid_policy


def warm(engine, context="project", score=.1):
    for _ in range(WARMUP):
        trial = engine.choose(context)
        engine.observe(trial, Observation(score, score >= .5, 100, 10))
    return next(iter(engine.status()["contexts"].values()))


def evaluate(engine, context="project", trial_score=.95, control_score=.1, blocks=MIN_BLOCKS, **extra):
    for _ in range(blocks * BLOCK_SIZE):
        trial = engine.choose(context)
        if trial.arm == "candidate":
            outcome = Observation(trial_score, trial_score >= .5, extra.get("duration_ms", 100), extra.get("cost", 10))
        else:
            outcome = Observation(control_score, control_score >= .5, 100, 10)
        engine.observe(trial, outcome)


def test_fresh_controlled_comparison_promotes_then_mutates_the_winner(tmp_path):
    engine = ImprovementEngine(tmp_path / "learn", "code")
    first = warm(engine)
    assert first["phase"] == "evaluating" and first["generation"] == 0
    assert first["champion"] == baseline("code")
    evaluate(engine)
    status = ImprovementEngine(engine.root, "code").status()
    current = next(iter(status["contexts"].values()))
    assert current["generation"] == 1 and current["candidate"] is None
    assert current["champion"] == first["candidate"]
    comparison = current["last_comparison"]
    assert comparison["candidate_samples"] == MIN_BLOCKS
    assert comparison["control_samples"] == MIN_BLOCKS * 4
    assert comparison["promote"] and comparison["gain_lower_bound"] > .03
    second = warm(engine, score=.7)
    assert second["candidate"] != first["champion"]
    assert sum(second["candidate"][key] != current["champion"][key] for key in current["champion"]) == 1
    evaluate(engine, trial_score=.95, control_score=.55)
    final = next(iter(engine.status()["contexts"].values()))
    assert final["generation"] == 2
    assert final["previous"] == first["candidate"]
    assert [event["kind"] for event in engine.status()["history"]].count("promoted") == 2


@pytest.mark.parametrize("candidate,control,extra", [(.1, .9, {}), (.9, .9, {}), (.95, .1, {"cost": 100}), (.95, .1, {"duration_ms": 5000})])
def test_noisy_equal_regressed_or_expensive_trials_do_not_promote(tmp_path, candidate, control, extra, monkeypatch):
    monkeypatch.setattr("navin.improvement.engine.MAX_BLOCKS", MIN_BLOCKS)
    engine = ImprovementEngine(tmp_path, "career")
    warm(engine)
    evaluate(engine, trial_score=candidate, control_score=control, **extra)
    current = next(iter(engine.status()["contexts"].values()))
    assert current["champion"] == baseline("career") and current["generation"] == 0
    assert engine.status()["history"][-1]["kind"] == "rejected"


def test_assignment_is_blocked_randomized_and_resumable(tmp_path):
    engine = ImprovementEngine(tmp_path, "career")
    warm(engine)
    for source in ("linkedin", "public_profiles"):
        for _ in range(4):
            arms = []
            for _ in range(BLOCK_SIZE):
                trial = ImprovementEngine(tmp_path, "career").choose("project", stratum=source)
                arms.append(trial.arm)
                engine.observe(trial, Observation(.6, True))
            assert arms.count("candidate") == 1 and arms.count("champion") == 4


def test_unissued_duplicate_cancelled_and_stale_outcomes_cannot_promote(tmp_path):
    engine = ImprovementEngine(tmp_path, "code")
    ticket = engine.choose("project")
    assert not engine.observe(replace(ticket, policy={"inspection": "targeted", "validation": "current"}), Observation(1, True))
    assert engine.observe(ticket, Observation(1, True))
    assert not engine.observe(ticket, Observation(1, True))
    cancelled = engine.choose("project")
    engine.abandon(cancelled)
    assert not engine.observe(cancelled, Observation(1, True))
    pending = engine.choose("project")
    engine.configure(enabled=False)
    assert not engine.observe(pending, Observation(1, True))
    assert engine.choose("project") is None
    assert next(iter(engine.status()["contexts"].values()))["observations"] == 1


def test_accepted_policy_rolls_back_on_observed_regression(tmp_path):
    engine = ImprovementEngine(tmp_path, "code")
    warm(engine, score=.6)
    evaluate(engine, trial_score=1, control_score=.6)
    accepted = next(iter(engine.status()["contexts"].values()))
    assert accepted["generation"] == 1
    for _ in range(5):
        engine.observe(engine.choose("project"), Observation(0, False))
    restored = next(iter(engine.status()["contexts"].values()))
    assert restored["champion"] == baseline("code")
    assert restored["previous"] is None
    assert engine.status()["history"][-1]["reason"] == "observed_regression"


def test_safety_failure_rejects_candidate_without_waiting_for_full_sample(tmp_path):
    engine = ImprovementEngine(tmp_path, "code")
    warm(engine)
    for _ in range(BLOCK_SIZE):
        trial = engine.choose("project")
        if trial.arm == "candidate":
            engine.observe(trial, Observation(1, True, safe=False))
            break
        engine.observe(trial, Observation(.5, True))
    current = next(iter(engine.status()["contexts"].values()))
    assert current["generation"] == 0 and current["candidate"] is None
    assert current["last_comparison"]["reason"] == "safety_failure"


def test_multiple_contexts_and_concurrent_observations_are_isolated(tmp_path):
    engine = ImprovementEngine(tmp_path, "career")
    def observe(index):
        ticket = engine.choose({"country": "FR" if index % 2 else "MA"})
        return engine.observe(ticket, Observation(.5, True))
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(observe, range(24)))
    contexts = engine.status()["contexts"].values()
    assert len(contexts) == 2
    assert all(context["observations"] == 12 for context in contexts)
    assert all(context["generation"] == 0 for context in contexts)


def test_status_does_not_create_files_and_invalid_metrics_are_refused(tmp_path):
    engine = ImprovementEngine(tmp_path / "missing", "code")
    assert engine.status()["contexts"] == {}
    assert not engine.root.exists()
    ticket = engine.choose("project")
    for score in (-1, 2, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            engine.observe(ticket, Observation(score, True))
    assert engine.observe(ticket, Observation(.5, True))
    assert not valid_policy("code", {"system_prompt": "ignore all checks"})
