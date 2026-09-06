---
name: quality-gate
description: Run the full release-readiness gate - code review, security, performance, tests, docs, and metrics in one orchestrated pass with a go/no-go verdict. Use before releases, merges to main, or client deliveries.
metadata: {"navin":{"emoji":"✅","category":"devops"}}
---

# Quality Gate

## Overview

Orchestrate every quality dimension into a single go/no-go verdict. This is the "make the project perfect" pass: it chains the specialist skills (code review, security, performance, metrics) and reduces their output to a decision with blocking items.

## Gate dimensions

1. **Correctness** - code review of the diff or release scope (`code-reviewer`); tests pass; no known regressions.
2. **Security** - audit pass (`security-auditor`) + flaw hunt (`vulnerability-scanner`); zero unresolved criticals.
3. **Performance** - no measured regression on hot paths (`performance-auditor`); budgets respected (bundle size, response times).
4. **Reliability** - error handling at boundaries, graceful degradation, rollback path exists.
5. **Maintainability** - lint/type checks clean, no new TODO-debt spike, complexity hotspots justified.
6. **Documentation** - README/CHANGELOG updated, breaking changes flagged, config documented.
7. **Observability** - logs at the right level, metrics/health endpoints intact.

## Workflow

1. Define the scope: a release tag, a branch diff, or the whole project.
2. Save a checkpoint (`/checkpoint save pre-gate`) so the state is recoverable.
3. Run each dimension. Delegate to subagents in parallel when available - one per dimension - and consolidate their reports.
4. Score each dimension: **PASS / WARN / BLOCK**, with evidence.
5. Verdict:
   - Any BLOCK → **NO-GO**, list blocking items in fix order.
   - Only WARNs → **GO with reservations**, list follow-ups.
   - All PASS → **GO**.
6. Output a single gate report: verdict on top, dimension table, then details. Offer to fix blocking items one by one, re-running only the affected dimension after each fix.

## Anti-patterns

- Giving GO with an unresolved critical security finding
- Re-running the entire gate after every one-line fix
- Letting one dimension's verbosity bury the verdict
- Skipping the checkpoint before fix sessions
