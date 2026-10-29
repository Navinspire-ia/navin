# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Check a proposed fix before anyone applies it.

The patch is never applied to the project. The files it touches are copied to
a temporary folder, ``git apply`` runs there (outside any repository, so the
paths mean what they say), and the patched Python and JSON files must still
parse. A patch is refused outright when it leaves the project, touches
``.git`` or a secret file, carries binary data, deletes a test, removes
assertions from a test, or is too large to review. Without ``git`` the patch
is reported ``unchecked``: never as valid.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from navin.utils.proc import no_window_kwargs

_HEADER = re.compile(r"^(---|\+\+\+) (?:[ab]/)?(\S+)", re.M)
_SECRET_FILE = re.compile(r"(?:^|/)(?:\.env(?:\.[\w.-]+)?|id_[rd]sa\w*|[^/]+\.(?:pem|key|p12|pfx|keystore|jks))$", re.I)
_TEST_FILE = re.compile(r"(?:^|/)(?:tests?/|test_[^/]+\.py$|[^/]+_test\.(?:py|go)$|[^/]+\.(?:test|spec)\.[jt]sx?$)")
_ASSERTION = re.compile(r"\bassert\w*\b|\bexpect\(|\bshould\b")


@dataclass
class PatchCheck:
    status: str  # ok | rejected | does_not_apply | syntax_error | unchecked
    files: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class _FilePatch:
    old: str
    new: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new if self.new != "/dev/null" else self.old


def _parse(diff: str) -> list[_FilePatch]:
    files: list[_FilePatch] = []
    current: _FilePatch | None = None
    old = ""
    for line in diff.splitlines():
        if line.startswith("--- "):
            match = _HEADER.match(line)
            old = match.group(2) if match else ""
            continue
        if line.startswith("+++ "):
            match = _HEADER.match(line)
            current = _FilePatch(old=old, new=match.group(2) if match else "")
            files.append(current)
            continue
        if current is None or line.startswith(("@@", "\\")):
            continue
        if line.startswith("+"):
            current.added.append(line[1:])
        elif line.startswith("-"):
            current.removed.append(line[1:])
    return files


def _unsafe_path(path: str) -> str:
    if path == "/dev/null":
        return ""
    pure = PurePosixPath(path)
    if path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) or ".." in pure.parts:
        return f"{path} leaves the project"
    if pure.parts[:1] == (".git",):
        return f"{path} is inside .git"
    if _SECRET_FILE.search(path):
        return f"{path} is a secret or key file"
    return ""


def _policy(files: list[_FilePatch], max_lines: int) -> list[str]:
    reasons: list[str] = []
    for item in files:
        for path in {item.old, item.new}:
            problem = _unsafe_path(path)
            if problem:
                reasons.append(problem)
        if _TEST_FILE.search(item.path):
            if item.new == "/dev/null":
                reasons.append(f"{item.old} is a test: a fix must not delete it")
            else:
                lost = sum(1 for line in item.removed if _ASSERTION.search(line))
                gained = sum(1 for line in item.added if _ASSERTION.search(line))
                if lost > gained:
                    reasons.append(f"{item.path} loses {lost - gained} assertion(s): a fix must not weaken its tests")
    total = sum(len(f.added) + len(f.removed) for f in files)
    if total > max_lines:
        reasons.append(f"{total} changed lines: too large to review as one fix (limit {max_lines})")
    return list(dict.fromkeys(reasons))


def validate_patch(root: str | Path, diff: str, *, timeout_s: float = 20.0, max_lines: int = 600) -> PatchCheck:
    """Check *diff* against the project at *root* without changing it. Blocking: call it off the loop."""
    base = Path(root).resolve()
    if "GIT binary patch" in diff or "Binary files " in diff:
        return PatchCheck("rejected", reasons=["binary patches are not accepted as a fix"])
    files = _parse(diff)
    if not files:
        return PatchCheck("rejected", reasons=["not a unified diff: no ---/+++ file headers"])
    check = PatchCheck("ok", files=[f.path for f in files], added=sum(len(f.added) for f in files),
                       removed=sum(len(f.removed) for f in files))
    check.reasons = _policy(files, max_lines)
    if check.reasons:
        check.status = "rejected"
        return check
    git = shutil.which("git")
    if not git:
        check.status = "unchecked"
        check.reasons = ["git is not installed: the patch could not be checked"]
        return check
    with tempfile.TemporaryDirectory(prefix="navin-patch-") as tmp:
        work = Path(tmp)
        for item in files:
            if item.old == "/dev/null":
                continue
            source = base / item.old
            if source.is_file():
                target = work / item.old
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CEILING_DIRECTORIES": str(work.parent)}
        try:
            done = subprocess.run(
                [git, "apply", "--whitespace=nowarn", "-"], cwd=work, input=diff, capture_output=True, text=True,
                timeout=timeout_s, env=env, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            check.status = "unchecked"
            check.reasons = [f"git apply did not finish in {timeout_s:g}s"]
            return check
        if done.returncode != 0:
            check.status = "does_not_apply"
            check.reasons = [line for line in (done.stderr or done.stdout).strip().splitlines()[:8]] or ["git apply failed"]
            return check
        for item in files:
            if item.new == "/dev/null":
                continue
            problem = _syntax_problem(work / item.new, item.new)
            if problem:
                check.status = "syntax_error"
                check.reasons.append(problem)
    return check


def _syntax_problem(path: Path, rel: str) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if rel.endswith(".py"):
        try:
            compile(text, rel, "exec", dont_inherit=True)
        except SyntaxError as exc:
            return f"{rel} no longer parses after the patch: {exc.msg} (line {exc.lineno})"
    elif rel.endswith(".json"):
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"{rel} is no longer valid JSON after the patch: {exc.msg} (line {exc.lineno})"
    return ""


async def validate_patch_async(root: str | Path, diff: str, **kwargs) -> PatchCheck:
    """Same as :func:`validate_patch`, off the event loop."""
    return await asyncio.to_thread(validate_patch, root, diff, **kwargs)
