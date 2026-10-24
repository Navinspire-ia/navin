# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured registry of known errors: symptom -> cause -> fix.

Stored as JSON lines in ``.navin/memory/errors.jsonl`` inside the project.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_REL = Path(".navin") / "memory" / "errors.jsonl"
_WORD = re.compile(r"[a-z0-9_]{3,}")


def registry_path(project: str | Path) -> Path:
    return Path(project) / REGISTRY_REL


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def load_errors(project: str | Path) -> list[dict[str, str]]:
    path = registry_path(project)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, str]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("symptom"):
            out.append({k: str(row.get(k, "")) for k in ("symptom", "cause", "fix")})
    return out


def record_error(project: str | Path, symptom: str, cause: str, fix: str) -> bool:
    """Append an entry; returns False when the symptom is already known."""
    symptom = (symptom or "").strip()
    if not symptom:
        return False
    if any(e["symptom"].lower() == symptom.lower() for e in load_errors(project)):
        return False
    path = registry_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"symptom": symptom, "cause": (cause or "").strip(), "fix": (fix or "").strip()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


def find_errors(project: str | Path, query: str, limit: int = 3) -> list[dict[str, str]]:
    """Entries whose symptom shares keywords with ``query``, best first."""
    wanted = _tokens(query)
    if not wanted:
        return []
    scored: list[tuple[int, dict[str, str]]] = []
    for entry in load_errors(project):
        score = len(wanted & _tokens(entry["symptom"]))
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: -item[0])
    return [e for _s, e in scored[: max(1, limit)]]


def format_errors(entries: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- symptom: {e['symptom']} | cause: {e['cause']} | fix: {e['fix']}" for e in entries
    )


CHECK_TOOLS = frozenset({"exec", "test_run", "verify", "lint"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch"})
_EXIT_CODE = re.compile(r"Exit code: (-?\d+)")
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_ERROR_HINT = re.compile(r"error|fail|exception|traceback|not found|denied|invalid", re.I)


def _command_key(tool: str, params: dict[str, Any]) -> str:
    parts = [tool]
    for key in ("command", "cmd", "action", "target", "targets", "path", "paths", "test_target"):
        value = params.get(key)
        if value:
            parts.append(f"{key}={json.dumps(value, sort_keys=True, default=str)}")
    return " ".join(" ".join(parts).split())


def _is_failure(result: Any) -> bool:
    if getattr(result, "is_error", False):
        return True
    codes = _EXIT_CODE.findall(str(result or ""))
    return bool(codes) and codes[-1] != "0"


def first_error_line(text: str) -> str:
    """First line that looks like an error, else the first non empty line."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    for line in lines:
        if _ERROR_HINT.search(line) and not line.startswith("["):
            return line[:300]
    return lines[0][:300] if lines else ""


def _written_paths(tool: str, params: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if path := params.get("path"):
        paths.append(str(path))
    for item in params.get("files") or params.get("edits") or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item["path"]))
    if tool == "apply_patch":
        for key in ("patch", "input"):
            if isinstance(params.get(key), str):
                paths.extend(m.strip() for m in _PATCH_PATH.findall(params[key]))
    return paths


class FailureTracker:
    """Remembers failing check commands per project and records the fix on recovery.

    Bounded: at most ``max_tracked`` pending failures across all projects.
    """

    def __init__(self, max_tracked: int = 64) -> None:
        self.max_tracked = max_tracked
        self._pending: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

    def observe(self, project: str | Path | None, tool: str, params: Any, result: Any) -> Any:
        """Update state after a tool call; returns ``result``, possibly with a recall note."""
        if not project or not isinstance(params, dict):
            return result
        try:
            return self._observe(str(project), tool, params, result)
        except Exception:
            logger.exception("error registry: failed to observe tool %s", tool)
            return result

    def _observe(self, project: str, tool: str, params: dict[str, Any], result: Any) -> Any:
        if tool in WRITE_TOOLS:
            if not getattr(result, "is_error", False):
                files = _written_paths(tool, params)
                for (proj, _key), entry in self._pending.items():
                    if proj == project:
                        for f in files:
                            if f not in entry["files"]:
                                entry["files"].append(f)
            return result
        if tool not in CHECK_TOOLS:
            return result
        key = (project, _command_key(tool, params))
        if _is_failure(result):
            symptom = first_error_line(str(result))
            if not symptom:
                return result
            self._pending[key] = {"symptom": symptom, "files": [], "cause": key[1]}
            self._pending.move_to_end(key)
            while len(self._pending) > self.max_tracked:
                self._pending.popitem(last=False)
            return _with_recall_note(project, symptom, result)
        entry = self._pending.pop(key, None)
        if entry is not None:
            fix = "modified: " + ", ".join(entry["files"]) if entry["files"] else "rerun passed"
            try:
                record_error(project, entry["symptom"], f"failing check: {entry['cause']}", fix)
            except Exception:
                logger.warning("error registry: record_error failed for %s", project, exc_info=True)
        return result


def _with_recall_note(project: str, symptom: str, result: Any) -> Any:
    try:
        hits = find_errors(project, symptom, limit=2)
    except Exception:
        logger.warning("error registry: find_errors failed for %s", project, exc_info=True)
        return result
    if not hits:
        return result
    note = "\n\n[Erreur deja rencontree, correctif applique:\n" + format_errors(hits) + "]"
    from navin.agent.tools.base import ToolResult

    if isinstance(result, ToolResult):
        return ToolResult(
            str(result) + note, is_error=result.is_error, recovery_hint=result.recovery_hint,
            verification=result.verification, made_progress=result.made_progress,
        )
    return str(result) + note
