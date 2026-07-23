"""Project-wide text search and git changes/diffs for the Dev workbench.

Search prefers ripgrep (``rg --json``) when available and falls back to a
pure-Python scan. Git data is read with the ``git`` CLI. Both are scoped to
the session's workspace root and never follow symlinks outside it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope

_RG_TIMEOUT_S = 15
_GIT_TIMEOUT_S = 10
_MAX_RESULTS = 500
_MAX_FILES = 200
_MAX_LINE_CHARS = 400
_MAX_PY_SCAN_FILES = 20_000
_MAX_PY_FILE_BYTES = 1024 * 1024
_MAX_DIFF_BYTES = 512 * 1024

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".ruff_cache",
    ".pytest_cache", "dist", "build", ".next", ".cache", ".checkpoints",
    ".idea", ".vscode", "coverage", "target", ".tox", ".mypy_cache",
}


class ProjectSearchError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _root(scope: WorkspaceScope) -> Path:
    root = Path(scope.project_path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise ProjectSearchError("project root not found", status=404)
    return root


def _clip(text: str) -> str:
    text = text.rstrip("\n")
    if len(text) > _MAX_LINE_CHARS:
        return text[:_MAX_LINE_CHARS] + "…"
    return text


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _rg_search(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
) -> list[dict[str, Any]] | None:
    rg = shutil.which("rg")
    if rg is None:
        return None
    cmd = [
        rg, "--json", "--max-count", "50",
        "--max-filesize", "1M", "--no-follow",
        # Editor-grade search: include hidden and gitignored files; the
        # explicit glob excludes below keep the noisy directories out.
        "--hidden", "--no-ignore-vcs",
    ]
    cmd.append("--case-sensitive" if case_sensitive else "--ignore-case")
    if not regex:
        cmd.append("--fixed-strings")
    for skip in _SKIP_DIRS:
        cmd.extend(["--glob", f"!{skip}/"])
    cmd.extend(["--", query, str(root)])
    try:
        out = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=_RG_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode not in (0, 1):  # 1 = no matches
        # 2 = usage/regex error → surface to the caller
        stderr = (out.stderr or "").strip().splitlines()
        raise ProjectSearchError(stderr[0] if stderr else "search failed")

    rows: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        if len(rows) >= _MAX_RESULTS:
            break
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = ((data.get("path") or {}).get("text")) or ""
        lines_text = ((data.get("lines") or {}).get("text")) or ""
        submatches = data.get("submatches") or []
        first = submatches[0] if submatches else {}
        try:
            rel = str(Path(path_text).resolve(strict=False).relative_to(root))
        except ValueError:
            continue
        rows.append({
            "path": rel,
            "line": int(data.get("line_number") or 1),
            "col": int(first.get("start") or 0) + 1,
            "text": _clip(lines_text),
        })
    return rows


def _python_search(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise ProjectSearchError(f"invalid regex: {exc}") from exc

    rows: list[dict[str, Any]] = []
    scanned = 0
    stack = [root]
    while stack and len(rows) < _MAX_RESULTS and scanned < _MAX_PY_SCAN_FILES:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if len(rows) >= _MAX_RESULTS or scanned >= _MAX_PY_SCAN_FILES:
                break
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
                continue
            if not entry.is_file():
                continue
            scanned += 1
            try:
                if entry.stat().st_size > _MAX_PY_FILE_BYTES:
                    continue
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for idx, line in enumerate(content.splitlines(), start=1):
                match = pattern.search(line)
                if match is None:
                    continue
                rows.append({
                    "path": str(entry.relative_to(root)),
                    "line": idx,
                    "col": match.start() + 1,
                    "text": _clip(line),
                })
                if len(rows) >= _MAX_RESULTS:
                    break
    return rows


def search_payload(
    scope: WorkspaceScope,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search the project for a string or regex. Grouped, capped, workspace-scoped."""
    cleaned = (query or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing query")
    if len(cleaned) > 512:
        raise ProjectSearchError("query too long")
    root = _root(scope)

    rows = _rg_search(root, cleaned, regex=regex, case_sensitive=case_sensitive)
    tool = "rg"
    if rows is None:
        rows = _python_search(root, cleaned, regex=regex, case_sensitive=case_sensitive)
        tool = "python"

    files: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        files.setdefault(row["path"], []).append(
            {"line": row["line"], "col": row["col"], "text": row["text"]}
        )
        if len(files) > _MAX_FILES:
            break

    return {
        "root": str(root),
        "query": cleaned,
        "tool": tool,
        "truncated": len(rows) >= _MAX_RESULTS,
        "total": len(rows),
        "files": [
            {"path": path, "matches": matches} for path, matches in files.items()
        ],
    }


# ---------------------------------------------------------------------------
# Git changes / diff
# ---------------------------------------------------------------------------


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None


_STATUS_LABELS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "T": "typechange", "U": "conflict", "?": "untracked",
}


def git_changes_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Changed files (staged + unstaged + untracked) for the source-control view."""
    root = _root(scope)
    out = _run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if out is None:
        return {"available": False, "is_repo": False, "files": []}
    if out.returncode != 0:
        return {"available": True, "is_repo": False, "files": []}

    files: list[dict[str, Any]] = []
    entries = out.stdout.split("\0")
    i = 0
    while i < len(entries) and len(files) < 500:
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        xy = entry[:2]
        path = entry[3:]
        if xy.startswith("R") or xy.startswith("C"):
            # Rename/copy: next NUL-separated entry is the original path.
            i += 1
        code = (xy.replace(" ", "") or "?")[0]
        files.append({
            "path": path,
            "xy": xy,
            "status": _STATUS_LABELS.get(code, "modified"),
            "staged": xy[0] not in (" ", "?"),
        })
    return {"available": True, "is_repo": True, "files": files}


def git_diff_payload(scope: WorkspaceScope, raw_file: str) -> dict[str, Any]:
    """Unified diff (HEAD → worktree) for one file; whole content for untracked."""
    root = _root(scope)
    cleaned = (raw_file or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing file")
    target = (root / cleaned).resolve(strict=False)
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise ProjectSearchError("file outside project", status=403) from exc

    out = _run_git(root, "diff", "HEAD", "--no-color", "--", str(rel))
    diff = out.stdout if out is not None and out.returncode == 0 else ""

    untracked = False
    if not diff.strip():
        # Untracked file: synthesize an "all added" diff.
        show = _run_git(
            root, "diff", "--no-color", "--no-index", "/dev/null", str(rel),
        )
        if show is not None and show.stdout.strip():
            diff = show.stdout
            untracked = True

    if len(diff) > _MAX_DIFF_BYTES:
        diff = diff[:_MAX_DIFF_BYTES] + "\n… diff truncated …\n"

    return {
        "path": str(rel),
        "untracked": untracked,
        "diff": diff,
    }
