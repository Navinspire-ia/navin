# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the AGI panel and ``navin agi policy`` read and press (S4.5).

``policy_state`` is one JSON-friendly snapshot: flag, radar (S3.3), battery,
trajectories, frozen set, adapters N / N+1 with their verdicts, the serving
adapter, the steer gate, the offline A/B, the live window, the published
adapters, the event journal tail. ``policy_update`` writes switches and
refuses ``enabled: true`` while the radar is not up and ``steer: true``
while the gate is closed. ``policy_action`` maps the buttons to the jobs;
everything runs in the caller's thread, outside any chat turn, and the
training itself in a child process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.policy import checkpoints as ck
from navin.policy.battery import BatteryInvalidError, load_battery
from navin.policy.dataset import HeldoutTamperedError, load_heldout
from navin.policy.jobs import run_due, runner_alive, spawn_train, turns_since_train
from navin.policy.journal import (
    count_episodes,
    count_steps,
    read_journal,
    read_steps,
    recent_steps,
    writer_alive,
)
from navin.policy.paths import POLICY_DIR_NAME, trajectories_path
from navin.policy.publish import adopt, publish, read_published, unpublish
from navin.policy.radar import radar
from navin.policy.settings import (
    FIELDS,
    SETTINGS_NAME,
    read_settings,
    update_settings,
    validate_fields,
)
from navin.policy.steerer import (
    HUMAN,
    latest_ab,
    live_summary,
    read_ab,
    run_ab,
    steer_gate,
)
from navin.policy.train import (
    TrainBudget,
    exam,
    force,
    freeze,
    latest_score,
    read_scoreboard,
    read_train_state,
    rollback,
    train,
)

ACTIONS = (
    "train",      # human: train now, in a child process (ignores the train sub-switch, not the master or the radar)
    "run",        # run the training job if it is due
    "exam",       # re-score the active adapter on the same frozen set
    "freeze",     # human: freeze a new held-out version
    "rollback",   # human: back to adapter N-1
    "force",      # human: activate a flat adapter on purpose (never a down one)
    "ab",         # offline A/B of the active adapter on the frozen set
    "publish",    # human: copy the active adapter to the machine folder
    "unpublish",  # human: remove one published adapter
    "adopt",      # human: import a published adapter as a checkpoint here
)
HUMAN_ONLY = frozenset({"train", "freeze", "rollback", "force", "publish", "unpublish", "adopt"})


class PolicyActionError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _heldout_payload(workspace: Path) -> dict[str, Any]:
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return {"version": None, "rows": 0, "error": str(exc)}
    if heldout is None:
        return {"version": None, "rows": 0}
    return heldout.describe()


def _battery_payload(workspace: Path) -> dict[str, Any]:
    try:
        return load_battery(workspace=workspace).describe()
    except BatteryInvalidError as exc:
        return {"version": None, "error": str(exc), "suites": []}


