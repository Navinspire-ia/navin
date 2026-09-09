# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exam runner: the same frozen battery, a score out of 20, a verdict (S2.2).

An exam grades a *skill set* (the project's ``.navin/skills`` layer, with or
without the draft under test) against ``Battery``. The runner reuses the
offline eval primitives of ``navin.evals.runner`` (``EvalCase``,
``scoreboard``) and adds what the corridor needs:

* a **budget** (wall-clock timeout plus a resident-memory ceiling) - an
  exam that exceeds it is a failure, never a promotion;
* a **verdict** against a baseline report: ``up`` / ``flat`` / ``down`` per
  suite and overall, and the project gate "up overall and no suite down";
* **tamper detection**: the battery file is re-hashed after the run.

Two models answer the prompts:

``LexicalSkillModel``
    Deterministic and offline. Its answer to a prompt is the set of skill
    lines that share vocabulary with the prompt, so the exam measures what
    the skill layer *says* for each situation. No network, milliseconds.

``LLMExamModel``
    The configured provider answers with the skill layer in its system
    prompt. Same battery, same scoring; costs tokens.

Neither model runs tools, so the exam never touches the real workspace; the
sandbox (``sandbox.py``) only exists so the draft is read from a copy.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from navin.evals.runner import EvalCase, Model
from navin.skills_evolve.battery import Battery, Suite

SCORE_MAX = 20
VERDICTS = ("up", "flat", "down")

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}", re.IGNORECASE)
_STOP = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "you", "your", "are", "not",
        "but", "have", "has", "was", "were", "will", "from", "into", "them",
        "then", "than", "when", "what", "which", "there", "here", "does", "did",
        "it", "its", "one", "our", "out", "can", "all", "any", "use", "used",
        "after", "before", "also", "each", "over", "just", "like", "how", "who",
        "les", "des", "une", "pour", "avec", "dans", "sur", "pas", "que", "qui",
    }
)


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


class ExamBudgetExceededError(RuntimeError):
    """Timeout or memory ceiling hit during an exam."""


@dataclass(frozen=True, slots=True)
class ExamBudget:
    timeout_s: float = 90.0
    max_rss_growth_mb: int = 768
    # Below this many bytes per answer the lexical model is unbounded anyway;
    # the cap protects the report file from a chatty LLM.
    max_output_chars: int = 6000


