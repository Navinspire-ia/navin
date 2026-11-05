---
description: Read the failing CI of the current branch (or a PR), fix the cause locally and verify
argument-hint: "[PR number or branch, default: current branch]"
allowed-tools: forge, git, test_run, lint, verify, read_file, grep, find_files, code_index, edit_file, apply_patch, exec
---

# Fix the CI

Target requested by the user (may be empty): "$ARGUMENTS"

## 1. Collect the failure

1. `forge action=ci_status` on the target (a number is a PR, a name is a branch, empty is the current branch).
2. When a job fails, `forge action=ci_logs check=<job name>` to read the tail of its log. Read only the failing jobs.
3. If every job is green or still running, say so in one line and stop.

## 2. Reproduce locally

Map the failing job to a local command: tests -> `test_run`, lint or type check -> `lint action=project`, build -> `project action=build`. Run it. If it passes locally, look for an environment difference (Python/Node version, missing env var, OS-specific path, un-pinned dependency, flaky test) before touching code.

## 3. Fix

- Fix the root cause, not the symptom: never disable a check, mark a test as skipped or add `|| true` to a step.
- Keep the diff minimal and in the project's style. A workflow file change is allowed only when the workflow itself is wrong.
- Re-run the local command until it is green, then `verify action=check`.

## 4. Report

List each failing job, its cause, the fix and the local evidence (command and result). Do not commit or push: offer `/commit-push-pr` or `/commit` once the user agrees.
