"""The verification loop: lint, test, auto-fix, and roll back.

This is what turns "the agent edited files" into "the agent knows whether the
edit is safe". One call lints exactly what changed, runs the test suite, and
returns a verdict plus the concrete next action. Snapshots taken before a change
make rollback a single deterministic call instead of a manual restore.

Snapshots live outside the project, keyed by content, so rolling back never
depends on git state or on the session that produced the edit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.quality.linters import (
    Diagnostic,
    LinterResult,
    fix_file,
    fixable_extensions,
    lint_file,
    lint_project,
)
from navin.quality.testing import TestOutcome, run_tests
from navin.utils.proc import no_window_kwargs

_GIT_TIMEOUT_S = 20
_MAX_CHANGED_FILES = 200
_SNAPSHOT_KEEP = 40

VERDICT_CLEAN = "clean"
VERDICT_LINT_ERRORS = "lint_errors"
VERDICT_TEST_FAILURES = "test_failures"
VERDICT_LINT_WARNINGS = "lint_warnings"
VERDICT_NO_CHANGES = "no_changes"


@dataclass(slots=True)
class VerificationReport:
    """Outcome of one verification pass."""

    verdict: str
    changed_paths: list[str] = field(default_factory=list)
    lint_results: list[LinterResult] = field(default_factory=list)
    test_outcomes: list[TestOutcome] = field(default_factory=list)
    duration_ms: int = 0
    snapshot_id: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def lint_errors(self) -> list[Diagnostic]:
        return [
            d
            for result in self.lint_results
            for d in result.diagnostics
            if d.severity == "error"
        ]

    @property
    def lint_warnings(self) -> list[Diagnostic]:
        return [
            d
            for result in self.lint_results
            for d in result.diagnostics
            if d.severity == "warning"
        ]

    @property
    def failed_tests(self) -> int:
        return sum(outcome.failed for outcome in self.test_outcomes)

    @property
    def ok(self) -> bool:
        return self.verdict in {VERDICT_CLEAN, VERDICT_LINT_WARNINGS, VERDICT_NO_CHANGES}

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "ok": self.ok,
            "changed_paths": self.changed_paths,
            "lint": [r.to_json() for r in self.lint_results],
            "tests": [o.to_json() for o in self.test_outcomes],
            "duration_ms": self.duration_ms,
            "snapshot_id": self.snapshot_id,
            "notes": self.notes,
        }

    def render(self) -> str:
        headline = {
            VERDICT_CLEAN: "PASS - no lint errors, tests green",
            VERDICT_LINT_WARNINGS: "PASS with warnings - no errors, tests green",
            VERDICT_LINT_ERRORS: "FAIL - lint errors must be fixed",
            VERDICT_TEST_FAILURES: "FAIL - tests are failing",
            VERDICT_NO_CHANGES: "Nothing to verify - no modified files detected",
        }.get(self.verdict, self.verdict)
        lines = [headline, ""]

        if self.changed_paths:
            shown = self.changed_paths[:20]
            lines.append(f"Changed files ({len(self.changed_paths)}):")
            lines.extend(f"  {path}" for path in shown)
            if len(self.changed_paths) > len(shown):
                lines.append(f"  … {len(self.changed_paths) - len(shown)} more")
            lines.append("")

        errors, warnings = self.lint_errors, self.lint_warnings
        skipped = [r for r in self.lint_results if not r.ran and r.skipped_reason]
        if errors or warnings:
            lines.append(f"Lint: {len(errors)} error(s), {len(warnings)} warning(s)")
            for diagnostic in errors[:20]:
                lines.append(f"  {diagnostic.render()}")
            for diagnostic in warnings[:10]:
                lines.append(f"  {diagnostic.render()}")
            if len(errors) > 20 or len(warnings) > 10:
                lines.append("  … more diagnostics omitted")
        elif self.lint_results:
            ran = sorted({r.linter for r in self.lint_results if r.ran})
            lines.append(f"Lint: clean ({', '.join(ran) if ran else 'no linter ran'})")
        if skipped:
            reasons = sorted({f"{r.linter} ({r.skipped_reason})" for r in skipped})
            lines.append(f"  not run: {', '.join(reasons[:4])}")
        lines.append("")

        if self.test_outcomes:
            lines.append("Tests:")
            for outcome in self.test_outcomes:
                lines.append(f"  {outcome.summary()}")
                for failure in outcome.failures[:10]:
                    lines.append(f"    - {failure.render()}")
        else:
            lines.append("Tests: not run")

        if self.notes:
            lines.append("")
            lines.extend(self.notes)

        lines.append("")
        lines.append(f"Next: {self.recommendation()}")
        return "\n".join(lines)

    def recommendation(self) -> str:
        if self.verdict == VERDICT_LINT_ERRORS:
            fixable = [
                d for d in self.lint_errors
                if Path(d.path).suffix.lower() in fixable_extensions()
            ]
            if fixable:
                return (
                    "fix the errors above; many are auto-fixable with "
                    "verify action=fix, then re-run verify action=check"
                )
            return "fix the lint errors above, then re-run verify action=check"
        if self.verdict == VERDICT_TEST_FAILURES:
            base = "fix the failing tests above, then re-run verify action=check"
            if self.snapshot_id:
                return (
                    f"{base}. If the change cannot be salvaged, restore with "
                    f"verify action=rollback snapshot_id={self.snapshot_id}"
                )
            return (
                f"{base}. If the change cannot be salvaged, discard it with "
                "verify action=rollback (restores tracked files from git)"
            )
        if self.verdict == VERDICT_LINT_WARNINGS:
            return "safe to keep; address warnings if they are in code you touched"
        if self.verdict == VERDICT_NO_CHANGES:
            return "make a change first, or pass paths explicitly"
        return "safe to keep"


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


_NOISE_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".class", ".o", ".a",
    ".log", ".lock", ".map", ".min.js", ".snap", ".tsbuildinfo",
})


def _is_verifiable(rel: str) -> bool:
    """Filter build artifacts: verifying them is noise, never signal."""
    from navin.index.store import SKIP_DIRS

    parts = rel.split("/")
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return False
    return Path(rel).suffix.lower() not in _NOISE_SUFFIXES


def changed_files(root: Path) -> list[str]:
    """Modified and untracked source files, via git.

    Build artifacts are filtered out: they are never what a verification pass
    should lint or reason about.
    """
    git = shutil.which("git")
    if git is None:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    out: list[str] = []
    for entry in completed.stdout.split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:]
        # Renames encode "old -> new"; keep the destination.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and _is_verifiable(path) and (root / path).is_file():
            out.append(path)
        if len(out) >= _MAX_CHANGED_FILES:
            break
    return out


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _snapshot_dir() -> Path:
    from navin.config.loader import get_config_path

    return get_config_path().parent / "snapshots"


def create_snapshot(root: Path, paths: list[str] | None = None) -> dict[str, Any]:
    """Store the current contents of ``paths`` so they can be restored later."""
    targets = paths if paths is not None else changed_files(root)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(f"{root}{stamp}{targets}".encode()).hexdigest()[:8]
    snapshot_id = f"{stamp}-{digest}"

    # Snapshots are stored as raw bytes rather than decoded text. A rollback has
    # to put the file back exactly as it was, and decoding first would lose the
    # CRLF endings, the byte-order mark and the original codec of any file that
    # is not plain UTF-8, restoring a subtly different file while reporting
    # success.
    files: dict[str, str | None] = {}
    for rel in targets:
        full = root / rel
        try:
            files[rel] = base64.b64encode(full.read_bytes()).decode("ascii")
        except OSError:
            # Unreadable or absent: record absence so restore deletes nothing.
            files[rel] = None

    directory = _snapshot_dir()
    payload = {
        "id": snapshot_id,
        "root": str(root),
        "created_at": time.time(),
        "encoding": "base64",
        "files": files,
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{snapshot_id}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        _prune_snapshots(directory)
    except OSError as exc:
        return {"id": "", "error": str(exc), "files": len(files)}
    return {"id": snapshot_id, "files": len([f for f in files.values() if f is not None])}


def _prune_snapshots(directory: Path) -> None:
    with suppress(OSError):
        entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for stale in entries[:-_SNAPSHOT_KEEP]:
            stale.unlink(missing_ok=True)


def list_snapshots(root: Path) -> list[dict[str, Any]]:
    directory = _snapshot_dir()
    out: list[dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("root")) != str(root):
            continue
        files = payload.get("files") or {}
        out.append(
            {
                "id": str(payload.get("id", path.stem)),
                "created_at": payload.get("created_at"),
                "files": len(files),
                "paths": sorted(files)[:10],
            }
        )
    return out


def restore_from_git(root: Path, paths: list[str] | None = None) -> dict[str, Any]:
    """Discard working-tree changes for tracked files, using git as the baseline.

    This is the zero-setup rollback path: no snapshot has to have been taken,
    because git already holds the pre-change content. Untracked files are left
    alone - deleting them is not something a rollback should decide.
    """
    git = shutil.which("git")
    if git is None:
        return {"restored": 0, "error": "git is not available"}
    targets = paths if paths is not None else changed_files(root)
    if not targets:
        return {"restored": 0, "skipped": [], "note": "no modified files to restore"}

    tracked: list[str] = []
    untracked: list[str] = []
    for rel in targets:
        try:
            probe = subprocess.run(  # noqa: S603
                [git, "-C", str(root), "ls-files", "--error-unmatch", rel],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_GIT_TIMEOUT_S,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            untracked.append(rel)
            continue
        (tracked if probe.returncode == 0 else untracked).append(rel)

    if not tracked:
        return {
            "restored": 0,
            "skipped": untracked,
            "error": "none of these files are tracked by git, so git has no "
            "baseline to restore; use a snapshot instead",
        }
    try:
        completed = subprocess.run(  # noqa: S603
            [git, "-C", str(root), "checkout", "--", *tracked],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"restored": 0, "error": str(exc)}
    if completed.returncode != 0:
        return {
            "restored": 0,
            "error": (completed.stderr or "git checkout failed").strip()[:300],
        }
    return {"restored": len(tracked), "paths": tracked, "skipped": untracked}


def restore_snapshot(root: Path, snapshot_id: str) -> dict[str, Any]:
    """Restore files captured in ``snapshot_id``. Returns what changed."""
    path = _snapshot_dir() / f"{snapshot_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"restored": 0, "error": f"snapshot not found or unreadable: {exc}"}
    if str(payload.get("root")) != str(root):
        return {"restored": 0, "error": "snapshot belongs to a different project"}

    # Snapshots written before contents were stored as bytes hold plain text.
    is_base64 = payload.get("encoding") == "base64"
    restored: list[str] = []
    failed: list[str] = []
    for rel, content in (payload.get("files") or {}).items():
        if content is None:
            continue
        target = root / str(rel)
        try:
            data = (
                base64.b64decode(str(content))
                if is_base64
                else str(content).encode("utf-8")
            )
        except (ValueError, TypeError):
            failed.append(str(rel))
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            restored.append(str(rel))
        except OSError:
            failed.append(str(rel))
    return {"restored": len(restored), "paths": restored, "failed": failed}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def _verdict_for(
    lint_results: list[LinterResult],
    test_outcomes: list[TestOutcome],
    changed: list[str],
) -> str:
    if not changed:
        return VERDICT_NO_CHANGES
    if any(d.severity == "error" for r in lint_results for d in r.diagnostics):
        return VERDICT_LINT_ERRORS
    if any(outcome.ran and not outcome.ok for outcome in test_outcomes):
        return VERDICT_TEST_FAILURES
    if any(d.severity == "warning" for r in lint_results for d in r.diagnostics):
        return VERDICT_LINT_WARNINGS
    return VERDICT_CLEAN


def verify_changes(
    root: Path,
    paths: list[str] | None = None,
    *,
    with_tests: bool = True,
    with_project_lint: bool = False,
    test_target: str | None = None,
    auto_fix: bool = False,
) -> VerificationReport:
    """Lint what changed, run the tests, and return a verdict.

    ``auto_fix`` applies safe linter fixes first, which resolves the mechanical
    problems (imports, formatting, obvious lint) before reporting, so the agent
    only sees issues that need real judgment.
    """
    started = time.monotonic()
    changed = paths if paths is not None else changed_files(root)
    notes: list[str] = []

    if auto_fix and changed:
        fixed: list[str] = []
        for rel in changed:
            for result in fix_file(root, rel):
                if result.ran:
                    fixed.append(f"{rel} ({result.linter})")
        if fixed:
            notes.append(f"Auto-fix applied to {len(fixed)} file(s).")

    lint_results: list[LinterResult] = []
    for rel in changed:
        lint_results.extend(lint_file(root, rel))

    if with_project_lint:
        project_results = lint_project(root)
        changed_set = set(changed)
        for result in project_results:
            # A project-wide type check reports the whole repo; keep the
            # diagnostics that belong to files this change touched.
            result.diagnostics = [
                d for d in result.diagnostics if d.path in changed_set
            ]
            lint_results.append(result)

    test_outcomes: list[TestOutcome] = []
    if with_tests:
        test_outcomes = run_tests(root, target=test_target)

    report = VerificationReport(
        verdict=_verdict_for(lint_results, test_outcomes, changed),
        changed_paths=changed,
        lint_results=lint_results,
        test_outcomes=test_outcomes,
        duration_ms=int((time.monotonic() - started) * 1000),
        notes=notes,
    )
    if not changed and paths is None and not shutil.which("git"):
        report.notes.append(
            "git is unavailable, so changed files could not be detected - "
            "pass paths explicitly."
        )
    return report
