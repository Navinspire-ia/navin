# Simple command examples

Each block is a complete file; save it under `.navin/commands/`.

## review.md - review staged changes

```markdown
---
description: Review staged changes for bugs and missing tests
allowed-tools: Read, Grep, Bash(git:*)
---

Review the staged changes.

Staged diff summary:
!`git diff --cached --stat`

1. Read each changed file around the modified lines.
2. Look for logic errors, unhandled errors, missing tests and security issues.
3. Report a table: file, line, severity (high, medium, low), problem, fix.
4. End with one line: "Ready to commit" or "Fix before committing".
```

## explain.md - explain a file

```markdown
---
description: Explain what a file does and how it is used
argument-hint: <path>
---

If $1 is empty, ask which file to explain and stop.

Explain @$1 for a developer new to this project:
- purpose in two sentences
- main functions or components and how they connect
- where it is used (search the workspace for its imports)
```

## fix-issue.md - positional arguments with a default

```markdown
---
description: Fix a GitHub issue end to end
argument-hint: <issue-number> [branch-prefix]
allowed-tools: Bash(gh:*), Bash(git:*), Read, Edit, Grep
---

Fix issue #$1 on a branch named ${2:-fix}/issue-$1.

Issue:
!`gh issue view $1 --json title,body --jq '.title + "\n\n" + .body'`

1. Reproduce the problem with a failing test.
2. Fix the cause, not the symptom.
3. Run the affected tests and report the results.
```

## standup.md - no placeholder, arguments appended

```markdown
---
description: Draft my standup update from recent commits
allowed-tools: Bash(git:*)
---

Draft a three-line standup (yesterday, today, blockers) from:
!`git log --since=yesterday --author="$(git config user.email)" --oneline`
```

`/standup mention the release freeze` adds `Arguments: mention the release
freeze` at the end of the prompt.
