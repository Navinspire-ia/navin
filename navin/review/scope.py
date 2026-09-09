# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Git change-set scoping for Review mode (OCR-style 5-gates + rules)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from navin.review.gates import apply_file_gates
from navin.review.rules import load_review_rules, rules_for_path
from navin.utils.proc import no_window_kwargs

_WALK_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        "vendor",
        "coverage",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return completed.returncode, completed.stdout or ""


def _is_git_repo(root: Path) -> bool:
    code, _ = _run_git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0


def _walk_project_files(root: Path, *, limit: int = 400) -> list[str]:
    """List project files when git is unavailable (non-repo / broken git)."""
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _WALK_SKIP_DIRS for part in rel_parts):
            continue
        files.append("/".join(rel_parts))
        if len(files) >= limit:
            break
    return files


def collect_review_scope(root: Path, *, path: str | None = None) -> dict[str, Any]:
    """Return gated changed files + unified diff + matching review rules."""
    root = root.expanduser().resolve(strict=False)
    rules = load_review_rules(root)
    include = list(rules.get("include") or [])
    exclude = list(rules.get("exclude") or [])

    if path:
        rel = path.replace("\\", "/").lstrip("./")
        gated = apply_file_gates([rel], include=include, exclude=exclude)
        files = list(gated["files"])
        _, diff = _run_git(root, "diff", "--", rel)
        if not diff:
            _, diff = _run_git(root, "diff", "HEAD", "--", rel)
        return {
            "ok": True,
            "mode": "path",
            "files": files,
            "dropped": gated.get("dropped") or [],
            "diff": diff[:80_000],
            "file_count": len(files),
            "git_repo": _is_git_repo(root),
            "rules_source": rules.get("source"),
            "path_rules": {f: rules_for_path(f, rules) for f in files},
            "note": "Scoped to a single path after OCR-style 5-gates.",
        }

    git_repo = _is_git_repo(root)
    raw_files: list[str] = []
    seen: set[str] = set()
    mode = "changed"
    note = (
        "OCR-style 5-gates: binary → user_exclude → user_include → "
        "unsupported_ext → default_path (tests). Prefer reviewing kept files."
    )
    diff = ""

    if git_repo:
        for args in (
            ("diff", "--name-only", "HEAD"),
            ("diff", "--name-only", "--cached"),
            ("ls-files", "--others", "--exclude-standard"),
        ):
            _, block = _run_git(root, *args)
            for line in block.splitlines():
                rel = line.strip()
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                raw_files.append(rel)
        _, diff = _run_git(root, "diff", "HEAD")
        if not diff:
            _, d1 = _run_git(root, "diff")
            _, d2 = _run_git(root, "diff", "--cached")
            diff = f"{d1}\n{d2}"
        if not raw_files:
            # Clean tree: fall back to tracked files so Review still has a scope.
            _, tracked = _run_git(root, "ls-files")
            for line in tracked.splitlines():
                rel = line.strip()
                if not rel or rel in seen:
                    continue
                seen.add(rel)
                raw_files.append(rel)
            mode = "tracked"
            note = (
                "Git working tree clean - scoped to tracked files after 5-gates. "
                "Pass path= to focus, or make a change-set first."
            )
    else:
        raw_files = _walk_project_files(root)
        mode = "filesystem"
        note = (
            "Not a git repository - scoped by walking the project tree after "
            "OCR-style 5-gates. Initialize git for change-set review."
        )

    gated = apply_file_gates(raw_files, include=include, exclude=exclude)
    files = list(gated["files"])[:200]
    return {
        "ok": True,
        "mode": mode,
        "files": files,
        "dropped": (gated.get("dropped") or [])[:100],
        "diff": (diff or "")[:80_000],
        "file_count": len(files),
        "raw_file_count": len(raw_files),
        "git_repo": git_repo,
        "rules_source": rules.get("source"),
        "path_rules": {f: rules_for_path(f, rules) for f in files if rules_for_path(f, rules)},
        "note": note,
    }
