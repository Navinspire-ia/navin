"""Project-wide text search and git changes/diffs for the Dev workbench.

Search prefers ripgrep (``rg --json``) when available and falls back to a
pure-Python scan. Git data is read with the ``git`` CLI. Both are scoped to
the session's workspace root and never follow symlinks outside it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.git_argv import git_argv
from navin.utils.proc import no_window_kwargs

_RG_TIMEOUT_S = 15
_GIT_TIMEOUT_S = 10
_MAX_RESULTS = 500
_MAX_FILES = 200
_MAX_LINE_CHARS = 400
_MAX_PY_SCAN_FILES = 20_000
_MAX_PY_FILE_BYTES = 1024 * 1024
_MAX_DIFF_BYTES = 512 * 1024
_MAX_GLOBS = 20

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".ruff_cache",
    ".pytest_cache", "dist", "build", ".next", ".cache", ".checkpoints",
    ".idea", ".vscode", "coverage", "target", ".tox", ".mypy_cache", ".navin",
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


def split_globs(raw: str | None) -> list[str]:
    """Split a comma-separated glob filter the way an editor's search box does."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()][:_MAX_GLOBS]


def _glob_matches(rel_posix: str, pattern: str) -> bool:
    """Match one path against one filter, with editor rather than shell rules.

    A pattern with no separator applies at any depth (``*.ts`` finds
    ``src/a.ts``) and a bare directory name means everything under it
    (``tests`` means ``tests/**``), which is what people type and expect.
    """
    pattern = pattern.strip().replace("\\", "/").lstrip("./")
    if not pattern:
        return False
    candidates = {pattern}
    if "/" not in pattern.rstrip("/"):
        candidates.add(f"**/{pattern}")
    if not pattern.endswith("/**"):
        candidates.add(f"{pattern.rstrip('/')}/**")
        if "/" not in pattern.rstrip("/"):
            candidates.add(f"**/{pattern.rstrip('/')}/**")
    for candidate in candidates:
        # fnmatch has no notion of ``**``; collapsing it to ``*`` is enough
        # because ``*`` here is also allowed to cross separators.
        if fnmatch(rel_posix, candidate.replace("**", "*")):
            return True
    return False


def _passes_globs(rel_posix: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(_glob_matches(rel_posix, p) for p in include):
        return False
    return not any(_glob_matches(rel_posix, p) for p in exclude)


def _rg_search(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
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
    for pattern in include or []:
        cmd.extend(["--glob", pattern])
    for pattern in exclude or []:
        cmd.extend(["--glob", f"!{pattern}"])
    # Searched as "." from inside the root rather than by absolute path: a glob
    # containing a separator anchors to the start of the path it is matched
    # against, so "src/**" only means what the user typed if that path is
    # relative to the project.
    cmd.extend(["--", query, "."])
    try:
        out = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_RG_TIMEOUT_S,
            cwd=str(root),
            **no_window_kwargs(),
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
        rel = PurePosixPath(path_text.replace("\\", "/")).as_posix()
        if rel.startswith("./"):
            rel = rel[2:]
        if not rel or rel.startswith("../") or rel.startswith("/"):
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
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[dict[str, Any]]:
    pattern = compile_query(query, regex=regex, case_sensitive=case_sensitive)
    include = include or []
    exclude = exclude or []

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
            rel = entry.relative_to(root).as_posix()
            if not _passes_globs(rel, include, exclude):
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
                    "path": rel,
                    "line": idx,
                    "col": match.start() + 1,
                    "text": _clip(line),
                })
                if len(rows) >= _MAX_RESULTS:
                    break
    return rows


