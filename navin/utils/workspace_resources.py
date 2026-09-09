# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Helpers for Navin-managed resources materialized in user workspaces."""

from __future__ import annotations

from pathlib import Path

WORKSPACE_NAVIN_DIR = Path(".navin")
WORKSPACE_RESOURCES_DIR = WORKSPACE_NAVIN_DIR / "resources"
# Only generated caches - board / agents / rules / continuity must stay visible to Git.
_GIT_EXCLUDE_ENTRIES = (
    "/.navin/resources/",
    "/.navin/tool-results/",
)


def prepare_workspace_resources(workspace: Path) -> Path | None:
    """Create the managed resource root and keep caches out of local Git status."""
    root = workspace.expanduser().resolve(strict=False)
    if not root.is_dir():
        return None
    resources = root / WORKSPACE_RESOURCES_DIR
    try:
        resources.mkdir(parents=True, exist_ok=True)
        (root / WORKSPACE_NAVIN_DIR / "tool-results").mkdir(parents=True, exist_ok=True)
        git_exclude = root / ".git" / "info" / "exclude"
        if git_exclude.parent.is_dir():
            existing = git_exclude.read_text(encoding="utf-8") if git_exclude.is_file() else ""
            lines = existing.splitlines()
            missing = [entry for entry in _GIT_EXCLUDE_ENTRIES if entry not in lines]
            if missing:
                prefix = "" if not existing or existing.endswith("\n") else "\n"
                block = "\n".join(
                    ["# Navin generated caches (board/agents/rules stay tracked)", *missing]
                )
                git_exclude.write_text(f"{existing}{prefix}{block}\n", encoding="utf-8")
    except OSError:
        return None
    return resources
