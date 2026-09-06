"""Code quality: diagnostics, test execution, and the verification loop.

Three layers, all driven by declarative JSON tables so new tools can be added
without code changes:

- :mod:`navin.quality.linters` runs linters and normalizes their output into a
  single diagnostic shape, for the editor gutter and for the agent.
- :mod:`navin.quality.testing` detects and runs test suites, parsing results
  into pass/fail counts and per-failure detail instead of raw log text.
- :mod:`navin.quality.verify` chains edit → lint → test and reports whether a
  change is safe to keep, so the agent can verify rather than assume.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navin.quality.linters import (
        Diagnostic,
        LinterResult,
        available_linters,
        lint_file,
        lint_project,
    )
    from navin.quality.testing import TestOutcome, detect_suites, run_tests
    from navin.quality.verify import VerificationReport, verify_changes

# Submodules are imported on first use: each pulls in subprocess plumbing that
# most navin entry points never touch.
_EXPORTS = {
    "Diagnostic": "linters",
    "LinterResult": "linters",
    "available_linters": "linters",
    "lint_file": "linters",
    "lint_project": "linters",
    "TestOutcome": "testing",
    "detect_suites": "testing",
    "run_tests": "testing",
    "VerificationReport": "verify",
    "verify_changes": "verify",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"navin.quality.{module_name}"), name)


__all__ = [
    "Diagnostic",
    "LinterResult",
    "TestOutcome",
    "VerificationReport",
    "available_linters",
    "detect_suites",
    "lint_file",
    "lint_project",
    "run_tests",
    "verify_changes",
]
