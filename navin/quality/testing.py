# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Test suite detection and structured execution.

The point of this module is that the agent stops reading raw test logs. Runs
produce counts and per-failure detail (test name, file, line, message), so the
agent can act on a failure instead of guessing from truncated output.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.quality import wsl_route
from navin.quality.check_budget import CheckBudget
from navin.quality.linters import _binary_for, _read_json
from navin.utils import wsl
from navin.utils.proc import detached_no_window_kwargs, kill_process_tree, run_killing_tree

_TABLE_PATH = Path(__file__).with_name("test_runners.json")
_MAX_FAILURES = 50
_MAX_MESSAGE_CHARS = 1200
_MAX_LOG_CHARS = 4000


@dataclass(frozen=True, slots=True)
class TestFailure:
    """One failing or erroring test."""

    name: str
    classname: str = ""
    file: str = ""
    line: int = 0
    kind: str = "failure"
    message: str = ""

    def render(self) -> str:
        where = self.file
        if where and self.line:
            where += f":{self.line}"
        head = f"{self.classname}::{self.name}" if self.classname else self.name
        parts = [head]
        if where:
            parts.append(f"({where})")
        text = " ".join(parts)
        if self.message:
            text += f"\n      {self.message.strip()[:_MAX_MESSAGE_CHARS]}"
        return text

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "classname": self.classname,
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "message": self.message[:_MAX_MESSAGE_CHARS],
        }


@dataclass(slots=True)
class TestOutcome:
    """Result of running one test suite."""

    runner: str
    ran: bool
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    failures: list[TestFailure] = field(default_factory=list)
    duration_ms: int = 0
    exit_code: int | None = None
    skipped_reason: str = ""
    log_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.ran and self.failed == 0 and (self.exit_code in (0, None))

    def summary(self) -> str:
        if not self.ran:
            return f"{self.runner}: not run - {self.skipped_reason}"
        if self.total == 0:
            # "0 passed" reads as a pass. A suite that discovered nothing has
            # proven nothing, and the agent has to know the difference.
            reason = self.skipped_reason or "no tests were collected"
            return f"{self.runner}: no tests ran - {reason}"
        bits = [f"{self.passed} passed"]
        if self.failed:
            bits.append(f"{self.failed} failed")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        return f"{self.runner}: {', '.join(bits)} ({self.duration_ms} ms)"

    def render(self) -> str:
        lines = [self.summary()]
        if self.failures:
            lines.append("")
            lines.append(f"Failures ({len(self.failures)}):")
            lines.extend(f"  - {failure.render()}" for failure in self.failures)
        if not self.ok and not self.failures and self.log_tail:
            lines.append("")
            lines.append("Output tail:")
            lines.append(self.log_tail)
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "runner": self.runner,
            "ran": self.ran,
            "ok": self.ok,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": self.total,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
            "skipped_reason": self.skipped_reason,
            "failures": [f.to_json() for f in self.failures],
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def runner_table() -> dict[str, dict[str, Any]]:
    packaged = _read_json(_TABLE_PATH).get("runners", {})
    merged: dict[str, dict[str, Any]] = dict(packaged) if isinstance(packaged, dict) else {}
    with suppress(Exception):
        from navin.config.loader import get_config_path

        user = _read_json(get_config_path().parent / "test_runners.json").get("runners")
        if isinstance(user, dict):
            merged.update(user)
    return merged


_SUBPROJECT_SKIP = frozenset({
    "node_modules", "dist", "build", "target", "coverage", "vendor", "__pycache__",
})
_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs", "testing", "e2e"})
# Monorepo roots whose children are the projects (apps/web, packages/api...).
_MONOREPO_DIRS = ("apps", "packages", "services", "libs")


def _subproject_dirs(root: Path) -> list[Path]:
    """Directories that may hold their own project: one level down, plus monorepo children.

    Separate front and back ends (``frontend/`` + ``backend/``, ``client/`` +
    ``server/``, ``apps/web``) keep their package.json, Cargo.toml, go.mod or
    pytest config there, not at the root.
    """
    def children(parent: Path) -> list[Path]:
        try:
            return sorted(
                child for child in parent.iterdir()
                if child.is_dir()
                and not child.name.startswith(".")
                and child.name not in _SUBPROJECT_SKIP
            )[:64]
        except OSError:
            return []

    first = children(root)
    nested = [grand for child in first if child.name in _MONOREPO_DIRS for grand in children(child)]
    return first + nested


def _workdir_for(spec: dict[str, Any], root: Path, target: str | None = None) -> Path:
    """Pick the directory this suite actually lives in.

    With a target, the nearest directory above it that holds this runner's
    project wins (``client/src/a.test.tsx`` runs from ``client``). Otherwise
    the root, the runner's listed candidates, then any subproject. Candidates
    usually start with ``"."``, which always exists, so choosing the first
    directory present would always answer the project root; a suite configured
    only in a subdirectory would then be reported as absent and silently
    skipped by ``verify``.
    """
    if target:
        rel = Path(target.split("::", 1)[0].replace("\\", "/"))
        current = (root / rel).parent if rel.suffix else root / rel
        try:
            current.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            current = root
        while True:
            # A test directory (tests/unit with its conftest.py) is not where
            # the project, and so the runner, lives.
            inside_tests = False
            with suppress(ValueError):
                parts = current.relative_to(root).parts
                inside_tests = any(part.lower() in _TEST_DIR_NAMES for part in parts)
            if not inside_tests and _detected(spec, current):
                return current
            if current == root or current.parent == current:
                break
            current = current.parent
    existing = [
        candidate
        for rel in spec.get("workdir", [])
        if (candidate := root / str(rel)).is_dir()
    ]
    for candidate in existing:
        if _detected(spec, candidate):
            return candidate
    if _detected(spec, root):
        return root
    for candidate in _subproject_dirs(root):
        if _detected(spec, candidate):
            return candidate
    return existing[0] if existing else root


def _target_for_workdir(target: str, workdir: Path, root: Path) -> str:
    """Rewrite a root-relative target so the runner, started in workdir, finds it."""
    if workdir == root:
        return target
    path, sep, rest = target.partition("::")
    try:
        rel = (root / path).resolve().relative_to(workdir.resolve())
    except (OSError, ValueError):
        return target
    return f"{rel.as_posix()}{sep}{rest}"


