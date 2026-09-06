# navin-cli

Full-screen terminal UI in the folder you start it from.

```bash
cd your-project
navin-cli
navin-cli ~/code/app
```

From a `navin-agi` clone: `.venv/bin/navin-cli`.

Type a message and Enter. Shift+Enter inserts a newline. `/` completes slash commands. **Ctrl+P** is the command palette.

## Keys

| Key | Action |
|---|---|
| Enter | Send |
| Shift+Enter | New line |
| Esc | Stop the running turn |
| Ctrl+P | Command palette |
| Ctrl+G | Settings |
| Ctrl+Y | Guardrails |
| Ctrl+K | Skills |
| Ctrl+E | Security |
| Ctrl+U | Tools & MCP |
| Ctrl+O | Model list |
| Ctrl+T | Mode list (default: agent) |
| Ctrl+S | Sessions |
| Ctrl+B | Show or hide the right panel |
| Ctrl+N | New chat |
| F2 / F3 | Graph / Evolve |
| F1 | Help |
| Ctrl+Q | Quit |

`exit`, `quit`, or `:q` in an empty prompt also quits.

## Modes

Default is **agent**. `/mode` or **Ctrl+T** opens the list: chat, ask, plan, agent, review, security, debug.

## Settings

**Ctrl+G**: Providers, Models, Tools & MCP, Skills, Image, Video, Voice, Web, System, Security, Guardrails, Git, Browser, Rules.
