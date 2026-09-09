# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The frozen policy battery (S4.3).

Three suites, ``code`` / ``browser`` / ``desk``, shipped as
``navin/policy/exams/battery.json``: each case is a fixture workspace, a
request, the tool script a competent agent follows and the eval check that
decides the reward. The same three suites as S2, with tools instead of text.

Every case carries a frozen ``split``: ``train`` cases feed the adapter,
``heldout`` cases are the exam the adapter never saw. The battery is
*versioned by content* (``policy-r<revision>-<sha256[:12]>``): editing a
case, a script or a split yields a new version, and a score is only
comparable to another score of the same version. ``verify_unchanged``
re-hashes the files after a run; a mismatch raises ``BatteryTamperedError``
and nothing is promoted. Reworking the exam to make N+1 win is the cheat S2.3
forbids for skills; the same rule holds here.

A project may add its own cases in ``.navin/policy/cases.jsonl`` (one JSON
object per line, same schema plus ``suite``). They enter the version hash
like the bundled ones, so a project that adds cases gets a new exam version,
never a quietly different one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.policy.paths import cases_path

BATTERY_FILE = Path(__file__).resolve().parent / "exams" / "battery.json"
REQUIRED_SUITES = ("code", "browser", "desk")
MIN_SUITES = 3
SPLITS = ("train", "heldout")
# What ``navin.evals.agent_loop._check_case`` knows how to verify; anything
# else is a typo that would reward a case for nothing.
EXPECT_KEYS = frozenset(
    {"tools_ok", "tools_blocked", "no_writes", "files_contain", "files_absent", "stop_reason", "final_contains"}
)
STEP_KEYS = frozenset({"tool", "args", "final"})
_MAX_PROJECT_CASES = 200
_MAX_FIXTURE_BYTES = 256 * 1024


class BatteryTamperedError(RuntimeError):
    """The battery changed on disk while an exam was using it."""


class BatteryInvalidError(ValueError):
    """The battery does not describe a usable exam."""


@dataclass(frozen=True, slots=True)
class PolicyCase:
    id: str
    suite: str
    split: str
    prompt: str
    workspace: dict[str, str]
    script: tuple[Mapping[str, Any], ...]
    expect: dict[str, Any]
    composer_mode: str = "agent"
    origin: str = "bundled"  # bundled | project

    @property
    def heldout(self) -> bool:
        return self.split == "heldout"


@dataclass(frozen=True, slots=True)
class PolicySuite:
    id: str
    title: str
    cases: tuple[PolicyCase, ...]

    @property
    def size(self) -> int:
        return len(self.cases)

    @property
    def heldout_cases(self) -> tuple[PolicyCase, ...]:
        return tuple(c for c in self.cases if c.heldout)

    @property
    def train_cases(self) -> tuple[PolicyCase, ...]:
        return tuple(c for c in self.cases if not c.heldout)


@dataclass(frozen=True, slots=True)
class PolicyBattery:
    version: str
    revision: int
    suites: tuple[PolicySuite, ...]
    source: Path
    project_source: Path | None
    fingerprint: str = field(repr=False)

    @property
    def cases(self) -> list[PolicyCase]:
        return [case for suite in self.suites for case in suite.cases]

    @property
    def total_cases(self) -> int:
        return sum(suite.size for suite in self.suites)

    def suite(self, suite_id: str) -> PolicySuite | None:
        for suite in self.suites:
            if suite.id == suite_id:
                return suite
        return None

    def verify_unchanged(self) -> None:
        """Raise ``BatteryTamperedError`` when the files no longer match this battery."""
        current = _fingerprint(_read_bundled(self.source), _read_project(self.project_source))
        if current != self.fingerprint:
            raise BatteryTamperedError(
                f"policy battery changed during the run ({self.fingerprint[:12]} -> {current[:12]})"
            )

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "total_cases": self.total_cases,
            "project_cases": sum(1 for c in self.cases if c.origin == "project"),
            "suites": [
                {
                    "id": suite.id,
                    "title": suite.title,
                    "cases": suite.size,
                    "train": len(suite.train_cases),
                    "heldout": len(suite.heldout_cases),
                }
                for suite in self.suites
            ],
        }


