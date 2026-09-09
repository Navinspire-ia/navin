# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project brain: SOUL.md / USER.md / MEMORY.md for the active workspace.

One project path = one shared brain for Code and every studio (Marketing, SEO,
Documents, …). This module ensures the scaffold exists, then reads the durable
files for the Project Home Brain panel.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.utils.helpers import ensure_project_scaffold
from navin.utils.proc import no_window_kwargs

BRAIN_FILES: dict[str, str] = {
    "soul": ".navin/SOUL.md",
    "user": ".navin/USER.md",
    "memory": ".navin/memory/MEMORY.md",
}

CONTINUITY_FILES: dict[str, str] = {
    "resume": ".navin/continuity/RESUME.md",
    "decisions": ".navin/continuity/DECISIONS.md",
}

_MAX_BRAIN_FILE_BYTES = 512 * 1024
_CONSTRAINT_HEADING = re.compile(r"^##\s+Constraints\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}")
# Soft drift matching ignores filler verbs / common English so "touch" does not
# flag every path that happens to contain an unrelated short token.
_DRIFT_STOPWORDS = frozenset(
    {
        "and",
        "any",
        "apis",
        "avoid",
        "change",
        "code",
        "constraint",
        "constraints",
        "delete",
        "do",
        "dont",
        "file",
        "files",
        "for",
        "from",
        "hard",
        "into",
        "keep",
        "make",
        "modify",
        "must",
        "never",
        "not",
        "only",
        "please",
        "public",
        "remove",
        "rule",
        "rules",
        "should",
        "stable",
        "sure",
        "that",
        "the",
        "their",
        "this",
        "touch",
        "update",
        "use",
        "using",
        "with",
        "without",
        "your",
    }
)
_MAX_DRIFT_PATHS = 80
_MAX_DRIFT_VIOLATIONS = 20