def _clean_query(query: str) -> str:
    cleaned = (query or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing query")
    if len(cleaned) > 512:
        raise ProjectSearchError("query too long")
    return cleaned


def compile_query(query: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    # MULTILINE keeps ``^`` and ``$`` anchored to lines, which is what ripgrep
    # does and therefore what the results on screen showed.
    flags = re.MULTILINE if case_sensitive else re.MULTILINE | re.IGNORECASE
    try:
        return re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise ProjectSearchError(f"invalid regex: {exc}") from exc


def search_payload(
    scope: WorkspaceScope,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    include: str | None = None,
    exclude: str | None = None,
) -> dict[str, Any]:
    """Search the project for a string or regex. Grouped, capped, workspace-scoped."""
    cleaned = _clean_query(query)
    root = _root(scope)
    include_globs = split_globs(include)
    exclude_globs = split_globs(exclude)

    rows = _rg_search(
        root, cleaned, regex=regex, case_sensitive=case_sensitive,
        include=include_globs, exclude=exclude_globs,
    )
    tool = "rg"
    if rows is None:
        rows = _python_search(
            root, cleaned, regex=regex, case_sensitive=case_sensitive,
            include=include_globs, exclude=exclude_globs,
        )
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
# Replace
# ---------------------------------------------------------------------------


_DOLLAR_GROUP = re.compile(r"\$(\$|\d{1,2}|\{\d{1,2}\})")


def expand_replacement(replacement: str, *, regex: bool) -> str:
    """Translate an editor-style replacement into one ``re.sub`` understands.

    Editors write capture groups as ``$1``; Python writes them as ``\\1``. In
    plain-text mode nothing is a reference, so the whole string is escaped and
    a literal ``$1`` stays a literal ``$1``.
    """

    if not regex:
        return replacement.replace("\\", "\\\\")

    # Backslashes the user typed are literal to them, so neutralise them before
    # introducing the ones that mean something.
    out = replacement.replace("\\", "\\\\")

    def sub(match: re.Match[str]) -> str:
        body = match.group(1)
        if body == "$":
            return "$"
        return "\\g<" + body.strip("{}") + ">"

    return _DOLLAR_GROUP.sub(sub, out)


def replace_payload(
    scope: WorkspaceScope,
    query: str,
    replacement: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
    include: str | None = None,
    exclude: str | None = None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Rewrite every match in the project, or in the given files only.

    Matching is redone here rather than trusted from the caller's last search:
    results shown on screen may be minutes old, and writing to a stale line
    number is how a replace-all quietly corrupts a file. ``paths`` narrows the
    work to one file for a per-file replace, but does not change the rule.
    """

    cleaned = _clean_query(query)
    if len(replacement) > 4096:
        raise ProjectSearchError("replacement too long")
    root = _root(scope)
    pattern = compile_query(cleaned, regex=regex, case_sensitive=case_sensitive)
    expanded = expand_replacement(replacement, regex=regex)

    if paths:
        targets = [_resolve_in_root(root, p) for p in paths[:_MAX_FILES]]
    else:
        rows = _rg_search(
            root, cleaned, regex=regex, case_sensitive=case_sensitive,
            include=split_globs(include), exclude=split_globs(exclude),
        )
        if rows is None:
            rows = _python_search(
                root, cleaned, regex=regex, case_sensitive=case_sensitive,
                include=split_globs(include), exclude=split_globs(exclude),
            )
        seen: dict[str, None] = {}
        for row in rows:
            seen.setdefault(row["path"], None)
        targets = [_resolve_in_root(root, p) for p in list(seen)[:_MAX_FILES]]

    changed: list[dict[str, Any]] = []
    total = 0
    for target in targets:
        try:
            if not target.is_file() or target.is_symlink():
                continue
            if target.stat().st_size > _MAX_PY_FILE_BYTES:
                continue
            # newline="" on both ends: universal-newline translation would
            # rewrite a CRLF file as LF throughout, turning a two-word
            # replacement into a diff against every line in the file.
            with open(target, encoding="utf-8", newline="") as handle:
                original = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
        updated, count = pattern.subn(expanded, original)
        if count == 0 or updated == original:
            continue
        try:
            _write_atomic(target, updated)
        except OSError as exc:
            raise ProjectSearchError(
                f"failed to write {target.name}", status=500
            ) from exc
        total += count
        changed.append(
            {"path": target.relative_to(root).as_posix(), "replacements": count}
        )

    return {
        "root": str(root),
        "query": cleaned,
        "replacements": total,
        "files_changed": len(changed),
        "files": changed,
    }


def _resolve_in_root(root: Path, relative: str) -> Path:
    """Resolve a caller-supplied result path, refusing anything outside root."""
    candidate = Path(relative)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectSearchError("path is outside the project", status=403) from exc
    return resolved


def _write_atomic(target: Path, text: str) -> None:
    tmp = target.with_name(target.name + ".navin-replace.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# Git changes / diff
# ---------------------------------------------------------------------------


def _git_argv(root: Path) -> list[str] | None:
    """The argv prefix that runs git *where the project lives*.

    Thin alias over :func:`navin.utils.git_argv.git_argv`, kept because tests
    and callers in this module reach for the private name.
    """
    return git_argv(root)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    argv = _git_argv(root)
    if argv is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [*argv, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None


_STATUS_LABELS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "T": "typechange", "U": "conflict", "?": "untracked",
}


def _branch_state(root: Path) -> dict[str, Any]:
    """Branch name, upstream presence and ahead/behind counts.

    Feeds the source-control header and the "work not pushed" badge, so a
    failure here degrades to empty fields rather than an error.
    """
    out = _run_git(root, "status", "--branch", "--porcelain=v2")
    state: dict[str, Any] = {
        "branch": "",
        "has_upstream": False,
        "ahead": 0,
        "behind": 0,
    }
    if out is None or out.returncode != 0:
        return state
    for line in out.stdout.splitlines():
        if line.startswith("# branch.head "):
            head = line.removeprefix("# branch.head ").strip()
            state["branch"] = "" if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            state["has_upstream"] = True
        elif line.startswith("# branch.ab "):
            parts = line.removeprefix("# branch.ab ").split()
            for part in parts:
                if part.startswith("+"):
                    state["ahead"] = int(part[1:] or 0)
                elif part.startswith("-"):
                    state["behind"] = int(part[1:] or 0)
    out = _run_git(root, "stash", "list")
    if out is not None and out.returncode == 0:
        lines = [line for line in out.stdout.splitlines() if line.strip()]
        state["stash_count"] = len(lines)
        state["stashes"] = _parse_stash_entries(out.stdout, limit=20)
    else:
        state["stash_count"] = 0
        state["stashes"] = []
    return state


_STASH_LINE_RE = re.compile(r"^stash@\{(\d+)\}:\s*(.*)$")


def _parse_stash_entries(text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Turn ``git stash list`` lines into ``{index, label, message}`` rows."""
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _STASH_LINE_RE.match(stripped)
        if match:
            idx = int(match.group(1))
            message = match.group(2)
        else:
            idx = len(entries)
            message = stripped
        entries.append({"index": idx, "label": stripped, "message": message})
        if len(entries) >= limit:
            break
    return entries


def _stash_entries(root: Path, *, limit: int = 10_000) -> list[dict[str, Any]]:
    out = _run_git(root, "stash", "list")
    if out is None or out.returncode != 0:
        return []
    return _parse_stash_entries(out.stdout, limit=limit)


def _refuse_history_rewrite(root: Path, action: str) -> None:
    """Amend / Undo must not rewrite a commit that is already on the remote."""
    state = _branch_state(root)
    if state.get("has_upstream") and int(state.get("ahead") or 0) == 0:
        raise ProjectSearchError(
            f"last commit is already on the remote; {action} would rewrite history",
            status=409,
        )


def _git_conflict_flags(root: Path) -> dict[str, bool]:
    git_dir = root / ".git"
    if git_dir.is_file():
        # Worktree / gitfile: best-effort; MERGE_HEAD still lives under .git.
        try:
            text = git_dir.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("gitdir:"):
                    candidate = Path(line.removeprefix("gitdir:").strip())
                    if not candidate.is_absolute():
                        candidate = (root / candidate).resolve(strict=False)
                    git_dir = candidate
                    break
        except OSError:
            pass
    return {
        "merge_in_progress": (git_dir / "MERGE_HEAD").is_file(),
        "rebase_in_progress": (git_dir / "REBASE_HEAD").is_file()
        or (git_dir / "rebase-merge").is_dir()
        or (git_dir / "rebase-apply").is_dir(),
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
    return {
        "available": True,
        "is_repo": True,
        "files": files,
        **_branch_state(root),
        **_git_conflict_flags(root),
    }


# Enough for a commit-message prompt: the model needs the shape of the change,
# not every byte of a generated lockfile.
_COMMIT_DIFF_CHARS = 20_000
_COMMIT_STAT_CHARS = 4_000
_COMMIT_UNTRACKED_FILE_CHARS = 1_200
_COMMIT_UNTRACKED_FILES = 20
_COMMIT_LOG_COUNT = 8


def git_commit_diff_context(
    scope: WorkspaceScope,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Working-tree diff the AI commit-message button feeds the model.

    Uses the same file set as the source-control panel: selected paths when
    given, otherwise every changed / untracked file. Never mutates the index.
    """
    root = _root(scope)
    snapshot = git_changes_payload(scope)
    if not snapshot.get("is_repo"):
        raise ProjectSearchError("not a git repository", status=409)

    changed = snapshot.get("files") or []
    if paths:
        wanted = set(_normalized_rel_paths(root, paths))
        changed = [row for row in changed if row.get("path") in wanted]
    if not changed:
        raise ProjectSearchError("nothing to commit", status=409)

    rels = [str(row.get("path") or "") for row in changed if row.get("path")]
    tracked = [row["path"] for row in changed if row.get("status") != "untracked"]
    untracked = [row["path"] for row in changed if row.get("status") == "untracked"]

    stat = ""
    diff = ""
    if tracked:
        stat_out = _run_git(root, "diff", "HEAD", "--stat", "--", *tracked)
        if stat_out is not None and stat_out.stdout:
            stat = stat_out.stdout[:_COMMIT_STAT_CHARS]
        diff_out = _run_git(root, "diff", "HEAD", "--no-color", "--", *tracked)
        if diff_out is not None and diff_out.stdout:
            diff = diff_out.stdout

    extra: list[str] = []
    for rel in untracked[:_COMMIT_UNTRACKED_FILES]:
        extra.append(_untracked_preview(root, rel))
    if extra:
        blob = "\n".join(extra)
        diff = f"{diff}\n{blob}" if diff else blob

    if len(diff) > _COMMIT_DIFF_CHARS:
        diff = diff[:_COMMIT_DIFF_CHARS] + "\n… diff truncated …\n"

    recent: list[str] = []
    log_out = _run_git(
        root, "log", f"-{_COMMIT_LOG_COUNT}", "--pretty=%s"
    )
    if log_out is not None and log_out.returncode == 0 and log_out.stdout:
        recent = [line.strip() for line in log_out.stdout.splitlines() if line.strip()]

    return {
        "files": rels,
        "stat": stat.strip(),
        "diff": diff,
        "recent": recent,
        "branch": snapshot.get("branch") or "",
    }


def _untracked_preview(root: Path, rel: str) -> str:
    """Short added-file preview so new files show up in the commit prompt."""
    header = f"--- /dev/null\n+++ b/{rel}\n"
    path = root / rel
    try:
        if not path.is_file():
            return header + "@@ new file (not a regular file) @@\n"
        data = path.read_bytes()[: _COMMIT_UNTRACKED_FILE_CHARS * 4]
    except OSError:
        return header + "@@ unreadable new file @@\n"
    if b"\x00" in data[:1024]:
        return header + "@@ binary file added @@\n"
    text = data.decode("utf-8", errors="replace")[:_COMMIT_UNTRACKED_FILE_CHARS]
    return header + text


def _normalized_rel_paths(root: Path, paths: list[str] | None) -> list[str]:
    if not paths:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        rel = _relative_in_project(root, raw).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
        if len(out) >= 500:
            break
    return out


def git_stage_payload(
    scope: WorkspaceScope,
    paths: list[str] | None,
    *,
    stage: bool = True,
    all_files: bool = False,
) -> dict[str, Any]:
    """Stage or unstage selected paths (Source Control checkboxes).

    ``all_files=True`` matches VS Code Stage All / Unstage All Changes.
    """
    root = _root(scope)
    if all_files:
        if stage:
            out = _run_git_write(root, "add", "-A")
            action = "add -A"
        else:
            out = _run_git_write(root, "reset", "HEAD")
            action = "reset HEAD"
            if out is not None and out.returncode != 0:
                out = _run_git_write(root, "restore", "--staged", ":/")
                action = "restore --staged :/"
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            raise ProjectSearchError(
                f"git {action} failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )
        return git_changes_payload(scope)

    rels = _normalized_rel_paths(root, paths)
    if not rels:
        raise ProjectSearchError("at least one path is required", status=400)
    if stage:
        out = _run_git_write(root, "add", "--", *rels)
        action = "add"
    else:
        out = _run_git_write(root, "restore", "--staged", "--", *rels)
        action = "restore --staged"
        if out is not None and out.returncode != 0:
            # Older git / first commit: fall back to reset HEAD for staged paths.
            out = _run_git_write(root, "reset", "HEAD", "--", *rels)
            action = "reset HEAD"
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError(
            f"git {action} failed: {(out.stderr or out.stdout).strip()[:500]}",
            status=409,
        )
    return git_changes_payload(scope)


def git_discard_payload(
    scope: WorkspaceScope,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Throw away local edits (VS Code Discard Changes).

    Tracked files go back to HEAD; untracked files are deleted. Omitting
    ``paths`` discards every pending change in the working tree.
    """
    root = _root(scope)
    snapshot = git_changes_payload(scope)
    wanted = (
        _normalized_rel_paths(root, paths)
        if paths
        else [str(row.get("path") or "") for row in snapshot.get("files") or []]
    )
    wanted = [path for path in wanted if path]
    if not wanted:
        return snapshot

    by_path = {
        str(row.get("path") or ""): row
        for row in snapshot.get("files") or []
    }
    tracked: list[str] = []
    untracked: list[str] = []
    for rel in wanted:
        row = by_path.get(rel)
        xy = str((row or {}).get("xy") or "")
        if xy.startswith("?"):
            untracked.append(rel)
        else:
            tracked.append(rel)

    if tracked:
        out = _run_git_write(
            root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked,
        )
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            out = _run_git_write(root, "checkout", "--", *tracked)
            if out is None or out.returncode != 0:
                detail = ""
                if out is not None:
                    detail = (out.stderr or out.stdout).strip()[:500]
                raise ProjectSearchError(
                    f"git restore failed: {detail or 'unknown error'}",
                    status=409,
                )

    if untracked:
        out = _run_git_write(root, "clean", "-fd", "--", *untracked)
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            raise ProjectSearchError(
                f"git clean failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )

    return git_changes_payload(scope)


def git_stash_payload(
    scope: WorkspaceScope,
    *,
    op: str = "push",
    index: int | None = None,
) -> dict[str, Any]:
    """Stash push/pop plus list/apply/drop for a chosen ``stash@{n}``."""
    root = _root(scope)
    action = (op or "push").strip().lower()
    if action == "list":
        listed = _stash_entries(root)
        return {
            "stashes": listed[:20],
            "stash_count": len(listed),
            **git_changes_payload(scope),
        }
    if action in {"apply", "drop"}:
        if index is None or type(index) is not int or index < 0:
            raise ProjectSearchError(
                "stash index must be a non-negative integer", status=400,
            )
        listed = _stash_entries(root)
        if index >= len(listed):
            raise ProjectSearchError("stash index is out of range", status=400)
        ref = f"stash@{{{index}}}"
        out = _run_git_write(root, "stash", action, ref)
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            raise ProjectSearchError(
                f"git stash {action} failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )
        return {
            "applied": action == "apply",
            "dropped": action == "drop",
            "stashed": False,
            "popped": False,
            "index": index,
            "detail": (out.stdout or out.stderr or "").strip()[:500],
            **git_changes_payload(scope),
        }
    if action == "pop":
        out = _run_git_write(root, "stash", "pop")
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            raise ProjectSearchError(
                f"git stash pop failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )
        return {
            "popped": True,
            "stashed": False,
            "detail": (out.stdout or out.stderr or "").strip()[:500],
            **git_changes_payload(scope),
        }
    if action not in {"push", "save"}:
        raise ProjectSearchError(
            "stash op must be push, pop, list, apply, or drop", status=400,
        )
    out = _run_git_write(
        root, "stash", "push", "-u", "-m", "WIP from Navin",
    )
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError(
            f"git stash failed: {(out.stderr or out.stdout).strip()[:500]}",
            status=409,
        )
    return {
        "stashed": True,
        "popped": False,
        "detail": (out.stdout or out.stderr or "").strip()[:500],
        **git_changes_payload(scope),
    }


def git_fetch_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Fetch remotes without merging (VS Code Fetch)."""
    root = _root(scope)
    out = _run_git_write(root, "fetch", "--all", "--prune")
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError(
            f"git fetch failed: {(out.stderr or out.stdout).strip()[:500]}",
            status=409,
        )
    return {
        "fetched": True,
        "detail": (out.stdout or out.stderr or "").strip()[:500],
        **git_changes_payload(scope),
    }


# Commit is local and fast; push crosses the network. One generous ceiling
# for both keeps a slow remote from looking like a hang forever.
_GIT_WRITE_TIMEOUT_S = 120

# A remote URL can carry ``user:token@`` - never echo that back to the UI.
_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")


def redact_git_output(text: str) -> str:
    """Drop credentials embedded in any URL git printed."""
    return _URL_CREDENTIALS_RE.sub(lambda m: m.group("scheme"), text or "")


_AUTH_FAILURE_MARKERS = (
    "authentication failed",
    "invalid username or password",
    "credentials are incorrect or have expired",
    "could not read username",
    "could not read password",
    "http basic: access denied",
    "bad credentials",
    "401",
)

_PERMISSION_MARKERS = (
    "permission denied",
    "permission to ",
    "403",
    "you are not allowed to push",
    "pre-receive hook declined",
)

_SSH_MARKERS = (
    "permission denied (publickey",
    "host key verification failed",
    "could not read from remote repository",
)


def explain_push_failure(stderr: str) -> str:
    """``git push`` failed - say what to do about it, not just what git said.

    "Authentication failed for https://forgejo.example/..." is git being
    accurate and the user being stuck: the remote credentials cached on this
    machine are wrong or expired, and nothing in the panel said how to
    replace them.
    """
    raw = redact_git_output((stderr or "").strip())
    detail = raw[:500] or "unknown error"
    lowered = raw.lower()

    if any(marker in lowered for marker in _SSH_MARKERS) and "publickey" in lowered:
        hint = (
            "The SSH key this machine offers is not accepted by the remote. "
            "Add your public key to the forge account, or switch the remote "
            "to HTTPS with a token."
        )
    elif any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        hint = (
            "The credentials stored for this remote are wrong or expired. "
            "Create a new access token on the forge, then update it in your "
            "git credential helper (git credential-manager / osxkeychain / "
            "libsecret) or run 'git push' once in a terminal to be prompted "
            "again. Use the token as the password, not your account password."
        )
    elif any(marker in lowered for marker in _PERMISSION_MARKERS):
        hint = (
            "The account behind these credentials cannot write to this "
            "repository. Check the token scope (write / read_repository is "
            "not enough to push) and your access level on the project."
        )
    elif "non-fast-forward" in lowered or "fetch first" in lowered or "rejected" in lowered:
        hint = "The remote moved ahead. Pull (or Sync) first, then push again."
    else:
        return f"git push failed: {detail}"
    return f"git push failed: {detail}\n{hint}"


def _run_git_write(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    argv = _git_argv(root)
    if argv is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [*argv, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_WRITE_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectSearchError(
            f"git {args[0]} timed out after {_GIT_WRITE_TIMEOUT_S}s", status=504
        ) from exc
    except (OSError, subprocess.SubprocessError):
        return None


def git_commit_payload(
    scope: WorkspaceScope,
    message: str | None,
    *,
    push: bool,
    commit: bool = True,
    paths: list[str] | None = None,
    amend: bool = False,
) -> dict[str, Any]:
    """Stage, commit, and optionally push - the editor's one-click
    "Commit & Push". With ``commit=False`` it is a bare push of work already
    committed. ``amend=True`` rewrites HEAD (VS Code Commit Amend).

    ``paths`` controls staging:
    - ``None`` (default): ``git add -A`` (legacy one-click behaviour)
    - non-empty list: stage only those paths
    - empty list: commit already-staged index only (no ``git add``)
    """
    root = _root(scope)
    committed = False

    if commit:
        cleaned = (message or "").strip()
        if amend:
            head = _run_git(root, "rev-parse", "--verify", "HEAD")
            if head is None or head.returncode != 0:
                raise ProjectSearchError("nothing to amend", status=409)
            _refuse_history_rewrite(root, "amend")
        elif not cleaned:
            raise ProjectSearchError("commit message is required", status=400)

        if paths is None:
            out = _run_git_write(root, "add", "-A")
            if out is None:
                raise ProjectSearchError("git is not available on this host", status=503)
            if out.returncode != 0:
                raise ProjectSearchError(
                    f"git add failed: {(out.stderr or out.stdout).strip()[:500]}",
                    status=409,
                )
        elif paths:
            rels = _normalized_rel_paths(root, paths)
            if not rels:
                raise ProjectSearchError("no valid paths to stage", status=400)
            out = _run_git_write(root, "add", "--", *rels)
            if out is None:
                raise ProjectSearchError("git is not available on this host", status=503)
            if out.returncode != 0:
                raise ProjectSearchError(
                    f"git add failed: {(out.stderr or out.stdout).strip()[:500]}",
                    status=409,
                )

        if amend:
            commit_args = (
                ["commit", "--amend", "--no-edit"]
                if not cleaned
                else ["commit", "--amend", "-m", cleaned]
            )
        else:
            commit_args = ["commit", "-m", cleaned]
        out = _run_git_write(root, *commit_args)
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            detail = (out.stderr or out.stdout).strip()[:500]
            if "nothing to commit" in detail.lower():
                if not push:
                    return {
                        "committed": False,
                        "pushed": False,
                        "detail": detail,
                        **_branch_state(root),
                    }
            else:
                # user.name not set, hook rejection, etc. - surface git's own words.
                raise ProjectSearchError(f"git commit failed: {detail}", status=409)
        else:
            committed = True

    pushed = False
    detail = ""
    if push:
        out = _run_git_write(root, "push")
        if out is not None and out.returncode != 0:
            stderr = (out.stderr or out.stdout).strip()
            if "no upstream" in stderr.lower() or "set-upstream" in stderr.lower():
                out = _run_git_write(root, "push", "-u", "origin", "HEAD")
                stderr = "" if out is not None and out.returncode == 0 else (
                    ((out.stderr or out.stdout).strip()) if out is not None else "git unavailable"
                )
            if out is None or out.returncode != 0:
                raise ProjectSearchError(
                    explain_push_failure(stderr or "unknown error"), status=409
                )
        pushed = out is not None and out.returncode == 0

    return {"committed": committed, "pushed": pushed, "detail": detail, **_branch_state(root)}


def git_pull_payload(scope: WorkspaceScope, *, rebase: bool = False) -> dict[str, Any]:
    """Fetch + pull the current branch (Source Control Pull action).

    Default stays fast-forward then merge. ``rebase=True`` runs
    ``git pull --rebase`` and returns ``rebase_in_progress`` on conflict
    instead of only raising, so the workbench can Abort / Continue.
    """
    root = _root(scope)
    if rebase:
        out = _run_git_write(root, "pull", "--rebase")
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        flags = _git_conflict_flags(root)
        if out.returncode != 0:
            if flags["rebase_in_progress"] or flags["merge_in_progress"]:
                return {
                    "pulled": True,
                    "detail": (
                        (out.stderr or out.stdout or "").strip() or "conflict"
                    )[:500],
                    **_branch_state(root),
                    **flags,
                }
            raise ProjectSearchError(
                f"git pull --rebase failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )
        return {
            "pulled": True,
            "rebased": True,
            "detail": (out.stdout or out.stderr or "").strip()[:500],
            **_branch_state(root),
            **flags,
        }

    out = _run_git_write(root, "pull", "--ff-only")
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        # Retry without --ff-only so a merge pull can still succeed when configured.
        detail = (out.stderr or out.stdout).strip()
        # A host-level pull.ff=only / pull.rebase=true would make a plain
        # `git pull` fail the same way. Force a merge so Sync can stop on
        # conflict instead of looking like a hard error.
        out = _run_git_write(
            root, "-c", "pull.ff=false", "-c", "pull.rebase=false", "pull",
        )
        flags = _git_conflict_flags(root)
        if flags["merge_in_progress"] or flags["rebase_in_progress"]:
            return {
                "pulled": True,
                "detail": (
                    ((out.stderr or out.stdout).strip() if out else "") or detail or "conflict"
                )[:500],
                **_branch_state(root),
                **flags,
            }
        if out is None or out.returncode != 0:
            raise ProjectSearchError(
                f"git pull failed: {(detail or (out.stderr if out else '') or 'unknown')[:500]}",
                status=409,
            )
    return {
        "pulled": True,
        "detail": (out.stdout or out.stderr or "").strip()[:500],
        **_branch_state(root),
        **_git_conflict_flags(root),
    }


def git_undo_last_commit_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Undo HEAD with ``git reset --soft HEAD~1`` (files stay staged).

    Same published-commit guard as Amend: refuse when the tip is already
    on the upstream (``has_upstream && ahead == 0``).
    """
    root = _root(scope)
    head = _run_git(root, "rev-parse", "--verify", "HEAD")
    if head is None or head.returncode != 0:
        raise ProjectSearchError("nothing to undo", status=409)
    _refuse_history_rewrite(root, "undo")
    parent = _run_git(root, "rev-parse", "--verify", "HEAD~1")
    if parent is None or parent.returncode != 0:
        raise ProjectSearchError("cannot undo the initial commit", status=409)
    out = _run_git_write(root, "reset", "--soft", "HEAD~1")
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError(
            f"git reset failed: {(out.stderr or out.stdout).strip()[:500]}",
            status=409,
        )
    return {
        "undone": True,
        "detail": (out.stdout or out.stderr or "").strip()[:500],
        **git_changes_payload(scope),
    }


def git_sync_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """VS Code Sync Changes: fetch, pull then push. Unpublished branches only publish."""
    root = _root(scope)
    _run_git_write(root, "fetch", "--all", "--prune")
    state = _branch_state(root)
    pulled = False
    if state.get("has_upstream"):
        pulled_payload = git_pull_payload(scope)
        pulled = True
        if pulled_payload.get("merge_in_progress") or pulled_payload.get("rebase_in_progress"):
            return {
                "synced": False,
                "pulled": True,
                "pushed": False,
                "detail": pulled_payload.get("detail") or "conflict",
                **pulled_payload,
            }
    pushed_payload = git_commit_payload(scope, "", push=True, commit=False)
    return {
        "synced": True,
        "pulled": pulled,
        **pushed_payload,
    }


git_undo_payload = git_undo_last_commit_payload


def git_branch_payload(
    scope: WorkspaceScope,
    *,
    op: str,
    name: str | None = None,
) -> dict[str, Any]:
    """List / create / checkout local branches for the workbench."""
    root = _root(scope)
    action = (op or "list").strip().lower()
    if action == "list":
        out = _run_git(root, "branch", "--list", "--format=%(refname:short)")
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        branches = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        state = _branch_state(root)
        return {
            "op": "list",
            "branches": branches,
            "current": state.get("branch") or "",
            **state,
        }

    cleaned = (name or "").strip()
    bad = (" ", "~", "^", ":", "\\", "?", "*", "[", "\x00")
    if (
        not cleaned
        or cleaned.startswith("-")
        or ".." in cleaned
        or any(ch in cleaned for ch in bad)
    ):
        raise ProjectSearchError("invalid branch name", status=400)

    if action == "create":
        out = _run_git_write(root, "switch", "-c", cleaned)
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            raise ProjectSearchError(
                f"git switch -c failed: {(out.stderr or out.stdout).strip()[:500]}",
                status=409,
            )
        return {"op": "create", "branch": cleaned, **_branch_state(root)}

    if action == "checkout":
        out = _run_git_write(root, "switch", cleaned)
        if out is None:
            raise ProjectSearchError("git is not available on this host", status=503)
        if out.returncode != 0:
            # Remote-tracking branch: try create local from origin/<name>.
            out = _run_git_write(root, "switch", "-c", cleaned, "--track", f"origin/{cleaned}")
            if out is None or out.returncode != 0:
                raise ProjectSearchError(
                    f"git switch failed: {(out.stderr or out.stdout).strip()[:500] if out else 'unavailable'}",
                    status=409,
                )
        return {"op": "checkout", "branch": cleaned, **_branch_state(root)}

    raise ProjectSearchError("op must be list, create, or checkout", status=400)


def git_conflict_action_payload(
    scope: WorkspaceScope,
    *,
    action: str,
) -> dict[str, Any]:
    """Abort or continue an in-progress merge/rebase from the workbench."""
    root = _root(scope)
    flags = _git_conflict_flags(root)
    op = (action or "").strip().lower()
    if op == "abort":
        if flags["rebase_in_progress"]:
            out = _run_git_write(root, "rebase", "--abort")
        elif flags["merge_in_progress"]:
            out = _run_git_write(root, "merge", "--abort")
        else:
            raise ProjectSearchError("no merge or rebase in progress", status=409)
    elif op == "continue":
        if flags["rebase_in_progress"]:
            # core.editor=true exits 0 so Continue never blocks on $EDITOR.
            out = _run_git_write(
                root, "-c", "core.editor=true", "-c", "sequence.editor=true",
                "rebase", "--continue",
            )
        elif flags["merge_in_progress"]:
            out = _run_git_write(root, "commit", "--no-edit")
        else:
            raise ProjectSearchError("no merge or rebase in progress", status=409)
    else:
        raise ProjectSearchError("action must be abort or continue", status=400)

    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError(
            f"git {op} failed: {(out.stderr or out.stdout).strip()[:500]}",
            status=409,
        )
    return {
        "ok": True,
        "action": op,
        **_branch_state(root),
        **_git_conflict_flags(root),
        "files": git_changes_payload(scope).get("files", []),
    }


def git_diff_payload(
    scope: WorkspaceScope,
    raw_file: str,
    raw_against: str | None = None,
    *,
    ignore_whitespace: bool = False,
) -> dict[str, Any]:
    """Unified diff (HEAD → worktree) for one file; whole content for untracked.

    With ``raw_against`` it compares the two paths against each other instead,
    which is what "Select for Compare" needs and git already does with
    ``--no-index``. ``ignore_whitespace`` maps to ``-w`` (VS Code Ignore
    Whitespace).
    """
    root = _root(scope)
    rel = _relative_in_project(root, raw_file)
    ws_args = ("-w",) if ignore_whitespace else ()

    if raw_against and raw_against.strip():
        other = _relative_in_project(root, raw_against)
        # as_posix(): the command may be routed into a WSL distribution, where
        # backslash-separated relatives would not match any file.
        out = _run_git(
            root, "diff", "--no-color", *ws_args, "--no-index", "--",
            other.as_posix(), rel.as_posix(),
        )
        # --no-index exits 1 when the files differ, which is the normal case
        # here, so the text is what decides rather than the status.
        diff = out.stdout if out is not None else ""
        if len(diff) > _MAX_DIFF_BYTES:
            diff = diff[:_MAX_DIFF_BYTES] + "\n… diff truncated …\n"
        return {
            "path": rel.as_posix(),
            "against": other.as_posix(),
            "untracked": False,
            "ignore_whitespace": ignore_whitespace,
            "diff": diff,
        }

    out = _run_git(root, "diff", "HEAD", "--no-color", *ws_args, "--", rel.as_posix())
    diff = out.stdout if out is not None and out.returncode == 0 else ""

    untracked = False
    if not diff.strip():
        tracked = _run_git(root, "ls-files", "--error-unmatch", "--", rel.as_posix())
        is_tracked = tracked is not None and tracked.returncode == 0
        if not is_tracked:
            # Untracked file: synthesize an "all added" diff. Git for Windows maps
            # /dev/null to NUL itself, so the same spelling works on every host.
            show = _run_git(
                root,
                "diff",
                "--no-color",
                *ws_args,
                "--no-index",
                "/dev/null",
                rel.as_posix(),
            )
            if show is not None and show.stdout.strip():
                diff = show.stdout
                untracked = True

    if len(diff) > _MAX_DIFF_BYTES:
        diff = diff[:_MAX_DIFF_BYTES] + "\n… diff truncated …\n"

    return {
        "path": rel.as_posix(),
        "against": None,
        "untracked": untracked,
        "ignore_whitespace": ignore_whitespace,
        "diff": diff,
    }


_MAX_BLAME_LINES = 8_000
_GIT_BLAME_TIMEOUT_S = 15


def git_blame_payload(
    scope: WorkspaceScope,
    raw_file: str,
    extra_root: Path | None = None,
    extra_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """Per-line blame for one file (author, date, commit subject).

    Used by the editor's light GitLens-style current-line blame strip. Large
    files are capped so a blame never blocks the workbench.
    """
    # The open Code project is tried first: the chat session may still be
    # scoped to another folder while this tab is a file from the editor.
    roots: list[Path] = []
    extras: list[Path] = []
    if extra_root is not None:
        extras.append(extra_root)
    extras.extend(extra_roots or [])
    for candidate_root in extras + [_root(scope)]:
        try:
            resolved = Path(candidate_root).expanduser().resolve(strict=False)
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    if not roots:
        roots.append(_root(scope))

    cleaned = (raw_file or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing file")
    candidate = Path(cleaned).expanduser()

    root: Path | None = None
    rel: Path | None = None
    last_outside = False
    for probe in roots:
        try:
            if candidate.is_absolute():
                rel = candidate.resolve(strict=False).relative_to(probe)
            else:
                rel = _relative_in_project(probe, cleaned)
        except ValueError:
            last_outside = True
            continue
        except ProjectSearchError:
            continue
        if (probe / rel).is_file():
            root = probe
            break
    if root is None or rel is None:
        from types import SimpleNamespace

        from navin.webui.file_preview import _is_bare_file_name, _resolve_unique_basename

        if _is_bare_file_name(cleaned):
            for probe in roots:
                found = _resolve_unique_basename(
                    cleaned,
                    scope=SimpleNamespace(project_path=probe),
                )
                if found is None:
                    continue
                try:
                    rel = found.relative_to(probe)
                except ValueError:
                    continue
                root = probe
                break
    if root is None or rel is None:
        if last_outside and candidate.is_absolute():
            raise ProjectSearchError("file outside project", status=403)
        raise ProjectSearchError("file not found", status=404)

    abs_path = root / rel

    try:
        line_count = sum(1 for _ in abs_path.open("rb"))
    except OSError as exc:
        raise ProjectSearchError(f"cannot read file: {exc}", status=400) from exc
    if line_count > _MAX_BLAME_LINES:
        return {
            "path": rel.as_posix(),
            "items": [],
            "total": 0,
            "available": True,
            "reason": f"file too large for blame ({line_count} lines)",
        }

    argv = _git_argv(root)
    if argv is None:
        return {
            "path": rel.as_posix(),
            "items": [],
            "total": 0,
            "available": False,
            "reason": "git unavailable",
        }
    try:
        out = subprocess.run(  # noqa: S603
            [*argv, "blame", "--line-porcelain", "--", rel.as_posix()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_BLAME_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectSearchError(
            f"git blame timed out after {_GIT_BLAME_TIMEOUT_S}s", status=504
        ) from exc
    except (OSError, subprocess.SubprocessError):
        return {
            "path": rel.as_posix(),
            "items": [],
            "total": 0,
            "available": False,
            "reason": "git blame failed",
        }

    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        reason = detail[0] if detail else "not blamed"
        # Untracked / outside git history is a normal empty result, not an error.
        return {
            "path": rel.as_posix(),
            "items": [],
            "total": 0,
            "available": True,
            "reason": reason[:200],
        }

    items = _parse_blame_porcelain(out.stdout)
    return {
        "path": rel.as_posix(),
        "items": items,
        "total": len(items),
        "available": True,
        "reason": "",
    }


def _parse_blame_porcelain(text: str) -> list[dict[str, Any]]:
    """Parse ``git blame --line-porcelain`` into 1-based line entries."""
    items: list[dict[str, Any]] = []
    commit = ""
    author = ""
    email = ""
    date = ""
    summary = ""
    line_no = 0
    for raw in text.splitlines():
        if raw.startswith("\t"):
            if line_no > 0 and commit:
                items.append(
                    {
                        "line": line_no,
                        "commit": commit[:12],
                        "author": author,
                        "email": email,
                        "date": date,
                        "summary": summary,
                    }
                )
            continue
        if not raw:
            continue
        # Header line: <40-hex> <old> <new> [<num>]
        if len(raw) >= 40 and all(ch in "0123456789abcdef" for ch in raw[:40]):
            parts = raw.split()
            commit = parts[0]
            try:
                line_no = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                line_no = 0
            continue
        if raw.startswith("author "):
            author = raw[7:]
        elif raw.startswith("author-mail "):
            email = raw[12:].strip().strip("<>")
        elif raw.startswith("author-time "):
            try:
                ts = int(raw[12:].strip())
                date = (
                    datetime.fromtimestamp(ts, tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except ValueError:
                date = ""
        elif raw.startswith("summary "):
            summary = raw[8:]
    return items


# The timeline graph reads every branch; keep the payload bounded so a huge
# monorepo history stays a quick request rather than a megabyte dump.
_GIT_LOG_DEFAULT = 200
_GIT_LOG_MAX = 500


def git_log_payload(scope: WorkspaceScope, limit: int = _GIT_LOG_DEFAULT) -> dict[str, Any]:
    """Commit history across all branches for the source-control timeline.

    Returns topologically ordered commits with their parent hashes (so the UI
    can draw the graph lanes and merge relations), the decorating refs, every
    local/remote branch tip, and a per-commit ``pushed`` flag computed from
    remote reachability.
    """
    root = _root(scope)
    limit = max(1, min(limit, _GIT_LOG_MAX))
    fmt = "%H%x1f%h%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%D%x1f%s"
    out = _run_git(
        root, "log", "--all", "--topo-order", f"--max-count={limit}",
        f"--pretty=format:{fmt}",
    )
    if out is None:
        return {"available": False, "is_repo": False, "commits": [], "branches": []}
    if out.returncode != 0:
        return {"available": True, "is_repo": False, "commits": [], "branches": []}

    # Everything reachable from a remote ref counts as pushed; the rest is
    # local-only work the UI should flag.
    remote = _run_git(root, "rev-list", "--remotes", f"--max-count={limit * 4}")
    pushed_set = (
        set(remote.stdout.split())
        if remote is not None and remote.returncode == 0
        else set()
    )

    commits: list[dict[str, Any]] = []
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 8:
            continue
        full, short, parents, author, email, date, refs_raw, subject = parts
        refs: list[str] = []
        for token in refs_raw.split(","):
            name = token.strip()
            if not name or name == "HEAD":
                continue
            refs.append(name.removeprefix("HEAD -> "))
        commits.append({
            "hash": full,
            "short": short,
            "parents": parents.split(),
            "author": author,
            "email": email,
            "date": date,
            "refs": refs,
            "subject": subject,
            "pushed": full in pushed_set,
        })

    branches: list[dict[str, Any]] = []
    refs_out = _run_git(
        root, "for-each-ref", "refs/heads", "refs/remotes",
        "--sort=-committerdate",
        "--format=%(refname)%1f%(refname:short)%1f%(objectname)%1f"
        "%(committerdate:iso8601-strict)%1f%(authorname)%1f%(symref)",
    )
    if refs_out is not None and refs_out.returncode == 0:
        for line in refs_out.stdout.splitlines():
            parts = line.split("\x1f")
            # Skip symbolic refs such as origin/HEAD, which merely alias a branch.
            if len(parts) != 6 or parts[5]:
                continue
            branches.append({
                "name": parts[1],
                "tip": parts[2],
                "date": parts[3],
                "author": parts[4],
                "remote": parts[0].startswith("refs/remotes/"),
            })

    head = _run_git(root, "rev-parse", "HEAD")
    total = _run_git(root, "rev-list", "--all", "--count")
    return {
        "available": True,
        "is_repo": True,
        "commits": commits,
        "branches": branches,
        "head": head.stdout.strip() if head is not None and head.returncode == 0 else "",
        "total": (
            int(total.stdout.strip())
            if total is not None and total.returncode == 0 and total.stdout.strip().isdigit()
            else len(commits)
        ),
        **_branch_state(root),
    }


def git_commit_detail_payload(scope: WorkspaceScope, raw_ref: str | None) -> dict[str, Any]:
    """Full message and per-file additions/deletions for one commit."""
    root = _root(scope)
    ref = (raw_ref or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", ref):
        raise ProjectSearchError("invalid commit reference", status=400)

    out = _run_git(
        root, "show", ref, "--numstat",
        "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%cI%x1f%B%x1e",
    )
    if out is None:
        raise ProjectSearchError("git is not available on this host", status=503)
    if out.returncode != 0:
        raise ProjectSearchError("commit not found", status=404)

    header, _, rest = out.stdout.partition("\x1e")
    parts = header.split("\x1f")
    if len(parts) != 7:
        raise ProjectSearchError("unexpected git output", status=500)

    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for line in rest.splitlines():
        cols = line.strip().split("\t")
        if len(cols) != 3 or not cols[2]:
            continue
        add = int(cols[0]) if cols[0].isdigit() else 0
        rem = int(cols[1]) if cols[1].isdigit() else 0
        additions += add
        deletions += rem
        files.append({
            "path": cols[2],
            "additions": add,
            "deletions": rem,
            "binary": cols[0] == "-",
        })

    return {
        "hash": parts[0],
        "author": parts[1],
        "email": parts[2],
        "date": parts[3],
        "committer": parts[4],
        "committed_date": parts[5],
        "message": parts[6].strip(),
        "files": files,
        "additions": additions,
        "deletions": deletions,
    }


def _relative_in_project(root: Path, raw: str | None) -> Path:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ProjectSearchError("missing file")
    target = (root / cleaned).resolve(strict=False)
    try:
        return target.relative_to(root)
    except ValueError as exc:
        raise ProjectSearchError("file outside project", status=403) from exc
