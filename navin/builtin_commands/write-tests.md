---
description: Write missing tests for a file, a symbol or the uncommitted changes
argument-hint: "[path or symbol, default: uncommitted changes]"
allowed-tools: git, read_file, grep, find_files, code_index, lsp, test_run, write_file, edit_file, apply_patch
---

# Write tests

Target requested by the user (may be empty): "$ARGUMENTS"

## 1. Scope

- Empty: the files changed in `git action=diff` plus untracked ones from `git action=status`.
- A path: that file. A symbol: `code_index action=definition` to find it.

## 2. Learn the conventions first

Find the existing tests of the same area (`find_files` on `tests/`, `__tests__`, `*.test.*`, `*_test.go`) and read two of them: runner, fixtures, naming, mocking style, how the project avoids the network and the file system. New tests must look like they were written by the same person.

## 3. Decide what to test

For each public function or behaviour in scope, list: the nominal case, the edge cases (empty, None, boundaries, unicode, concurrency when relevant), and the error paths. Check with `code_index action=references` what is already covered and skip it. Prefer a few tests that each pin one behaviour over one test that checks everything.

## 4. Write and run

Create or extend the test file next to the existing ones. Run it with `test_run target=<file>`; a new test must pass on the current code (unless the user asked for a failing test that documents a bug, then say so). Fix the test, not the code, when it fails for a reason that is yours.

## 5. Report

List the test file(s), each test and the behaviour it pins, and the run result. Do not commit.
