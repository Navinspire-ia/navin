# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Where policy learning keeps its files.

Everything lives under ``<project>/.navin/policy/``::

    .navin/policy/trajectories.jsonl   one line per eval step (S4.1)
    .navin/policy/cases.jsonl          optional project eval cases (same schema as the battery)
    .navin/policy/heldout.jsonl        the frozen exam set: header + never-trained steps (S4.3)
    .navin/policy/checkpoints/adapter-0001.json   versioned local heads (S4.2)
    .navin/policy/active.json          which adapter serves (N), and the previous one
    .navin/policy/scoreboard.jsonl     one line per exam: reference -> N+1, verdict, per suite
    .navin/policy/ab.jsonl             offline A/B runs of the steerer (S4.4)
    .navin/policy/live.jsonl           live steer outcomes (kill switch input)
    .navin/policy/train-state.json     turns and rows seen at the last training run
    .navin/policy/journal.jsonl        trained / activated / rejected / rollback / steer

Published adapters (a human click, S4.5) live outside the project, in
``~/.navin/policy/published/<name>.json``.

With the flag off none of these exists: no code path here creates a folder
without a settings check upstream.
"""

from __future__ import annotations

from pathlib import Path

from navin.workspace_layout import navin_dir

POLICY_DIR_NAME = "policy"
TRAJECTORIES_NAME = "trajectories.jsonl"
CASES_NAME = "cases.jsonl"
HELDOUT_NAME = "heldout.jsonl"
CHECKPOINTS_DIR_NAME = "checkpoints"
CHECKPOINT_PREFIX = "adapter-"
ACTIVE_NAME = "active.json"
SCOREBOARD_NAME = "scoreboard.jsonl"
AB_NAME = "ab.jsonl"
LIVE_NAME = "live.jsonl"
TRAIN_STATE_NAME = "train-state.json"
JOURNAL_NAME = "journal.jsonl"
PUBLISHED_DIR_NAME = "published"


def policy_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / POLICY_DIR_NAME


def trajectories_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / TRAJECTORIES_NAME


def cases_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / CASES_NAME


def heldout_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / HELDOUT_NAME


def checkpoints_dir(workspace: Path | str) -> Path:
    return policy_dir(workspace) / CHECKPOINTS_DIR_NAME


def checkpoint_file(workspace: Path | str, number: int) -> Path:
    return checkpoints_dir(workspace) / f"{CHECKPOINT_PREFIX}{number:04d}.json"


def checkpoint_number(path: Path) -> int | None:
    """``adapter-0007.json`` -> 7; None for any other file."""
    name = path.name
    if not name.startswith(CHECKPOINT_PREFIX) or not name.endswith(".json"):
        return None
    digits = name[len(CHECKPOINT_PREFIX) : -len(".json")]
    return int(digits) if digits.isdigit() else None


def active_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / ACTIVE_NAME


def scoreboard_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / SCOREBOARD_NAME


def ab_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / AB_NAME


def live_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / LIVE_NAME


def train_state_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / TRAIN_STATE_NAME


def journal_path(workspace: Path | str) -> Path:
    return policy_dir(workspace) / JOURNAL_NAME


def published_dir() -> Path:
    """Adapters a human published for every project on this machine."""
    return navin_dir(Path.home()) / POLICY_DIR_NAME / PUBLISHED_DIR_NAME
