"""Per-subagent policy for isolated worktrees: paths, commands, tests, conflicts.

Three small, independent pieces:

* ``WorkspacePolicy``: allowed path globs and allowed command prefixes for one
  subagent, plus the targeted tests to run in its worktree before merging.
* ``pre_merge_gate``: checks the files a worktree changed against the policy
  and runs the policy's tests; returns a blocking note or None.
* ``detect_conflicts``: finds files modified by two or more active worktrees
  ahead of time and renders a short report.

Policies are registered by task id (``set_policy``); a task without a policy is
never gated, so the integration is opt-in and non-invasive.
"""

from __future__ import annotations

import fnmatch
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from loguru import logger

from navin.utils.proc import no_window_kwargs

_TEST_TIMEOUT = 300.0


@dataclass(frozen=True)
class WorkspacePolicy:
    """What one subagent may touch and which tests must pass before merging."""

    allowed_paths: tuple[str, ...] = ()      # globs; empty means any path
    allowed_commands: tuple[str, ...] = ()   # command prefixes; empty means any
    test_commands: tuple[str, ...] = ()      # run in the worktree before merge
    denied_paths: tuple[str, ...] = ()

    def path_allowed(self, path: str) -> bool:
        rel = _norm(path)
        if rel is None:
            return False
        if any(_match(rel, g) for g in self.denied_paths):
            return False
        if not self.allowed_paths:
            return True
        return any(_match(rel, g) for g in self.allowed_paths)

    def command_allowed(self, command: str) -> bool:
        if not self.allowed_commands:
            return True
        try:
            words = shlex.split(command)
        except ValueError:
            return False
        if not words or any(c in command for c in ";&|`$\n"):
            return False
        for prefix in self.allowed_commands:
            p = prefix.split()
            if p and words[: len(p)] == p:
                return True
        return False

    def violations(self, paths: Iterable[str]) -> list[str]:
        return sorted(p for p in paths if not self.path_allowed(p))


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False  # not a pytest class

    command: str
    ok: bool
    output: str = ""


@dataclass(frozen=True)
class GateResult:
    ok: bool
    violations: tuple[str, ...] = ()
    tests: tuple[TestOutcome, ...] = ()

    def note(self) -> str:
        if self.ok:
            return ""
        lines = ["Merge blocked by the workspace policy."]
        if self.violations:
            lines.append("Files outside the allowed paths: " + ", ".join(self.violations))
        for t in self.tests:
            if not t.ok:
                lines.append(f"Test failed: {t.command}\n{t.output[-1500:]}")
        return "\n".join(lines)


_POLICIES: dict[str, WorkspacePolicy] = {}
_LOCK = threading.Lock()


def set_policy(task_id: str, policy: WorkspacePolicy) -> None:
    with _LOCK:
        _POLICIES[task_id] = policy


def get_policy(task_id: str) -> WorkspacePolicy | None:
    with _LOCK:
        return _POLICIES.get(task_id)


def clear_policy(task_id: str | None = None) -> None:
    with _LOCK:
        if task_id is None:
            _POLICIES.clear()
        else:
            _POLICIES.pop(task_id, None)


def policy_from_dict(data: Mapping[str, Any]) -> WorkspacePolicy:
    def tup(key: str) -> tuple[str, ...]:
        value = data.get(key) or ()
        if isinstance(value, str):
            value = (value,)
        return tuple(str(v) for v in value)

    return WorkspacePolicy(
        allowed_paths=tup("allowed_paths"),
        allowed_commands=tup("allowed_commands"),
        test_commands=tup("test_commands"),
        denied_paths=tup("denied_paths"),
    )