def _rss_bytes() -> int | None:
    """Resident set size of this process, Linux only (``/proc``)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            fields = handle.read().split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


class _BudgetClock:
    def __init__(self, budget: ExamBudget) -> None:
        self._budget = budget
        self._deadline = time.monotonic() + budget.timeout_s
        self._rss0 = _rss_bytes()

    def check(self, where: str) -> None:
        if time.monotonic() > self._deadline:
            raise ExamBudgetExceededError(f"exam timeout after {self._budget.timeout_s:.0f}s ({where})")
        if self._rss0 is not None:
            now = _rss_bytes()
            if now is not None:
                grown_mb = (now - self._rss0) / (1024 * 1024)
                if grown_mb > self._budget.max_rss_growth_mb:
                    raise ExamBudgetExceededError(
                        f"exam memory ceiling {self._budget.max_rss_growth_mb} MB exceeded ({where})"
                    )


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


def tokenize(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if token.lower() not in _STOP
    }


def skill_body_lines(markdown: str) -> list[str]:
    """Non-empty body lines of a SKILL.md, frontmatter dropped."""
    text = markdown.lstrip()
    if text.startswith("---"):
        match = re.match(r"^---\s*\r?\n.*?\r?\n---\s*\r?\n?", text, re.DOTALL)
        if match:
            text = text[match.end():]
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= {"-", "=", "#", "*", "|"}:
            continue
        lines.append(line)
    return lines


class LexicalSkillModel:
    """Answer = the skill lines that overlap the prompt vocabulary.

    A prompt with no matching line gets an empty answer, so an empty skill
    layer scores 0/20: the baseline of a project without skills.
    """

    def __init__(self, skills: Sequence[str], *, top_k: int = 12) -> None:
        self._lines: list[tuple[str, set[str]]] = []
        for markdown in skills:
            for line in skill_body_lines(markdown):
                tokens = tokenize(line)
                if tokens:
                    self._lines.append((line, tokens))
        self._top_k = top_k

    def complete(self, prompt: str) -> str:
        wanted = tokenize(prompt)
        if not wanted:
            return ""
        scored: list[tuple[int, int, str]] = []
        for index, (line, tokens) in enumerate(self._lines):
            overlap = len(wanted & tokens)
            if overlap:
                scored.append((-overlap, index, line))
        scored.sort()
        return "\n".join(line for _, _, line in scored[: self._top_k])


def _run_sync(coro: Any) -> Any:
    """Run a coroutine from sync code, even when a loop runs in this thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc

    worker = threading.Thread(target=_target, name="navin-skills-exam", daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class LLMExamModel:
    """The configured provider answers with the skill layer as system prompt."""

    SYSTEM = (
        "You are Navin, a coding and desk agent. Answer the user's request in a "
        "short, concrete plan (at most 12 lines). Follow the playbooks below when "
        "they apply and name the tools and checks you would use.\n\n"
    )

    def __init__(self, skills: Sequence[str], *, snapshot: Any | None = None, config_path: Any = None) -> None:
        if snapshot is None:
            from navin.providers.factory import load_provider_snapshot

            snapshot = load_provider_snapshot(config_path)
        self._provider = snapshot.provider
        self._model = snapshot.model
        joined = "\n\n".join(skills).strip()
        self._system = self.SYSTEM + (f"Playbooks:\n\n{joined}" if joined else "No playbooks.")

    def complete(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": prompt},
        ]
        response = _run_sync(
            self._provider.chat_with_retry(messages, model=self._model, temperature=0.0)
        )
        content = getattr(response, "content", None)
        return content if isinstance(content, str) else ""


ModelFactory = Callable[[Sequence[str]], Model]
"""Builds the model that answers for a given skill set (list of SKILL.md texts)."""


def lexical_model_factory(skills: Sequence[str]) -> Model:
    return LexicalSkillModel(skills)


def llm_model_factory(skills: Sequence[str]) -> Model:
    return LLMExamModel(skills)


def model_factory_for(kind: str) -> ModelFactory:
    return llm_model_factory if kind == "llm" else lexical_model_factory


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    suite: str
    passed: bool
    missing: tuple[str, ...]
    forbidden: tuple[str, ...]
    prompt: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "suite": self.suite,
            "passed": self.passed,
            "missing": list(self.missing),
            "forbidden": list(self.forbidden),
            "prompt": self.prompt,
        }


@dataclass(frozen=True, slots=True)
class SuiteScore:
    id: str
    passed: int
    total: int

    @property
    def score(self) -> int:
        return score_out_of_20(self.passed, self.total)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "passed": self.passed, "total": self.total, "score": self.score}


@dataclass(frozen=True, slots=True)
class ExamReport:
    battery_version: str
    passed: int
    total: int
    suites: tuple[SuiteScore, ...]
    outcomes: tuple[CaseOutcome, ...] = ()
    failed: bool = False
    reason: str | None = None
    duration_ms: int = 0

    @property
    def score(self) -> int:
        return 0 if self.failed else score_out_of_20(self.passed, self.total)

    def suite(self, suite_id: str) -> SuiteScore | None:
        for entry in self.suites:
            if entry.id == suite_id:
                return entry
        return None

    def failures(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.passed]

    def as_dict(self, *, with_outcomes: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "battery_version": self.battery_version,
            "score": self.score,
            "passed": self.passed,
            "total": self.total,
            "suites": [entry.as_dict() for entry in self.suites],
            "failed": self.failed,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
        }
        if with_outcomes:
            data["outcomes"] = [outcome.as_dict() for outcome in self.outcomes]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExamReport:
        suites = tuple(
            SuiteScore(id=str(s["id"]), passed=int(s["passed"]), total=int(s["total"]))
            for s in data.get("suites", [])
            if isinstance(s, dict)
        )
        return cls(
            battery_version=str(data.get("battery_version", "")),
            passed=int(data.get("passed", 0)),
            total=int(data.get("total", 0)),
            suites=suites,
            failed=bool(data.get("failed", False)),
            reason=data.get("reason"),
            duration_ms=int(data.get("duration_ms", 0)),
        )


