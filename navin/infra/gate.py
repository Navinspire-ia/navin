# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The infrastructure policy applied to ``exec``.

Without it the policy could be bypassed by typing the same command in a
shell. Only lines that call an infrastructure tool are judged; anything else
goes through untouched. The decision is the strictest of the line's
commands: ``deny`` refuses, ``ask`` puts the question to the user.
"""

from __future__ import annotations

from pathlib import Path

from navin.infra.parse import classify_command, mentions_infra
from navin.infra.policy import Decision, combine, current_contexts, decide, load_policy


def exec_decision(command: str, workspace: str | Path, cwd: str | Path | None = None) -> Decision | None:
    """The policy decision for a shell line, ``None`` when it calls no infrastructure tool."""
    if not mentions_infra(command):
        return None
    segments = classify_command(command)
    if not segments:
        return None
    policy = load_policy(workspace)
    contexts = current_contexts(cwd or workspace)
    return combine([decide(segment, policy, contexts) for segment in segments])


def approval_scope(command: str, decision: Decision) -> str:
    """Narrow "allow every time" scope: tool, verb and target, never the whole tool.

    Critical operations get no scope at all, so the user is asked every time:
    one approved production delete must not cover the next one.
    """
    if decision.risk == "critical":
        return ""
    segments = classify_command(command)
    head = max(segments, key=lambda s: ["read", "plan", "change", "critical"].index(s.risk)) if segments else None
    target = next((value for key in ("kube_context", "aws_profile", "gcp_project", "azure_subscription", "terraform_workspace", "host") if (value := decision.targets.get(key))), "")
    parts = ["infra", head.binary if head else "", (head.verb if head else "").replace(" ", "-"), target]
    return ":".join(part for part in parts if part)
