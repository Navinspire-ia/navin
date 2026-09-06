"""Keep the gateway's ``recall`` tool in step with the projects that want it.

Tools are registered once when an agent boots, for one workspace. A gateway
serves many projects, each with its own ``.navin/cognition.json``, and the
Guardrails switch is flipped while it runs. This module bridges the two with
the registry's public ``register`` / ``unregister`` only: nothing in the
agent loop changes, and a gateway that never calls it behaves exactly as
before (the tool follows the boot workspace's flag).

Which projects count: the boot workspace plus a small machine-level index,
``<data dir>/cognition-projects.json``, that ``update_settings`` maintains
(a path is added when a project opts in, dropped when it opts out). Every
entry is re-checked against the project's own flag file, so a stale index
can only cost a few ``stat`` calls, never a wrong tool set.
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

from navin.cognition.settings import cognition_enabled

RECALL_TOOL_NAME = "recall"
INDEX_NAME = "cognition-projects.json"
_MAX_INDEX_ENTRIES = 200
_INDEX_LOCK = threading.Lock()


def index_path() -> Path | None:
    """Machine-level list of projects that opted in; None without a data dir."""
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
    out: list[Path] = []
    for item in items[:_MAX_INDEX_ENTRIES]:
        if isinstance(item, str) and item.strip():
            out.append(Path(item).expanduser())
    return out


def remember_project(workspace: Path | str, wanted: bool) -> None:
    """Add or drop one project in the index. Best effort, never raises."""
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
            fd, tmp = tempfile.mkstemp(prefix=".cognition-projects-", suffix=".json", dir=str(path.parent))
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
        logger.debug("cognition project index not updated: {}", exc)


def projects_wanting_recall(
    workspace: Path | str | None,
    extra: Iterable[Path | str] = (),
) -> list[Path]:
    """Every known project whose flag asks for the recall tool."""
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
        if cognition_enabled(path, "recall"):
            wanting.append(path)
    return wanting


def recall_registered(registry: Any) -> bool:
    try:
        return registry.get(RECALL_TOOL_NAME) is not None
    except Exception:
        return False


def sync_recall_tool(
    registry: Any,
    *,
    workspace: Path | str | None,
    extra: Iterable[Path | str] = (),
) -> bool:
    """Register or drop ``recall`` so the registry matches the projects' flags.

    Returns whether the tool is registered afterwards. Never raises: a
    registry that cannot be touched leaves the previous state in place.
    """
    try:
        wanted = bool(projects_wanting_recall(workspace, extra))
        present = recall_registered(registry)
        if wanted and not present:
            from navin.agent.tools.recall import RecallTool

            registry.register(RecallTool(workspace=workspace or Path.cwd()))
            logger.info("recall tool registered: a project opted in to cognition")
            return True
        if present and not wanted:
            registry.unregister(RECALL_TOOL_NAME)
            logger.info("recall tool unregistered: no known project asks for it")
            return False
        return present
    except Exception as exc:
        logger.warning("recall tool sync skipped: {}", exc)
        return recall_registered(registry)
