"""Where the world model keeps its files.

Everything lives under ``<project>/.navin/world/`` except the readable
summary, which is a brain file next to the others::

    .navin/world/trajectories.jsonl   one line per tool call (S3.1)
    .navin/world/salt                 per-project salt for the argument hashes
    .navin/world/heldout.jsonl        the frozen exam set, header + rows (S3.3)
    .navin/world/checkpoints/ckpt-0001.json   versioned local heads (S3.2)
    .navin/world/active.json          which checkpoint serves, and the previous one
    .navin/world/scoreboard.jsonl     one line per exam: baseline -> N, verdict
    .navin/world/ab.jsonl             offline A/B runs of the advisor (S3.5)
    .navin/world/live.jsonl           live advice outcomes (kill switch input)
    .navin/world/train-state.json     rows seen at the last training run
    .navin/world/journal.jsonl        trained / activated / rejected / rollback / advise
    .navin/world/beliefs-ignored.json belief keys a human threw away
    .navin/BELIEFS.md                 the 10-line readable summary (S3.4)

With the flag off none of these exists: no code path here creates a folder
without a settings check upstream.
"""

from __future__ import annotations

from pathlib import Path

from navin.workspace_layout import navin_dir

WORLD_DIR_NAME = "world"
TRAJECTORIES_NAME = "trajectories.jsonl"
SALT_NAME = "salt"
HELDOUT_NAME = "heldout.jsonl"
CHECKPOINTS_DIR_NAME = "checkpoints"
CHECKPOINT_PREFIX = "ckpt-"
ACTIVE_NAME = "active.json"
SCOREBOARD_NAME = "scoreboard.jsonl"
AB_NAME = "ab.jsonl"
LIVE_NAME = "live.jsonl"
TRAIN_STATE_NAME = "train-state.json"
JOURNAL_NAME = "journal.jsonl"
BELIEFS_IGNORED_NAME = "beliefs-ignored.json"
BELIEFS_FILE_NAME = "BELIEFS.md"


def world_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / WORLD_DIR_NAME


def trajectories_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / TRAJECTORIES_NAME


def salt_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / SALT_NAME


def heldout_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / HELDOUT_NAME


def checkpoints_dir(workspace: Path | str) -> Path:
    return world_dir(workspace) / CHECKPOINTS_DIR_NAME


def checkpoint_file(workspace: Path | str, number: int) -> Path:
    return checkpoints_dir(workspace) / f"{CHECKPOINT_PREFIX}{number:04d}.json"


def checkpoint_number(path: Path) -> int | None:
    """``ckpt-0007.json`` -> 7; None for any other file."""
    name = path.name
    if not name.startswith(CHECKPOINT_PREFIX) or not name.endswith(".json"):
        return None
    digits = name[len(CHECKPOINT_PREFIX) : -len(".json")]
    return int(digits) if digits.isdigit() else None


def active_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / ACTIVE_NAME


def scoreboard_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / SCOREBOARD_NAME


def ab_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / AB_NAME


def live_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / LIVE_NAME


def train_state_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / TRAIN_STATE_NAME


def journal_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / JOURNAL_NAME


def beliefs_ignored_path(workspace: Path | str) -> Path:
    return world_dir(workspace) / BELIEFS_IGNORED_NAME


def beliefs_file(workspace: Path | str) -> Path:
    return navin_dir(workspace) / BELIEFS_FILE_NAME
