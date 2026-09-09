# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Automated pre-commit review of pending changes ("Review my changes").

Builds a structured review prompt from the working-tree diff (staged +
unstaged + untracked) so the agent can review a change the way a human
reviewer would - before it is committed or pushed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.git_argv import git_argv
from navin.utils.proc import no_window_kwargs

# Bounds keep the seed prompt within a sane context budget.
MAX_DIFF_CHARS = 60_000
MAX_UNTRACKED_FILES = 12
MAX_UNTRACKED_CHARS = 4_000


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    # Same routing as the Git panel: a project on \\wsl.localhost\... is a
    # Linux repo, and Windows git refuses it as dubious ownership. Reviewing
    # changes used to report "not a repository" there while the Git panel
    # right next to it listed them.
    argv = git_argv(root)
    if argv is None:
        return 1, "", "git is not installed or not on PATH"
    try:
        proc = subprocess.run(
            [*argv, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def collect_pending_changes(
    root: Path,
    *,
    max_diff_chars: int = MAX_DIFF_CHARS,
) -> dict[str, Any]:
    """Snapshot of everything that would go into the next commit.

    Returns is_repo, branch, files ([{path, status}]), and a bounded unified
    diff. Untracked files are appended as pseudo-diff blocks so new files are
    reviewed too.
    """
    code, out, _err = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out.strip() != "true":
        return {"is_repo": False, "branch": "", "files": [], "diff": ""}

    _, branch_out, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_out.strip()

    files: list[dict[str, str]] = []
    untracked: list[str] = []
    code, status_out, _ = _git(root, "status", "--porcelain")
    if code == 0:
        for line in status_out.splitlines():
            if len(line) < 4:
                continue
            marker, path = line[:2], line[3:].strip()
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            if marker == "??":
                untracked.append(path)
                files.append({"path": path, "status": "untracked"})
            else:
                files.append({"path": path, "status": marker.strip() or "M"})

    # HEAD..worktree covers staged and unstaged in one pass. A brand-new repo
    # has no HEAD yet; fall back to the plain worktree diff.
    code, diff_out, _ = _git(root, "diff", "HEAD")
    if code != 0:
        _, diff_out, _ = _git(root, "diff")
    diff = diff_out

    for path in untracked[:MAX_UNTRACKED_FILES]:
        target = root / path
        try:
            if not target.is_file():
                continue
            body = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        excerpt = body[:MAX_UNTRACKED_CHARS]
        suffix = "\n[... truncated ...]" if len(body) > MAX_UNTRACKED_CHARS else ""
        diff += f"\n--- /dev/null\n+++ b/{path} (new file)\n{excerpt}{suffix}\n"

    truncated = len(diff) > max_diff_chars
    if truncated:
        diff = diff[:max_diff_chars] + "\n[... diff truncated ...]\n"

    return {
        "is_repo": True,
        "branch": branch,
        "files": files,
        "diff": diff,
        "truncated": truncated,
    }


def build_review_prompt(
    branch: str,
    files: list[dict[str, str]],
    diff: str,
) -> str:
    """Reviewer instructions + the bounded diff, ready to seed the agent."""
    names = ", ".join(item["path"] for item in files[:30]) or "(none)"
    header = (
        "Review the pending changes in this repository like a strict human "
        "reviewer, before they are committed.\n"
        f"Branch: {branch or '(unknown)'}\n"
        f"Changed files: {names}\n"
        "For each file, report: (1) bugs or logic errors, (2) security issues, "
        "(3) performance problems, (4) missing tests or edge cases. "
        "Read the surrounding code when the diff alone is not enough to judge. "
        "Finish with a verdict: 'ready to commit' or a numbered list of "
        "blocking fixes. Do not modify any file during the review.\n"
    )
    if not diff.strip():
        return header + "\nThere is no pending diff; say so and stop.\n"
    return header + "\n--- pending diff ---\n" + diff + "\n--- end diff ---\n"


def review_prompt_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Payload for the Git panel's 'Review changes' agent action."""
    root = Path(scope.project_path)
    snapshot = collect_pending_changes(root)
    if not snapshot["is_repo"]:
        return {
            "available": False,
            "branch": "",
            "files": [],
            "prompt": "",
            "detail": "not a git repository",
        }
    prompt = build_review_prompt(
        snapshot["branch"], snapshot["files"], snapshot["diff"]
    )
    return {
        "available": True,
        "branch": snapshot["branch"],
        "files": snapshot["files"],
        "prompt": prompt,
        "detail": "",
    }
