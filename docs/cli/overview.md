# CLI

Two commands after install. The AGI switches live under `navin agi` (first command in [Commands](./commands.md)).

| Command | Role |
|---|---|
| `navin agi` | Skills, world model, policy, transfer protocol, memory ([full page](./agi.md)) |
| `navin-cli` | Terminal UI in the current folder |
| `navin` | Everything else: `navin .`, status, providers |

Config: `~/.navin/config.json`. Workspace and chats stay on the machine.

```bash
cd your-project
navin-cli

navin .
navin --version
navin status
navin doctor
```

From a `navin` clone, after install:

```bash
.venv/bin/navin-cli
```

`navin .` opens the desktop window when it is installed, otherwise the local workbench.
