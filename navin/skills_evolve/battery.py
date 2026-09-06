"""The frozen exam battery (S2.2).

Three suites, ``code`` / ``browser`` / ``desk``, twenty cases, shipped as
``navin/skills_evolve/exams/battery.json``. The battery is *versioned by
content*: ``Battery.version`` is ``<battery>-r<revision>-<sha256[:12]>`` of
the canonical JSON, so editing one case yields a new version, and a score
is only comparable to another score of the same version.

The corrector (S2.3) never sees this module: it receives the failing prompts
and the missing expectations from the exam report and cannot open the file.
``Battery.verify_unchanged()`` re-hashes the file after every run; a
mismatch raises ``ExamTamperedError`` and the pipeline aborts without promoting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.evals.runner import EvalCase

BATTERY_FILE = Path(__file__).resolve().parent / "exams" / "battery.json"
REQUIRED_SUITES = ("code", "browser", "desk")
MIN_SUITES = 3


class ExamTamperedError(RuntimeError):
    """The battery file changed while an exam was using it."""


class BatteryInvalidError(ValueError):
    """The battery file does not describe a usable exam."""


@dataclass(frozen=True, slots=True)
class Suite:
    id: str
    title: str
    cases: tuple[EvalCase, ...]

    @property
    def size(self) -> int:
        return len(self.cases)


@dataclass(frozen=True, slots=True)
class Battery:
    version: str
    revision: int
    suites: tuple[Suite, ...]
    source: Path
    fingerprint: str = field(repr=False)

    @property
    def total_cases(self) -> int:
        return sum(suite.size for suite in self.suites)

    def suite(self, suite_id: str) -> Suite | None:
        for suite in self.suites:
            if suite.id == suite_id:
                return suite
        return None

    def verify_unchanged(self) -> None:
        """Raise ``ExamTamperedError`` when the file no longer matches this battery."""
        current = _fingerprint(_read_raw(self.source))
        if current != self.fingerprint:
            raise ExamTamperedError(
                f"exam battery {self.source} changed during the run "
                f"({self.fingerprint[:12]} -> {current[:12]})"
            )

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "total_cases": self.total_cases,
            "suites": [
                {"id": suite.id, "title": suite.title, "cases": suite.size}
                for suite in self.suites
            ],
        }


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatteryInvalidError(f"cannot read exam battery {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BatteryInvalidError(f"exam battery {path} must be a JSON object")
    return raw


def _fingerprint(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _case(suite_id: str, data: Any, index: int) -> EvalCase:
    if not isinstance(data, dict):
        raise BatteryInvalidError(f"suite {suite_id}: case #{index} must be an object")
    case_id = str(data.get("id") or f"{suite_id}-{index}")
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} has no prompt")
    contains = data.get("expect_contains") or []
    forbid = data.get("forbid") or []
    if not isinstance(contains, list) or not isinstance(forbid, list):
        raise BatteryInvalidError(f"suite {suite_id}: case {case_id} expectations must be lists")
    return EvalCase(
        id=case_id,
        input=prompt,
        expect_contains=tuple(str(item) for item in contains),
        category=suite_id,
        expect={"forbid": [str(item) for item in forbid]},
    )


def load_battery(path: Path | None = None) -> Battery:
    """Parse and validate the battery. Three suites minimum, all non-empty."""
    source = Path(path) if path is not None else BATTERY_FILE
    raw = _read_raw(source)
    suites_raw = raw.get("suites")
    if not isinstance(suites_raw, list) or len(suites_raw) < MIN_SUITES:
        raise BatteryInvalidError(f"exam battery needs at least {MIN_SUITES} suites")
    suites: list[Suite] = []
    seen: set[str] = set()
    for entry in suites_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise BatteryInvalidError("every suite needs a string id")
        suite_id = entry["id"]
        if suite_id in seen:
            raise BatteryInvalidError(f"duplicate suite id {suite_id}")
        seen.add(suite_id)
        cases_raw = entry.get("cases")
        if not isinstance(cases_raw, list) or not cases_raw:
            raise BatteryInvalidError(f"suite {suite_id} has no cases")
        cases = tuple(_case(suite_id, data, index) for index, data in enumerate(cases_raw, 1))
        suites.append(Suite(id=suite_id, title=str(entry.get("title") or suite_id), cases=cases))
    missing = [suite_id for suite_id in REQUIRED_SUITES if suite_id not in seen]
    if missing and source == BATTERY_FILE:
        raise BatteryInvalidError(f"bundled battery is missing suites: {', '.join(missing)}")
    fingerprint = _fingerprint(raw)
    revision = raw.get("revision")
    revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    name = str(raw.get("battery") or "exam")
    return Battery(
        version=f"{name}-r{revision}-{fingerprint[:12]}",
        revision=revision,
        suites=tuple(suites),
        source=source,
        fingerprint=fingerprint,
    )


def battery_version(path: Path | None = None) -> str:
    return load_battery(path).version
