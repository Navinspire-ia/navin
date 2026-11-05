---
description: Run the test suite and fix every failing test until it is green
argument-hint: "[test file, directory or test id, default: whole suite]"
allowed-tools: test_run, verify, read_file, grep, find_files, code_index, lsp, edit_file, apply_patch, exec, git
---

# Fix the failing tests

Scope requested by the user (may be empty): "$ARGUMENTS"

## 1. Run

Run `test_run` on the scope (the whole suite when empty). If everything passes, say so in one line with the counts and stop.

## 2. Diagnose each failure

For every failing test, in order:

1. Read the assertion message and the stack trace. Open the test and the code under test (`read_file`, `code_index action=definition` / `callers`).
2. Decide **who is wrong**: the code (a regression) or the test (an outdated expectation). Default to fixing the code; change a test only when the behaviour it checks was intentionally changed, and say so.
3. Never make a test pass by deleting it, skipping it, loosening the assertion, or catching the exception. If a test cannot be fixed honestly, report it instead.

## 3. Fix and re-run

- Apply the smallest correct fix with `edit_file` / `apply_patch`, following the project conventions.
- Re-run only the failing target (`test_run target=...`) until it passes, then the full scope once at the end to catch regressions.
- Stop after 5 rounds on the same test and report what you learnt rather than looping.

## 4. Report

One table: `| Test | Cause | Fix | Status |`, then the final pass/fail/skip counts. Do not commit; offer `/commit` when the suite is green.
