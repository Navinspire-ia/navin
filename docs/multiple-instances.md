# Multiple Instances

Run multiple navin instances simultaneously with separate configs and runtime data. Use `--config` as the main entrypoint. Optionally pass `--workspace` during `onboard` when you want to initialize or update the saved workspace for a specific instance.

## Quick Start

If you want each instance to have its own dedicated workspace from the start, pass both `--config` and `--workspace` during onboarding.

**Initialize instances:**

```bash
# Create separate instance configs and workspaces
navin onboard --config ~/.navin-telegram/config.json --workspace ~/.navin-telegram/workspace
navin onboard --config ~/.navin-discord/config.json --workspace ~/.navin-discord/workspace
navin onboard --config ~/.navin-work/config.json --workspace ~/.navin-work/workspace
```

**Configure each instance:**

Edit `~/.navin-telegram/config.json`, `~/.navin-discord/config.json`, etc. with different channel settings. The workspace you passed during `onboard` is saved into each config as that instance's default workspace.

**Run instances:**

```bash
# Check one instance before starting it
navin status --config ~/.navin-telegram/config.json

# Instance A - Telegram bot
navin gateway --config ~/.navin-telegram/config.json

# Instance B - Discord bot
navin gateway --config ~/.navin-discord/config.json

# Instance C - Discord bot with custom port
navin gateway --config ~/.navin-work/config.json --port 18792
```

## Path Resolution

When using `--config`, navin derives its runtime data directory from the config file location. The workspace still comes from `agents.defaults.workspace` unless you override it with `--workspace`.

To open a CLI session against one of these instances locally:

```bash
navin agent -c ~/.navin-telegram/config.json -m "Hello from Telegram instance"
navin agent -c ~/.navin-discord/config.json -m "Hello from Discord instance"

# Open the browser workbench for a specific instance
navin webui -c ~/.navin-telegram/config.json

# Optional one-off workspace override
navin agent -c ~/.navin-telegram/config.json -w /tmp/navin-telegram-test
```

> `navin agent` starts a local CLI agent using the selected workspace/config. It does not attach to or proxy through an already running `navin gateway` process.

| Component | Resolved From | Example |
|-----------|---------------|---------|
| **Config** | `--config` path | `~/.navin-A/config.json` |
| **Workspace** | `--workspace` or config | `~/.navin-A/workspace/` |
| **Cron Jobs** | workspace directory | `~/.navin-A/workspace/cron/` |
| **Media / runtime state** | config directory | `~/.navin-A/media/` |

## How It Works

- `--config` selects which config file to load
- By default, the workspace comes from `agents.defaults.workspace` in that config
- If you pass `--workspace`, it overrides the workspace from the config file

## Minimal Setup

1. Copy your base config into a new instance directory.
2. Set a different `agents.defaults.workspace` for that instance.
3. Start the instance with `--config`.

Example config fragment:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.navin-telegram/workspace"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

The copied base config can keep using the same `modelPresets` and `agents.defaults.modelPreset`. If this instance needs a different model, add another preset and set `agents.defaults.modelPreset` to that preset name.

Start separate instances:

```bash
navin status --config ~/.navin-telegram/config.json
navin gateway --config ~/.navin-telegram/config.json
navin gateway --config ~/.navin-discord/config.json
```

Each gateway instance also exposes a lightweight HTTP health endpoint on `gateway.host:gateway.port`. By default, the gateway binds to `127.0.0.1`, so the endpoint stays local unless you explicitly set `gateway.host` to a public or LAN-facing address.

- `GET /health` returns `{"status":"ok"}`
- Other paths return `404`

Override workspace for one-off runs when needed:

```bash
navin gateway --config ~/.navin-telegram/config.json --workspace /tmp/navin-telegram-test
```

## Common Use Cases

- Run separate bots for Telegram, Discord, Slack, and other platforms
- Keep testing and production instances isolated
- Use different models or providers for different teams
- Serve multiple tenants with separate configs and runtime data

## Notes

- Each instance must use a different port if they run at the same time
- Use a different workspace per instance if you want isolated memory, sessions, and skills
- `--workspace` overrides the workspace defined in the config file
- Cron jobs are stored in the active workspace; runtime media/state is derived from the config directory
