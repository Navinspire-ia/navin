# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""End-of-turn checks: lint and test what the turn changed before it ends.

Off by default (``NAVIN_STOP_CHECKS=1`` turns it on). When the agent is about
to finish a turn that edited files, the linters of those files and the tests
that exercise them (edited tests, tests next to or importing the edited
modules) run once, through the same engine as ``verify action=check``.

- Green: the turn ends, the user sees a one-line notice.
- Red on the changed files: the report goes back to the agent as a Stop reason,
  so it fixes the cause before answering.
- The same red result again with no new edit: the turn ends with a notice, so a
  failure the agent cannot fix never loops.
- Failures far from every changed file (pre-existing, environment): notice
  only, never a block.

The whole suite stays the job of CI: running it on every turn would cost
minutes and block on failures the turn did not cause.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger

_EDIT_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "Write", "Edit", "MultiEdit"})
_MAX_SESSIONS = 64
_MAX_PATHS = 200
_MAX_REPORT = 6000
_DEFAULT_TIMEOUT_S = 300.0


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off", ""}


def enabled() -> bool:
    return _flag("NAVIN_STOP_CHECKS", "0")


def timeout_s() -> float:
    try:
        value = float(os.environ.get("NAVIN_STOP_CHECKS_TIMEOUT", _DEFAULT_TIMEOUT_S))
    except ValueError:
        return _DEFAULT_TIMEOUT_S
    return value if value > 0 else _DEFAULT_TIMEOUT_S


def edited_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    """Paths written by a file tool call (single file or multi-file payloads)."""
    if tool_name not in _EDIT_TOOLS or not isinstance(tool_input, dict):
        return []
    found: list[str] = []
    for key in ("path", "file_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    for key in ("files", "edits"):
        items = tool_input.get(key)
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            for sub in ("path", "file_path"):
                value = item.get(sub)
                if isinstance(value, str) and value.strip():
                    found.append(value.strip())
    return list(dict.fromkeys(found))


def _relative(root: Path, raw: str) -> str | None:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None  # outside the project: not ours to test


class _State:
    """Per-session files edited since the last check, and the last red result."""

    def __init__(self) -> None:
        self._paths: OrderedDict[str, dict[str, None]] = OrderedDict()
        self._red: dict[str, str] = {}

    def add(self, session: str, paths: list[str]) -> None:
        slot = self._paths.setdefault(session, {})
        self._paths.move_to_end(session)
        while len(self._paths) > _MAX_SESSIONS:
            stale, _ = self._paths.popitem(last=False)
            self._red.pop(stale, None)
        for path in paths:
            if len(slot) < _MAX_PATHS:
                slot[path] = None

    def paths(self, session: str) -> list[str]:
        return list(self._paths.get(session, {}))

    def red(self, session: str) -> str:
        return self._red.get(session, "")

    def mark_red(self, session: str, fingerprint: str) -> None:
        self._red[session] = fingerprint

    def clear(self, session: str) -> None:
        self._paths.pop(session, None)
        self._red.pop(session, None)

    def reset(self) -> None:
        self._paths.clear()
        self._red.clear()


STATE = _State()


def record_edit(
    tool_name: str, tool_input: dict[str, Any], workspace: Path | None, session: str,
) -> None:
    """Remember the project files a file tool just wrote."""
    if not enabled() or workspace is None:
        return
    root = Path(workspace)
    paths = [rel for rel in (_relative(root, p) for p in edited_paths(tool_name, tool_input)) if rel]
    if paths:
        STATE.add(session, paths)


def _fingerprint(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha1()  # noqa: S324 - change detection, not security
    for rel in sorted(paths):
        digest.update(rel.encode("utf-8"))
        try:
            digest.update((root / rel).read_bytes())
        except OSError:
            digest.update(b"\0missing")
    return digest.hexdigest()


def _tests_line(report: Any) -> str:
    ran = [o for o in getattr(report, "test_outcomes", []) if getattr(o, "ran", False)]
    passed = sum(int(getattr(o, "passed", 0)) for o in ran)
    failed = sum(int(getattr(o, "failed", 0)) for o in ran)
    if not passed and not failed:
        return "no test covers these files"
    return f"{passed} test(s) passed" + (f", {failed} failed" if failed else "")


async def stop_checks(workspace: Path | None, session: str) -> tuple[str, str]:
    """(reason to keep working, user notice); empty strings mean nothing to say."""
    if not enabled() or workspace is None:
        return "", ""
    root = Path(workspace)
    paths = [rel for rel in STATE.paths(session) if (root / rel).is_file()]
    if not paths:
        STATE.clear(session)
        return "", ""
    fingerprint = await asyncio.to_thread(_fingerprint, root, paths)
    if STATE.red(session) == fingerprint:
        # Same files, same content, already reported: let the turn end.
        STATE.clear(session)
        return "", "End-of-turn checks still fail with no change since the last report; not blocking again."
    from navin.quality import verify as quality_verify

    limit = timeout_s()
    try:
        report = await asyncio.wait_for(
            asyncio.to_thread(quality_verify.verify_changes, root, paths, with_tests=True),
            timeout=limit,
        )
    except TimeoutError:
        STATE.clear(session)
        return "", f"End-of-turn checks did not finish within {int(limit)} s; the turn ends without them."
    except Exception as exc:  # noqa: BLE001 - a broken check never breaks a turn
        logger.warning("End-of-turn checks crashed: {}", exc)
        STATE.clear(session)
        return "", f"End-of-turn checks could not run: {exc}"

    errors = len(report.lint_errors)
    failed = int(report.failed_tests)
    if report.ok:
        STATE.clear(session)
        return "", (
            f"End-of-turn checks passed on {len(paths)} changed file(s): "
            f"lint clean, {_tests_line(report)}."
        )
    if not errors and report.failures_look_unrelated():
        STATE.clear(session)
        return "", (
            f"End-of-turn checks: {failed} failing test(s) away from the changed files, "
            "likely pre-existing; not blocking."
        )
    STATE.mark_red(session, fingerprint)
    body = report.render()
    if len(body) > _MAX_REPORT:
        body = body[:_MAX_REPORT] + "\n... (report truncated)"
    reason = (
        "End-of-turn checks failed on the files changed in this turn "
        f"({errors} lint error(s), {failed} failing test(s)). Fix the cause before finishing, "
        "or tell the user why it cannot be fixed. Never weaken, skip or delete a test to get green."
        f"\n\n{body}"
    )
    notice = (
        f"End-of-turn checks failed: {errors} lint error(s), {failed} failing test(s). "
        "The agent keeps working on it."
    )
    return reason, notice


def summary() -> str:
    """One line for the system prompt ('' when off)."""
    if not enabled():
        return ""
    return (
        "- end-of-turn checks: before a turn that edited files ends, Navin lints those files "
        "and runs the tests that cover them; a red result comes back as a Stop reason to fix."
    )
