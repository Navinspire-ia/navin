---
name: self-healing-retry
description: Recover from tool failures and flaky commands with intelligent retries, alternate tools, and clear fallbacks. Use when exec, network, or MCP calls fail mid-task.
metadata: {"navin":{"emoji":"🩹","category":"intelligence"}}
---

# Self-Healing & Retry

## Overview

Treat failures as information. Retry smartly, switch approach, or escalate - never loop blindly.

## Classification

| Failure | First response |
|---------|----------------|
| Transient network / rate limit | Wait briefly, retry once or twice with backoff |
| Command not found / missing CLI | Install if policy allows, or switch tool / skill |
| Permission denied | Stop destructive retries; ask or use safer path |
| Assertion / test failure | Fix root cause; do not rerun hoping for luck |
| Ambiguous / empty result | Change query or tool; verify inputs |

## Retry policy

1. Max **2 automatic retries** for the same exact command.
2. On retry #2, **change something** (flags, cwd, tool, smaller scope).
3. Capture stderr; quote the relevant error in your reasoning.
4. If still failing → **fallback** or ask the user with a concrete question.

## Fallback ladder

1. Built-in tool alternative (`web_fetch` vs `exec curl`, `grep` vs shell grep)
2. Smaller reproduction (isolate the failing step)
3. Read-only diagnosis, then propose a fix
4. User intervention (credentials, network, approval)

## Reporting

When you recover, briefly state:

- what failed
- what you changed
- current status

Do not hide repeated failures behind “still working on it”.
