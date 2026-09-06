# How AI Agent Memory Works in Navin

This guide explains Navin's long-term AI agent memory: session history, compressed archives, durable memory files, Dream consolidation, and versioned memory changes - from the desktop app.

## What you will use

- a project with persistent session history
- compressed history archives for older turns
- durable memory files such as `USER.md` and `MEMORY.md`
- a Dream workflow for curating long-term memory

## When to use this

Use memory when an agent should remember stable preferences, project facts, decisions, and recurring context across sessions. Do not use memory as a dumping ground for every raw transcript; Navin separates short-term messages from curated durable knowledge.

## Open memory in the app

1. Open Navin and select your project.
2. Chat normally; Navin keeps session history for that project.
3. Open the **Memory** page (or review memory files in the project tree) when you want to inspect durable facts.
4. Adjust Dream timing under **Settings** when you want a different consolidation cadence.

You do not need a terminal. Full product detail: [`../memory.md`](../memory.md).

## Minimal working example

Ask the agent to remember a stable fact in a normal chat, then run Dream from the composer:

```text
/dream
```

Inspect recent memory changes:

```text
/dream-log
```

These are composer actions inside Navin, not system shell commands. Durable files live in the active project (for example under that project's `memory/` folder).

## Production notes

- Use one project per personal or team context.
- Keep durable facts concise; old session details belong in the history archive.
- Use `/dream-prompt init` when a project needs custom memory guidance, then edit the guide from the project tree.
- Review versioned memory changes when memory affects important workflows.

## Security notes

- Memory files may contain sensitive user or project facts.
- Avoid sharing projects without reviewing `SOUL.md`, `USER.md`, and `memory/MEMORY.md`.
- Use separate projects for personal and team contexts.

## Troubleshooting

- Memory feels stale: run `/dream` and inspect `/dream-log`.
- Memory changed incorrectly: use `/dream-restore` to inspect and restore previous versions.
- A new chat lacks context: confirm it uses the same project in the project picker.

## Related docs

- [Memory in Navin](../memory.md)
