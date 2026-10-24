# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn the ``## Constraints`` section of MEMORY.md into enforced rules.

Supported bullet forms (case-insensitive)::

    - never write path: secrets/*
    - forbid command: rm -rf
    - interdit chemin: build/*
    - interdit commande: git push --force
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RULE = re.compile(
    r"^[-*]\s*(?:never|forbid|forbidden|interdit|interdire)\s+"
    r"(?:(?:write|edit|touch|run|ecrire|modifier|lancer)\s+)?"
    r"(?P<kind>path|chemin|command|commande)\s*:\s*`?(?P<value>.+?)`?\s*$",
    re.IGNORECASE,
)
_WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "notebook_edit"}
_EXEC_TOOLS = {"exec"}


@dataclass(frozen=True)
class Rule:
    kind: str  # "path" or "command"
    value: str


def parse_constraints(text: str) -> list[Rule]:
    rules: list[Rule] = []
    inside = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            inside = line.lstrip("#").strip().lower() == "constraints"
            continue
        if not inside:
            continue
        match = _RULE.match(line)
        if match:
            kind = "path" if match["kind"].lower() in ("path", "chemin") else "command"
            rules.append(Rule(kind, match["value"].strip()))
    return rules


def load_rules(project: str | Path) -> list[Rule]:
    for rel in (Path("MEMORY.md"), Path(".navin") / "MEMORY.md", Path("memory") / "MEMORY.md"):
        try:
            text = (Path(project) / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        rules = parse_constraints(text)
        if rules:
            return rules
    return []


def _paths_of(tool: str, params: dict[str, Any]) -> list[str]:
    paths = [str(params[k]) for k in ("path", "file_path") if params.get(k)]
    for edit in params.get("edits") or []:
        if isinstance(edit, dict) and edit.get("path"):
            paths.append(str(edit["path"]))
    return paths


def check_call(project: str | Path, tool: str, params: Any) -> str | None:
    """Return a refusal message if the call violates a constraint, else None."""
    if not isinstance(params, dict) or (tool not in _WRITE_TOOLS and tool not in _EXEC_TOOLS):
        return None
    rules = load_rules(project)
    if not rules:
        return None
    if tool in _EXEC_TOOLS:
        command = str(params.get("command") or params.get("cmd") or "")
        for rule in rules:
            if rule.kind == "command" and rule.value.lower() in command.lower():
                return f"Blocked by MEMORY.md constraint: command '{rule.value}' is forbidden."
        return None
    for path in _paths_of(tool, params):
        norm = path.replace("\\", "/").lstrip("./")
        for rule in rules:
            pattern = rule.value.replace("\\", "/").lstrip("./")
            if rule.kind == "path" and (
                fnmatch.fnmatch(norm, pattern) or norm.startswith(pattern.rstrip("*"))
            ):
                return f"Blocked by MEMORY.md constraint: path '{rule.value}' is forbidden."
    return None
