# Editor AI

Inline assistance in the Code workbench: completions while typing, selection edits, language navigation, and change review. Layout and panels are described in [Workbench](./workbench.md).

## Tab completions

Ghost text appears as you type and streams into the editor:

| Action | Shortcut |
| --- | --- |
| Accept next word | **Tab** |
| Accept line | **Ctrl/Cmd+→** |
| Accept full suggestion | **Ctrl/Cmd+Enter** |

Suggestions use the current file and, when available, related project files (imports and nearby modules). Completion latency is shown in the status area. For faster suggestions, assign a Code (or code-fast) preset under **Settings → Models**.

## Inline edit (Cmd+K)

**Ctrl/Cmd+K** opens an inline edit on the selection (or at the cursor):

1. Describe the change.
2. Preview the streamed proposal.
3. Apply, discard, or retry.

Multi-hunk edits in one file are supported. Recent Cmd+K attempts for the file are kept in a short local history.

Use Cmd+K for focused edits in the open buffer. For multi-file work, use Agent or Plan in chat ([Modes](./modes.md)).

## Navigation and Problems

| Action | Shortcut |
| --- | --- |
| Go to definition | F12 |
| Find references | Shift+F12 |
| Rename symbol | F2 (preview, then confirm) |
| Hover | Mouse over symbol |
| Jump from diagnostic | Click an entry in Problems |
| Outline / breadcrumbs | Current-file structure |

Python (Ruff) and JSON diagnostics underline issues in the editor and appear in the status bar.

## Quick Open and Command Palette

| Shortcut | Opens |
| --- | --- |
| Ctrl/Cmd+P | Quick Open (files) |
| Ctrl/Cmd+Shift+P | Command Palette |
| Ctrl+T | Symbols |
| Ctrl/Cmd+Shift+F | Project search |

## Diff and review

For agent or Cmd+K changes:

- Side-by-side hunk review
- **F7** / **Shift+F7** - next / previous hunk
- **Ctrl/Cmd+Y** / **Ctrl/Cmd+N** - accept / reject (workbench bindings)
- Optional line blame when git metadata is available

Session checkpoints can rewind chat, code, or both ([Workbench](./workbench.md)). Project conventions for completions and the agent belong in `.navin/rules` ([Project Home](./project-home.md)).

## Related

- [Workbench](./workbench.md)
- [Modes](./modes.md)
- [Code agent](./code-agent.md)
- [Command output compaction](./command-output-compaction.md)
