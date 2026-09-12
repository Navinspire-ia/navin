# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep code edits and their validation together across execution slices."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from navin.quality.evidence import VerificationEvidence
from navin.utils.proc import no_window_kwargs

_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
    ".vue", ".svelte", ".go", ".rs", ".java", ".kt", ".kts", ".cs", ".fs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".m", ".mm", ".rb", ".php",
    ".ex", ".exs", ".erl", ".hs", ".scala", ".clj", ".dart", ".lua", ".sql",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".r", ".jl", ".pl",
})
_CHECK_SUFFIXES = _CODE_SUFFIXES | {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".css", ".scss",
    ".sass", ".less", ".graphql", ".proto", ".tf", ".hcl",
}
_CHECK_FILENAMES = frozenset({"dockerfile", "makefile", "cmakelists.txt", "justfile"})
_EDIT_TOOLS = frozenset({"apply_patch", "edit_file", "write_file", "manage_files"})
_NO_TESTS = re.compile(
    r"no tests (?:were )?(?:ran|found|collected|to run)|(?:collected|ran) 0 (?:items|tests)"
    r"|\b0 passed\b|\[no test files\]|^# pass 0\b",
    re.IGNORECASE | re.MULTILINE,
)
_SESSION_ID = re.compile(r"\bsession_id:\s*([\w.-]+)")
_EXIT_CODE = re.compile(r"\bExit code: (-?\d+)")
_GENERATED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".navin", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", ".cache", ".turbo", "dist", "build", "target", "vendor",
})


def workspace_code_snapshot(root: Path) -> dict[str, tuple[int, int] | None]:
    """Observe shell/delegated edits without reading file contents or dependencies.

    Git supplies its ignore rules when available. New projects also work before
    git init. The runner calls this off the event loop and only around tools
    that can edit arbitrary paths, not on each read in a long audit.
    """
    paths = None
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
            cwd=root, capture_output=True, timeout=5, **no_window_kwargs(),
        )
        if result.returncode == 0:
            paths = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
    except (OSError, subprocess.TimeoutExpired):
        pass
    if paths is None:
        paths = []
        for current, directories, names in os.walk(root):
            directories[:] = [name for name in directories if name not in _GENERATED_DIRS]
            paths.extend(str((Path(current) / name).relative_to(root)) for name in names)
    snapshot = {}
    for raw in paths:
        path = raw.replace("\\", "/")
        if development_path(path) and not _GENERATED_DIRS.intersection(PurePosixPath(path).parts):
            snapshot[path] = _file_stamp(root / raw)
    return snapshot


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def edit_paths(params: dict[str, Any]) -> set[str]:
    paths = set()
    for key in ("path", "file_path", "source", "destination", "src", "dst"):
        if isinstance(params.get(key), str):
            paths.add(params[key].replace("\\", "/"))
    for value in params.get("paths", []) or []:
        if isinstance(value, str):
            paths.add(value.replace("\\", "/"))
    for edit in params.get("edits", []) or []:
        if isinstance(edit, dict):
            paths.update(edit_paths(edit))
    return paths


def development_path(path: str) -> bool:
    candidate = PurePosixPath(path.replace("\\", "/").lower())
    return candidate.suffix in _CHECK_SUFFIXES or candidate.name in _CHECK_FILENAMES


def _legacy_evidence(name: str, params: dict[str, Any], text: str) -> VerificationEvidence:
    """Compatibility for custom quality tools that still return plain strings.

    Built-in tools supply structured evidence, including an explicit empty
    result when no checks ran. A generic PASS here never proves tests ran.
    """
    allowed = {
        "verify": {"check", "fix"},
        "lint": {"file", "changed", "project", "fix"},
        "test_run": {"run"},
    }
    if params.get("action") not in allowed.get(name, set()):
        return VerificationEvidence()
    if _NO_TESTS.search(text):
        return VerificationEvidence(summary=text[:800])
    failed = re.search(r"\bFAIL(?:ED|URE|URES)?\b|\b[1-9]\d* (?:failed|error)", text, re.I)
    passed = re.search(r"\b(?:PASS|CLEAN|OK)\b|\b[1-9]\d* passed\b", text, re.I)
    outcome = False if failed else True if passed else None
    if name == "test_run":
        return VerificationEvidence(tests_ok=outcome, summary=text[:800])
    return VerificationEvidence(checks_ok=outcome, summary=text[:800])


