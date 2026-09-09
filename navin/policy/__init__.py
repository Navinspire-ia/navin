# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Policy learning (S4): Navin learns which action to take, not just what to expect.

S1 remembers. S2 lets a skill in only when the exam score goes up. S3 learns
"if I call this tool like that, I will see this". S4 learns "so what do I
call?": a small local policy head trained offline on trajectories of
**eval** runs (state, action, observation, eval reward), judged by how often
it names the next action of the successful runs on a frozen held-out set,
and allowed to speak in a chat turn only once adapter N+1 beat adapter N
with no suite down and an offline A/B proved a gain.

Three corridors, all behind ``.navin/policy.json`` (off by default, and
refused while the world model radar S3.3 is not ``up``):

* **log** (S4.1): one compact, secret-free line per step of an eval
  episode, written by the training process after the run. A chat turn never
  produces a trajectory and never carries a reward;
* **train** (S4.2, S4.3): a job in a child process runs the frozen battery
  in sandboxes, fits the head, scores it on the frozen set against the
  baselines and against N, activates N+1 only if it won; rollback is one
  write;
* **steer** (S4.4): a short advisory block and the ``policy_next`` tool,
  gated by the exam and the A/B, cut automatically when the live precision
  drops. The head proposes; it never executes.

Nothing here is imported by the agent loop or the context builder: the only
entry points are the turn hook factory (``create_policy_hook``), the tool
the loader discovers, the HTTP routes and ``navin agi policy``.
"""

from navin.policy.hook import PolicyHook, create_policy_hook
from navin.policy.radar import radar, radar_up
from navin.policy.settings import (
    SETTINGS_NAME,
    PolicySettings,
    policy_enabled,
    read_settings,
    settings_path,
    update_settings,
    write_settings,
)
from navin.policy.state import (
    ACTIONS,
    PolicyActionError,
    policy_action,
    policy_state,
    policy_update,
)
from navin.policy.steerer import run_ab, steer_gate, steer_open

__all__ = [
    "ACTIONS",
    "SETTINGS_NAME",
    "PolicyActionError",
    "PolicyHook",
    "PolicySettings",
    "create_policy_hook",
    "policy_action",
    "policy_enabled",
    "policy_state",
    "policy_update",
    "radar",
    "radar_up",
    "read_settings",
    "run_ab",
    "settings_path",
    "steer_gate",
    "steer_open",
    "update_settings",
    "write_settings",
]
