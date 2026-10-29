# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Security code analyzers: dataflow, authentication, tenant isolation, business logic.

``analyze_project`` reads the tree once (bounded) and runs the analyzers on
it. ``checked_controls`` says which catalog controls a set of engines really
checked, so a clean run is reported as clean only for those.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.security import catalog
from navin.security.analyzers import auth, business_logic, dataflow, tenant
from navin.security.analyzers.common import MAX_FILES, SourceSet, collect_sources
from navin.security.model import Finding, sort_findings
from navin.security.remediation.guidance import apply_guidance

ANALYZERS: dict[str, Callable[[SourceSet], list[Finding]]] = {
    dataflow.ANALYZER_ID: dataflow.analyze,
    auth.ANALYZER_ID: auth.analyze,
    tenant.ANALYZER_ID: tenant.analyze,
    business_logic.ANALYZER_ID: business_logic.analyze,
}
ANALYZER_CONTROLS: dict[str, frozenset[str]] = {
    dataflow.ANALYZER_ID: dataflow.CHECKED_CONTROLS,
    auth.ANALYZER_ID: auth.CHECKED_CONTROLS,
    tenant.ANALYZER_ID: tenant.CHECKED_CONTROLS,
    business_logic.ANALYZER_ID: business_logic.CHECKED_CONTROLS,
}


@dataclass
class AnalysisReport:
    findings: list[Finding]
    ran: list[str]
    files_scanned: int
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    durations: dict[str, float] = field(default_factory=dict)  # seconds per analyzer, plus "read" for the walk

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "ran": list(self.ran),
            "files_scanned": self.files_scanned,
            "skipped": list(self.skipped),
            "notes": list(self.notes),
            "durations": dict(self.durations),
            "checked_controls": sorted(checked_controls(self.ran)),
        }


def analyze_project(root: str | Path, *, only: Iterable[str] | None = None, max_files: int = MAX_FILES) -> AnalysisReport:
    """Run the analyzers on *root*. Blocking: call it off the loop."""
    ids = list(only) if only is not None else list(ANALYZERS)
    unknown = [i for i in ids if i not in ANALYZERS]
    if unknown:
        raise ValueError(f"unknown analyzer {unknown[0]!r}; known: {', '.join(ANALYZERS)}")
    clock = time.monotonic()
    sources = collect_sources(root, max_files=max_files)
    durations = {"read": round(time.monotonic() - clock, 2)}
    findings: list[Finding] = []
    for analyzer_id in ids:
        clock = time.monotonic()
        findings.extend(ANALYZERS[analyzer_id](sources))
        durations[analyzer_id] = round(time.monotonic() - clock, 2)
    apply_guidance(findings)
    return AnalysisReport(
        findings=sort_findings(findings),
        ran=ids,
        files_scanned=sources.files_scanned,
        skipped=list(dict.fromkeys(sources.skipped)),
        notes=list(dict.fromkeys(sources.notes)),
        durations=durations,
    )


async def analyze_project_async(root: str | Path, **kwargs: Any) -> AnalysisReport:
    """Same as :func:`analyze_project`, off the event loop."""
    return await asyncio.to_thread(analyze_project, root, **kwargs)


def checked_controls(engine_ids: Iterable[str]) -> set[str]:
    """Controls the given engines really check (analyzers declare theirs; scanners per the catalog)."""
    out: set[str] = set()
    for engine_id in engine_ids:
        if engine_id in ANALYZER_CONTROLS:
            out |= ANALYZER_CONTROLS[engine_id]
        else:
            out |= {c.id for c in catalog.controls_for_scanner(engine_id)}
    return out


__all__ = ["ANALYZERS", "ANALYZER_CONTROLS", "AnalysisReport", "analyze_project", "analyze_project_async", "checked_controls"]