def _read_bundled(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatteryInvalidError(f"cannot read policy battery {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BatteryInvalidError(f"policy battery {path} must be a JSON object")
    return raw


def _read_project(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BatteryInvalidError(f"{path}:{line_no}: {exc}") from exc
        if not isinstance(data, dict):
            raise BatteryInvalidError(f"{path}:{line_no}: a case must be an object")
        rows.append(data)
        if len(rows) >= _MAX_PROJECT_CASES:
            break
    return rows


def _fingerprint(bundled: dict[str, Any], project: list[dict[str, Any]]) -> str:
    canonical = json.dumps({"bundled": bundled, "project": project}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case(suite_id: str, data: Any, index: int, *, origin: str) -> PolicyCase:
    if not isinstance(data, dict):
        raise BatteryInvalidError(f"suite {suite_id}: case #{index} must be an object")
    case_id = str(data.get("id") or f"{suite_id}-{index}")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} has no prompt")
    split = str(data.get("split") or "train")
    if split not in SPLITS:
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} split must be train or heldout")
    script = data.get("script")
    if not isinstance(script, list) or not script:
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} needs a non-empty script")
    for step_no, step in enumerate(script):
        if not isinstance(step, dict) or not ("tool" in step or "final" in step):
            raise BatteryInvalidError(f"suite {suite_id}: case {case_id} step {step_no} needs 'tool' or 'final'")
        unknown_step_keys = set(step) - STEP_KEYS
        if unknown_step_keys:
            raise BatteryInvalidError(
                f"suite {suite_id}: case {case_id} step {step_no} has unknown keys {sorted(unknown_step_keys)} "
                f"(allowed: {sorted(STEP_KEYS)})"
            )
        if "args" in step and not isinstance(step["args"], dict):
            raise BatteryInvalidError(f"suite {suite_id}: case {case_id} step {step_no} args must be an object")
    workspace = data.get("workspace") or {}
    if not isinstance(workspace, dict):
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} workspace must be an object")
    files = {str(k): str(v) for k, v in workspace.items()}
    if sum(len(v.encode("utf-8")) for v in files.values()) > _MAX_FIXTURE_BYTES:
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} fixture is too large")
    for rel in files:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise BatteryInvalidError(f"suite {suite_id}: case {case_id} fixture path {rel!r} must stay inside the sandbox")
    expect = data.get("expect") or {}
    if not isinstance(expect, dict) or not expect:
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} needs an expect block (the reward)")
    unknown_expect = set(expect) - EXPECT_KEYS
    if unknown_expect:
        # An unknown check would silently pass and hand out a reward for nothing.
        raise BatteryInvalidError(
            f"suite {suite_id}: case {case_id} expect has unknown keys {sorted(unknown_expect)} "
            f"(allowed: {sorted(EXPECT_KEYS)})"
        )
    return PolicyCase(
        id=case_id,
        suite=suite_id,
        split=split,
        prompt=prompt,
        workspace=files,
        script=tuple(dict(step) for step in script),
        expect=dict(expect),
        composer_mode=str(data.get("composer_mode") or "agent"),
        origin=origin,
    )


def load_battery(path: Path | None = None, *, workspace: Path | str | None = None) -> PolicyBattery:
    """Parse and validate the battery, project cases merged in.

    Three suites minimum; every suite needs at least two train cases and one
    held-out case, so each suite can be scored on steps the adapter never saw.
    """
    source = Path(path) if path is not None else BATTERY_FILE
    project_source = cases_path(workspace) if workspace is not None else None
    bundled = _read_bundled(source)
    project = _read_project(project_source)
    suites_raw = bundled.get("suites")
    if not isinstance(suites_raw, list) or len(suites_raw) < MIN_SUITES:
        raise BatteryInvalidError(f"policy battery needs at least {MIN_SUITES} suites")
    cases_by_suite: dict[str, list[PolicyCase]] = {}
    titles: dict[str, str] = {}
    seen_ids: set[str] = set()
    for entry in suites_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise BatteryInvalidError("every suite needs a string id")
        suite_id = entry["id"]
        if suite_id in cases_by_suite:
            raise BatteryInvalidError(f"duplicate suite id {suite_id}")
        titles[suite_id] = str(entry.get("title") or suite_id)
        cases_raw = entry.get("cases")
        if not isinstance(cases_raw, list) or not cases_raw:
            raise BatteryInvalidError(f"suite {suite_id} has no cases")
        cases_by_suite[suite_id] = []
        for index, data in enumerate(cases_raw, 1):
            case = _case(suite_id, data, index, origin="bundled")
            if case.id in seen_ids:
                raise BatteryInvalidError(f"duplicate case id {case.id}")
            seen_ids.add(case.id)
            cases_by_suite[suite_id].append(case)
    for index, data in enumerate(project, 1):
        suite_id = str(data.get("suite") or "")
        if suite_id not in cases_by_suite:
            raise BatteryInvalidError(f"project case #{index}: suite must be one of {', '.join(cases_by_suite)}")
        case = _case(suite_id, data, index, origin="project")
        if case.id in seen_ids:
            raise BatteryInvalidError(f"project case {case.id} duplicates a bundled id")
        seen_ids.add(case.id)
        cases_by_suite[suite_id].append(case)
    suites: list[PolicySuite] = []
    for suite_id, cases in cases_by_suite.items():
        suite = PolicySuite(id=suite_id, title=titles[suite_id], cases=tuple(cases))
        if len(suite.train_cases) < 2 or not suite.heldout_cases:
            raise BatteryInvalidError(f"suite {suite_id} needs at least two train cases and one held-out case")
        suites.append(suite)
    missing = [suite_id for suite_id in REQUIRED_SUITES if suite_id not in cases_by_suite]
    if missing and source == BATTERY_FILE:
        raise BatteryInvalidError(f"bundled policy battery is missing suites: {', '.join(missing)}")
    fingerprint = _fingerprint(bundled, project)
    revision = bundled.get("revision")
    revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    name = str(bundled.get("battery") or "policy")
    return PolicyBattery(
        version=f"{name}-r{revision}-{fingerprint[:12]}",
        revision=revision,
        suites=tuple(suites),
        source=source,
        project_source=project_source,
        fingerprint=fingerprint,
    )


def battery_version(workspace: Path | str | None = None) -> str:
    return load_battery(workspace=workspace).version
