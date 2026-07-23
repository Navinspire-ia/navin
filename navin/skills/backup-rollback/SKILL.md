---
name: backup-rollback
description: Snapshot configs, memory, and skills before risky edits, and define a rollback path. Use before bulk refactors, migrations, or skill/config changes.
metadata: {"navin":{"emoji":"💾","category":"security"}}
---

# Backup & Rollback

## Overview

Make change reversible before you make it hard to undo.

## Native checkpoints (first line of defense)

Navin saves a checkpoint **automatically before every user prompt**: the conversation state plus the pre-edit content of every file the agent modifies with its file tools. Rewind with:

- `/checkpoint` — list restore points (auto + manual)
- `/checkpoint save <note>` — add a named restore point before a risky step
- `/checkpoint restore <name> code` — revert the files the agent edited, keep the conversation
- `/checkpoint restore <name> chat` — rewind the conversation, keep the code
- `/checkpoint restore <name>` — rewind both

Limits (recommend git for anything beyond these):
- Shell-command changes (`rm`, `mv`, scripts) are **not** tracked — only edits made through the file tools
- External/manual edits are not tracked
- Checkpoints are session-local recovery, not version control

## What to snapshot

| Area | How |
|------|-----|
| Code | native checkpoints for tool edits; git branch / commit for everything else |
| Config | copy of `config.json` or relevant snippets |
| Skills | copy `skills/<name>/` before overwrite |
| Data | DB dump / export when migrating |

## Workflow

1. Identify blast radius (files, skills, services).
2. Create a restore point:
   - `git status` + commit or stash when in a repo
   - or copy critical files to `backups/<timestamp>/` in the workspace
3. Perform the change in the smallest steps.
4. Verify (tests, smoke check).
5. If failed: restore from the snapshot; do not “fix forward” blindly on production data.

## Rollback note (always leave for the user)

```markdown
## Rollback
Restore point: ...
Command / steps: ...
```
