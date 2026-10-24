---
description: Commit, push, and open a pull request
allowed-tools: git, forge, Bash(git status:*), Bash(git diff:*), Bash(git branch:*), Bash(git log:*)
---

## Context

- Git status: !`git status`
- Staged and unstaged changes against HEAD: !`git diff HEAD`
- Current branch: !`git branch --show-current`
- Last commits: !`git log --oneline -10`

## Task

From the changes above:

1. If the current branch is the default branch (`main` or `master`), create a feature branch with `git action=switch create=true name=<short-descriptive-name>`.
2. Create one commit with a fitting message (`git action=add paths=[...]`, then `git action=commit`). Never stage secrets.
3. Push the branch to origin with `git action=push set_upstream=true`.
4. Open the pull request with `forge action=pr_create`, a clear title and a body made of a short `## Summary` (1 to 3 bullets covering every commit of the branch) and a `## Test plan` checklist.
5. You can call several tools in one reply: you MUST do all of the above in a single reply. Use no other tool, do nothing else, and send no text besides these tool calls.