def _outside_project_root(root: Path, targets: list[str]) -> Path | None:
    """The repository the targets live in, when it is not the one being run from.

    A session opened on ``app/test`` that edited ``app/frontend`` asks for
    ``/abs/app/frontend/src/x.test.ts``. Searched from ``app/test``, Vitest
    found no test file, exited 1, and the run read "runner exited nonzero
    without reporting tests" forever, so the board could never close the step.
    The runner has to start in the target's own project, with its own
    node_modules or virtualenv. Only absolute targets that all sit in one other
    git repository move the run; anything else keeps the given root.
    """
    found: Path | None = None
    for item in targets:
        path = Path(item.split("::", 1)[0])
        if not path.is_absolute():
            return None
        try:
            resolved = path.resolve()
            inside = resolved.is_relative_to(root.resolve())
        except OSError:
            return None
        if inside:
            return None
        repo = next(
            (parent for parent in resolved.parents if (parent / ".git").exists()),
            None,
        )
        if repo is None or (found is not None and repo != found):
            return None
        found = repo
    return found


def _detected(spec: dict[str, Any], root: Path) -> bool:
    for rel in spec.get("detect_paths", []):
        if (root / str(rel)).exists():
            return True
    for rule in spec.get("detect_content", []):
        if not isinstance(rule, dict):
            continue
        target = root / str(rule.get("file", ""))
        needle = str(rule.get("contains", ""))
        if not needle or not target.is_file():
            continue
        with suppress(OSError):
            if needle in target.read_text(encoding="utf-8", errors="ignore"):
                return True
    return False


def detect_suites(root: Path) -> list[dict[str, Any]]:
    """Report the test suites this project has, and whether they can run."""
    out: list[dict[str, Any]] = []
    table = sorted(runner_table().items())
    location = wsl_route.wsl_root(root)
    # A WSL project: one probe inside the distribution answers for every runner.
    found = wsl_route.available(dict(table), root, location) if location is not None else None
    for name, spec in table:
        workdir = _workdir_for(spec, root)
        detected = _detected(spec, workdir) or _detected(spec, root)
        reason = ""
        if not detected:
            reason = "no test layout or config detected"
        elif location is not None:
            if found is not None and name not in found:
                reason = (
                    f"{(spec.get('binary') or {}).get('name', name)} not installed "
                    f"in the WSL distribution {location.distro}"
                )
        elif _runner_argv(spec, root) is None:
            reason = f"{(spec.get('binary') or {}).get('name', name)} not installed"
        out.append(
            {
                "runner": name,
                "language": str(spec.get("language", "")),
                "available": not reason,
                "fallback_only": bool(spec.get("fallback_only")),
                "workdir": str(workdir),
                "reason": reason,
            }
        )
    return out


def _primary_runners(root: Path) -> list[str]:
    """Runners to use by default: detected, installed, non-fallback."""
    rows = detect_suites(root)
    primary = [r["runner"] for r in rows if r["available"] and not r["fallback_only"]]
    if primary:
        return primary
    return [r["runner"] for r in rows if r["available"]]


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------


def _failure_message(attribute: str | None, body: str | None) -> str:
    """Combine a JUnit failure's summary attribute with the detail in its body.

    pytest labels an import error `message="collection failure"` and puts the
    real cause in the element text. Reporting only the attribute tells the agent
    a suite is broken without saying why, which invites it to guess.
    """
    summary = (attribute or "").strip()
    lines = [line.rstrip() for line in (body or "").splitlines() if line.strip()]
    detail = ""
    if lines:
        flagged = [line for line in lines if line.lstrip().startswith("E ")]
        detail = (flagged[-1] if flagged else lines[-1]).lstrip()
        if detail.startswith("E "):
            detail = detail[2:].lstrip()
    if not summary:
        return detail[:_MAX_MESSAGE_CHARS]
    if not detail or detail in summary or summary in detail:
        return summary[:_MAX_MESSAGE_CHARS]
    return f"{summary}: {detail}"[:_MAX_MESSAGE_CHARS]


def _parse_junit_xml(report: Path, root: Path) -> tuple[int, int, int, int, list[TestFailure]]:
    try:
        from defusedxml import ElementTree
    except ImportError:  # pragma: no cover - defusedxml is a hard dependency
        return 0, 0, 0, 0, []
    try:
        tree = ElementTree.parse(str(report))
    except Exception:
        return 0, 0, 0, 0, []
    root_el = tree.getroot()
    suites = (
        list(root_el.iter("testsuite"))
        if root_el.tag in {"testsuites", "testsuite"}
        else []
    )
    if root_el.tag == "testsuite" and not suites:
        suites = [root_el]

    total = failed = skipped = 0
    failures: list[TestFailure] = []
    for suite in suites:
        for case in suite.iter("testcase"):
            total += 1
            skip_el = case.find("skipped")
            if skip_el is not None:
                skipped += 1
                continue
            problem = case.find("failure")
            kind = "failure"
            if problem is None:
                problem = case.find("error")
                kind = "error"
            if problem is None:
                continue
            failed += 1
            if len(failures) >= _MAX_FAILURES:
                continue
            file_attr = case.get("file") or ""
            with suppress(ValueError):
                if file_attr:
                    candidate = Path(file_attr)
                    if candidate.is_absolute():
                        file_attr = candidate.relative_to(root).as_posix()
            message = _failure_message(problem.get("message"), problem.text)
            failures.append(
                TestFailure(
                    name=case.get("name") or "",
                    classname=case.get("classname") or "",
                    file=file_attr,
                    line=int(case.get("line") or 0),
                    kind=kind,
                    message=message.strip(),
                )
            )
    passed = max(total - failed - skipped, 0)
    return passed, failed, skipped, total, failures


