# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the AGI panel and ``navin agi world`` read and press (S3.5, S3.6).

``world_state`` is one JSON-friendly snapshot: flag, journal size, frozen
set, checkpoints with their scores, the serving head, the advice gate, the
offline A/B, the live window, the beliefs, the event journal tail.
``world_update`` writes switches and refuses ``advise: true`` while the gate
is closed. ``world_action`` maps the buttons to the jobs; everything runs in
the caller's thread, outside any chat turn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.world_model import checkpoints as ck
from navin.world_model.advisor import (
    HUMAN,
    advice_gate,
    latest_ab,
    live_summary,
    read_ab,
    run_ab,
)
from navin.world_model.beliefs import (
    current_beliefs,
    discard_belief,
    read_beliefs_file,
    read_ignored,
    render_beliefs,
    restore_beliefs,
)
from navin.world_model.dataset import HeldoutTamperedError, load_heldout
from navin.world_model.jobs import run_due, runner_alive
from navin.world_model.journal import (
    count_trajectories,
    read_journal,
    recent_trajectories,
    writer_alive,
)
from navin.world_model.paths import (
    BELIEFS_FILE_NAME,
    WORLD_DIR_NAME,
    trajectories_path,
)
from navin.world_model.settings import (
    FIELDS,
    SETTINGS_NAME,
    read_settings,
    update_settings,
    validate_fields,
)
from navin.world_model.train import (
    TrainBudget,
    exam,
    freeze,
    latest_score,
    read_scoreboard,
    read_train_state,
    rollback,
    train,
)
from navin.world_model.trajectory import CLASSES

ACTIONS = (
    "train",     # human: train now (ignores the train sub-switch, not the master)
    "run",       # run the training job if it is due
    "exam",      # re-score the active head on the same frozen set
    "freeze",    # human: freeze a new held-out version
    "rollback",  # human: back to checkpoint N-1
    "ab",        # offline A/B of the active head on the frozen set
    "beliefs",   # render .navin/BELIEFS.md now
    "discard_belief",  # human: throw one belief away (never comes back)
    "restore_beliefs", # human: forget the discards
)


class WorldActionError(ValueError):
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
        "log_loss": metrics.get("log_loss"),
        "error_rate": metrics.get("error_rate"),
        "ece": metrics.get("ece"),
        "baseline_kind": baseline.get("kind"),
        "baseline_log_loss": baseline.get("log_loss"),
        "baseline_error_rate": baseline.get("error_rate"),
        "verdict": entry.get("verdict_vs_baseline"),
        "verdict_vs_active": entry.get("verdict_vs_active"),
        "activated": entry.get("activated"),
        "exam": bool(entry.get("exam")),
    }


def world_state(workspace: Path | str, *, journal_limit: int = 30) -> dict[str, Any]:
    workspace = Path(workspace)
    settings = read_settings(workspace)
    base: dict[str, Any] = {
        **settings.as_dict(),
        "settings_file": ".navin/" + SETTINGS_NAME,
        "world_dir": ".navin/" + WORLD_DIR_NAME,
        "beliefs_file": ".navin/" + BELIEFS_FILE_NAME,
        "classes": list(CLASSES),
        "fields": list(FIELDS),
    }
    if not settings.enabled:
        # Off: report the flag and nothing else; no file is read or created.
        return {
            **base,
            "rows": 0,
            "journal_bytes": 0,
            "recent": [],
            "heldout": {"version": None, "rows": 0},
            "checkpoints": [],
            "active": None,
            "score": None,
            "scoreboard": [],
            "gate": {"open": False, "reasons": ["world model is off"], "checkpoint": None, "heldout_version": None, "exam_verdict": None, "ab_verdict": None},
            "ab": None,
            "ab_history": [],
            "live": {"advised": 0, "window": 0, "precision": None, "logged": 0, "logged_precision": None},
            "belief_items": [],
            "beliefs_ignored": [],
            "beliefs_markdown": None,
            "train_state": {},
            "runner_alive": runner_alive(),
            "writer_alive": writer_alive(),
            "journal": [],
        }
    try:
        journal_bytes = trajectories_path(workspace).stat().st_size
    except OSError:
        journal_bytes = 0
    active = ck.read_active(workspace)
    heldout = _heldout_payload(workspace)
    gate = advice_gate(workspace)
    return {
        **base,
        "rows": count_trajectories(workspace),
        "journal_bytes": journal_bytes,
        "recent": recent_trajectories(workspace, limit=12),
        "heldout": heldout,
        "checkpoints": ck.checkpoint_summaries(workspace),
        "active": active,
        "score": _score_payload(latest_score(workspace, heldout_version=heldout.get("version"))),
        "scoreboard": [_score_payload(e) for e in read_scoreboard(workspace, limit=12)],
        "gate": gate.as_dict(),
        "ab": latest_ab(workspace, checkpoint=gate.checkpoint, heldout_version=gate.heldout_version),
        "ab_history": read_ab(workspace, limit=8),
        "live": live_summary(workspace),
        "belief_items": [b.as_dict() for b in current_beliefs(workspace)],
        "beliefs_ignored": sorted(read_ignored(workspace)),
        "beliefs_markdown": read_beliefs_file(workspace),
        "train_state": read_train_state(workspace),
        "runner_alive": runner_alive(),
        "writer_alive": writer_alive(),
        "journal": read_journal(workspace, limit=journal_limit),
    }