class ProjectBrainError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _read_text(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        return "", False
    try:
        if path.stat().st_size > _MAX_BRAIN_FILE_BYTES:
            raise ProjectBrainError(f"file too large: {path.name}", status=413)
        return path.read_text(encoding="utf-8"), True
    except OSError as exc:
        raise ProjectBrainError(f"could not read {path.name}: {exc}", status=500) from exc


def _extract_constraints(memory: str) -> list[str]:
    match = _CONSTRAINT_HEADING.search(memory)
    if not match:
        return []
    rest = memory[match.end() :]
    next_heading = _NEXT_HEADING.search(rest)
    body = rest[: next_heading.start()] if next_heading else rest
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.startswith("*This file"):
            continue
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        elif re.match(r"^\d+[.)]\s+", line):
            line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        if line.startswith("(") and line.endswith(")"):
            continue
        if line.lower().startswith("example:"):
            continue
        if line:
            lines.append(line)
    return lines[:40]


def _constraint_tokens(constraint: str) -> list[str]:
    """Meaningful tokens from a constraint line (best-effort)."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(constraint or ""):
        tok = raw.lower()
        if tok in _DRIFT_STOPWORDS or len(tok) < 4:
            continue
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return tokens


def _run_git(root: Path, *args: str) -> tuple[int, str, str]:
    git = shutil.which("git")
    if not git:
        return 1, "", "git not found"
    try:
        completed = subprocess.run(  # noqa: S603
            [git, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _git_change_context(root: Path) -> dict[str, Any]:
    """Dirty paths, or last-commit paths + message. Never raises."""
    empty = {"paths": [], "source": "none", "commit_message": ""}
    try:
        code, inside, _ = _run_git(root, "rev-parse", "--is-inside-work-tree")
        if code != 0 or inside.strip() != "true":
            return empty

        # -uall expands untracked dirs so "billing" inside src/billing/ is visible.
        code, porcelain, _ = _run_git(
            root, "status", "--porcelain", "--untracked-files=all"
        )
        paths: list[str] = []
        if code == 0 and porcelain.strip():
            for line in porcelain.splitlines():
                raw = line[3:].strip() if len(line) > 3 else line.strip()
                if " -> " in raw:
                    raw = raw.split(" -> ", 1)[-1].strip()
                raw = raw.strip().strip('"').replace("\\", "/")
                if raw and raw not in paths:
                    paths.append(raw)
                if len(paths) >= _MAX_DRIFT_PATHS:
                    break
            if paths:
                return {"paths": paths, "source": "dirty", "commit_message": ""}

        code, names, _ = _run_git(
            root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        )
        if code == 0:
            for line in names.splitlines():
                rel = line.strip().replace("\\", "/")
                if rel and rel not in paths:
                    paths.append(rel)
                if len(paths) >= _MAX_DRIFT_PATHS:
                    break
        code, msg, _ = _run_git(root, "log", "-1", "--pretty=%s")
        commit_message = msg.strip() if code == 0 else ""
        if paths or commit_message:
            return {
                "paths": paths,
                "source": "last_commit",
                "commit_message": commit_message,
            }
        return empty
    except Exception:
        return empty


def detect_constraint_drift(
    constraints: list[str],
    *,
    paths: list[str] | None = None,
    commit_message: str = "",
) -> list[dict[str, Any]]:
    """Soft warnings when changed paths/messages echo constraint keywords.

    Best-effort only: never raises. Each hit is a warning the UI can show;
    nothing here blocks agent runs or board edits.
    """
    violations: list[dict[str, Any]] = []
    path_list = [str(p).replace("\\", "/") for p in (paths or []) if str(p).strip()]
    commit_hay = (commit_message or "").lower()
    for constraint in constraints:
        tokens = _constraint_tokens(constraint)
        if not tokens:
            continue
        matched_paths: list[str] = []
        matched_tokens: list[str] = []
        for path in path_list:
            path_l = path.lower()
            for tok in tokens:
                if tok in path_l:
                    if path not in matched_paths:
                        matched_paths.append(path)
                    if tok not in matched_tokens:
                        matched_tokens.append(tok)
        commit_hits = [tok for tok in tokens if tok in commit_hay]
        for tok in commit_hits:
            if tok not in matched_tokens:
                matched_tokens.append(tok)
        if not matched_paths and not commit_hits:
            continue
        violations.append(
            {
                "constraint": constraint,
                "severity": "warning",
                "paths": matched_paths[:12],
                "tokens": matched_tokens[:12],
                "in_commit_message": bool(commit_hits),
                "detail": (
                    f"Possible constraint drift: {constraint!r} may relate to "
                    + (
                        ", ".join(matched_paths[:3])
                        if matched_paths
                        else "the latest commit message"
                    )
                ),
            }
        )
        if len(violations) >= _MAX_DRIFT_VIOLATIONS:
            break
    return violations


def constraint_drift_payload(
    root: Path,
    constraints: list[str],
) -> dict[str, Any]:
    """Run the detector against the project's git dirty / last-commit context."""
    try:
        ctx = _git_change_context(root)
        violations = detect_constraint_drift(
            constraints,
            paths=list(ctx.get("paths") or []),
            commit_message=str(ctx.get("commit_message") or ""),
        )
        return {
            "supported": True,
            "violations": violations,
            "source": ctx.get("source") or "none",
            "paths_checked": list(ctx.get("paths") or [])[:40],
            "note": (
                "Soft warnings only: dirty or last-commit paths matched "
                "keywords from MEMORY.md Constraints. Review before shipping."
                if violations
                else (
                    "Constraint drift detector is active. No likely violations "
                    "in dirty files or the latest commit."
                )
            ),
        }
    except Exception:
        return {
            "supported": True,
            "violations": [],
            "source": "error",
            "paths_checked": [],
            "note": "Constraint drift check failed softly; constraints still apply.",
        }


def project_brain_payload(scope: WorkspaceScope) -> dict[str, Any]:
    """Ensure scaffold, then return SOUL / USER / MEMORY for the project."""
    root = Path(scope.project_path).expanduser()
    if not root.is_dir():
        raise ProjectBrainError(f"project not found: {root}", status=404)

    created = ensure_project_scaffold(root, silent=True)
    files: dict[str, Any] = {}
    for key, rel in BRAIN_FILES.items():
        path = root / rel
        content, exists = _read_text(path)
        files[key] = {
            "path": rel,
            "exists": exists,
            "content": content,
            "bytes": len(content.encode("utf-8")) if content else 0,
        }

    continuity: dict[str, Any] = {}
    for key, rel in CONTINUITY_FILES.items():
        path = root / rel
        content, exists = _read_text(path)
        continuity[key] = {
            "path": rel,
            "exists": exists,
            "content": content,
            "bytes": len(content.encode("utf-8")) if content else 0,
        }

    pack = {
        "path": ".navin",
        "exists": (root / ".navin" / "project.json").is_file(),
        "manifest": None,
    }
    manifest_path = root / ".navin" / "project.json"
    if manifest_path.is_file():
        try:
            import json

            pack["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pack["manifest"] = None

    memory_content = str(files["memory"]["content"] or "")
    constraints = _extract_constraints(memory_content)

    from navin.agent.project_rules import list_navin_rules

    rules = list_navin_rules(root)
    return {
        "project_path": str(root),
        "project_name": scope.project_name or root.name,
        "created": created,
        "shared": True,
        "files": files,
        "continuity": continuity,
        "pack": pack,
        "constraints": constraints,
        "rules": rules,
        "rules_dir": ".navin/rules",
        "drift": constraint_drift_payload(root, constraints),
    }
