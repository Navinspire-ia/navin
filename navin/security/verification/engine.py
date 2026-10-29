# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Verification engine: evidence promotes a finding, a rescan closes it.

Statuses only go up, and only on evidence of the right kind:

- ``potential``: a rule or a suspicion (static evidence).
- ``confirmed``: a traced dataflow from a controlled source to the sink, or a
  dynamic observation matching what the weakness predicts, on an authorized
  target.
- ``exploitable``: a non-destructive reproduction on an isolated environment
  with a human approval, of a finding that was already confirmed.

A probe that does not show the predicted behavior leaves the status where it
was and says why: it is not proof of absence, so nothing is marked clean from
it. A finding is closed only by a rescan of the same check that ran
successfully and no longer reports it, with a regression test recorded.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from navin.security.model import Evidence, Finding
from navin.security.verification.policy import (
    Authorization,
    VerificationRefusedError,
    check_request,
)
from navin.security.verification.probe import ProbeResult, redact, send_probe

Sender = Callable[..., ProbeResult]


@dataclass(frozen=True)
class Expectation:
    """What a probe must show for the weakness to be confirmed."""

    status_in: tuple[int, ...] = ()
    status_not_in: tuple[int, ...] = ()
    body_contains: tuple[str, ...] = ()
    body_absent: tuple[str, ...] = ()
    body_regex: str = ""
    headers_present: tuple[str, ...] = ()
    headers_absent: tuple[str, ...] = ()
    header_contains: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not any(
            (self.status_in, self.status_not_in, self.body_contains, self.body_absent, self.body_regex,
             self.headers_present, self.headers_absent, self.header_contains)
        )

    def mismatches(self, result: ProbeResult) -> list[str]:
        """Every way the response differs from the expectation; empty means it matches."""
        if not result.ok:
            return [f"no response: {result.error or 'unknown error'}"]
        out: list[str] = []
        names = {k.lower(): v for k, v in result.headers.items()}
        if self.status_in and result.status not in self.status_in:
            out.append(f"status {result.status} not in {list(self.status_in)}")
        if self.status_not_in and result.status in self.status_not_in:
            out.append(f"status {result.status} is one of {list(self.status_not_in)}")
        for needle in self.body_contains:
            if needle not in result.body:
                out.append(f"body lacks {needle!r}")
        for needle in self.body_absent:
            if needle in result.body:
                out.append(f"body holds {needle!r}")
        if self.body_regex and not re.search(self.body_regex, result.body):
            out.append(f"body does not match /{self.body_regex}/")
        for name in self.headers_present:
            if name.lower() not in names:
                out.append(f"header {name} is missing")
        for name in self.headers_absent:
            if name.lower() in names:
                out.append(f"header {name} is present")
        for name, needle in self.header_contains:
            if needle not in names.get(name.lower(), ""):
                out.append(f"header {name} lacks {needle!r}")
        return out


@dataclass
class Outcome:
    finding: Finding
    verified: bool
    status: str
    reason: str = ""
    probe: ProbeResult | None = None
    refused: bool = False
    mismatches: list[str] = field(default_factory=list)


def _outcome(finding: Finding, verified: bool, reason: str = "", **extra) -> Outcome:
    return Outcome(finding=finding, verified=verified, status=finding.status, reason=reason, **extra)