def _parse_go_json(output: str) -> tuple[int, int, int, int, list[TestFailure]]:
    passed = failed = skipped = 0
    failures: list[TestFailure] = []
    output_by_test: dict[str, list[str]] = {}
    for raw_line in output.splitlines():
        if not raw_line.startswith("{"):
            continue
        try:
            event = json.loads(raw_line)
        except ValueError:
            continue
        action = event.get("Action")
        test = event.get("Test")
        if not test:
            continue
        key = f"{event.get('Package', '')}::{test}"
        if action == "output":
            output_by_test.setdefault(key, []).append(str(event.get("Output", "")))
        elif action == "pass":
            passed += 1
        elif action == "skip":
            skipped += 1
        elif action == "fail":
            failed += 1
            if len(failures) < _MAX_FAILURES:
                failures.append(
                    TestFailure(
                        name=test,
                        classname=str(event.get("Package", "")),
                        kind="failure",
                        message="".join(output_by_test.get(key, []))[:_MAX_MESSAGE_CHARS],
                    )
                )
    return passed, failed, skipped, passed + failed + skipped, failures


_JEST_FRAME_RE = re.compile(r"\((?P<file>[^():]+):(?P<line>\d+):\d+\)")
_VENDOR_FRAME_RE = re.compile(r"^\s+at\b.*(node_modules|node:internal)")


def _trim_vendor_frames(message: str) -> str:
    """Drop stack frames inside the runner and the standard library.

    A jest assertion failure carries a dozen frames through jest-circus and the
    node internals before reaching anything the agent can act on. They are pure
    noise in a tool result and crowd out the assertion itself.
    """
    kept = [line for line in message.splitlines() if not _VENDOR_FRAME_RE.match(line)]
    return "\n".join(kept).strip()


def _parse_jest_json(
    report: Path, root: Path
) -> tuple[int, int, int, int, list[TestFailure]]:
    """Read `jest --json`, which names each test and why it failed.

    The counts alone were all the summary regex could see, so a red suite told the
    agent how many tests broke but never which ones, leaving it to re-run jest by
    hand and read the output itself.
    """
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0, 0, 0, []
    if not isinstance(payload, dict):
        return 0, 0, 0, 0, []

    def count(key: str) -> int:
        value = payload.get(key)
        return value if isinstance(value, int) else 0

    passed = count("numPassedTests")
    failed = count("numFailedTests")
    skipped = count("numPendingTests") + count("numTodoTests")
    total = count("numTotalTests") or passed + failed + skipped

    failures: list[TestFailure] = []
    for suite in payload.get("testResults") or []:
        if not isinstance(suite, dict):
            continue
        suite_file = _relative_to(str(suite.get("name") or ""), root)
        # A suite that fails to compile reports no assertions at all, only a
        # message; without this the agent sees a failure count and no reason.
        assertions = suite.get("assertionResults") or []
        if suite.get("status") == "failed" and not any(
            isinstance(row, dict) and row.get("status") == "failed"
            for row in assertions
        ):
            if len(failures) < _MAX_FAILURES:
                failures.append(
                    TestFailure(
                        name=suite_file or "suite",
                        file=suite_file,
                        kind="error",
                        message=str(suite.get("message") or "")[:_MAX_MESSAGE_CHARS],
                    )
                )
            continue
        for row in assertions:
            if not isinstance(row, dict) or row.get("status") != "failed":
                continue
            message = _trim_vendor_frames(
                "\n".join(str(m) for m in row.get("failureMessages") or [])
            )
            line = 0
            location = row.get("location")
            if isinstance(location, dict) and isinstance(location.get("line"), int):
                line = location["line"]
            elif suite_file:
                for frame in _JEST_FRAME_RE.finditer(message):
                    if frame.group("file").endswith(Path(suite_file).name):
                        line = int(frame.group("line"))
                        break
            ancestors = [str(a) for a in row.get("ancestorTitles") or []]
            if len(failures) < _MAX_FAILURES:
                failures.append(
                    TestFailure(
                        name=str(row.get("title") or row.get("fullName") or ""),
                        classname=" > ".join(ancestors),
                        file=suite_file,
                        line=line,
                        kind="failure",
                        message=message[:_MAX_MESSAGE_CHARS],
                    )
                )
    return passed, failed, skipped, total, failures


_CARGO_RESULT_RE = re.compile(
    r"^test result:\s+\w+\.\s+(?P<passed>\d+) passed;\s+(?P<failed>\d+) failed;"
    r"\s+(?P<ignored>\d+) ignored",
    re.MULTILINE,
)
_CARGO_STDOUT_RE = re.compile(r"^---- (?P<name>\S+) stdout ----$", re.MULTILINE)
_CARGO_PANIC_RE = re.compile(
    r"panicked at (?P<file>[^\s:]+(?::[^\s:]+)*?):(?P<line>\d+):\d+"
)


def _parse_cargo_text(output: str) -> tuple[int, int, int, int, list[TestFailure]]:
    """Read `cargo test` output, panics included.

    Cargo emits machine-readable results only on nightly, so the human output is
    what there is. It is stable and structured enough: each failure gets a
    ``---- name stdout ----`` block carrying the panic, its file and its line.
    Counts are summed across binaries, since a workspace runs several.
    """
    passed = failed = skipped = 0
    for match in _CARGO_RESULT_RE.finditer(output):
        passed += int(match.group("passed"))
        failed += int(match.group("failed"))
        skipped += int(match.group("ignored"))

    failures: list[TestFailure] = []
    blocks = list(_CARGO_STDOUT_RE.finditer(output))
    for index, match in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(output)
        body = output[match.end() : end]
        # The trailing "failures:" recap repeats the names with no detail.
        body = body.split("\nfailures:\n", 1)[0]
        panic = _CARGO_PANIC_RE.search(body)
        reason = _cargo_reason(body)
        if len(failures) < _MAX_FAILURES:
            failures.append(
                TestFailure(
                    name=match.group("name"),
                    file=panic.group("file") if panic else "",
                    line=int(panic.group("line")) if panic else 0,
                    kind="failure",
                    message=reason[:_MAX_MESSAGE_CHARS],
                )
            )
    if not failed and failures:
        failed = len(failures)
    return passed, failed, skipped, passed + failed + skipped, failures


def _cargo_reason(body: str) -> str:
    """The panic message, plus the values a failed comparison printed.

    `assertion left == right failed` on its own does not say what the values were,
    and those two lines are what turn the failure into something actionable.
    """
    lines = [line for line in body.strip().splitlines() if line.strip()]
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("note:", "thread '")):
            continue
        if not collected:
            collected.append(stripped)
            continue
        # Only the comparison operands are worth trailing the headline with.
        if stripped.startswith(("left:", "right:")):
            collected.append(stripped)
            continue
        break
    return "\n".join(collected)


