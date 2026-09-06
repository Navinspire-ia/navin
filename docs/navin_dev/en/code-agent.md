# Code agent

How the Code agent plans, edits, verifies, and resumes work. Composer postures are documented in [Modes](./modes.md). Editor surfaces are in [Editor AI](./editor-ai.md).

## Ask and Agent

| Mode | Behavior |
| --- | --- |
| **Ask** | Read-only. The agent may inspect the project; mutating tools are blocked. |
| **Agent** | Implements, runs commands, and iterates. Use free text or `/forge` when you want code written. |

Switch mode from the composer Mode menu. Workflows may sync the menu when they start.

## Patches

When a Code workflow enables patch-only editing:

- File changes go through **`apply_patch`** (structured patches).
- Non-engineering tools (for example scrape) stay unavailable in Code profiles.

Diff review, reject, and checkpoints remain available after every edit.

## Verification

Code workflows expect proof before completion:

- Lint, tests, or project verify steps defined by the workflow
- If verification is missing or failing, the agent continues until checks pass or the task is blocked with evidence

## Context pack

When available, each turn includes a compact pack instead of an unguided tree walk:

- Open editor files
- Git dirty set
- Symbols and diagnostics
- Project rules under `.navin/rules` (Cursor-compatible rule files are also read where supported)

Shared project memory lives under [Project Home](./project-home.md).

## Git, pull requests, and CI

From the workbench and task board (with consent):

- Stage and inspect diffs
- Isolate work on a branch per task
- Open a pull request when a task is done
- Drive a CI fix loop when checks fail

Global brakes: **Settings → Security → Git**. Board consent and trails: [Board autonomy](./board-autonomy.md).

## Debugger

Python (debugpy) and Node debug adapters can attach through DAP from Code: breakpoints, step, and inspect stay in the workbench when the runtime is configured.

## Continuity

- Resume missions and board state from [Project Home](./project-home.md)
- Keep tasks aligned with branches and pull requests
- Use Plan / Mission ledgers so the next session starts from recorded decisions ([Plan mode](./plan-mode.md))

## Shell output

Command results from `exec` (`git`, tests, docker, Kubernetes, package installs, and similar) are compacted before they enter the model context. Live terminals are unchanged while a process runs. Full logs are kept on disk when compaction shortens the tool result. See [Command output compaction](./command-output-compaction.md).

## Related

- [Editor AI](./editor-ai.md)
- [Modes](./modes.md)
- [Commands](./commands.md)
- [Expert tools](./expert-tools.md)
- [Board autonomy](./board-autonomy.md)
