# Workbench

Every part of the Dev workbench is interactive: panels resize by dragging separators, collapse with the toggles in the tab bar, and remember their state.

## Project selector

Top right of the workbench. It works like the folder picker in VS Code/Cursor:

- **Recent projects** — the last projects you opened, one click to switch back.
- **Quick access roots** — home directory, filesystem root, and (under WSL) Windows drives like `/mnt/c`.
- **Browse** — a folder browser dialog to navigate the filesystem.
- **Type a path** — paste or type any absolute path (`/home/me/app`, `/mnt/c/Users/me/proj`, a mounted server path…).

Selecting a project sets the **workspace scope** of the active agent session: the file tree, terminals, diagnostics, git status, and every agent tool operate inside that folder. If no agent session exists yet, one is created automatically with that scope.

Environments detected: Linux, WSL (with Windows interop), macOS, Windows.

## Explorer

- Lazy-loaded file tree of the project.
- Create, rename, and delete files and folders.
- Click a file to open it in an editor tab.
- Collapsible with the panel toggle; resizable by dragging its edge.

## Editor

- CodeMirror-based, with syntax highlighting per language and dark/light themes.
- Multiple tabs, unsaved-draft indicator, save with the toolbar button or `Ctrl/Cmd+S`.
- **Inline diagnostics**: Python files are checked with Ruff, JSON files with a parser. Errors and warnings are underlined (squiggles) and listed in the status bar. Diagnostics refresh on open and on save.
- Files mentioned by the agent in chat are clickable and open directly in the editor.

## Terminals

- Real PTY terminals inside the workbench, multiple tabs.
- Shell picker: `bash`, `sh`, `zsh` when available — and under WSL, Windows interop shells: **PowerShell** and **cmd.exe**.
- Full interactivity (arrows, ctrl-keys, TUI apps), resizable and collapsible panel.
- The agent's own shell executions stream into the chat; your terminals are yours.

## Preview / Browser

- Built-in preview pane for running web apps (dev servers, etc.).
- Switch the center area between **editor** and **browser** modes; the preview URL is editable and reloadable.

## Agent chat panel

- Right-hand panel, resizable by dragging the separator and hideable with the panel toggle.
- **Multiple agents**: each tab is an independent chat session with its own agent loop, memory, name, and workspace scope. Create with **+** (you are prompted for a name), rename by double-clicking the tab, close with **×**.
- A green dot marks agents that are currently running.
- The chat renders the agent's work live: reasoning steps, tool calls, file diffs, command output — Cursor-style structured blocks.

## Status bar

Bottom of the workbench, VS Code-style:

| Item | Meaning |
| --- | --- |
| Environment | `WSL`, `Linux`, `macOS`… detected from the backend |
| Git | current branch, dirty indicator, ahead/behind counts (refreshes periodically and on save) |
| Project | active project name |
| Active file | filename plus its error/warning counts from diagnostics |

## Checkpoints

Before every prompt, Navin snapshots the session (conversation + tracked file contents). Use `/checkpoint list`, `/checkpoint restore <name> [all|chat|code]`, `/checkpoint save [note]`, `/checkpoint delete <name>`. Checkpoints complement git — they are quick rewind points, not version control.

## Related

- [Commands](./commands.md)
- [Actions](./actions.md)
