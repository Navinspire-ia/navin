---
name: shell-sandbox
description: Run shell commands with strict limits - prefer workspace sandbox, avoid privileged ops, and quote unsafe output. Use whenever exec is required.
metadata: {"navin":{"emoji":"🧰","category":"devops"}}
---

# Shell Sandbox

## Overview

`exec` is powerful. Use the smallest command, shortest timeout, and clearest cwd.

## Rules of engagement

1. Prefer built-in tools (`read_file`, `grep`, `edit`) over shell equivalents.
2. Stay inside the workspace when restriction/sandbox is on.
3. No `sudo`, no curling pipes to shells, no rewriting history.
4. Quote paths; avoid untrusted interpolation.
5. Cap output - pipe through `tail`/`head` when listing huge trees.
6. Destructive commands require `human-approval`.

## Pattern

```text
intent → exact command → expected success signal → run → interpret
```
