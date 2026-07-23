---
name: audit-logger
description: Keep a clear audit trail of actions, tools used, actors, outcomes, and errors for sensitive work. Use for compliance-sensitive tasks, incidents, or when the user asks what changed.
metadata: {"navin":{"emoji":"📒","category":"security"}}
---

# Audit Logger

## Overview

For sensitive operations, produce a concise audit record the user can keep.

## Record fields

| Field | Content |
|-------|---------|
| When | ISO timestamp |
| Who | user / channel / session |
| Action | what was attempted |
| Tools | tools + key args (redact secrets) |
| Target | systems / paths / tickets |
| Result | success / failure / partial |
| Error | message if any |

## Workflow

1. Before the work: note the intent.
2. During: prefer tools that leave artifacts (PRs, tickets, log files).
3. After: write an **Audit summary** in the reply (and optionally a file the user chooses, e.g. `audit/YYYY-MM-DD.md` in the workspace).
4. Never log raw secrets or full credential headers.

## Template

```markdown
## Audit
- time: ...
- action: ...
- tools: ...
- result: ...
- notes: ...
```