def _relative_to(path: str, root: Path) -> str:
    """Project-relative posix path, or the name alone when it sits outside."""
    if not path:
        return ""
    candidate = Path(path)
    try:
        return candidate.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return candidate.name


_UNITTEST_BLOCK_RE = re.compile(
    r"^(?P<kind>FAIL|ERROR):\s+(?P<name>\S+)\s+\((?P<dotted>[^)]+)\)\s*$",
    re.MULTILINE,
)
_UNITTEST_TALLY_RE = re.compile(r"^Ran (?P<total>\d+) tests?", re.MULTILINE)
_UNITTEST_VERDICT_RE = re.compile(
    r"^(?:OK|FAILED)(?:\s+\((?P<detail>[^)]*)\))?\s*$", re.MULTILINE
)
_UNITTEST_FRAME_RE = re.compile(
    r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+)', re.MULTILINE
)
# The run's own epilogue, which the last failure block would otherwise absorb.
_UNITTEST_EPILOGUE_RE = re.compile(r"^Ran \d+ tests?\b", re.MULTILINE)


def _parse_unittest_text(
    output: str, root: Path
) -> tuple[int, int, int, int, list[TestFailure]]:
    """Read `python -m unittest -v` output, failures included.

    A counts-only regex could not see the ``FAILED (failures=1, errors=1)``
    verdict, so a suite with two broken tests was reported as entirely passing.
    Individual failures matter as well: the traceback names the file and line, and
    without them the agent has to re-run the suite by hand to find out what broke.
    """
    total = 0
    tally = _UNITTEST_TALLY_RE.search(output)
    if tally:
        total = int(tally.group("total"))

    failed = errors = skipped = 0
    verdict = None
    for candidate in _UNITTEST_VERDICT_RE.finditer(output):
        verdict = candidate
    if verdict is not None:
        for part in (verdict.group("detail") or "").split(","):
            key, _, value = part.strip().partition("=")
            if not value.isdigit():
                continue
            if key == "failures":
                failed = int(value)
            elif key == "errors":
                errors = int(value)
            elif key in {"skipped", "expected failures", "unexpected successes"}:
                skipped += int(value)

    # The blocks are the ground truth for which tests broke; the verdict line only
    # counts them. When a run dies before printing a verdict, the blocks are all
    # there is, so the tallies fall back to them.
    failures: list[TestFailure] = []
    blocks = list(_UNITTEST_BLOCK_RE.finditer(output))
    for index, match in enumerate(blocks):
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(output)
        body = output[match.end() : end]
        epilogue = _UNITTEST_EPILOGUE_RE.search(body)
        if epilogue:
            body = body[: epilogue.start()]
        file_path, line_no = _unittest_origin(body, root)
        if len(failures) < _MAX_FAILURES:
            failures.append(
                TestFailure(
                    name=match.group("name"),
                    classname=match.group("dotted").rsplit(".", 1)[0],
                    file=file_path,
                    line=line_no,
                    kind="error" if match.group("kind") == "ERROR" else "failure",
                    message=_unittest_reason(body),
                )
            )

    broken = failed + errors
    if verdict is None or (not broken and blocks):
        broken = len(blocks)
    passed = max(total - broken - skipped, 0)
    return passed, broken, skipped, total or (passed + broken + skipped), failures


def _unittest_origin(body: str, root: Path) -> tuple[str, int]:
    """Locate a traceback, preferring the deepest frame in the project's own code.

    An assertion that fails inside a helper leaves the last frame in the standard
    library or a dependency, which the agent cannot act on; the deepest frame it
    owns is the one worth reporting.
    """
    fallback = ("", 0)
    best = ("", 0)
    for frame in _UNITTEST_FRAME_RE.finditer(body):
        raw, line_no = frame.group("file"), int(frame.group("line"))
        try:
            relative = Path(raw).resolve(strict=False).relative_to(root).as_posix()
        except (ValueError, OSError):
            fallback = (raw, line_no)
            continue
        best = (relative, line_no)
    return best if best[0] else fallback


def _unittest_reason(body: str) -> str:
    """Pull the exception line out of a traceback, keeping a little context."""
    lines = [line for line in body.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    # The final line is the exception and its message; a separator is not.
    for line in reversed(lines):
        if set(line.strip()) <= {"-", "="}:
            continue
        return line.strip()[:_MAX_MESSAGE_CHARS]
    return ""


def _parse_summary(
    spec: dict[str, Any], output: str
) -> tuple[int, int, int, int, list[TestFailure]]:
    raw = spec.get("summary_regex")
    if not raw:
        return 0, 0, 0, 0, []
    try:
        pattern = re.compile(str(raw))
    except re.error:
        return 0, 0, 0, 0, []
    match = None
    for candidate in pattern.finditer(output):
        match = candidate
    if match is None:
        return 0, 0, 0, 0, []
    groups = match.groupdict()

    def value(key: str) -> int:
        try:
            return int(groups.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    passed, failed, skipped = value("passed"), value("failed"), value("skipped")
    total = value("total") or (passed + failed + skipped)
    if not passed and total and not failed:
        # `unittest` reports only a total plus an OK/FAILED verdict.
        passed = total - failed - skipped
    return passed, failed, skipped, total, []


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


# Parsers that read a file the runner writes, and the suffix it expects. A
# parser absent from here gets no {report} path, and any argument mentioning one
# is dropped - which silently cost jest its whole report before it was listed.
_REPORT_SUFFIXES = {"junit_xml": ".xml", "jest_json": ".json"}


_CHANGE_LANGUAGES: dict[str, str] = {
    **dict.fromkeys((".py", ".pyi"), "python"),
    **dict.fromkeys((
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
        ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html",
    ), "javascript"),
    ".go": "go",
    ".rs": "rust",
}


def runners_for_changes(root: Path, changed: list[str]) -> list[str] | None:
    """The detected suites that can see this change, or None for all of them.

    A change whose language no detected suite covers gets an empty list, not
    every suite: a CSS edit in a pytest-only repo has no test to run.

    A restyle of the frontend must not run (and then chase) the backend's
    pytest suite: in a measured session a CSS edit drew the agent into
    unrelated webhook-secret failures, a JWT hunt and a Playwright install.
    Files of no known language (docs, config) keep the old behaviour.
    """
    languages = {_CHANGE_LANGUAGES.get(Path(path).suffix.lower()) for path in changed}
    if None in languages or not languages:
        return None
    table = runner_table()
    primary = _primary_runners(root)
    matching = [name for name in primary if table.get(name, {}).get("language") in languages]
    return matching


# Test files by name: pytest (test_x.py, x_test.py), go (x_test.go), the JS runners
# (x.test.ts, x.spec.tsx, ...).
_TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.(?:py|go)|[^/]+\.(?:test|spec)\.[cm]?[jt]sx?)$",
    re.IGNORECASE,
)
_JS_SOURCE_SUFFIXES = frozenset({
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte",
})
_JS_TEST_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_MAX_RELATED_TESTS = 24
# Above this many importers a module is a hub (the app, a shared widget): its
# importers are the whole suite, so only tests named after it are kept.
_MAX_IMPORTERS = 6


