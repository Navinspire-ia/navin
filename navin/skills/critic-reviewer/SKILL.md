---
name: critic-reviewer
description: Critically review code, plans, or agent output for bugs, security issues, missing tests, and weak assumptions before delivery. Use before merging, deploying, or sending user-facing results.
metadata: {"navin":{"emoji":"🔎","category":"intelligence"}}
---

# Critic / Reviewer

## Overview

Act as a skeptical second pair of eyes. Prefer concrete findings over generic praise.

## Review checklist

1. **Correctness** - logic errors, edge cases, off-by-ones, race conditions
2. **Security** - injection, secrets leakage, authz gaps, unsafe `exec`
3. **Reliability** - error handling, retries, partial failure
4. **Tests** - missing coverage for the change; broken existing tests
5. **Scope** - unrelated churn, incomplete TODOs, silent behavior changes
6. **Clarity** - naming, API contracts, migration notes
7. **Product UI (web)** - cardboard apps are blockers:
   - Blank / broken dashboard or main route
   - Dead buttons (`onClick={() => {}}`, `alert`, "Coming soon", lorem)
   - Missing `framer-motion` on a React/Next UI (Google, Fluent, or Carbon)
   - Missing `three` + `@react-three/fiber` + `@react-three/drei` on a Dev / Marketing / Montage web UI
   - New UI that defaulted to Tailwind / shadcn / Chakra / Ant instead of MUI, Fluent, or Carbon
   - Em/en dashes (U+2014 / U+2013) in copy (must be `-`)
   - Skills `ui-ux-pro-max` / `make-interfaces-feel-better` skipped while shipping UI
8. **Preview proof** - was `open_preview` used and the happy path actually exercised?

## Workflow

1. Identify the **diff / deliverable** (files, PR, plan, report).
2. Restate intended behavior in one sentence.
3. Inspect with `grep` / `read_file`; run tests via `exec` when possible.
4. Produce findings ordered by severity:

| Severity | Meaning |
|----------|---------|
| Blocker | Must fix before ship |
| Major | High risk / likely bug |
| Minor | Improve when cheap |
| Nit | Style / optional |

5. End with a **verdict**: Approve / Approve with nits / Request changes.

## Output format

```markdown
## Verdict
Request changes | Approve | ...

## Findings
1. [Blocker] path:line - issue - why - fix sketch
2. ...

## Residual risks
- ...
```

## Rules

- No finding without evidence (file path + real excerpt or observed behavior).
- Do not rewrite the whole solution unless asked - review first.
- If the work is solid, say so briefly; do not invent issues.
- Reject absences and absolutes ("does not exist", "always sequential") unless
  you searched for a counter-example in this turn and found none.
