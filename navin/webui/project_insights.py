# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project insight helpers for the Dev status bar: git status and file diagnostics.

Git information is read with the ``git`` CLI (porcelain v2, stable output).
Diagnostics are delegated to :mod:`navin.quality.linters`, which runs whichever
linters the project actually has (ruff, ESLint, shellcheck, yamllint, language
syntax checks…) from a declarative table, and normalizes their output into one
shape for the editor gutter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from navin.utils.git_argv import git_argv
from navin.utils.proc import no_window_kwargs

# Generous enough for wsl.exe to wake a cold distribution service; a plain
# local git answers in milliseconds either way.
_GIT_TIMEOUT_S = 10
_RUFF_TIMEOUT_S = 20
_MAX_DIAGNOSTICS = 200
_MAX_WORKSPACE_DIAGNOSTICS = 500


class ProjectInsightsError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _check_dir(raw_path: str) -> Path:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise ProjectInsightsError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise ProjectInsightsError("path must be absolute")
    if not path.is_dir():
        raise ProjectInsightsError("not a directory", status=404)
    return path


def _git_argv(path: Path) -> list[str] | None:
    """The argv prefix that runs git *where the project lives*.

    Thin alias over :func:`navin.utils.git_argv.git_argv`, kept because tests
    and callers in this module reach for the private name.
    """
    return git_argv(path)