def _target_language(target: str) -> str | None:
    path = target.split("::", 1)[0].replace("\\", "/")
    suffix = Path(path).suffix.lower()
    if not suffix:
        return "python" if Path(path).name.lower() in _TEST_DIR_NAMES else None
    return _CHANGE_LANGUAGES.get(suffix)


def _python_test_files(root: Path, rel: str, cache: dict[str, list[str]]) -> list[str]:
    """Python test files in the test folders above ``rel`` (tests/, site/back/tests/)."""
    found: list[str] = []
    current = (root / rel).parent
    while True:
        for name in _TEST_DIR_NAMES:
            folder = current / name
            key = folder.as_posix()
            if key not in cache:
                files: list[str] = []
                with suppress(OSError):
                    if folder.is_dir():
                        files = sorted(
                            path.relative_to(root).as_posix()
                            for path in folder.rglob("*.py")
                            if _TEST_FILE_RE.search(path.as_posix())
                            and not any(part in _SUBPROJECT_SKIP for part in path.parts)
                        )
                cache[key] = files
            found.extend(cache[key])
        if current == root or current.parent == current:
            break
        current = current.parent
    return list(dict.fromkeys(found))


# Test sources already read, keyed by absolute path: (mtime_ns, size, text).
# A lookup used to re-read every test file for each changed module: 761 files
# times the changed modules, 100 s per lookup on a WSL tree read from Windows.
_TEST_TEXTS: dict[str, tuple[int, int, str]] = {}
_TEST_TEXTS_MAX = 5000
# Importer scan budget per lookup: the result is a hint, never worth a stall.
_IMPORT_SCAN_BUDGET_S = 5.0


def _test_text(path: Path, texts: dict[str, str | None]) -> str | None:
    """Source of one test file: read once per lookup, reused while unchanged."""
    key = str(path)
    if key in texts:
        return texts[key]
    text: str | None = None
    try:
        stat = path.stat()
        cached = _TEST_TEXTS.get(key)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            text = cached[2]
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if len(_TEST_TEXTS) >= _TEST_TEXTS_MAX:
                _TEST_TEXTS.clear()
            _TEST_TEXTS[key] = (stat.st_mtime_ns, stat.st_size, text)
    except OSError:
        _TEST_TEXTS.pop(key, None)
    texts[key] = text
    return text


def _python_related(
    root: Path,
    rel: str,
    cache: dict[str, list[str]],
    texts: dict[str, str | None] | None = None,
    deadline: float | None = None,
) -> list[str]:
    named, importers = _python_related_parts(root, rel, cache, texts, deadline)
    return named + importers


def _python_related_parts(
    root: Path,
    rel: str,
    cache: dict[str, list[str]],
    texts: dict[str, str | None] | None = None,
    deadline: float | None = None,
) -> tuple[list[str], list[str]]:
    """Tests named after the module, then the ones importing it (dropped for a hub)."""
    stem = Path(rel).stem
    module = rel[: -len(Path(rel).suffix)].replace("/", ".")
    if stem == "__init__":
        module = module.rsplit(".", 1)[0]
        stem = module.rsplit(".", 1)[-1]
    parent, _, leaf = module.rpartition(".")
    imports = re.compile(
        rf"(?:\b{re.escape(module)}\b|from\s+{re.escape(parent)}\s+import\s[^\n]*\b{re.escape(leaf)}\b)"
        if parent else rf"\b{re.escape(module)}\b"
    )
    texts = {} if texts is None else texts
    named: list[str] = []
    importers: list[str] = []
    for test in _python_test_files(root, rel, cache):
        words = Path(test).stem.lower().split("_")
        if stem.lower() in words:
            named.append(test)
            continue
        if deadline is not None and time.monotonic() > deadline:
            continue  # out of budget: the tests named after the module still count
        text = _test_text(root / test, texts)
        if text is not None and imports.search(text):
            importers.append(test)
    return named, (importers if len(importers) <= _MAX_IMPORTERS else [])


def _js_related(root: Path, rel: str) -> list[str]:
    path = Path(rel)
    found: list[str] = []
    for folder in (path.parent, path.parent / "__tests__"):
        for kind in ("test", "spec"):
            for suffix in _JS_TEST_SUFFIXES:
                candidate = folder / f"{path.stem}.{kind}{suffix}"
                if (root / candidate).is_file():
                    found.append(candidate.as_posix())
    return found


