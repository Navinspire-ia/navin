# Dev module — Overview

The **Dev** module turns Navin into a full development environment, in the browser. It combines a VS Code-style workbench (file explorer, code editor with inline diagnostics, terminals, live preview) with one or more autonomous agent chats that can plan, write, run, test, and fix code in your project.

Open it from the sidebar (**Dev**) or navigate to `#/dev`.

## Layout

```
┌────────────┬──────────────────────────────┬──────────────────┐
│  Explorer  │  Editor / Preview / Browser  │  Agent chat(s)   │
│  (files)   │  + tabs, diagnostics         │  tabs, streaming │
├────────────┴──────────────────────────────┤                  │
│  Terminals (bash, sh, PowerShell, cmd)    │                  │
├───────────────────────────────────────────┴──────────────────┤
│  Status bar: env (WSL/Linux/macOS) · git branch · errors     │
└──────────────────────────────────────────────────────────────┘
```

Every panel is **resizable, collapsible, and hideable**: drag the separators, use the panel toggles in the tab bar, or collapse the explorer / terminal / chat entirely.

## Key capabilities

- **Project selector** (top right): open any folder — local, WSL, mounted server paths — by browsing or typing a path. Recent projects are remembered.
- **Multi-agent tabs**: run several independent agent sessions side by side, each with its own name, memory, and workspace scope. Double-click a tab to rename it.
- **Actions menu**: one-click audits (code review, security, vulnerabilities, performance, UX/UI, accessibility, refactoring, docs) applied to the whole project or the active file, with optional auto-fix. See [Actions](./actions.md).
- **Slash commands**: 32 built-in commands, including agent workflows (`/blueprint`, `/forge`, `/inspect`, `/fortify`, `/probe`, `/turbo`, `/pulse`) and utilities (`/checkpoint`, `/pilot`, `/pack`). See [Commands](./commands.md).
- **Checkpoints**: agent state (conversation + files) is snapshotted automatically before every prompt; rewind chat, code, or both with `/checkpoint`.
- **Model routing**: `/pilot <task>` switches to the model preset mapped to a task type (search, plan, review, security, dev, fast, deep, docs).
- **Project atlas & metagraph**: `/atlas` builds a `.metadata/` knowledge base (role, kind, and dependencies of every file). The **Graph** tab renders it as an interactive map — nodes colored by nature (front, back, SQL, config, test), edges from parsed imports, click a node to open the file.
- **Agent permissions**: a graphical panel in **Settings → Security → Agent permissions** to block or allow shell commands (plain prefixes or regex), toggle workspace restriction, and review built-in protections. Changes hot-apply to the running agent.
- **Diagnostics**: Python (Ruff) and JSON files are linted live; errors and warnings are underlined in the editor and counted in the status bar.
- **Project-wide search**: the **Search** tab in the side panel greps the whole project (ripgrep-backed, case/regex toggles); click a result to open the file at that exact line.
- **Source control**: the **Git** tab lists staged, unstaged, and untracked files; click a file to view its unified diff (untracked files render as all-added) and jump into the editor.
- **Git awareness**: the status bar shows the current branch, dirty state, and ahead/behind counts.
- **WSL support**: on Windows/WSL, terminals can open Linux shells and Windows interop shells (PowerShell, cmd.exe), and Windows drives (`/mnt/c`, …) are browsable.
- **Continuous operation**: sustained goals (`/goal`), cron jobs, and heartbeat checks let agents run long, autonomous loops with built-in safeguards.

## Quick start

1. Open **Dev** in the sidebar.
2. Pick a project with the **project selector** (top right) — browse, type a path, or reuse a recent project.
3. Create an agent with the **+** button in the chat tab bar and give it a name.
4. Ask for anything ("add a dark mode toggle"), use a slash command (`/forge implement the login page`), or click an **Action**.
5. Watch the agent work: steps, tool calls, file edits, and terminal output stream into the chat. Files it mentions are clickable and open in the editor.

## Related pages

- [Workbench](./workbench.md) — every panel in detail
- [Commands](./commands.md) — full slash command reference
- [Actions](./actions.md) — the quick actions menu
- [Skills](./skills.md) — dev skills the agent loads
- [Plugins](./plugins.md) — extend Navin with packs
