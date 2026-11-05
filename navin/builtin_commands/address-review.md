---
description: Address the open review comments on the current pull request, reply and resolve the threads
argument-hint: "[PR number, default: the PR of the current branch]"
allowed-tools: forge, git, read_file, grep, code_index, edit_file, apply_patch, test_run, verify, ask_user
---

# Address the review

PR requested by the user (may be empty): "$ARGUMENTS"

## 1. Read the threads

`forge action=pr_view` for the summary, then `forge action=pr_threads` for the open threads (unresolved only). If there is none, say so and stop.

## 2. Triage each thread

For every thread, read the referenced file and line (`read_file`) and decide:

- **Fix**: the reviewer is right, change the code.
- **Explain**: the reviewer missed something, answer with the evidence (file, line, behaviour). Never argue without a fact.
- **Ask**: the request is ambiguous or widens the scope. Collect these and ask the user once with `ask_user` before acting.

## 3. Apply the fixes

Make the changes with `edit_file` / `apply_patch`, run the relevant tests (`test_run target=...`), then `verify action=check`. Commit once with a message that references the review (`git action=commit`), push with `git action=push`.

## 4. Reply and resolve

For each thread: `forge action=pr_reply thread_id=...` with one or two sentences (what changed and where, or why not), then `forge action=pr_resolve_thread` for the fixed ones. Leave the threads you answered without changing code open for the reviewer.

## 5. Report

One table: `| Thread | File:line | Decision | Reply |`, plus the commit sha pushed.
