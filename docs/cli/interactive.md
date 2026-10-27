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

## Modes

Default is **agent**. `/mode` or **Ctrl+T** opens the list: chat, ask, plan, agent, review, security, debug.

## Settings

**Ctrl+G**: Providers, Models, Tools & MCP, Skills, Image, Video, Voice, Web, System, Security, Guardrails, Git, Browser, Computer, Rules.
