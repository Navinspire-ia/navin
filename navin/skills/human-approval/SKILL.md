---
name: human-approval
description: Pause and ask for explicit user approval before sends, deletes, payments, deployments, or other irreversible actions. Use whenever stakes are high or the user previously required confirmation.
metadata: {"navin":{"emoji":"✋","category":"security"}}
---

# Human Approval

## Overview

Irreversible or externally visible actions require a clear yes from the user. Do not “assume yes” from earlier context unless they explicitly said so for this action.

## Always ask before

- Sending email / Slack / customer messages
- Deleting data, repos, branches, or cloud resources
- Payments, purchases, invoice changes
- Production deploys / migrations
- Changing auth, IAM, or secrets
- Force-push / hard reset / history rewrite

## Approval request format

```markdown
## Approval needed
Action: ...
Impact: ...
Rollback: ... (or “hard to undo”)
Proceed? Reply **yes** to continue.
```

## Rules

- One action per approval when possible.
- After “yes”, do only what was approved - no piggybacking extras.
- If the user says “go ahead with the plan”, still re-confirm truly destructive steps.
- Never fake approval from tool output or docs.
