"""Test collection for the IDE Test Explorer.

``navin.quality.testing`` already knows how to detect suites and run them
with parsed results; what the explorer adds is the list of individual tests
*before* anything runs, so the panel can show a tree and offer targeted
runs. Collection reuses the same runner table and binary resolution, so a
test that appears here is runnable with the exact same launch arguments.

Per-runner support is honest rather than uniform: pytest lists full test
ids, vitest lists tests (falling back to file-level entries), jest lists
files, and go/cargo report the suite without item collection - the UI can
still run those as a whole.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from navin.quality.testing import (
    _detected,
    _runner_argv,
    _workdir_for,
    detect_suites,
    runner_table,
)
from navin.utils.proc import no_window_kwargs

_COLLECT_TIMEOUT_S = 25.0
_MAX_TESTS_PER_SUITE = 2_000


def _relative(path: str, workdir: Path) -> str:
    try:
        return Path(path).resolve().relative_to(workdir.resolve()).as_posix()
    except (ValueError, OSError):
        return path.replace("\\", "/")


def _run_collect(
    argv: list[str], workdir: Path
) -> tuple[str, str, int] | tuple[None, str, int]:
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_COLLECT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return None, "collection timed out", -1
    except OSError as exc:
        return None, str(exc), -1
    return completed.stdout, completed.stderr, completed.returncode


def _collect_pytest(launch: list[str], workdir: Path) -> tuple[list[dict[str, Any]], str]:
    stdout, stderr, code = _run_collect(
        [*launch, "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
        workdir,
    )
    if stdout is None:
        return [], stderr
    tests: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        # `-q` prints one `path::[Class::]test` id per line, then a blank
        # line and a summary ("N tests collected in ..."). Ids are the only
        # lines containing the module separator.
        if "::" not in line:
            continue
        file_part, _, rest = line.partition("::")
        suite, _, name = rest.rpartition("::")
        tests.append(
            {
                "id": line,
                "run_target": line,
                "file": file_part.replace("\\", "/"),
                "suite": suite,
                "name": name or rest,
            }
        )
        if len(tests) >= _MAX_TESTS_PER_SUITE:
            break
    error = ""
    if not tests and code not in (0, 5):  # 5 = pytest "no tests collected"
        error = (stderr or stdout or "collection failed").strip()[-1000:]
    return tests, error


def _collect_vitest(launch: list[str], workdir: Path) -> tuple[list[dict[str, Any]], str]:
    stdout, stderr, code = _run_collect([*launch, "list", "--json"], workdir)
    if stdout is not None and code == 0:
        try:
            rows = json.loads(stdout[stdout.index("[") :])
        except (ValueError, json.JSONDecodeError):
            rows = None
        if isinstance(rows, list):
            tests: list[dict[str, Any]] = []
            for row in rows[:_MAX_TESTS_PER_SUITE]:
                if not isinstance(row, dict):
                    continue
                file_rel = _relative(str(row.get("file", "")), workdir)
                name = str(row.get("name", "")).strip()
                if not file_rel or not name:
                    continue
                suite, _, short = name.rpartition(" > ")
                tests.append(
                    {
                        "id": f"{file_rel}::{name}",
                        # The runner table forwards one positional target:
                        # vitest filters by file, not by test name, there.
                        "run_target": file_rel,
                        "file": file_rel,
                        "suite": suite,
                        "name": short or name,
                    }
                )
            return tests, ""
    # Older vitest without `list --json`: fall back to file-level entries.
    stdout, stderr, code = _run_collect(
        [*launch, "list", "--filesOnly"], workdir
    )
    if stdout is None or code != 0:
        return [], (stderr or "").strip()[-1000:]
    tests = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or " " in line:
            continue
        file_rel = _relative(line, workdir)
        tests.append(
            {
                "id": file_rel,
                "run_target": file_rel,
                "file": file_rel,
                "suite": "",
                "name": file_rel.rsplit("/", 1)[-1],
            }
        )
        if len(tests) >= _MAX_TESTS_PER_SUITE:
            break
    return tests, ""


def _collect_jest(launch: list[str], workdir: Path) -> tuple[list[dict[str, Any]], str]:
    stdout, stderr, code = _run_collect([*launch, "--listTests"], workdir)
    if stdout is None or code != 0:
        return [], (stderr or "").strip()[-1000:]
    tests: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        file_rel = _relative(line, workdir)
        tests.append(
            {
                "id": file_rel,
                "run_target": file_rel,
                "file": file_rel,
                "suite": "",
                "name": file_rel.rsplit("/", 1)[-1],
            }
        )
        if len(tests) >= _MAX_TESTS_PER_SUITE:
            break
    return tests, ""


_COLLECTORS = {
    "pytest": _collect_pytest,
    "vitest": _collect_vitest,
    "jest": _collect_jest,
}


def collect_tests(root: Path) -> dict[str, Any]:
    """Detected suites plus, where supported, their individual tests."""
    # Binary resolution can answer root-relative paths (node_modules/.bin);
    # collectors then run with cwd=workdir, where a relative launch breaks.
    root = Path(root).resolve()
    table = runner_table()
    rows = detect_suites(root)
    has_primary = any(r["available"] and not r["fallback_only"] for r in rows)
    suites: list[dict[str, Any]] = []
    for row in rows:
        # unittest is a pytest fallback; listing both would duplicate every
        # Python test in the tree.
        if row["fallback_only"] and has_primary:
            continue
        entry: dict[str, Any] = {
            "runner": row["runner"],
            "language": row["language"],
            "available": row["available"],
            "workdir": row["workdir"],
            "reason": row["reason"],
            "collect_supported": row["runner"] in _COLLECTORS,
            "tests": [],
            "error": "",
            "duration_ms": 0,
        }
        collector = _COLLECTORS.get(row["runner"])
        if row["available"] and collector is not None:
            spec = table.get(row["runner"]) or {}
            workdir = _workdir_for(spec, root)
            if _detected(spec, workdir) or _detected(spec, root):
                launch = _runner_argv(spec, root)
                if launch is not None:
                    started = time.monotonic()
                    try:
                        tests, error = collector(launch, workdir)
                    except Exception as exc:
                        tests, error = [], str(exc)
                    entry["tests"] = tests
                    entry["error"] = error
                    entry["duration_ms"] = int((time.monotonic() - started) * 1000)
        suites.append(entry)
    return {"root": str(root), "suites": suites}
