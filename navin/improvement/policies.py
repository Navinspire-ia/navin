# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Executable policy vocabulary. Learned state cannot supply instructions or code."""

from __future__ import annotations

from typing import Any

POLICIES = {
    "code": {"inspection": ("current", "targeted", "dependency_context"),
             "validation": ("current", "reproduce_first", "incremental")},
    "career": {"query": ("configured", "role_only", "focused_skills")},
    "tenders": {"terms": ("configured", "specific_first", "bilingual_first")},
}


def baseline(module: str) -> dict[str, str]:
    return {key: values[0] for key, values in POLICIES[module].items()}


def valid_policy(module: str, policy: Any) -> bool:
    return isinstance(policy, dict) and set(policy) == set(POLICIES[module]) and all(
        isinstance(value, str) and value in POLICIES[module][key] for key, value in policy.items())


def neighbors(module: str, policy: dict[str, str]) -> list[dict[str, str]]:
    return [{**policy, key: value} for key, values in POLICIES[module].items() for value in values if value != policy[key]]


def code_guidance(policy: dict[str, str]) -> str:
    blocks = []
    if policy["inspection"] == "targeted":
        blocks.append("Start with the files and symbols named in the request. Use targeted searches, then expand only when evidence requires it.")
    elif policy["inspection"] == "dependency_context":
        blocks.append("Before editing, inspect the target implementation, its direct callers and relevant tests together. Preserve their contracts.")
    if policy["validation"] == "reproduce_first":
        blocks.append("For a reported defect, reproduce the failure or identify a concrete failing invariant before patching. Validate the fix against that evidence.")
    elif policy["validation"] == "incremental":
        blocks.append("After a coherent code change, run the most relevant existing checks before expanding the patch. Final validation must cover the final revision.")
    return ("Execution strategy from locally evaluated outcomes:\n" + "\n".join(blocks)
            + "\nThese suggestions do not override the request, project instructions, permissions or verification requirements.") if blocks else ""