def related_test_targets(
    root: Path,
    changed: list[str],
    *,
    budget: CheckBudget | None = None,
) -> list[str]:
    """The test files that exercise the changed files, closest first.

    A change is judged by its own tests: the edited test files, tests next to
    or named after the edited source (``a.ts`` -> ``a.test.ts``,
    ``pkg/widgets.py`` -> ``test_widgets.py``), then Python tests importing
    the edited module (the integration side). Never the whole suite, so a
    slow or broken test elsewhere cannot send the agent off repairing code
    nobody asked about. ``budget`` caps the list (2 files by default in
    Settings); without one the old breadth applies (24, conftest folders).
    """
    limit = budget.max_files if budget is not None else _MAX_RELATED_TESTS
    whole_folders = budget.whole_folders if budget is not None else True
    tests: list[str] = []
    named: list[str] = []
    folders: list[str] = []
    importers: list[str] = []
    cache: dict[str, list[str]] = {}
    texts: dict[str, str | None] = {}
    deadline = time.monotonic() + _IMPORT_SCAN_BUDGET_S
    for raw in changed:
        rel = raw.replace("\\", "/")
        if rel.startswith("./"):
            rel = rel[2:]
        suffix = Path(rel).suffix.lower()
        if _TEST_FILE_RE.search(rel):
            if (root / rel).is_file():
                tests.append(rel)
        elif suffix == ".py":
            if Path(rel).name == "conftest.py":
                if whole_folders:
                    folders.append(Path(rel).parent.as_posix() or ".")
            else:
                own, using = _python_related_parts(root, rel, cache, texts, deadline)
                named.extend(own)
                importers.extend(using)
        elif suffix in _JS_SOURCE_SUFFIXES:
            named.extend(_js_related(root, rel))
    return list(dict.fromkeys(tests + named + folders + importers))[:limit]


# A target names a file or folder when it carries a separator or a source
# suffix; ``-k expr``, ``TestX.test_y`` and runner options name none.
_TARGET_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".go", ".rs",
})
_NAME_FILLER = frozenset({"test", "tests", "spec", "specs"})
_CLOSE_NAME_RATIO = 0.6


def target_path(target: str) -> str | None:
    """The file or folder a test target points at, or None when it names none.

    ``tests/test_a.py::TestX::test_y`` -> ``tests/test_a.py``; ``./pkg/...`` ->
    ``./pkg``. A bare test name, a ``-k`` expression, a glob or a shell
    variable is left to the runner.
    """
    head = str(target or "").split("::", 1)[0].strip().replace("\\", "/")
    if not head or head.startswith("-") or any(ch in head for ch in "*?[$`"):
        return None
    if head.endswith("/..."):
        head = head[:-4] or "."
    if "/" not in head and Path(head).suffix.lower() not in _TARGET_SUFFIXES:
        return None
    return head


def _name_words(stem: str) -> set[str]:
    return {word for word in re.split(r"[_.-]+", stem.lower()) if word and word not in _NAME_FILLER}


def closest_test_files(root: Path, rel: str, *, limit: int = 3) -> list[str]:
    """Existing test files whose name is closest to the missing ``rel``.

    One listing of the nearest existing folder on the way up (``tests/`` for
    ``tests/test_metagraph_tool.py``), ranked by shared name words then by
    similarity; no walk.
    """
    wanted = Path(rel)
    try:
        base = root.resolve(strict=False)
        folder = (base / wanted.parent).resolve(strict=False)
    except OSError:
        return []
    while not folder.is_dir():
        if folder.parent == folder:
            return []
        folder = folder.parent
    if folder != base and base not in folder.parents:
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    wanted_words = _name_words(wanted.stem)
    ranked: list[tuple[int, float, str]] = []
    for name in names:
        if not _TEST_FILE_RE.search(name):
            continue
        # Longer shared words weigh more: ``metagraph`` says more than ``tool``.
        shared = sum(len(word) for word in wanted_words & _name_words(Path(name).stem))
        ratio = difflib.SequenceMatcher(None, wanted.stem.lower(), Path(name).stem.lower()).ratio()
        if shared or ratio >= _CLOSE_NAME_RATIO:
            ranked.append((-shared, -ratio, name))
    ranked.sort()
    prefix = folder.relative_to(base).as_posix() if folder != base else ""
    return [f"{prefix}/{name}" if prefix else name for _, _, name in ranked[:limit]]


def missing_target_error(root: Path, target: str) -> str | None:
    """Why ``target`` cannot run, or None when its file or folder exists.

    pytest stops at the first unknown path ("file or directory not found") and
    runs nothing, vitest and jest report an empty selection: one guessed name
    wastes the whole run. Checked before the runner starts, with the existing
    files whose name is closest, so the next call names a real one.
    """
    rel = target_path(target)
    if rel is None:
        return None
    path = Path(os.path.expanduser(rel))
    if not path.is_absolute():
        path = root / path
    try:
        if path.exists():
            return None
    except OSError:
        # Unreadable rather than absent (permissions, a stale share): the
        # runner reports that itself, with the real error.
        return None
    lines = [f"{rel} does not exist (resolved from {root})."]
    folder = Path(rel).parent
    if folder.as_posix() not in ("", ".") and not (root / folder).is_dir():
        dirs = [name for name in sorted(_TEST_DIR_NAMES) if (root / name).is_dir()]
        where = f"; test folders here: {', '.join(dirs)}" if dirs else ""
        lines.append(f"Its folder {folder.as_posix()}/ does not exist either{where}.")
    close = closest_test_files(root, rel)
    if close:
        lines.append("Existing tests with a similar name: " + ", ".join(close) + ".")
    words = Path(rel).stem.split("_")
    stem = "_".join(words[:2]) if len(words) > 1 else Path(rel).stem
    pattern = f"{folder.as_posix()}/{stem}*" if folder.as_posix() not in ("", ".") else f"{stem}*"
    lines.append(
        "Run one of those, or list the real files first with find_files "
        f'glob="{pattern}"; never guess a test path.'
    )
    return " ".join(lines)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


def _runner_for_target(root: Path, target: str) -> tuple[str, Path] | None:
    """The installed runner whose project holds ``target``, and where it runs."""
    language = _target_language(target)
    if language is None:
        return None
    table = runner_table()
    names = sorted(
        (name for name, spec in table.items() if spec.get("language") == language),
        key=lambda name: bool(table[name].get("fallback_only")),
    )
    path = root / target.split("::", 1)[0]
    for name in names:
        spec = table[name]
        workdir = _workdir_for(spec, root, target)
        # webui's vitest must not be handed site/back/a.test.ts: it would find
        # no test and fail. The runner has to live above the file it runs.
        if not _detected(spec, workdir) or not _inside(path, workdir):
            continue
        if wsl_route.wsl_root(root) is not None or _runner_argv(spec, workdir) or _runner_argv(spec, root):
            # On a WSL project the runner is resolved inside the distribution
            # when it runs; a missing one comes back as "not installed".
            return name, workdir
    return None