@dataclass(slots=True)
class CodeValidationState:
    """Small, request-local evidence record, independent of transcript compaction."""

    paths: set[str] = field(default_factory=set)
    revision: int = 0
    needs_tests: bool = False
    checks_revision: int = -1
    tests_revision: int = -1
    check_failure: str = ""
    test_failure: str = ""
    # A running test validates the revision at launch, not subsequent edits.
    pending_tests: dict[str, int] = field(default_factory=dict)
    last_nudge_key: tuple[Any, ...] | None = None
    ignored_nudges: int = 0
    workspace_snapshot: dict[str, tuple[int, int] | None] | None = None

    def observe_workspace(self, snapshot: dict[str, tuple[int, int] | None]) -> None:
        before = self.workspace_snapshot
        self.workspace_snapshot = snapshot
        if before is None:
            return
        changed = {path for path in before.keys() | snapshot.keys() if before.get(path) != snapshot.get(path)}
        if changed:
            self.edited(changed, require_tests=any(
                PurePosixPath(path.lower()).suffix in _CODE_SUFFIXES for path in changed
            ))

    def sync_known_edits(self, root: Path, paths: set[str]) -> None:
        """File-tool edits were already observed; do not rediscover them as new."""
        if self.workspace_snapshot is None:
            return
        for path in paths:
            candidate = Path(path)
            if candidate.is_absolute():
                try:
                    path = candidate.relative_to(root).as_posix()
                except ValueError:
                    continue
            if development_path(path):
                self.workspace_snapshot[path] = _file_stamp(root / path)

    def edited(self, paths: set[str], *, require_tests: bool) -> None:
        self.paths.update(paths)
        self.revision += 1
        self.needs_tests |= require_tests

    def record(self, evidence: VerificationEvidence, *, revision: int | None = None) -> None:
        checked_revision = self.revision if revision is None else revision
        for kind, ok in (("checks", evidence.checks_ok), ("tests", evidence.tests_ok)):
            if ok is None:
                continue
            failure_attr = "check_failure" if kind == "checks" else "test_failure"
            if ok:
                # An old test completing after an edit cannot clear a new failure.
                if checked_revision == self.revision:
                    setattr(self, f"{kind}_revision", checked_revision)
                    setattr(self, failure_attr, "")
            else:
                setattr(self, f"{kind}_revision", -1)
                setattr(self, failure_attr, evidence.summary)

    def observe(
        self, name: str, params: dict[str, Any], result: Any, *, status: str,
        require_verify: bool, validate_code: bool,
        is_test_command: Callable[[str], bool],
        execution_revision: int | None = None,
    ) -> None:
        action = params.get("action")
        mutation = name in _EDIT_TOOLS and not params.get("dry_run")
        if name == "manage_files" and action in {"mkdir", "list", "stat"}:
            mutation = False
        if name == "lsp" and action == "rename" and params.get("apply"):
            mutation = True
        if name in {"verify", "lint"} and action in {"fix", "rollback"}:
            mutation = True
        if status == "ok" and mutation:
            paths = edit_paths(params)
            relevant = {path for path in paths if development_path(path)}
            if require_verify or (validate_code and relevant) or (self.revision and name in {"verify", "lint"}):
                self.edited(paths, require_tests=validate_code and any(
                    PurePosixPath(path.lower()).suffix in _CODE_SUFFIXES for path in relevant
                ))

        evidence = getattr(result, "verification", None)
        if isinstance(evidence, VerificationEvidence):
            self.record(evidence)
        elif name in {"verify", "lint", "test_run"}:
            self.record(_legacy_evidence(name, params, str(result or "")))

        if name not in {"exec", "write_stdin"}:
            return
        text = str(result or "")
        revision = self.revision if execution_revision is None else execution_revision
        if name == "exec":
            command = str(params.get("command") or params.get("cmd") or "")
            if not is_test_command(command):
                return
            session_match = _SESSION_ID.search(text)
            if session_match:
                self.pending_tests[session_match[1]] = revision
        else:
            session_id = str(params.get("session_id") or "")
            if session_id not in self.pending_tests:
                return
            revision = self.pending_tests[session_id]
        codes = _EXIT_CODE.findall(text)
        if not codes:
            return
        if name == "write_stdin":
            self.pending_tests.pop(session_id, None)
        ok = codes[-1] == "0" and status == "ok"
        skipped_only = re.search(r"\b[1-9]\d* skipped\b", text) and not re.search(r"\b[1-9]\d* (?:passed|passing)\b", text)
        if ok and (_NO_TESTS.search(text) or skipped_only):
            return
        self.record(VerificationEvidence(tests_ok=ok, summary=text[-800:]), revision=revision)

    @property
    def pending(self) -> bool:
        if not self.revision:
            return False
        return bool(
            self.check_failure or self.test_failure
            or max(self.checks_revision, self.tests_revision) != self.revision
            or (self.needs_tests and self.tests_revision != self.revision)
        )

    @property
    def failed(self) -> bool:
        return bool(self.check_failure or self.test_failure)

    def missing(self) -> str:
        details = []
        if self.check_failure:
            details.append(self.check_failure)
        if self.test_failure:
            details.append(self.test_failure)
        if self.needs_tests and self.tests_revision != self.revision:
            details.append("Run meaningful tests for the requested behavior after the latest code/test edits.")
        elif max(self.checks_revision, self.tests_revision) != self.revision:
            details.append("Run an appropriate check after the latest edits.")
        if self.paths:
            details.append("Changed files: " + ", ".join(sorted(self.paths)[:12]))
        return "\n".join(details)

    def nudge(self) -> int:
        # Repair edits reopen a full opportunity to validate. Repeating a
        # final answer with the same missing evidence is the only capped loop.
        key = (self.revision, self.checks_revision, self.tests_revision,
               bool(self.check_failure), bool(self.test_failure))
        self.ignored_nudges = self.ignored_nudges + 1 if key == self.last_nudge_key else 1
        self.last_nudge_key = key
        return self.ignored_nudges
