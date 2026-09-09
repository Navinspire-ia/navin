# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-project ``.navin/`` pack - continuity hub (like ``.cursor`` / ``.claude``).

The ``.navin/`` folder holds everything Navin manages for the project: the
durable brain files (``SOUL.md``, ``USER.md``, ``memory/MEMORY.md``) and the
ops / continuity layer that travels with the repository: board, agents,
rules, resume, decisions. The project root stays clean.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

NAVIN_DIR_NAME = ".navin"
PROJECT_JSON_SCHEMA = 1

_README = """# .navin - project continuity pack

This folder is Navin's project hub (same role as `.cursor` / `.claude` for
other tools). It stays with the repository so month-long work keeps its
board, agents, rules and resume briefs.

## Layout

- `board/` - tasks, milestones, activity, mission ledger
- `agents/` - project subagent definitions (`.md` with frontmatter)
- `rules/` - human notes for review / architecture rules
- `review-rules.json` - machine rules for `/inspect` and code review
- `continuity/` - RESUME.md + DECISIONS.md for long-running projects
- `resources/` - generated local assets (gitignored)
- `project.json` - pack manifest

## Shared brain (inside this folder)

Code, Documents, Marketing, SEO and every other studio share one brain:

- `SOUL.md` - voice / behaviour
- `USER.md` - stable user preferences
- `memory/MEMORY.md` - project facts, decisions, constraints
- `HEARTBEAT.md` - periodic background task list

Do not create a separate MEMORY per studio.
"""

_AGENTS_README = """# Project agents

Drop agent definitions here (same format as `.claude/agents`):

```md
---
name: reviewer
description: Reviews diffs for security and style
---
You are a meticulous reviewer...
```

Navin discovers `.navin/agents` automatically for `spawn(agent=...)`.
"""

_RULES_README = """# Project rules

Human-readable architecture and review notes for this project.

Machine-readable path rules live in `../review-rules.json` and are used by
review / inspect flows.
"""

_RESUME = """# Resume brief

Update this file when you leave a long-running project (or let the agent
do it). Next session starts here.

## Where we left off

(State, open risks, next concrete action)

## Hot paths

(Files / modules currently in play)

## Do not break

(Constraints mirrored from memory/MEMORY.md ## Constraints)
"""

_DECISIONS = """# Decisions log

Durable product and architecture decisions for this project.

## Template

### YYYY-MM-DD - Title

- Status: active | superseded
- Why:
- Alternatives considered:
- Impact:
"""

_REVIEW_RULES: dict[str, Any] = {
    "include": [],
    "exclude": [
        "node_modules/**",
        ".git/**",
        "dist/**",
        "build/**",
        ".next/**",
        ".navin/resources/**",
        ".navin/tool-results/**",
        ".navin/checkpoints/**",
        ".checkpoints/**",
    ],
    "rules": [],
}

_GITIGNORE = """# Local / generated - keep board, agents, rules, continuity in git
resources/
tool-results/
checkpoints/
memory/history.jsonl
memory/.log_navin
memory/.dream_cursor
memory/HISTORY.md.bak*
**/*.tmp
**/*~
"""


def navin_dir(project_path: Path | str) -> Path:
    return Path(project_path).expanduser() / NAVIN_DIR_NAME


def _write_new(path: Path, content: str, added: list[str], root: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        added.append(str(path.relative_to(root)))
    except ValueError:
        added.append(str(path))


def _write_json_new(path: Path, payload: dict[str, Any], added: list[str], root: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        added.append(str(path.relative_to(root)))
    except ValueError:
        added.append(str(path))


def ensure_navin_project_pack(project_path: Path | str) -> list[str]:
    """Create the full ``.navin/`` continuity pack (idempotent, never overwrites)."""
    root = Path(project_path).expanduser()
    try:
        root = root.resolve(strict=False)
    except OSError:
        return []
    if not root.is_dir():
        return []

    added: list[str] = []
    base = root / NAVIN_DIR_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)

        _write_new(base / "README.md", _README, added, root)
        _write_new(base / ".gitignore", _GITIGNORE, added, root)

        project_json = {
            "schema_version": PROJECT_JSON_SCHEMA,
            "kind": "navin-project",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "continuity": {
                "brain": [
                    ".navin/SOUL.md",
                    ".navin/USER.md",
                    ".navin/memory/MEMORY.md",
                ],
                "shared_across_studios": True,
                "board": ".navin/board",
                "agents": ".navin/agents",
                "rules": ".navin/review-rules.json",
                "continuity": ".navin/continuity",
                "graph": ".navin/metadata/index.json",
                "checkpoints": ".navin/checkpoints",
            },
        }
        # Only write project.json when missing so created_at stays stable.
        _write_json_new(base / "project.json", project_json, added, root)

        board = base / "board"
        board.mkdir(parents=True, exist_ok=True)
        _write_json_new(
            board / "board.json",
            {"schema_version": 1, "tasks": [], "updated_at": None},
            added,
            root,
        )
        _write_json_new(
            board / "milestones.json",
            {"schema_version": 1, "milestones": [], "updated_at": None},
            added,
            root,
        )
        _write_new(board / "activity.jsonl", "", added, root)

        agents = base / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        _write_new(agents / "README.md", _AGENTS_README, added, root)

        rules = base / "rules"
        rules.mkdir(parents=True, exist_ok=True)
        _write_new(rules / "README.md", _RULES_README, added, root)
        _write_json_new(base / "review-rules.json", _REVIEW_RULES, added, root)

        continuity = base / "continuity"
        continuity.mkdir(parents=True, exist_ok=True)
        _write_new(continuity / "RESUME.md", _RESUME, added, root)
        _write_new(continuity / "DECISIONS.md", _DECISIONS, added, root)

        resources = base / "resources"
        resources.mkdir(parents=True, exist_ok=True)
        tool_results = base / "tool-results"
        tool_results.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("failed to create .navin pack under {}", root)
        return added

    return added
