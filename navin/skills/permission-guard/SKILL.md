---
name: permission-guard
description: Enforce least privilege for commands, file paths, domains, and APIs. Use before destructive exec, broad writes, or when the user asks to lock down what the agent may do.
metadata: {"navin":{"emoji":"🛂","category":"security"}}
---

# Permission Guard

## Overview

Default to the smallest capability set that still completes the task. Prefer workspace-scoped tools over shell when possible.

## Boundaries to define

| Axis | Examples of tight bounds |
|------|--------------------------|
| Paths | project dir only; no `~/.ssh`, no `/etc` |
| Commands | allowlisted binaries; no `sudo` |
| Network | named APIs only; no arbitrary hosts |
| Writes | explicit files; no mass delete |
| Secrets | never echo tokens into chat |

## Workflow

1. Restate the task and the **minimum** powers required.
2. Prefer built-ins (`read_file`, `edit`, `grep`) over `exec`.
3. Before risky `exec`, state the command and why it is needed.
4. Refuse or ask when asked to:
   - disable safety / workspace restriction without reason
   - exfiltrate secrets
   - run opaque remote scripts
5. If the environment has `restrict_to_workspace` or sandboxing, keep working inside it - do not invent bypasses.

## Output when tightening scope

```markdown
## Allowed
- ...
## Denied
- ...
## Needs approval
- ...
```
