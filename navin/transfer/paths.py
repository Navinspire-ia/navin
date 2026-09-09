# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Where S5 keeps its files.

Inside the project, under ``.navin/transfer/``: the campaign scoreboard, the
safety dossier and the journal. Nothing here is a secret item.

Outside the project (never in a repository): the secret suites and their
lock, by default under ``~/.navin/transfer/suites``. The campaign refuses a
suites folder that sits inside the project or inside any git checkout.
"""

from __future__ import annotations

from pathlib import Path

from navin.workspace_layout import navin_dir

TRANSFER_DIR_NAME = "transfer"
CAMPAIGNS_NAME = "campaigns.jsonl"
SAFETY_NAME = "safety.json"
JOURNAL_NAME = "journal.jsonl"
SUITES_DIR_NAME = "suites"
LOCK_NAME = "lock.json"


def transfer_dir(workspace: Path | str) -> Path:
    return navin_dir(workspace) / TRANSFER_DIR_NAME


def campaigns_path(workspace: Path | str) -> Path:
    return transfer_dir(workspace) / CAMPAIGNS_NAME


def safety_path(workspace: Path | str) -> Path:
    return transfer_dir(workspace) / SAFETY_NAME


def journal_path(workspace: Path | str) -> Path:
    return transfer_dir(workspace) / JOURNAL_NAME


def default_suites_dir() -> Path:
    """The machine folder for secret suites: outside every project."""
    return navin_dir(Path.home()) / TRANSFER_DIR_NAME / SUITES_DIR_NAME


def lock_path(suites_dir: Path | str) -> Path:
    return Path(suites_dir) / LOCK_NAME