def git_status_payload(raw_path: str) -> dict[str, Any]:
    """Branch / dirty / ahead-behind summary for the project root."""
    path = _check_dir(raw_path)
    argv = _git_argv(path)
    if argv is None:
        return {"available": False, "is_repo": False}
    try:
        out = subprocess.run(  # noqa: S603
            [*argv, "status", "--porcelain=v2", "--branch"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": True, "is_repo": False}
    if out.returncode != 0:
        return {"available": True, "is_repo": False}

    branch = ""
    ahead = behind = 0
    staged = unstaged = untracked = 0
    for line in out.stdout.splitlines():
        if line.startswith("# branch.head "):
            branch = line.removeprefix("# branch.head ").strip()
        elif line.startswith("# branch.ab "):
            parts = line.removeprefix("# branch.ab ").split()
            for part in parts:
                if part.startswith("+"):
                    ahead = int(part[1:] or 0)
                elif part.startswith("-"):
                    behind = int(part[1:] or 0)
        elif line.startswith(("1 ", "2 ")):
            xy = line.split(" ", 2)[1]
            if len(xy) == 2:
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    unstaged += 1
        elif line.startswith("? "):
            untracked += 1

    return {
        "available": True,
        "is_repo": True,
        "branch": branch,
        "detached": branch == "(detached)",
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "dirty": (staged + unstaged + untracked) > 0,
    }


_ROOT_MARKERS = (
    ".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
    "setup.py", "setup.cfg", "Gemfile", "composer.json",
)


def project_root_for(path: Path) -> Path:
    """Nearest ancestor that looks like a project root.

    Linters need this: ESLint and type checkers resolve their configuration
    relative to the project, and diagnostics are reported project-relative.
    """
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return start


def _semantic_diagnostics(root: Path, rel: str) -> tuple[list[dict[str, Any]], str]:
    """Language-server diagnostics, when a server is installed for this file.

    These are the errors a linter cannot see: wrong types, unknown attributes,
    bad call signatures. Absence of a server is normal, not a failure, so any
    problem here degrades to "no semantic diagnostics" rather than an error.
    """
    try:
        from navin.lsp import LspManager
        from navin.lsp.manager import servers_for
        from navin.quality.linters import tool_argv

        suffix = Path(rel).suffix.lower()
        if not any(tool_argv(spec, root) for _, spec in servers_for(suffix)):
            return [], ""
        rows = LspManager.for_root(root).diagnostics(rel, wait_s=2.5)
    except Exception:
        return [], ""
    out: list[dict[str, Any]] = []
    tool = ""
    for row in rows:
        tool = tool or str(row.get("tool", ""))
        out.append(
            {
                "line": int(row.get("line", 1)),
                "col": int(row.get("col", 1)),
                "end_line": int(row.get("end_line", 1)),
                "end_col": int(row.get("end_col", 1)),
                "severity": str(row.get("severity", "error")),
                "code": str(row.get("code", "")),
                "message": str(row.get("message", "")),
                "tool": str(row.get("tool", "lsp")),
            }
        )
    return out, tool


_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2, "hint": 3}


def _deduplicate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same problem reported by several tools.

    A language server and a linter routinely flag one issue at the same spot
    (an undefined name, say). Showing it twice in the gutter is noise, so keep
    the most severe report per position and wording.
    """
    best: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in rows:
        # Tools word the same finding differently in case and punctuation.
        fingerprint = "".join(
            ch for ch in str(row.get("message", "")).lower() if ch.isalnum()
        )
        key = (int(row.get("line", 0)), int(row.get("col", 0)), fingerprint)
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        if _SEVERITY_RANK.get(row["severity"], 9) < _SEVERITY_RANK.get(
            current["severity"], 9
        ):
            best[key] = row
    return list(best.values())


def file_diagnostics_payload(raw_path: str, *, raw_root: str = "") -> dict[str, Any]:
    """Linter diagnostics for one file, from every applicable local linter.

    ``raw_path`` may be relative: editor panes address files by their
    tree-relative path, so a bare name is resolved against ``raw_root`` (the
    project root the caller is browsing) instead of being rejected.
    """
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise ProjectInsightsError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        root_cleaned = (raw_root or "").strip()
        root = Path(root_cleaned).expanduser() if root_cleaned else None
        if root is None or not root.is_absolute():
            raise ProjectInsightsError(
                "relative path needs a root query parameter"
            )
        path = root / path
    if not path.is_file():
        raise ProjectInsightsError("not a file", status=404)

    from navin.quality.linters import lint_file

    root = project_root_for(path)
    try:
        rel = path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        rel = path.name

    results = lint_file(root, rel)
    diagnostics: list[dict[str, Any]] = []
    tools_ran: list[str] = []
    skipped: list[dict[str, str]] = []

    semantic, semantic_tool = _semantic_diagnostics(root, rel)
    if semantic_tool:
        tools_ran.append(semantic_tool)
    diagnostics.extend(semantic)
    for result in results:
        if result.ran:
            tools_ran.append(result.linter)
        elif result.skipped_reason:
            skipped.append({"linter": result.linter, "reason": result.skipped_reason})
        for diagnostic in result.diagnostics:
            # The editor addresses the open buffer, so only this file's
            # diagnostics are relevant even when a linter reports on others.
            if diagnostic.path not in (rel, path.name):
                continue
            diagnostics.append(
                {
                    "line": diagnostic.line,
                    "col": diagnostic.col,
                    "end_line": diagnostic.end_line,
                    "end_col": diagnostic.end_col,
                    "severity": diagnostic.severity,
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "tool": diagnostic.tool,
                }
            )
    diagnostics = _deduplicate(diagnostics)
    diagnostics.sort(key=lambda d: (d["line"], d["col"]))
    del diagnostics[_MAX_DIAGNOSTICS:]

    errors = sum(1 for d in diagnostics if d["severity"] == "error")
    warnings = sum(1 for d in diagnostics if d["severity"] == "warning")
    return {
        "path": str(path),
        "supported": bool(tools_ran),
        "tool": ", ".join(tools_ran),
        "tools": tools_ran,
        "skipped": skipped,
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics,
    }


def workspace_diagnostics_payload(
    raw_root: str,
    *,
    seed_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Project-wide diagnostics from workspace-mode language servers.

    Unlike :func:`file_diagnostics_payload`, this surfaces errors in files the
    editor never opened (the case pyright already computes when
    ``diagnosticMode`` is ``workspace``).
    """
    root = _check_dir(raw_root)
    seed_rels: list[str] = []
    for raw in seed_paths or []:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        candidate = Path(cleaned).expanduser()
        try:
            if candidate.is_absolute():
                seed_rels.append(
                    candidate.resolve(strict=False).relative_to(root).as_posix()
                )
            else:
                seed_rels.append(candidate.as_posix().lstrip("./"))
        except ValueError:
            continue

    rows: list[dict[str, Any]] = []
    try:
        from navin.lsp import LspManager

        rows = LspManager.for_root(root).workspace_diagnostics(
            seed_rels=seed_rels or None,
            wait_s=2.5,
        )
    except Exception:
        rows = []

    files: dict[str, dict[str, Any]] = {}
    flat: list[dict[str, Any]] = []
    for row in rows:
        message = str(row.get("message") or "").strip()
        if not message:
            continue
        abs_path = str(row.get("abs_path") or (root / str(row.get("path") or "")).resolve())
        entry = {
            "line": int(row.get("line", 1) or 1),
            "col": int(row.get("col", 1) or 1),
            "end_line": int(row.get("end_line", 1) or 1),
            "end_col": int(row.get("end_col", 1) or 1),
            "severity": str(row.get("severity") or "error"),
            "code": str(row.get("code") or ""),
            "message": message,
            "tool": str(row.get("tool") or "lsp"),
        }
        bucket = files.setdefault(
            abs_path,
            {
                "path": abs_path,
                "supported": True,
                "tool": entry["tool"],
                "tools": [entry["tool"]] if entry["tool"] else [],
                "skipped": [],
                "errors": 0,
                "warnings": 0,
                "diagnostics": [],
            },
        )
        bucket["diagnostics"].append(entry)
        flat.append({**entry, "path": abs_path})

    for bucket in files.values():
        bucket["diagnostics"] = _deduplicate(bucket["diagnostics"])
        bucket["diagnostics"].sort(key=lambda d: (d["line"], d["col"]))
        del bucket["diagnostics"][_MAX_DIAGNOSTICS:]
        bucket["errors"] = sum(
            1 for d in bucket["diagnostics"] if d["severity"] == "error"
        )
        bucket["warnings"] = sum(
            1 for d in bucket["diagnostics"] if d["severity"] == "warning"
        )
        tools = sorted(
            {
                str(d.get("tool") or "")
                for d in bucket["diagnostics"]
                if d.get("tool")
            }
        )
        bucket["tools"] = tools
        bucket["tool"] = ", ".join(tools)

    flat.sort(
        key=lambda d: (
            0 if d["severity"] == "error" else 1,
            str(d.get("path") or ""),
            int(d.get("line") or 0),
            int(d.get("col") or 0),
        )
    )
    del flat[_MAX_WORKSPACE_DIAGNOSTICS:]

    errors = sum(1 for d in flat if d["severity"] == "error")
    warnings = sum(1 for d in flat if d["severity"] == "warning")
    return {
        "root": str(root),
        "supported": bool(files),
        "errors": errors,
        "warnings": warnings,
        "file_count": len(files),
        "files": files,
        "diagnostics": flat,
    }
