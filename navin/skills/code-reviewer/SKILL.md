---
name: code-reviewer
description: Review code changes for bugs, security issues, regressions, and maintainability. Use on diffs, PRs, or before merge - pairs with gh CLI and code_review tool when available.
metadata: {"navin":{"emoji":"🧪","category":"devops"}}
---

# Code Reviewer

## Overview

Review like a careful senior engineer with **precision over recall** (open-code-review). Combine pr-agent style describe + findings + patch suggestions.

## Workflow

1. Call `code_review(action=scope)` (or scope a path) - OCR 5-gates + optional
   `.navin/review-rules.json` / `.opencodereview/rule.json`.
2. Restate intent in 3-6 bullets + estimate effort 1-5 + whether tests cover the change.
3. Deep-read risky hunks (auth, data, concurrency, migrations). Follow `path_rules`
   returned by scope when present.
4. Run relevant tests / lint with `exec` when the environment allows.
5. Emit findings with the schema below; call `code_review(action=filter)` to drop FP.
6. Close with `code_review(action=report, findings_json=..., verdict=..., effort=N)` - File Preview opens automatically in the WebUI.
7. Optional PR: `pr_comments(action=preview|post, kind=review, findings_json=...)`.

## Finding schema

| Field | Values |
|-------|--------|
| severity | critical / high / medium / low / info / nit |
| category | bug / security / performance / maintainability / test / style / documentation / api / data / other |
| confidence | 0.0-1.0 (keep ≥ 0.6) |
| file_path, start_line, end_line | required when known (`file:line`) |
| existing_code / suggested_code | required for High+ when a patch is clear |
| summary, explanation, recommendation | actionable + REAL evidence |

## Focus areas

- Correctness & edge cases
- Security (injection, authz, secrets) - but defer full AppSec to `/fortify`
- Performance footguns only when real
- API/contract breaks
- Missing tests for new branches

## Rules

- Cite `path` (and line when known).
- Prefer actionable fixes over style nits unless asked for nits.
- Use `pr_comments` (via `gh`) when the user asks to comment on a PR.
- Answer follow-up Q&A scoped to finding #N or selected lines.
- **Never invent.** Every finding needs a tool-observed excerpt (`existing_code` /
  failing output). Before claiming something is missing or "always X", `grep`
  for the opposite. Drop could/might/peut-être claims. Confidence floor 0.75.
