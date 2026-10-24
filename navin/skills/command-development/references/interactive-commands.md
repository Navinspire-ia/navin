# Interactive commands

Arguments cover what the user knows up front. When a choice depends on what the
command discovers, ask during the turn with the `ask_user` tool: 2 to 4
concrete options, exactly one recommended.

## When to use arguments vs questions

| Situation | Use |
|---|---|
| Value the user always knows (file, version, issue id) | argument |
| Choice among options found at runtime (which of 3 failing tests) | ask_user |
| Risky or irreversible step (delete, publish, migrate) | ask_user confirmation |
| Free text that is long (a description) | argument, or ask in chat |

## Pattern: ask only when missing

```markdown
---
description: Create a component skeleton
argument-hint: [component-type]
---

Create a new plugin component.

If $1 is one of skill, agent, command, hook, mcp: use it.
Otherwise call ask_user with the question "Which component?" and the options
skill (recommended), agent, command, hook. Continue with the answer.
```

## Pattern: confirm before acting

```markdown
List the files that would be removed. Then call ask_user with the options
"Remove them" and "Keep them (recommended)". Delete only after an explicit
"Remove them".
```

## Pattern: multi-question discovery

Ask the questions one decision at a time, each with its trade-off, instead of
one paragraph of questions. Record the answers in the final summary so the user
sees what was decided.

## Rules

- Never ask what the arguments or the workspace already answer.
- One ask_user call per real fork; do not ask for permission to continue work
  the command already describes.
- If the user skips, take the recommended option and say so.
