# navin-cli

Full-screen terminal UI in the folder you start it from.

```bash
cd your-project
navin-cli
navin-cli ~/code/app
```

From a `navin` clone: `.venv/bin/navin-cli`.

Type a message and Enter. Shift+Enter inserts a newline. `/` completes slash commands. **Ctrl+Shift+P** is the command palette.

## Keys

| Key | Action |
|---|---|
| Enter | Send |
| Shift+Enter | New line |
| Esc | Stop the running turn |
| Ctrl+Shift+P | Command palette |
| Ctrl+G | Settings |
| Ctrl+Y | Guardrails |
| Ctrl+K | Skills |
| Ctrl+E | Security |
| Ctrl+U | Tools & MCP |
| Ctrl+O | Model list |
| Ctrl+T | Mode list (default: agent) |
| Ctrl+P / Ctrl+B | Show or hide the AGI and settings panel on the right |
| Ctrl+S | Show or hide the projects and sessions on the right |
| Ctrl+N | New chat |
| F2 / F3 | Graph / Evolve |
| F1 | Help |
| Ctrl+C / Cmd+C | Copy the selection (selecting text or right-click also copies it) |
| Ctrl+Q | Quit |

`exit`, `quit`, or `:q` in an empty prompt also quits.

## Projects and sessions

**Ctrl+S** (or **Sessions** in the bottom line) lists every project with its latest sessions. Several sessions can work at once, in one project or in several: opening another project does not stop the one you leave. Its work goes on in the background, a spinner stays on each working session and on its folder, and a short note says when one finishes or waits for your answer. Opening the project again brings it back as it was: its draft, its queued messages, what the turn did meanwhile, then the rest live. A session that is still working cannot be deleted (stop it first with Esc), and quitting while other projects still work asks first. The **⋯** menu of a session opens, forks (a copy of the chat to go on from there), renames, pins, downloads, shares or deletes it.

## Modes

Default is **agent**. `/mode` or **Ctrl+T** opens the list: chat, ask, plan, agent, review, security, debug.

## Settings

**Ctrl+G**: Providers, Models, Tools & MCP, Skills, Image, Video, Voice, Web, System, Security, Guardrails, Git, Browser, Computer, Rules.
