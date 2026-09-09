# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The S3.3 dependency: no policy without a radar (S4.0).

S4 learns which action to take from trajectories whose observation class is
what the world model (S3) predicts. A project whose world model never
learned to predict its tools has no honest ground under a policy, so S4
refuses to switch on, and the trainer refuses to run, until the world model's
active checkpoint says ``up`` on its current frozen exam set.

Everything here is a read of S3 files; nothing is created.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Radar:
    up: bool
    reasons: tuple[str, ...]
    checkpoint: int | None
    heldout_version: str | None
    verdict: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "up": self.up,
            "reasons": list(self.reasons),
            "checkpoint": self.checkpoint,
            "heldout_version": self.heldout_version,
            "verdict": self.verdict,
        }


def radar(workspace: Path | str | None) -> Radar:
    """Is the world model of this project ``up`` on its frozen exam set?"""
    if workspace is None:
        return Radar(False, ("no project",), None, None, None)
    from navin.world_model import checkpoints as ck
    from navin.world_model.dataset import HeldoutTamperedError, load_heldout
    from navin.world_model.settings import read_settings as read_world_settings
    from navin.world_model.train import latest_score

    if not read_world_settings(workspace).enabled:
        return Radar(False, ("world model is off for this project",), None, None, None)
    active = ck.active_checkpoint(workspace)
    if active is None:
        return Radar(False, ("world model has no active checkpoint: train it first",), None, None, None)
    try:
        heldout = load_heldout(workspace)
    except HeldoutTamperedError as exc:
        return Radar(False, (f"world model exam set: {exc}",), active.number, None, None)
    if heldout is None:
        return Radar(False, ("world model has no frozen exam set",), active.number, None, None)
    score = latest_score(workspace, heldout_version=heldout.version)
    if score is None or score.get("checkpoint") != active.number:
        return Radar(
            False,
            ("the active world model checkpoint has no score on its current exam set",),
            active.number,
            heldout.version,
            None,
        )
    verdict = str(score.get("verdict_vs_baseline") or "flat")
    if verdict != "up":
        return Radar(False, (f"world model exam verdict is {verdict}, not up",), active.number, heldout.version, verdict)
    return Radar(True, (), active.number, heldout.version, verdict)


def radar_up(workspace: Path | str | None) -> bool:
    return radar(workspace).up
