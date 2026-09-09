# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The ladder: what each switch of the AGI panel waits for, in plain words.

Four stages earn each other. Skills evolution and the world model can be
turned on whenever; the policy switch is locked until the world model has
passed its exam; the transfer protocol is locked until a skill draft passed
its exam, the world model exam is up and a policy adapter beat its
predecessor. A locked switch is not broken: it becomes clickable by itself
once the stage below has its proof. Nothing to edit, no file to touch.

This module reads facts already on disk (settings, radar, prerequisites) and
never writes. The CLI ``navin agi status`` and the ``/transfer`` route show
the same rungs; the panel renders them at the top of the AGI entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGES: tuple[str, ...] = ("skills", "world", "policy", "transfer")

LABELS: dict[str, str] = {
    "skills": "Skills evolution",
    "world": "World model",
    "policy": "Policy",
    "transfer": "Transfer protocol",
}

#: What unlocks the next rung, one sentence each (empty when nothing is needed).
WAITS: dict[str, str] = {
    "skills": "",
    "world": "",
    "policy": "waits for the world model to pass its exam",
    "transfer": "waits for a skill draft that passed its exam, the world model exam up, and a policy adapter that beat its predecessor",
}


@dataclass(frozen=True, slots=True)
class Rung:
    id: str
    state: str  # off | on | locked
    reasons: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return LABELS[self.id]

    @property
    def waits(self) -> str:
        return WAITS[self.id] if self.state == "locked" else ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "state": self.state, "waits": self.waits, "reasons": list(self.reasons)}


def _state(enabled: bool, locked: bool) -> str:
    if enabled:
        return "on"
    return "locked" if locked else "off"


def ladder(workspace: Path | str) -> list[Rung]:
    """The four rungs for one project. Every import is lazy: a missing stage is
    a rung that reads ``off``, never a crash."""
    workspace = Path(workspace)
    rungs: list[Rung] = []

    try:
        from navin.skills_evolve.settings import read_settings as read_skills

        skills_on = bool(read_skills(workspace).enabled)
    except Exception:  # noqa: BLE001
        skills_on = False
    rungs.append(Rung("skills", _state(skills_on, False)))

    try:
        from navin.world_model.settings import read_settings as read_world

        world_on = bool(read_world(workspace).enabled)
    except Exception:  # noqa: BLE001
        world_on = False
    rungs.append(Rung("world", _state(world_on, False)))

    policy_on = False
    policy_reasons: tuple[str, ...] = ()
    try:
        from navin.policy.radar import radar
        from navin.policy.settings import read_settings as read_policy

        policy_on = bool(read_policy(workspace).enabled)
        if not policy_on:
            signal = radar(workspace)
            policy_reasons = () if signal.up else tuple(signal.reasons)
    except Exception as exc:  # noqa: BLE001
        policy_reasons = (f"policy unavailable: {exc}",)
    rungs.append(Rung("policy", _state(policy_on, bool(policy_reasons)), policy_reasons))

    transfer_on = False
    transfer_reasons: tuple[str, ...] = ()
    try:
        from navin.transfer.prereqs import prereqs
        from navin.transfer.settings import read_settings as read_transfer

        transfer_on = bool(read_transfer(workspace).enabled)
        if not transfer_on:
            gates = prereqs(workspace)
            transfer_reasons = () if gates.ok else tuple(gates.reasons)
    except Exception as exc:  # noqa: BLE001
        transfer_reasons = (f"transfer unavailable: {exc}",)
    rungs.append(Rung("transfer", _state(transfer_on, bool(transfer_reasons)), transfer_reasons))
    return rungs


def ladder_dicts(workspace: Path | str) -> list[dict[str, Any]]:
    return [r.as_dict() for r in ladder(workspace)]
