# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep the gateway's ``policy_next`` tool in step with the projects' gates.

Same bridge as ``navin.world_model.registration`` for ``world_predict``:
tools are registered once at boot for one workspace, a gateway serves many
projects, and the AGI switch flips while it runs. Only the registry's public
``register`` / ``unregister`` are used; the agent loop does not change.

The tool is wanted by a project when ``steer`` is on **and** every gate is
open (adapter eligible on the current exam set, offline A/B ``gain``). A
project that turned ``steer`` on but lost the gate later (kill switch, new
exam set) drops the tool at the next sync, and the prompt goes back to what
it was.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from loguru import logger

POLICY_TOOL_NAME = "policy_next"
INDEX_NAME = "policy-projects.json"
_MAX_INDEX_ENTRIES = 200
_INDEX_LOCK = threading.Lock()


def index_path() -> Path | None:
    try:
        from navin.config.paths import get_data_dir

        return get_data_dir() / INDEX_NAME
    except Exception:
        return None


def read_index() -> list[Path]:
    path = index_path()
    if path is None:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    items = raw.get("projects") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    return [Path(item).expanduser() for item in items[:_MAX_INDEX_ENTRIES] if isinstance(item, str) and item.strip()]


def remember_project(workspace: Path | str, wanted: bool) -> None:
    """Add or drop one project in the machine index. Best effort, never raises."""
    path = index_path()
    if path is None:
        return
    key = str(Path(workspace).expanduser().resolve())
    try:
        with _INDEX_LOCK:
            current = [str(p) for p in read_index()]
            if wanted:
                if key in current:
                    return
                current = [key, *current][:_MAX_INDEX_ENTRIES]
            else:
                if key not in current:
                    return
                current = [p for p in current if p != key]
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".policy-projects-", suffix=".json", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"schema_version": 1, "projects": current}, handle, indent=2)
                    handle.write("\n")
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    except Exception as exc:
        logger.debug("policy project index not updated: {}", exc)


def projects_wanting_steer(workspace: Path | str | None, extra: Iterable[Path | str] = ()) -> list[Path]:
    from navin.policy.steerer import steer_open

    seen: set[str] = set()
    wanting: list[Path] = []
    candidates: list[Path] = []
    if workspace is not None:
        candidates.append(Path(workspace).expanduser())
    candidates.extend(Path(p).expanduser() for p in extra)
    candidates.extend(read_index())
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if steer_open(path):
            wanting.append(path)
    return wanting


def policy_tool_registered(registry: Any) -> bool:
    try:
        return registry.get(POLICY_TOOL_NAME) is not None
    except Exception:
        return False


def sync_policy_tool(registry: Any, *, workspace: Path | str | None, extra: Iterable[Path | str] = ()) -> bool:
    """Register or drop ``policy_next`` so the registry matches the gates.

    Returns whether the tool is registered afterwards. Never raises.
    """
    try:
        wanted = bool(projects_wanting_steer(workspace, extra))
        present = policy_tool_registered(registry)
        if wanted and not present:
            from navin.agent.tools.policy_next import PolicyNextTool

            registry.register(PolicyNextTool(workspace=workspace or Path.cwd()))
            logger.info("policy_next tool registered: a project opened the steer gate")
            return True
        if present and not wanted:
            registry.unregister(POLICY_TOOL_NAME)
            logger.info("policy_next tool unregistered: no project asks for steering")
            return False
        return present
    except Exception as exc:
        logger.warning("policy_next tool sync skipped: {}", exc)
        return policy_tool_registered(registry)
