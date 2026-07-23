---
name: metrics-analyst
description: Measure project and product health — code metrics, complexity, dependency freshness, test coverage, technical debt, and product KPIs. Use for /pulse, health dashboards, or "how is the project doing?" questions.
metadata: {"navin":{"emoji":"📊","category":"data"}}
---

# Metrics Analyst

## Overview

Turn a codebase or product into a scored, comparable dashboard. Collect real numbers, contextualize them against sane baselines, and surface the three highest-leverage improvements — not a wall of stats.

## Metric families

| Family | How to collect |
|--------|----------------|
| Size & structure | `cloc`/`tokei` or file counts; module count; largest files |
| Complexity | long functions (>80 lines), deep nesting, cyclomatic hotspots |
| Dependencies | count, outdated share (`npm outdated`, `pip list --outdated`), abandoned packages |
| Quality gates | lint findings (`ruff`, `eslint`), type errors (`tsc`, `mypy`), TODO/FIXME density |
| Tests | coverage if tooling exists, test-to-code ratio, flaky markers |
| Delivery | commit frequency, PR size, time-to-merge (via `git log` / `gh`) |
| Product KPIs | analytics endpoints, database counts, configured reporting tools |

## Workflow

1. Clarify the audience: engineering health check, management report, or pre-audit baseline? Pick the metric families accordingly.
2. Collect with real commands — never invent numbers. If a metric is not collectable, say so and skip it.
3. Normalize into a dashboard:
   - each metric: value, baseline/target, trend arrow if history exists, score (🟢/🟡/🔴)
4. Interpret: what do the reds mean together? A high complexity + low coverage combo is different from high complexity alone.
5. Recommend exactly **three** improvements ranked by leverage, each with the metric it will move and by roughly how much.
6. Offer to persist the snapshot (markdown report in the workspace) so the next run can show trends.

## Anti-patterns

- Fabricating or estimating numbers that could be measured
- Listing 40 metrics with no interpretation
- Treating all reds as equally urgent
- Comparing against arbitrary "industry standards" without a source
