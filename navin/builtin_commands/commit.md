---
description: Create a git commit from the current changes
allowed-tools: git, Bash(git status:*), Bash(git diff:*), Bash(git branch:*), Bash(git log:*)
---

## Context

- Git status: !`git status`
- Staged and unstaged changes against HEAD: !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Last commits (follow their message style): !`git log --oneline -10`

## Task

From the changes above, create exactly one commit:

1. Stage the relevant files with `git action=add paths=[...]`. Never stage secrets (`.env`, credentials, private keys).
2. Commit them with `git action=commit message="..."`, a message that describes the change in the style of the recent commits.

You can call several tools in one reply: stage and commit in a single reply. Use no other tool, do nothing else, and send no text besides these tool calls.