def confirm_dataflow(finding: Finding, trace: list[str], *, source: str = "", sink: str = "", tool: str = "navin_dataflow") -> Outcome:
    """Promote to ``confirmed`` on a traced path from a controlled source to the sink.

    ``trace`` lists the steps (``file:line description``), source first and sink
    last: fewer than two steps, or no source or sink, is a pattern match and
    stays ``potential``.
    """
    steps = [str(s).strip() for s in trace if str(s).strip()]
    if len(steps) < 2 or not (source or steps[0]) or not (sink or steps[-1]):
        return _outcome(finding, False, "a dataflow needs at least a source step and a sink step")
    finding.add_evidence(
        Evidence(
            kind="dataflow",
            description=f"source {source or steps[0]} reaches sink {sink or steps[-1]}",
            tool=tool,
            observed=redact("\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))[:4000],
            location=finding.file_path and (f"{finding.file_path}:{finding.line}" if finding.line else finding.file_path),
        )
    )
    return _outcome(finding, True, "dataflow traced")


def confirm_dynamic(
    finding: Finding,
    url: str,
    auth: Authorization,
    expect: Expectation,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    sender: Sender = send_probe,
    timeout_s: float = 10.0,
) -> Outcome:
    """Send one read-only request and promote to ``confirmed`` if it shows the predicted behavior."""
    if expect.is_empty():
        return _outcome(finding, False, "no expectation given: a probe that predicts nothing proves nothing")
    try:
        check_request(method, url, auth)
    except VerificationRefusedError as exc:
        return _outcome(finding, False, str(exc), refused=True)
    result = sender(method, url, auth, headers=headers, timeout_s=timeout_s)
    misses = expect.mismatches(result)
    if misses:
        return _outcome(finding, False, "; ".join(misses), probe=result, mismatches=misses)
    finding.add_evidence(
        Evidence(
            kind="dynamic",
            description=f"{result.method} {url} showed the predicted behavior",
            tool="navin_verify",
            request=result.request_line(tuple((headers or {}).keys())),
            observed=result.observed(),
            location=url,
        )
    )
    return _outcome(finding, True, "observed on the target", probe=result)


def demonstrate(
    finding: Finding,
    url: str,
    auth: Authorization,
    expect: Expectation,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    sender: Sender = send_probe,
    timeout_s: float = 10.0,
) -> Outcome:
    """Promote a confirmed finding to ``exploitable`` with a non-destructive reproduction."""
    if finding.status == "potential":
        return _outcome(finding, False, "only a confirmed finding can be reproduced: confirm it first")
    if expect.is_empty():
        return _outcome(finding, False, "no expectation given: a reproduction must predict the impact")
    try:
        check_request(method, url, auth, reproduction=True)
    except VerificationRefusedError as exc:
        return _outcome(finding, False, str(exc), refused=True)
    result = sender(method, url, auth, headers=headers, timeout_s=timeout_s, reproduction=True)
    misses = expect.mismatches(result)
    if misses:
        return _outcome(finding, False, "; ".join(misses), probe=result, mismatches=misses)
    finding.add_evidence(
        Evidence(
            kind="reproduction",
            description=f"{result.method} {url} reproduced the impact without changing state",
            tool="navin_verify",
            request=result.request_line(tuple((headers or {}).keys())),
            observed=result.observed(),
            location=url,
        )
    )
    return _outcome(finding, True, "reproduced in the isolated environment", probe=result)


def triage(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Group findings by what verifying them needs, so the cheapest proofs run first."""
    groups: dict[str, list[Finding]] = {"dataflow": [], "dynamic": [], "reproduce": [], "static_only": [], "closed": []}
    for item in findings:
        if item.validation.state in {"fixed", "accepted", "false_positive"}:
            groups["closed"].append(item)
        elif item.status == "confirmed":
            groups["reproduce"].append(item)
        elif item.status == "exploitable":
            groups["closed"].append(item)
        elif item.endpoint:
            groups["dynamic"].append(item)
        elif item.file_path:
            groups["dataflow"].append(item)
        else:
            groups["static_only"].append(item)
    return groups


def add_regression_test(finding: Finding, test_path: str, root: str | Path = ".") -> Outcome:
    """Record the test that fails before the fix and passes after it; the file must exist."""
    base = Path(root).resolve()
    candidate = (base / test_path).resolve()
    if base not in candidate.parents:
        return _outcome(finding, False, f"{test_path} is outside the project")
    if not candidate.is_file():
        return _outcome(finding, False, f"{test_path} does not exist: write the test first")
    relative = candidate.relative_to(base).as_posix()
    if relative not in finding.validation.regression_tests:
        finding.validation.regression_tests.append(relative)
        finding.add_evidence(Evidence(kind="regression", description=f"regression test {relative}", tool="navin_verify", location=relative))
    return _outcome(finding, True, "regression test recorded")


def rescan(finding: Finding, findings_after: list[Finding], *, scanner_ran: bool) -> Outcome:
    """Close the finding only if the same check ran again and no longer reports it.

    ``scanner_ran`` must be True only when the scanner that found it finished
    with an ``ok`` run: a missing tool or a timeout never counts as a fix.
    """
    if not scanner_ran:
        finding.validation.rescan = "pending"
        return _outcome(finding, False, "the scanner did not run again: nothing can be concluded")
    if finding.fingerprint() in {item.fingerprint() for item in findings_after}:
        finding.validation.rescan = "still_present"
        finding.validation.state = "open"
        return _outcome(finding, False, "the same check still reports it")
    finding.validation.rescan = "clean"
    if not finding.validation.regression_tests:
        return _outcome(finding, True, "clean on rescan; record a regression test to close it")
    finding.validation.state = "fixed"
    return _outcome(finding, True, "clean on rescan and covered by a regression test")
