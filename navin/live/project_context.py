# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the Live voice knows about the chat's project, read on demand.

The realtime voice only had the agent's last message: asked "who worked on
this project?" it could only hand the question to the agent and wait. This
gathers, in one fast read (no model call), the facts a colleague on a call
answers at once: the project folder, git branch and contributors, recent
commits, uncommitted work, the Collaboration team of the chat, the board,
and the README's opening lines.

Every part is optional and bounded: a missing git, an empty board or no team
gives an empty section, never an error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT_S = 4.0
MAX_CONTRIBUTORS = 25
MAX_COMMITS = 15
MAX_DIRTY = 30
MAX_ENTRIES = 60
README_CHARS = 1800
_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md", "README.rst", "README.txt", "README")
_SKIP_ENTRIES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".navin"}


def _git(project: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_summary(project: Path) -> dict[str, Any]:
    """Branch, contributors (commit counts), recent commits, uncommitted files."""
    if _git(project, "rev-parse", "--is-inside-work-tree") is None:
        return {"repository": False}
    out: dict[str, Any] = {"repository": True}
    branch = (_git(project, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    out["branch"] = branch or None
    contributors = []
    for line in (_git(project, "shortlog", "-sne", "HEAD") or "").splitlines():
        count, _, who = line.strip().partition("\t")
        if who and count.strip().isdigit():
            name, _, email = who.partition(" <")
            contributors.append({"name": name.strip(), "email": email.rstrip(">").strip(), "commits": int(count)})
    out["contributors"] = contributors[:MAX_CONTRIBUTORS]
    commits = []
    raw_log = _git(project, "log", f"-n{MAX_COMMITS}", "--pretty=format:%h\x1f%an\x1f%ar\x1f%s") or ""
    for line in raw_log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1], "when": parts[2], "subject": parts[3][:160]})
    out["recent_commits"] = commits
    total = (_git(project, "rev-list", "--count", "HEAD") or "").strip()
    out["total_commits"] = int(total) if total.isdigit() else None
    first = (_git(project, "log", "--reverse", "--format=%ar", "-n1") or "").strip()
    out["first_commit"] = first.splitlines()[0] if first else None
    dirty = [line[3:].strip() for line in (_git(project, "status", "--porcelain") or "").splitlines() if line.strip()]
    out["uncommitted_files"] = dirty[:MAX_DIRTY]
    out["uncommitted_count"] = len(dirty)
    remote = (_git(project, "remote", "get-url", "origin") or "").strip()
    # Never repeat credentials embedded in a remote URL.
    if "@" in remote and "://" in remote:
        scheme, _, rest = remote.partition("://")
        remote = f"{scheme}://{rest.split('@', 1)[-1]}"
    out["remote"] = remote or None
    return out


def team_summary_for(sessions: Any, session_key: str) -> dict[str, Any]:
    """The Collaboration team of this chat: who exists and what each one does.

    No settings, no team or no session give the default (empty) team: those
    cases are handled where they are read (current_collab, session_team_id).
    """
    from navin.collab.runtime import current_collab, session_team, session_team_id

    collab = current_collab()
    team, members = session_team(collab, session_team_id(sessions, session_key))
    return {
        "enabled": bool(getattr(collab, "enabled", False)),
        "team_name": getattr(team, "name", "") if team is not None else "",
        "members": [
            {
                "name": str(getattr(m, "name", "")),
                "title": str(getattr(m, "title", "")),
                "role": str(getattr(m, "role", "")),
                "specialty": str(getattr(m, "specialty", ""))[:200],
                "email": str(getattr(m, "email", "")),
            }
            for m in members
        ],
    }


def board_summary(project: Path) -> dict[str, Any]:
    """Task counts by status and what is in progress, with who holds it.

    A project without a board reads as no tasks; an unreadable board file is
    logged and read as empty by the store itself.
    """
    from navin.board.store import ProjectBoardStore

    tasks = ProjectBoardStore(project).read_tasks()
    by_status: dict[str, int] = {}
    for task in tasks:
        by_status[task.get("status", "backlog")] = by_status.get(task.get("status", "backlog"), 0) + 1
    active = []
    for task in tasks:
        if task.get("status") in {"in_progress", "review", "audit", "fix", "blocked"}:
            assignee = task.get("assignee") or {}
            active.append({
                "title": task.get("title"),
                "status": task.get("status"),
                "assignee": assignee.get("name") if isinstance(assignee, dict) else None,
            })
    return {"tasks": len(tasks), "by_status": by_status, "active": active[:20]}


def readme_excerpt(project: Path) -> str | None:
    for name in _README_NAMES:
        path = project / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:README_CHARS].strip() or None
            except OSError:
                return None
    return None


def top_entries(project: Path) -> list[str]:
    try:
        entries = sorted(project.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    names = [p.name + ("/" if p.is_dir() else "") for p in entries if p.name not in _SKIP_ENTRIES]
    return names[:MAX_ENTRIES]


def project_context(project_path: str | Path | None, *, sessions: Any = None, session_key: str = "") -> dict[str, Any]:
    """Everything the Live voice can say about the chat's project right now."""
    out: dict[str, Any] = {"team": team_summary_for(sessions, session_key) if session_key else {"members": []}}
    project = Path(project_path).expanduser() if project_path else None
    if project is None or not project.is_dir():
        out["project"] = None
        return out
    out["project"] = {"name": project.name, "path": str(project), "top_level": top_entries(project)}
    out["git"] = git_summary(project)
    out["board"] = board_summary(project)
    out["readme"] = readme_excerpt(project)
    return out
