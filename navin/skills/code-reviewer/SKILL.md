---
name: code-reviewer
description: Review code changes for bugs, security issues, regressions, and maintainability. Use on diffs, PRs, or before merge — pairs with gh CLI when available.
metadata: {"navin":{"emoji":"🧪","category":"devops"}}
---

# Code Reviewer

## Overview

Review like a careful senior engineer. Combine static reading with tests when possible.

## Workflow

1. Identify the change set (`git diff`, PR via `gh`, or listed files).
2. Skim for intent; restate the intended behavior.
3. Deep-read risky areas (auth, data, concurrency, migrations).
4. Run relevant tests with `exec` when the environment allows.
5. Emit findings with severity (see `critic-reviewer` scale) + verdict.

## Focus areas

- Correctness & edge cases
- Security (injection, authz, secrets)
- Performance footguns only when real
- API/contract breaks
- Missing tests for new branches

## Rules

- Cite `path` (and line when known).
- Prefer actionable fixes over style nits unless asked for nits.
- If `gh` is available, you may comment on PRs; otherwise return a review in chat.
