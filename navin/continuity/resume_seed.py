# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Unified Resume seed for Project Home and the Code workbench.

One payload feeds the UI composer and mirrors what the agent already sees via
continuity + board + git digests - so a return after N days does not require
re-explaining the project.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from navin.continuity.context import (
    _AUTO_HANDOFF_END,
    _AUTO_HANDOFF_START,
    _clip,
    _decision_headings,
    _extract_constraints,
    _read_text,
    _resume_is_actionable,
    _split_auto_handoff,
)
from navin.utils.git_state import repo_state, summary_lines

_MAX_TASKS = 5
_MAX_DECISIONS = 5
_MAX_CONSTRAINTS = 5
_MAX_RESUME_CHARS = 2_000
_DECISIONS_SECTION = re.compile(
    r"(?im)^(?:\*\*)?decisions(?:\*\*)?\s*:?\s*$"
)


def _open_tasks(root: Path) -> list[dict[str, str]]:
    try:
        from navin.board.store import ProjectBoardStore

        tasks = ProjectBoardStore(root).read_tasks()
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for task in tasks:
        status = str(task.get("status") or "")
        if status in {"done", "cancelled"}:
            continue
        title = str(task.get("title") or "").strip()
        if not title:
            continue
        rows.append(
            {
                "id": str(task.get("id") or ""),
                "title": title,
                "status": status,
                "branch": str(task.get("branch") or ""),
                "pr_url": str(task.get("pr_url") or ""),
                "head_sha": str(task.get("head_sha") or ""),
            }
        )
        if len(rows) >= _MAX_TASKS:
            break
    return rows


def _git_block(root: Path) -> list[str]:
    try:
        state = repo_state(root, refresh=True)
        return summary_lines(state)[:3]
    except Exception:
        return []


def _resume_brief(root: Path) -> str:
    raw = _read_text(root / ".navin" / "continuity" / "RESUME.md")
    manual, auto = _split_auto_handoff(raw)
    parts: list[str] = []
    if manual and _resume_is_actionable(manual):
        parts.append(_clip(manual, _MAX_RESUME_CHARS))
    if auto:
        parts.append(_clip(auto, _MAX_RESUME_CHARS))
    return "\n\n".join(parts).strip()


def build_resume_seed(project_path: Path | str, *, project_name: str | None = None) -> dict[str, Any]:
    """Structured Resume seed + ready-to-paste composer text."""
    root = Path(project_path).expanduser()
    try:
        root = root.resolve(strict=False)
    except OSError:
        pass
    name = (project_name or root.name or "project").strip() or "project"

    from navin import workspace_layout

    brief = _resume_brief(root)
    constraints = _extract_constraints(
        _read_text(workspace_layout.read_with_root_fallback(root, "memory/MEMORY.md"))
    )[:_MAX_CONSTRAINTS]
    decisions = _decision_headings(
        _read_text(root / ".navin" / "continuity" / "DECISIONS.md")
    )[:_MAX_DECISIONS]
    tasks = _open_tasks(root)
    git_lines = _git_block(root)

    task_lines = [
        f"- [{row['status']}] {row['title']}"
        + (f" (branch {row['branch']})" if row.get("branch") else "")
        + (f" PR {row['pr_url']}" if row.get("pr_url") else "")
        + (f" @{row['head_sha'][:7]}" if row.get("head_sha") else "")
        for row in tasks
    ]
    decision_lines = [f"- {heading}" for heading in decisions]
    constraint_lines = [f"- {item}" for item in constraints]

    seed_lines = [
        f"Resume work on {name} after time away.",
        "Use the brief, constraints, decisions, git state and open tasks below.",
        "Propose the single next concrete action; do not re-ask for context already listed.",
        "",
        "## Resume brief",
        brief or "(No actionable resume brief yet - check the board and git state.)",
        "",
        "## Git state",
        *(git_lines or ["(Not a git repository or git unavailable.)"]),
        "",
        "## Durable decisions",
        *(decision_lines or ["(No durable decisions recorded yet.)"]),
        "",
        "## Hard constraints",
        *(constraint_lines or ["(No hard constraints in MEMORY.md.)"]),
        "",
        "## Open tasks",
        *(task_lines or ["(Board is clear - no open tasks.)"]),
        "",
        "Start by confirming the next step, then execute it with apply_patch + verify.",
    ]
    seed = "\n".join(seed_lines)
    return {
        "project_path": str(root),
        "project_name": name,
        "seed": seed,
        "brief": brief,
        "git": git_lines,
        "decisions": decisions,
        "constraints": constraints,
        "tasks": tasks,
        "actionable": bool(brief or tasks or decisions or git_lines),
    }