def world_update(workspace: Path | str, fields: dict[str, Any]) -> dict[str, Any]:
    """Write switches. ``advise: true`` is refused (409) while the gate is closed."""
    workspace = Path(workspace)
    try:
        validate_fields(fields)
    except ValueError as exc:
        raise WorldActionError(str(exc)) from exc
    if fields.get("advise") is True:
        current = read_settings(workspace)
        enabled = fields.get("enabled", current.enabled)
        if not enabled:
            raise WorldActionError("turn the world model on before advise", 409)
        gate = advice_gate(workspace)
        if not gate.open:
            raise WorldActionError("advice gate closed: " + "; ".join(gate.reasons), 409)
    try:
        settings = update_settings(workspace, fields)
    except ValueError as exc:
        raise WorldActionError(str(exc)) from exc
    if "advise" in fields or "enabled" in fields:
        from navin.world_model.journal import journal
        from navin.world_model.registration import remember_project

        wants = settings.feature("advise")
        remember_project(workspace, wants)
        if "advise" in fields:
            journal(workspace, "advise_on" if fields["advise"] else "advise_off", actor=HUMAN)
    return world_state(workspace)


def world_action(
    workspace: Path | str,
    action: str,
    *,
    actor: str = "auto",
    key: str | None = None,
    budget: TrainBudget | None = None,
) -> dict[str, Any]:
    """Press one button. Returns ``{"ok", "action", "result", "state"}``.

    Raises ``WorldActionError`` with an HTTP-ish status: 400 for a bad
    request, 403 when a human is required, 409 when the flag is off or the
    state forbids the transition.
    """
    workspace = Path(workspace)
    if action not in ACTIONS:
        raise WorldActionError(f"unknown action: {action}")
    settings = read_settings(workspace)
    if not settings.enabled:
        raise WorldActionError("world model is off for this project", 409)
    human_only = {"train", "freeze", "rollback", "discard_belief", "restore_beliefs"}
    if action in human_only and actor != HUMAN:
        raise WorldActionError(f"{action} needs a human", 403)
    result: Any
    if action == "train":
        result = train(workspace, actor=HUMAN, budget=budget, force=True).as_dict()
    elif action == "run":
        result = run_due(workspace) or {"status": "not_due"}
    elif action == "exam":
        result = exam(workspace, actor=actor)
    elif action == "freeze":
        result = freeze(workspace, actor=HUMAN)
    elif action == "rollback":
        result = rollback(workspace, actor=HUMAN)
    elif action == "ab":
        result = run_ab(workspace, actor=actor)
    elif action == "beliefs":
        if ck.active_checkpoint(workspace) is None:
            raise WorldActionError("no active checkpoint: train first", 409)
        result = {"file": str(render_beliefs(workspace)), "beliefs": [b.as_dict() for b in current_beliefs(workspace)]}
    elif action == "discard_belief":
        if not key:
            raise WorldActionError("a belief key is required")
        result = {"beliefs": [b.as_dict() for b in discard_belief(workspace, key)]}
    else:
        result = {"beliefs": [b.as_dict() for b in restore_beliefs(workspace)]}
    if result.get("status") == "tampered":
        raise WorldActionError(str(result.get("reason")), 409)
    return {"ok": True, "action": action, "result": result, "state": world_state(workspace)}
