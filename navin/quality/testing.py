"""Test suite detection and structured execution.

The point of this module is that the agent stops reading raw test logs. Runs
produce counts and per-failure detail (test name, file, line, message), so the
agent can act on a failure instead of guessing from truncated output.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.quality.linters import _binary_for, _read_json
from navin.utils.proc import no_window_kwargs

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


def _workdir_for(spec: dict[str, Any], root: Path) -> Path:
    """Pick the directory this suite actually lives in.

    Candidates usually start with ``"."``, which always exists, so choosing the
    first directory present would always answer the project root and never look
    into ``webui`` or ``frontend``. A suite configured only in a subdirectory
    would then be reported as absent and silently skipped by ``verify``.
    """
    existing = [
        candidate
        for rel in spec.get("workdir", [])
        if (candidate := root / str(rel)).is_dir()
    ]
    for candidate in existing:
        if _detected(spec, candidate):
            return candidate
    return existing[0] if existing else root


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
    for name, spec in sorted(runner_table().items()):
        workdir = _workdir_for(spec, root)
        detected = _detected(spec, workdir) or _detected(spec, root)
        reason = ""
        if not detected:
            reason = "no test layout or config detected"
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


def run_tests(
    root: Path,
    *,
    runners: list[str] | None = None,
    target: str | None = None,
) -> list[TestOutcome]:
    """Run the project's test suites and return structured outcomes."""
    table = runner_table()
    selected = runners if runners is not None else _primary_runners(root)
    outcomes: list[TestOutcome] = []
    for name in selected:
        spec = table.get(name)
        if spec is None:
            outcomes.append(
                TestOutcome(runner=name, ran=False, skipped_reason="unknown runner")
            )
            continue
        outcomes.append(_run_one(name, spec, root, target))
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
    name: str, spec: dict[str, Any], root: Path, target: str | None
) -> TestOutcome:
    workdir = _workdir_for(spec, root)
    if not (_detected(spec, workdir) or _detected(spec, root)):
        return TestOutcome(
            runner=name, ran=False, skipped_reason="no test layout or config detected"
        )
    launch = _runner_argv(spec, root)
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
    for arg in spec.get("args", []):
        text = str(arg).replace("{root}", str(root))
        if "{report}" in text:
            if report_path is None:
                continue
            text = text.replace("{report}", str(report_path))
        argv.append(text)
    if target:
        for arg in spec.get("target_args", []):
            argv.append(str(arg).replace("{target}", target))

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(spec.get("timeout_s", 600)),
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
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

    duration_ms = int((time.monotonic() - started) * 1000)
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"

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