def write_leave_handoff(
    project_path: Path | str,
    *,
    body: str,
    session_key: str | None = None,
) -> dict[str, Any]:
    """Write/replace the auto-handoff block in RESUME.md (explicit leave note)."""
    root = Path(project_path).expanduser()
    text = (body or "").strip()
    if not text or text.lower() in {"(nothing)", "nothing"}:
        raise ValueError("handoff body is required")
    continuity = root / ".navin" / "continuity"
    continuity.mkdir(parents=True, exist_ok=True)
    resume_path = continuity / "RESUME.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    label = f"session {session_key}" if session_key else "manual leave"
    clipped = _clip(text, 4_000)
    block = (
        f"{_AUTO_HANDOFF_START}\n"
        f"## Last auto handoff ({stamp}, {label})\n\n"
        f"{clipped}\n"
        f"{_AUTO_HANDOFF_END}"
    )
    existing = resume_path.read_text(encoding="utf-8") if resume_path.is_file() else ""
    start = existing.find(_AUTO_HANDOFF_START)
    end = existing.find(_AUTO_HANDOFF_END)
    if start != -1 and end != -1 and end > start:
        updated = existing[:start] + block + existing[end + len(_AUTO_HANDOFF_END) :]
    else:
        prefix = existing.rstrip()
        updated = f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"
    resume_path.write_text(updated, encoding="utf-8")
    return {"saved": True, "path": ".navin/continuity/RESUME.md", "chars": len(clipped)}


def append_decisions_from_brief(project_path: Path | str, brief: str) -> list[str]:
    """Extract Decisions from a consolidator brief and append to DECISIONS.md."""
    root = Path(project_path).expanduser()
    text = (brief or "").strip()
    if not text or text.lower() in {"(nothing)", "nothing"}:
        return []
    section = _extract_decisions_section(text)
    if not section:
        return []
    bullets = _decision_bullets(section)
    if not bullets:
        return []

    continuity = root / ".navin" / "continuity"
    continuity.mkdir(parents=True, exist_ok=True)
    path = continuity / "DECISIONS.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# Decisions log\n"
    stamp = datetime.now().strftime("%Y-%m-%d")
    # One heading per compaction wave; bullets are the durable choices.
    title = bullets[0][:80] if len(bullets) == 1 else "Session decisions"
    heading = f"### {stamp} - {title}"
    if heading in existing or any(b in existing for b in bullets):
        return []
    block = (
        f"\n{heading}\n\n"
        + "\n".join(f"- {item}" for item in bullets[:8])
        + "\n"
    )
    path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    return bullets


def _extract_decisions_section(brief: str) -> str:
    lines = brief.splitlines()
    start = -1
    for index, line in enumerate(lines):
        cleaned = line.strip()
        if _DECISIONS_SECTION.match(cleaned) or cleaned.lower().startswith("**decisions"):
            start = index + 1
            break
        if cleaned.lower().startswith("decisions:"):
            # Inline "Decisions: foo" form
            rest = cleaned.split(":", 1)[1].strip()
            return rest
    if start < 0:
        return ""
    collected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            continue
        if re.match(r"^(?:\*\*)?(Goal|Task|Done|Verified|State|Next)(?:\*\*)?\s*:?", stripped, re.I):
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _decision_bullets(section: str) -> list[str]:
    out: list[str] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        elif re.match(r"^\d+[.)]\s+", line):
            line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        if len(line) < 8:
            continue
        out.append(line)
        if len(out) >= 8:
            break
    if not out and len(section.strip()) >= 8:
        out.append(section.strip()[:200])
    return out
