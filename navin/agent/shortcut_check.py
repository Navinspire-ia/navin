# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Find the shortcuts a turn took to get a green run instead of a real fix.

A test that keeps failing tempts the agent to skip it, mark it xfail, drop the
assertion, or silence the checker (``# type: ignore``, ``@ts-ignore``,
``eslint-disable``, ``# noqa``) or the error (``except: pass``, an empty
``catch``). The run turns green, the report says done, and the bug is still
there. This module reads the diff of the files the turn wrote and names each
such line before the final answer, so the agent either fixes the cause or says
in one line why the shortcut is intended.

Misses are the safe side: only lines the turn added (or assertions it removed
from tests) count, a tree seen through the WSL bridge or a slow git returns
nothing, and the list is capped.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from navin.index.service import is_bridged_root
from navin.utils.proc import no_window_kwargs

_MAX_FINDINGS = 12
_MAX_READ_BYTES = 400_000
_GIT_TIMEOUT_S = 5

_TEST_DIRS = frozenset({"__tests__", "test", "tests", "spec", "e2e"})
_TEST_NAME = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|Test\.java$)")

# Added anywhere in a test file.
_SKIP = re.compile(
    r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(|@unittest\.skip"
    r"|\bself\.skipTest\(|\b(it|test|describe)\.(skip|todo)\(|\bx(it|test|describe)\("
    r"|\bt\.Skip(Now|f)?\(|#\[ignore\]",
)
# Added in any source file.
_SUPPRESS = re.compile(
    r"#\s*type:\s*ignore|#\s*noqa\b|#\s*pyright:\s*ignore|@ts-(ignore|nocheck|expect-error)"
    r"|eslint-disable|#\s*pylint:\s*disable|#\[allow\(",
)
_EXCEPT_PASS = re.compile(r"^\s*except\b[^:]*:\s*(pass|\.\.\.)\s*(#.*)?$")
_EXCEPT_HEAD = re.compile(r"^\s*except\b[^:]*:\s*(#.*)?$")
_BARE_PASS = re.compile(r"^\s*(pass|\.\.\.)\s*(#.*)?$")
_EMPTY_CATCH = re.compile(r"\bcatch\s*(\([^)]*\))?\s*\{\s*\}")
_ASSERTION = re.compile(r"\bassert\w*\b|\bexpect\(|\bt\.(Error|Fatal)f?\(|\bassert_eq!|\bassert!")

_CODE_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs", ".vue", ".svelte",
)


def is_test_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return bool(_TEST_NAME.search(parts[-1])) or bool(_TEST_DIRS.intersection(parts[:-1]))


def shortcut_findings(
    root: Path | str,
    changed: Iterable[str],
    *,
    since: float | None = None,
) -> list[str]:
    """``path: reason`` for each shortcut the turn added to ``changed``.

    ``changed`` holds the paths this turn wrote, relative to ``root`` or
    absolute. The diff is taken against the last commit before ``since`` (the
    turn start) when the turn committed, else against HEAD; a file git does
    not track counts as fully added.
    """
    root = Path(root)
    if is_bridged_root(root):
        return []
    rels = _relative_code_paths(root, changed)
    if not rels:
        return []
    base = _base_commit(root, since)
    if base is None:
        return []
    findings: list[str] = []
    for rel in rels:
        added, removed = _file_diff(root, rel, base)
        findings.extend(_scan(rel, added, removed))
        if len(findings) >= _MAX_FINDINGS:
            return findings[:_MAX_FINDINGS]
    return findings


def build_shortcut_nudge(findings: list[str]) -> str:
    listed = "\n".join(f"- {item}" for item in findings)
    return (
        "Shortcut check before the final answer: this turn added lines that make a "
        f"check pass without fixing the cause:\n{listed}\n"
        "Fix the code so the tests pass as written: restore the removed assertions, "
        "drop the skip or the suppression, and handle or raise the error instead of "
        "swallowing it. Then run the tests again. If one is intended (a documented "
        "false positive, an upstream flaky test), keep it and say why in one line in "
        "the answer."
    )


def _relative_code_paths(root: Path, changed: Iterable[str]) -> list[str]:
    resolved_root = root.resolve()
    rels: list[str] = []
    for raw in changed:
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            try:
                rel = path.resolve().relative_to(resolved_root).as_posix()
            except (OSError, ValueError):
                continue
        else:
            rel = Path(raw).as_posix()
        if rel.endswith(_CODE_SUFFIXES) and rel not in rels:
            rels.append(rel)
    return sorted(rels)


def _git(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args], cwd=root, capture_output=True, timeout=_GIT_TIMEOUT_S, check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="ignore")


def _base_commit(root: Path, since: float | None) -> str | None:
    head = _git(root, ["rev-parse", "--verify", "-q", "HEAD"])
    if head is None:
        return None
    head = head.strip()
    if since is None:
        return head
    commits = _git(root, ["log", f"--since=@{int(since)}", "--format=%H"])
    if not commits or not commits.split():
        return head
    oldest = commits.split()[-1]
    parent = _git(root, ["rev-parse", "--verify", "-q", f"{oldest}^"])
    return parent.strip() if parent else head


def _file_diff(root: Path, rel: str, base: str) -> tuple[list[str], list[str]]:
    tracked = _git(root, ["cat-file", "-e", f"{base}:{rel}"])
    if tracked is None:
        try:
            with (root / rel).open("rb") as handle:
                text = handle.read(_MAX_READ_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            return [], []
        return text.splitlines(), []
    diff = _git(root, ["diff", "--no-color", "--no-ext-diff", "-U0", base, "--", rel])
    if not diff:
        return [], []
    added: list[str] = []
    removed: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def _scan(rel: str, added: list[str], removed: list[str]) -> list[str]:
    test = is_test_path(rel)
    found: list[str] = []
    for index, line in enumerate(added):
        if test and _SKIP.search(line):
            found.append(f"{rel}: test skipped or marked expected-to-fail (`{line.strip()[:80]}`)")
        elif _SUPPRESS.search(line):
            found.append(f"{rel}: checker silenced (`{line.strip()[:80]}`)")
        elif _EXCEPT_PASS.search(line) or _EMPTY_CATCH.search(line) or (
            _EXCEPT_HEAD.search(line)
            and index + 1 < len(added)
            and _BARE_PASS.search(added[index + 1])
        ):
            found.append(f"{rel}: error swallowed (`{line.strip()[:80]}`)")
    if test:
        dropped = sum(1 for line in removed if _ASSERTION.search(line)) - sum(
            1 for line in added if _ASSERTION.search(line)
        )
        if dropped > 0:
            found.append(f"{rel}: {dropped} assertion(s) removed from the tests")
    return found