def run_tests_for_changes(
    root: Path,
    changed: list[str],
    *,
    on_output: Callable[[str], None] | None = None,
    budget: CheckBudget | None = None,
) -> tuple[list[TestOutcome], list[str]]:
    """Run only the tests of the changed files, each with its own runner.

    Go and Rust keep their tests inside the package, so a change there runs
    that language's suite. Files no test can see (docs, JSON, styles) run
    nothing: the notes say so instead of a red run of unrelated suites.
    ``budget`` caps the test files (see related_test_targets).
    """
    table = runner_table()
    notes: list[str] = []
    groups: dict[tuple[str, str], list[str]] = {}
    targets = related_test_targets(root, changed, budget=budget)
    for target in targets:
        picked = _runner_for_target(root, target)
        if picked is None:
            notes.append(f"No installed test runner reaches {target}; run it with the project's own command.")
            continue
        name, workdir = picked
        groups.setdefault((name, str(workdir)), []).append(target)
    outcomes = [
        _run_one(name, table[name], root, group, on_output=on_output)
        for (name, _workdir), group in groups.items()
    ]
    languages = {_CHANGE_LANGUAGES.get(Path(path).suffix.lower()) for path in changed}
    suites = [
        name for name in _primary_runners(root)
        if table.get(name, {}).get("language") in languages & {"go", "rust"}
    ]
    if suites:
        outcomes.extend(run_tests(root, runners=suites, on_output=on_output))
    if not targets and not suites:
        notes.append(
            "No test file covers the changed files, so no suite was run. "
            "Run the tests that exercise this change with test_run target=<test file>."
        )
    return outcomes, notes