def _score_payload(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
    baseline = entry.get("baseline") if isinstance(entry.get("baseline"), dict) else {}
    return {
        "ts": entry.get("ts"),
        "checkpoint": entry.get("checkpoint"),
        "heldout_version": entry.get("heldout_version"),
        "n": entry.get("n"),
        "accuracy": metrics.get("accuracy"),
        "log_loss": metrics.get("log_loss"),
        "score": metrics.get("score"),
        "suites": metrics.get("suites"),
        "baseline_kind": baseline.get("kind"),
        "baseline_accuracy": baseline.get("accuracy"),
        "baseline_score": baseline.get("score"),
        "verdict_vs_baseline": entry.get("verdict_vs_baseline"),
        "verdict_vs_active": entry.get("verdict_vs_active"),
        "reference": entry.get("reference"),
        "activated": entry.get("activated"),
        "exam": bool(entry.get("exam")),
    }


def _off_state(base: dict[str, Any], workspace: Path) -> dict[str, Any]:
    return {
        **base,
        "radar": radar(workspace).as_dict(),
        "battery": {"version": None, "suites": []},
        "rows": 0,
        "episodes": 0,
        "journal_bytes": 0,
        "recent": [],
        "heldout": {"version": None, "rows": 0},
        "checkpoints": [],
        "active": None,
        "score": None,
        "scoreboard": [],
        "gate": {
            "open": False,
            "reasons": ["policy learning is off"],
            "checkpoint": None,
            "heldout_version": None,
            "exam_verdict": None,
            "ab_verdict": None,
            "forced": False,
        },
        "ab": None,
        "ab_history": [],
        "live": {"suggested": 0, "window": 0, "precision": None, "logged": 0, "logged_precision": None},
        "published": read_published(),
        "train_state": {},
        "turns_since_train": 0,
        "runner_alive": runner_alive(),
        "writer_alive": writer_alive(),
        "journal": [],
    }


def policy_state(workspace: Path | str, *, journal_limit: int = 30) -> dict[str, Any]:
    workspace = Path(workspace)
    settings = read_settings(workspace)
    base: dict[str, Any] = {
        **settings.as_dict(),
        "settings_file": ".navin/" + SETTINGS_NAME,
        "policy_dir": ".navin/" + POLICY_DIR_NAME,
        "fields": list(FIELDS),
    }
    if not settings.enabled:
        # Off: report the flag and the radar, nothing else; no file is created.
        return _off_state(base, workspace)
    try:
        journal_bytes = trajectories_path(workspace).stat().st_size
    except OSError:
        journal_bytes = 0
    active = ck.read_active(workspace)
    heldout = _heldout_payload(workspace)
    gate = steer_gate(workspace)
    rows = read_steps(workspace)
    return {
        **base,
        "radar": radar(workspace).as_dict(),
        "battery": _battery_payload(workspace),
        "rows": count_steps(workspace),
        "episodes": count_episodes(rows),
        "journal_bytes": journal_bytes,
        "recent": recent_steps(workspace, limit=12),
        "heldout": heldout,
        "checkpoints": ck.checkpoint_summaries(workspace),
        "active": active,
        "score": _score_payload(latest_score(workspace, heldout_version=heldout.get("version"))),
        "scoreboard": [_score_payload(e) for e in read_scoreboard(workspace, limit=12)],
        "gate": gate.as_dict(),
        "ab": latest_ab(workspace, checkpoint=gate.checkpoint, heldout_version=gate.heldout_version),
        "ab_history": read_ab(workspace, limit=8),
        "live": live_summary(workspace),
        "published": read_published(),
        "train_state": read_train_state(workspace),
        "turns_since_train": turns_since_train(workspace),
        "runner_alive": runner_alive(),
        "writer_alive": writer_alive(),
        "journal": read_journal(workspace, limit=journal_limit),
    }


def policy_update(workspace: Path | str, fields: dict[str, Any]) -> dict[str, Any]:
    """Write switches. ``enabled: true`` needs the radar up (409); ``steer: true`` needs the gate open (409)."""
    workspace = Path(workspace)
    try:
        validate_fields(fields)
    except ValueError as exc:
        raise PolicyActionError(str(exc)) from exc
    current = read_settings(workspace)
    if fields.get("enabled") is True and not current.enabled:
        signal = radar(workspace)
        if not signal.up:
            raise PolicyActionError("no policy without a radar: " + "; ".join(signal.reasons), 409)
    if fields.get("steer") is True:
        enabled = fields.get("enabled", current.enabled)
        if not enabled:
            raise PolicyActionError("turn policy learning on before steer", 409)
        gate = steer_gate(workspace)
        if not gate.open:
            raise PolicyActionError("steer gate closed: " + "; ".join(gate.reasons), 409)
    try:
        settings = update_settings(workspace, fields)
    except ValueError as exc:
        raise PolicyActionError(str(exc)) from exc
    if "steer" in fields or "enabled" in fields:
        from navin.policy.journal import journal
        from navin.policy.registration import remember_project

        remember_project(workspace, settings.feature("steer"))
        if "enabled" in fields:
            journal(workspace, "enabled" if fields["enabled"] else "disabled", actor=HUMAN)
        if "steer" in fields:
            journal(workspace, "steer_on" if fields["steer"] else "steer_off", actor=HUMAN)
    return policy_state(workspace)


def policy_action(
    workspace: Path | str,
    action: str,
    *,
    actor: str = "auto",
    name: str | None = None,
    number: int | None = None,
    budget: TrainBudget | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Press one button. Returns ``{"ok", "action", "result", "state"}``.

    Raises ``PolicyActionError`` with an HTTP-ish status: 400 for a bad
    request, 403 when a human is required, 409 when the flag is off or the
    state forbids the transition. ``inline`` trains in this process (tests);
    the panel and the CLI train in a child.
    """
    workspace = Path(workspace)
    if action not in ACTIONS:
        raise PolicyActionError(f"unknown action: {action}")
    settings = read_settings(workspace)
    if not settings.enabled and action != "unpublish":
        raise PolicyActionError("policy learning is off for this project", 409)
    if action in HUMAN_ONLY and actor != HUMAN:
        raise PolicyActionError(f"{action} needs a human", 403)
    result: Any
    if action == "train":
        if inline:
            result = train(workspace, actor=HUMAN, budget=budget, force=True).as_dict()
        else:
            budget = budget or TrainBudget()
            result = spawn_train(
                workspace,
                actor=HUMAN,
                force=True,
                timeout_s=budget.timeout_s,
                max_rss_mb=budget.max_rss_growth_mb,
                max_cases=budget.max_cases,
            )
    elif action == "run":
        result = run_due(workspace) or {"status": "not_due"}
    elif action == "exam":
        result = exam(workspace, actor=actor)
    elif action == "freeze":
        result = freeze(workspace, actor=HUMAN)
    elif action == "rollback":
        result = rollback(workspace, actor=HUMAN)
    elif action == "force":
        result = force(workspace, actor=HUMAN, number=number)
    elif action == "ab":
        result = run_ab(workspace, actor=actor)
    elif action == "publish":
        result = publish(workspace, actor=HUMAN, note=name or "")
    elif action == "unpublish":
        if not name:
            raise PolicyActionError("a published adapter name is required")
        result = unpublish(name, actor=HUMAN, workspace=workspace)
    else:
        if not name:
            raise PolicyActionError("a published adapter name is required")
        result = adopt(workspace, name, actor=HUMAN)
    status = result.get("status")
    if status == "tampered":
        raise PolicyActionError(str(result.get("reason")), 409)
    if status == "radar_down":
        raise PolicyActionError(str(result.get("reason")), 409)
    if status == "refused":
        raise PolicyActionError(str(result.get("reason")), 409)
    return {"ok": True, "action": action, "result": result, "state": policy_state(workspace)}
