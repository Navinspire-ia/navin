# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Execution evidence shared by quality tools and the agent completion gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """None means no check ran; a successful discovery call proves nothing."""

    checks_ok: bool | None = None
    tests_ok: bool | None = None
    summary: str = ""

    @property
    def ok(self) -> bool:
        results = (self.checks_ok, self.tests_ok)
        return True in results and False not in results


def test_evidence(outcomes: list[Any]) -> VerificationEvidence:
    ran = [outcome for outcome in outcomes if outcome.ran]
    passed = sum(outcome.passed for outcome in ran)
    failed = sum(outcome.failed for outcome in ran)
    ok = None
    if any(not outcome.ok for outcome in ran):
        ok = False
    elif passed > 0:
        ok = True
    summary = f"Tests: {passed} passed, {failed} failed" if ok is not None else "No tests ran."
    return VerificationEvidence(tests_ok=ok, summary=summary)


def lint_evidence(results: list[Any]) -> VerificationEvidence:
    ran = [result for result in results if result.ran]
    errors = sum(result.errors for result in ran)
    ok = None if not ran else not errors and all(
        result.exit_code in (0, None) or result.diagnostics for result in ran
    )
    return VerificationEvidence(
        checks_ok=ok,
        summary=f"Lint: {errors} errors" if ran else "No linter ran.",
    )


def verify_evidence(report: Any) -> VerificationEvidence:
    lint = lint_evidence(list(report.lint_results))
    tests = test_evidence(list(report.test_outcomes))
    return VerificationEvidence(
        checks_ok=lint.checks_ok,
        tests_ok=tests.tests_ok,
        summary=f"{lint.summary}; {tests.summary}",
    )