def run_tests(
    root: Path,
    *,
    runners: list[str] | None = None,
    target: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> list[TestOutcome]:
    """Run the project's test suites and return structured outcomes."""
    table = runner_table()
    selected = runners if runners is not None else _primary_runners(root)
    if runners is None and target:
        # A Python test in a mixed backend/frontend project must not also be
        # sent to Vitest: its empty collection would turn a pass into a failure.
        suffix = Path(target.split("::", 1)[0].replace("\\", "/")).suffix.lower()
        languages = {
            ".py": {"python"}, ".pyi": {"python"},
            ".js": {"javascript", "typescript"}, ".jsx": {"javascript", "typescript"},
            ".ts": {"javascript", "typescript"}, ".tsx": {"javascript", "typescript"},
            ".mjs": {"javascript", "typescript"}, ".cjs": {"javascript", "typescript"},
            ".mts": {"javascript", "typescript"}, ".cts": {"javascript", "typescript"},
            ".go": {"go"}, ".rs": {"rust"},
        }.get(suffix, set())
        matching = [name for name in selected if table.get(name, {}).get("language") in languages]
        if matching:
            selected = matching
    outcomes: list[TestOutcome] = []
    for name in selected:
        spec = table.get(name)
        if spec is None:
            outcomes.append(
                TestOutcome(runner=name, ran=False, skipped_reason="unknown runner")
            )
            continue
        outcomes.append(_run_one(name, spec, root, target, on_output=on_output))
    if not outcomes:
        outcomes.append(
            TestOutcome(
                runner="none",
                ran=False,
                skipped_reason=(
                    "no test suite detected in this project (looked for pytest, "
                    "vitest, jest, go test, cargo test)"
                ),
            )
        )
    return outcomes


def _launch_argv(spec: dict[str, Any], binary: str) -> list[str]:
    """Argv prefix for the runner.

    ``run_as_module`` launches the runner as ``<interpreter> -m <module>``
    instead of calling its console script. For pytest this is not cosmetic: the
    console script leaves the working directory off ``sys.path``, so a plain
    ``pkg/`` + ``tests/`` project that is not installed fails to collect with
    "No module named pkg" while ``python -m pytest`` passes. Reporting a healthy
    suite as broken is worse than not running it, so prefer the module form.

    The interpreter is taken from the resolved binary's own directory, which
    keeps a project-local virtualenv's dependencies in scope. The last resort is
    navin's own interpreter, which in a packaged build is reached through the
    ``navin python`` subcommand rather than by naming an executable that does not
    exist there.
    """
    from navin.python_runtime import python_command

    module = str((spec.get("binary") or {}).get("run_as_module") or "")
    if not module:
        return [binary]
    bindir = Path(binary).parent
    for candidate in ("python3", "python", "python3.exe", "python.exe"):
        interpreter = bindir / candidate
        if interpreter.is_file():
            return [str(interpreter), "-m", module]
    return [*python_command(), "-m", module]


def _runner_argv(spec: dict[str, Any], root: Path) -> list[str] | None:
    """How to start this runner here, or None when it is not available.

    A packaged build has no console script for pytest, but it does have the
    module: `tool_argv` then answers with the ``navin python -m pytest`` form.
    """
    from navin.quality.linters import tool_argv

    binary = _binary_for(spec, root)
    if binary is not None:
        return _launch_argv(spec, binary)
    return tool_argv(spec, root)


def _run_one(
    name: str, spec: dict[str, Any], root: Path, target: str | list[str] | None,
    *, on_output: Callable[[str], None] | None = None,
) -> TestOutcome:
    targets = [target] if isinstance(target, str) else list(target or [])
    root = _outside_project_root(root, targets) or root
    workdir = _workdir_for(spec, root, targets[0] if targets else None)
    if not (_detected(spec, workdir) or _detected(spec, root)):
        return TestOutcome(
            runner=name, ran=False, skipped_reason="no test layout or config detected"
        )
    targets = [_target_for_workdir(item, workdir, root) for item in targets]
    location = wsl_route.wsl_root(root)
    if location is not None:
        # A WSL project seen from Windows: its runner, interpreter and
        # dependencies live in the distribution, so the run happens there.
        launch: list[str] | None = []
    else:
        # The subproject's own tools first (client/node_modules/.bin/vitest,
        # backend/.venv/bin/pytest), then the root's.
        launch = _runner_argv(spec, workdir) if workdir != root else None
        launch = launch or _runner_argv(spec, root)
    if launch is None:
        wanted = (spec.get("binary") or {}).get("name", name)
        return TestOutcome(runner=name, ran=False, skipped_reason=f"{wanted} not installed")

    parser = str(spec.get("parser", "summary"))
    report_path: Path | None = None
    suffix = _REPORT_SUFFIXES.get(parser)
    if suffix is not None:
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            suffix=suffix, prefix="navin-tests-", delete=False
        )
        handle.close()
        report_path = Path(handle.name)

    argv = list(launch)
    spelled_root = location.posix if location is not None else str(root)
    spelled_report = (
        wsl_route.linux_path(report_path, location)
        if location is not None and report_path is not None else str(report_path)
    )
    for arg in spec.get("args", []):
        text = str(arg).replace("{root}", spelled_root)
        if "{report}" in text:
            if report_path is None:
                continue
            text = text.replace("{report}", spelled_report)
        argv.append(text)
    for item in targets:
        if location is not None and wsl.parse_unc(item.split("::", 1)[0]) is not None:
            item = wsl_route.linux_path(item, location)
        for arg in spec.get("target_args", []):
            argv.append(str(arg).replace("{target}", item))
    if on_output is not None and name == "pytest":
        argv.extend(["-o", "console_output_style=progress", "-vv"])
    cwd = str(workdir)
    if location is not None:
        argv = wsl_route.tool_argv(spec, root, workdir, argv, location=location) or argv
        cwd = wsl_route.launch_cwd()

    started = time.monotonic()
    try:
        # Runners spawn workers (xdist, vitest threads, npx -> node): the
        # timeout must kill the whole tree, not only the launcher.
        execute = _run_streaming if on_output is not None else run_killing_tree
        completed = execute(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(spec.get("timeout_s", 600)),
            **({"on_output": on_output} if on_output is not None else {}),
        )
    except subprocess.TimeoutExpired:
        wsl_route.note_launch(argv, timed_out=True)
        if report_path is not None:
            report_path.unlink(missing_ok=True)
        return TestOutcome(
            runner=name,
            ran=False,
            skipped_reason=f"timed out after {spec.get('timeout_s', 600)}s",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if report_path is not None:
            report_path.unlink(missing_ok=True)
        return TestOutcome(runner=name, ran=False, skipped_reason=str(exc))

    wsl_route.note_launch(argv, timed_out=False, completed=completed)
    duration_ms = int((time.monotonic() - started) * 1000)
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if location is not None and completed.returncode == wsl_route.MISSING_EXIT:
        if report_path is not None:
            report_path.unlink(missing_ok=True)
        wanted = (spec.get("binary") or {}).get("name", name)
        return TestOutcome(
            runner=name, ran=False, duration_ms=duration_ms,
            skipped_reason=f"{wanted} not installed in the WSL distribution {location.distro}",
        )

    try:
        if parser == "junit_xml" and report_path is not None and report_path.exists():
            passed, failed, skipped, total, failures = _parse_junit_xml(report_path, root)
        elif parser == "go_json":
            passed, failed, skipped, total, failures = _parse_go_json(output)
        elif parser == "unittest_text":
            passed, failed, skipped, total, failures = _parse_unittest_text(output, root)
        elif parser == "cargo_text":
            passed, failed, skipped, total, failures = _parse_cargo_text(output)
        elif parser == "jest_json" and report_path is not None and report_path.exists():
            passed, failed, skipped, total, failures = _parse_jest_json(report_path, root)
        else:
            passed, failed, skipped, total, failures = _parse_summary(spec, output)
    finally:
        if report_path is not None:
            report_path.unlink(missing_ok=True)

    outcome = TestOutcome(
        runner=name,
        ran=True,
        passed=passed,
        failed=failed,
        skipped=skipped,
        total=total,
        failures=failures,
        duration_ms=duration_ms,
        exit_code=completed.returncode,
        log_tail=_log_tail(output),
    )
    if total == 0 and completed.returncode != 0:
        # The runner itself failed (import error, bad config): say so rather
        # than reporting a misleading "0 tests passed".
        outcome.skipped_reason = "runner exited nonzero without reporting tests"
    elif total == 0:
        # A green run of nothing is not a green run. `unittest discover` reports
        # this whenever the test directory is not an importable package, and it
        # must not read as a pass.
        outcome.skipped_reason = "runner found no tests to run"
    return outcome


def _run_streaming(argv: list[str], *, on_output: Callable[[str], None], **kwargs: Any):
    """Drain runner output without pipes filling up, preserving the time limit."""
    timeout = kwargs.pop("timeout")
    # creationflags / start_new_session too: the Popen below spreads
    # detached_no_window_kwargs(). Passing one twice was a TypeError on every Windows run.
    for key in ("capture_output", "text", "encoding", "errors", "creationflags", "start_new_session"):
        kwargs.pop(key, None)
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="navin-test-output-") as logdir:
        log = Path(logdir) / "output.log"
        with log.open("wb") as output, log.open("rb") as reader, subprocess.Popen(
            argv, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}, **kwargs,
            **detached_no_window_kwargs(),
        ) as process:
            tail = b""
            try:
                while True:
                    try:
                        process.wait(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                    except subprocess.TimeoutExpired:
                        pass
                    chunk = reader.read()
                    if chunk:
                        tail = (tail + chunk)[-16000:]
                        on_output(tail.decode("utf-8", errors="replace"))
                    if process.poll() is not None:
                        break
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, timeout)
            finally:
                if process.poll() is None:
                    kill_process_tree(process)
                    try:
                        # SIGKILL lands at once; the bound only guards a child
                        # stuck in an uninterruptible kernel wait (D state).
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logger.warning("Test runner {} did not exit after kill", process.pid)
            reader.seek(0)
            text = reader.read().decode("utf-8", errors="replace")
            return subprocess.CompletedProcess(argv, process.returncode, stdout=text, stderr="")


def _log_tail(output: str) -> str:
    """Last of the output, minus any machine-readable payload already parsed.

    A runner asked for JSON usually prints it to stdout as well as to the report
    file. That copy is one enormous line, so it would crowd out the human summary
    that follows it and swallow the agent's context for no added information.
    """
    kept = [
        line
        for line in output.strip().splitlines()
        if not (len(line) > 2000 and line.lstrip().startswith(("{", "[")))
    ]
    return "\n".join(kept)[-_MAX_LOG_CHARS:]
