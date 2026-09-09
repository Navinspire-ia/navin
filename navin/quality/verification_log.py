# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Record of real verification runs (verify / test_run / lint) per project.

The board's "done requires evidence" gate used to accept any prose string,
which a model can invent without ever running a test. This log is written
only by the quality tools when they actually execute, so "the tests passed"
becomes a checkable claim: the gate looks the run up here instead of
trusting the sentence.

Storage is one small JSON file under the project's ``.navin/quality/``:
human-inspectable, survives gateway restarts, and scoped to the workspace
the way the board itself is.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

_LOG_NAME = "verification-log.json"
_MAX_ENTRIES = 20
# A passing run older than this predates the change it is supposed to cover;
# closing a step on it is exactly the stale-evidence problem the gate exists
# to stop. Long suites finish minutes before the move-to-done, so the window
# is generous rather than tight.
DEFAULT_MAX_AGE_S = 30 * 60


def _log_path(root: Path | str) -> Path:
    return Path(root).expanduser() / ".navin" / "quality" / _LOG_NAME


def record_verification(
    root: Path | str,
    *,
    source: str,
    ok: bool,
    tests_ran: bool = False,
    summary: str = "",
) -> None:
    """Append one real verification run. Never raises: recording is advisory."""
    entry = {
        "ts": time.time(),
        "source": source,
        "ok": bool(ok),
        "tests_ran": bool(tests_ran),
        "summary": (summary or "").strip()[:400],
    }
    path = _log_path(root)
    try:
        entries = _read_entries(path)
        entries.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(entries[-_MAX_ENTRIES:], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        logger.debug("verification log write failed for {}", path, exc_info=True)


def last_verification(root: Path | str) -> dict[str, Any] | None:
    """The most recent recorded run for this project, or None."""
    entries = _read_entries(_log_path(root))
    return entries[-1] if entries else None


def last_verification_summary(root: Path | str, *, limit: int = 3) -> str:
    """Compact digest of the last N runs, newest first, for the model prompt."""
    entries = _read_entries(_log_path(root))
    if not entries:
        return ""
    lines: list[str] = []
    for entry in reversed(entries[-max(1, limit):]):
        source = str(entry.get("source") or "verify")
        verdict = "PASS" if entry.get("ok") else "FAIL"
        summary = str(entry.get("summary") or "").strip()
        if summary:
            lines.append(f"{source} {verdict}: {summary}")
        else:
            lines.append(f"{source} {verdict}")
    return "\n".join(lines)


def refusal_to_close_without_proof(
    root: Path | str,
    *,
    require_tests: bool = False,
    max_age_s: float = DEFAULT_MAX_AGE_S,
) -> str | None:
    """Why a step must not be closed yet, or None when a fresh green run exists.

    The message is written for the model: it names the exact tool call that
    unblocks the close, so a refusal converts directly into the next action.
    ``require_tests`` additionally rejects lint-only passes (validation=test
    means tests, not a clean linter).
    """
    last = last_verification(root)
    if last is None:
        return (
            "no verification run is recorded for this project. Run "
            "`verify action=check` (or `test_run action=run`) and let it "
            "pass first; prose evidence alone is not accepted."
        )
    age_s = max(0.0, time.time() - float(last.get("ts") or 0.0))
    age_min = int(age_s // 60)
    if not last.get("ok"):
        summary = str(last.get("summary") or "").strip()
        detail = f": {summary}" if summary else ""
        return (
            f"the last verification run ({last.get('source')}, {age_min} min "
            f"ago) FAILED{detail}. Fix the failures and re-run "
            "`verify action=check` until it passes."
        )
    if age_s > max_age_s:
        return (
            f"the last passing verification is {age_min} min old and may "
            "predate your changes. Re-run `verify action=check` and close "
            "the step while it is green."
        )
    if require_tests and not last.get("tests_ran"):
        return (
            "the last verification passed but did not run any tests, and this "
            "step declares validation=test. Run `test_run action=run` (or "
            "`verify action=check` with tests) first."
        )
    return None


def _read_entries(path: Path) -> list[dict[str, Any]]:
    try:
        if not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        return [e for e in raw if isinstance(e, dict)]
    except Exception:
        logger.debug("verification log read failed for {}", path, exc_info=True)
        return []
