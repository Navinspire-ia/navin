---
name: adaptive-reasoning
description: Choose the right depth of reasoning for the task — shallow for routine edits, deep for architecture, security, or ambiguous bugs. Use when work quality depends on thinking harder (or intentionally less).
metadata: {"navin":{"emoji":"🧠","category":"intelligence"}}
---

# Adaptive Reasoning

## Overview

Match cognitive effort to problem difficulty. Overthinking wastes tokens; underthinking causes rework.

## Difficulty signals

| Signal | Mode |
|--------|------|
| Typo, rename, single-file edit, clear instruction | **Shallow** — act immediately |
| Multi-file change, unclear bug, API design | **Standard** — inspect, plan briefly, act |
| Security, data loss, architecture, prod incident | **Deep** — explore alternatives, verify, then act |
| Conflicting requirements or missing facts | **Clarify** — ask 1–3 precise questions first |

## Workflow

1. Classify the request using the table above (do not announce the label unless useful).
2. **Shallow**: apply the change; skip long preambles.
3. **Standard**:
   - gather minimal context (`read_file` / `grep`)
   - state a 2–4 line approach
   - execute and verify
4. **Deep**:
   - map constraints and failure modes
   - compare 2 options when stakes are high
   - verify with tests, dry-runs, or `exec` checks
   - document the chosen trade-off in the final answer
5. **Clarify**: ask only blockers; propose a default if the user is silent.

## Escalation

If a shallow task reveals surprises (unexpected deps, failing tests), escalate to Standard/Deep mid-turn without restarting from scratch.

## Anti-patterns

- Writing a thesis for a one-line fix
- Jumping into code on security-sensitive changes without a threat check
- Asking many open-ended questions instead of a short plan with assumptions