def _norm(path: str) -> str | None:
    p = PurePosixPath(str(path).replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return None
    return p.as_posix().lstrip("./") if str(p) != "." else None


def _match(rel: str, glob: str) -> bool:
    g = glob.replace("\\", "/").strip()
    if g.endswith("/"):
        g += "**"
    if fnmatch.fnmatchcase(rel, g):
        return True
    # "dir/**" also covers dir itself nested deeper, "**/x" the top level
    if g.startswith("**/") and fnmatch.fnmatchcase(rel, g[3:]):
        return True
    return g.endswith("/**") and rel == g[:-3]


def _git_lines(args: list[str], cwd: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def changed_files(path: Path, base: str) -> set[str]:
    """Files a worktree changed since ``base`` (commits, edits, new files)."""
    files = set(_git_lines(["diff", "--name-only", base], path))
    files.update(_git_lines(["ls-files", "--others", "--exclude-standard"], path))
    return files


def run_tests(path: Path, commands: Iterable[str], timeout: float = _TEST_TIMEOUT) -> tuple[TestOutcome, ...]:
    results: list[TestOutcome] = []
    for cmd in commands:
        try:
            argv = shlex.split(cmd)
            proc = subprocess.run(
                argv, cwd=path, capture_output=True, text=True, timeout=timeout, check=False,
                **no_window_kwargs(),
            )
            results.append(TestOutcome(cmd, proc.returncode == 0, (proc.stdout + proc.stderr)))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            results.append(TestOutcome(cmd, False, f"{type(exc).__name__}: {exc}"))
        if not results[-1].ok:
            break
    return tuple(results)


def evaluate(policy: WorkspacePolicy, path: Path, base: str) -> GateResult:
    """Check a worktree's changes and run its tests. Tests only run if paths pass."""
    bad = tuple(policy.violations(changed_files(path, base)))
    if bad:
        return GateResult(False, bad)
    tests = run_tests(path, policy.test_commands)
    return GateResult(all(t.ok for t in tests), (), tests)


def pre_merge_gate(checkout: Any, task_id: str) -> str | None:
    """Blocking note for ``task_id``'s worktree, or None when it may merge."""
    policy = get_policy(task_id)
    if policy is None or checkout is None:
        return None
    result = evaluate(policy, Path(checkout.path), str(checkout.base))
    return None if result.ok else result.note()


def guarded_pre_merge_gate(checkout: Any, task_id: str) -> str | None:
    """``pre_merge_gate`` that fails closed: a crash in the gate blocks the merge.

    The worktree is then kept unmerged, so no work is lost and nothing
    unchecked reaches the user's tree.
    """
    try:
        return pre_merge_gate(checkout, task_id)
    except Exception as exc:
        logger.exception("workspace_policy: gate failed for [{}]", task_id)
        return (
            f"Workspace policy check crashed ({type(exc).__name__}: {exc}); "
            "the worktree was not merged and stays available for review."
        )


@dataclass
class ConflictReport:
    overlaps: dict[str, list[str]] = field(default_factory=dict)  # file -> task ids

    @property
    def has_conflicts(self) -> bool:
        return bool(self.overlaps)

    def render(self) -> str:
        if not self.overlaps:
            return "No file is modified by more than one worktree."
        lines = [f"{len(self.overlaps)} file(s) modified by several worktrees:"]
        for f in sorted(self.overlaps):
            lines.append(f"- {f}: {', '.join(sorted(self.overlaps[f]))}")
        return "\n".join(lines)


def detect_conflicts(changes: Mapping[str, Iterable[str]]) -> ConflictReport:
    """``changes`` maps a task id to the files its worktree modified."""
    owners: dict[str, list[str]] = {}
    for task_id, files in changes.items():
        for f in set(files):
            owners.setdefault(f, []).append(task_id)
    return ConflictReport({f: ids for f, ids in owners.items() if len(ids) > 1})


def conflicts_for(checkouts: Mapping[str, Any]) -> ConflictReport:
    """Same as ``detect_conflicts`` but reading each checkout from git."""
    return detect_conflicts(
        {tid: changed_files(Path(c.path), str(c.base)) for tid, c in checkouts.items()}
    )
