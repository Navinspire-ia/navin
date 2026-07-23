"""Project insight helpers for the Dev status bar: git status and file diagnostics.

Git information is read with the ``git`` CLI (porcelain v2, stable output).
Diagnostics use fast local tools only: ruff for Python (bundled as a navin
dev dependency and commonly on PATH) and the stdlib JSON parser for JSON.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_GIT_TIMEOUT_S = 5
_RUFF_TIMEOUT_S = 20
_MAX_DIAGNOSTICS = 200


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


def git_status_payload(raw_path: str) -> dict[str, Any]:
    """Branch / dirty / ahead-behind summary for the project root."""
    path = _check_dir(raw_path)
    git = shutil.which("git")
    if git is None:
        return {"available": False, "is_repo": False}
    try:
        out = subprocess.run(  # noqa: S603
            [git, "-C", str(path), "status", "--porcelain=v2", "--branch"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
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


def _ruff_binary() -> str | None:
    candidate = Path(sys.executable).parent / "ruff"
    if candidate.is_file():
        return str(candidate)
    return shutil.which("ruff")


def _python_diagnostics(path: Path) -> tuple[bool, str, list[dict[str, Any]]]:
    ruff = _ruff_binary()
    if ruff is None:
        return False, "", []
    try:
        out = subprocess.run(  # noqa: S603
            [ruff, "check", "--output-format", "json", "--no-cache", str(path)],
            capture_output=True,
            text=True,
            timeout=_RUFF_TIMEOUT_S,
        )
        rows = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False, "ruff", []
    diagnostics: list[dict[str, Any]] = []
    for row in rows[:_MAX_DIAGNOSTICS]:
        if not isinstance(row, dict):
            continue
        loc = row.get("location") or {}
        end = row.get("end_location") or {}
        code = str(row.get("code") or "")
        # Syntax errors block execution; everything else is a lint warning.
        severity = (
            "error"
            if code in {"E999", "invalid-syntax", ""} or "syntax" in code
            else "warning"
        )
        diagnostics.append(
            {
                "line": int(loc.get("row") or 1),
                "col": int(loc.get("column") or 1),
                "end_line": int(end.get("row") or loc.get("row") or 1),
                "end_col": int(end.get("column") or loc.get("column") or 1),
                "severity": severity,
                "code": code,
                "message": str(row.get("message") or ""),
            }
        )
    return True, "ruff", diagnostics


def _json_diagnostics(path: Path) -> tuple[bool, str, list[dict[str, Any]]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ProjectInsightsError(f"cannot read file: {exc}", status=404) from exc
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        return True, "json", [
            {
                "line": exc.lineno,
                "col": exc.colno,
                "end_line": exc.lineno,
                "end_col": exc.colno + 1,
                "severity": "error",
                "code": "json",
                "message": exc.msg,
            }
        ]
    return True, "json", []


def file_diagnostics_payload(raw_path: str) -> dict[str, Any]:
    """Fast linter diagnostics for one file, selected by extension."""
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise ProjectInsightsError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise ProjectInsightsError("path must be absolute")
    if not path.is_file():
        raise ProjectInsightsError("not a file", status=404)

    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi"}:
        supported, tool, diagnostics = _python_diagnostics(path)
    elif suffix == ".json":
        supported, tool, diagnostics = _json_diagnostics(path)
    else:
        supported, tool, diagnostics = False, "", []

    errors = sum(1 for d in diagnostics if d["severity"] == "error")
    warnings = len(diagnostics) - errors
    return {
        "path": str(path),
        "supported": supported,
        "tool": tool,
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics,
    }