def score_out_of_20(passed: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(SCORE_MAX * passed / total)


def _grade_case(model: Model, case: EvalCase, *, max_chars: int) -> CaseOutcome:
    try:
        output = model.complete(case.input)
    except Exception as exc:  # noqa: BLE001 - one bad answer is one failed case
        logger.debug("exam case {} raised: {}", case.id, exc)
        output = ""
    if not isinstance(output, str):
        output = str(output)
    output = output[:max_chars]
    lowered = output.lower()
    missing = tuple(needle for needle in case.expect_contains if needle.lower() not in lowered)
    forbid = case.expect.get("forbid") or ()
    forbidden = tuple(str(token) for token in forbid if str(token).lower() in lowered)
    return CaseOutcome(
        case_id=case.id,
        suite=case.category or "uncategorized",
        passed=not missing and not forbidden,
        missing=missing,
        forbidden=forbidden,
        prompt=case.input,
    )


def run_exam(
    battery: Battery,
    skills: Sequence[str],
    *,
    model_factory: ModelFactory = lexical_model_factory,
    budget: ExamBudget | None = None,
) -> ExamReport:
    """Grade one skill set against the whole battery.

    Never raises for a model error or a budget overrun: both come back as a
    report with ``failed=True`` (score 0), which the gate turns into "no
    promotion". ``ExamTamperedError`` does propagate: it is not a grade.
    """
    budget = budget or ExamBudget()
    clock = _BudgetClock(budget)
    started = time.monotonic()
    outcomes: list[CaseOutcome] = []
    failed_reason: str | None = None
    try:
        model = model_factory(list(skills))
        for suite in battery.suites:
            for case in suite.cases:
                clock.check(f"{suite.id}/{case.id}")
                outcomes.append(_grade_case(model, case, max_chars=budget.max_output_chars))
        clock.check("end")
    except ExamBudgetExceededError as exc:
        failed_reason = str(exc)
    except Exception as exc:  # noqa: BLE001 - a broken model is a failed exam
        failed_reason = f"exam model error: {exc}"
    battery.verify_unchanged()
    duration_ms = int((time.monotonic() - started) * 1000)
    suites = tuple(
        SuiteScore(
            id=suite.id,
            passed=sum(1 for o in outcomes if o.suite == suite.id and o.passed),
            total=suite.size,
        )
        for suite in battery.suites
    )
    passed = sum(entry.passed for entry in suites)
    return ExamReport(
        battery_version=battery.version,
        passed=0 if failed_reason else passed,
        total=battery.total_cases,
        suites=suites,
        outcomes=tuple(outcomes),
        failed=failed_reason is not None,
        reason=failed_reason,
        duration_ms=duration_ms,
    )


# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdict:
    overall: str
    suites: dict[str, str] = field(default_factory=dict)
    baseline_score: int = 0
    candidate_score: int = 0
    reason: str | None = None

    @property
    def eligible(self) -> bool:
        """The project gate: up overall and no suite down."""
        return self.overall == "up" and "down" not in self.suites.values()

    @property
    def regressed(self) -> bool:
        return self.overall == "down" or "down" in self.suites.values()

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "suites": dict(self.suites),
            "baseline_score": self.baseline_score,
            "candidate_score": self.candidate_score,
            "eligible": self.eligible,
            "reason": self.reason,
        }


def _compare(before: int, after: int) -> str:
    if after > before:
        return "up"
    if after < before:
        return "down"
    return "flat"


def grade(candidate: ExamReport, baseline: ExamReport) -> Verdict:
    """Compare two reports of the same battery version."""
    if candidate.battery_version != baseline.battery_version:
        return Verdict(
            overall="down",
            baseline_score=baseline.score,
            candidate_score=candidate.score,
            reason="battery version mismatch: rerun the baseline",
        )
    if candidate.failed:
        return Verdict(
            overall="down",
            suites={entry.id: "down" for entry in candidate.suites},
            baseline_score=baseline.score,
            candidate_score=0,
            reason=candidate.reason or "exam failed",
        )
    suites: dict[str, str] = {}
    for entry in candidate.suites:
        base = baseline.suite(entry.id)
        suites[entry.id] = _compare(base.passed if base else 0, entry.passed)
    return Verdict(
        overall=_compare(baseline.passed, candidate.passed),
        suites=suites,
        baseline_score=baseline.score,
        candidate_score=candidate.score,
    )


def feedback_for(report: ExamReport) -> list[dict[str, Any]]:
    """What the corrector may see: failing prompts and what was missing.

    Deliberately not the battery itself: the author gets the same signal a
    developer reads in a failed CI job, never the file to edit.
    """
    return [
        {
            "suite": outcome.suite,
            "case": outcome.case_id,
            "prompt": outcome.prompt,
            "missing": list(outcome.missing),
            "forbidden": list(outcome.forbidden),
        }
        for outcome in report.failures()
    ]


def suites_summary(reports: Iterable[Suite]) -> list[str]:
    return [suite.id for suite in reports]
