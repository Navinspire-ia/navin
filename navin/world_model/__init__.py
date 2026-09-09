# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""World model (S3): Navin learns to anticipate what a tool will answer.

S1 remembers. S2 lets a skill in only when the exam score goes up. S3 learns
"if I call this tool like that, I will see this": a local statistics head
trained offline on the project's own tool calls, judged by its prediction
error on a frozen held-out set, and allowed to speak in a chat turn only
once that error went down and an A/B proved a gain.

Three corridors, all behind ``.navin/world-model.json`` (off by default):

* **log** (S3.1): one compact, secret-free line per tool call, written
  after the call by a background thread;
* **train** (S3.2, S3.3): a job outside any turn fits the head, scores it on
  the frozen set against the baselines and activates the checkpoint only if
  it learned; rollback is one write;
* **advise** (S3.5): a short advisory block and the ``world_predict`` tool,
  gated by the exam and the A/B, cut automatically when the live precision
  drops.

Nothing here is imported by the agent loop or the context builder: the only
entry points are the turn hook factory (``create_world_model_hook``), the
tool the loader discovers, the HTTP routes and ``navin agi world``.
"""

from navin.world_model.advisor import advice_gate, advice_open, run_ab
from navin.world_model.hook import WorldModelHook, create_world_model_hook
from navin.world_model.settings import (
    SETTINGS_NAME,
    WorldModelSettings,
    read_settings,
    settings_path,
    update_settings,
    world_enabled,
    write_settings,
)
from navin.world_model.state import (
    ACTIONS,
    WorldActionError,
    world_action,
    world_state,
    world_update,
)
from navin.world_model.train import TrainBudget, exam, freeze, rollback, train

__all__ = [
    "ACTIONS",
    "SETTINGS_NAME",
    "TrainBudget",
    "WorldActionError",
    "WorldModelHook",
    "WorldModelSettings",
    "advice_gate",
    "advice_open",
    "create_world_model_hook",
    "exam",
    "freeze",
    "read_settings",
    "rollback",
    "run_ab",
    "settings_path",
    "train",
    "update_settings",
    "world_action",
    "world_enabled",
    "world_state",
    "world_update",
    "write_settings",
]
