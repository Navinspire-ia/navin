# How to Use the Navin Desktop App Interface

Navin is a desktop application with a full workbench: persistent chat, visible agent activity, project controls, Apps, MCP presets, Skills, Settings, and Automations.

## What you will use

- the Navin desktop window (Windows, macOS, or Linux)
- one or more persistent chat sessions
- a live timeline of agent messages, tool calls, and file edit diffs
- Settings panels for providers, models, channels, and tools

## When to use this

Use the desktop UI for everyday agent work: project chat, file attachments, model switching, project selection, Apps, Skills, and scheduled automations. You do not need a terminal.

## Open Navin

1. Install Navin from [navin.live/download](https://navin.live/download).
2. Open the app and complete the setup wizard if prompted.
3. Confirm a provider under **Settings → Providers**, then a model under **Settings → Models**.
4. Open a project (or the demo workspace) and send a message in chat.

## What you see while the agent works

When Navin edits a file, the activity timeline can show changed line counts, a unified diff, and an **Open file** action for a read-only preview. File previews follow the chat's current workspace access mode: restricted access stays inside the selected project; Full Access can preview files outside the project when Settings allow it.

Use the Dev workbench for explorer, editor, terminals, preview, and agent chat side by side. Details: [`../navin_dev/en/workbench.md`](../navin_dev/en/workbench.md).

## Everyday surfaces

| Area | What it is for |
|---|---|
| Chat | Persistent sessions, attachments, composer actions (`/dream`, `/goal`, …) |
| Settings → Providers / Models | API keys, local endpoints, active model, task routing |
| Settings → Channels | Telegram, Discord, Slack, and other chat apps |
| Apps / MCP | External tools via Model Context Protocol |
| Skills | Install and enable skill packs |
| Automations | Scheduled jobs and system heartbeat inspection |
| Memory | Durable project memory and Dream (see [`../memory.md`](../memory.md)) |

## Security notes

- The desktop app stays on your computer by default.
- Do not expose Navin to a LAN or public network without an intentional access model.
- Keep file and shell tools scoped to the project before inviting other users through chat channels.

## Troubleshooting

- Chat opens but messages fail: check **Settings → Providers** and **Settings → Models**, then send a short test message.
- No project files: pick a folder with the project selector in the workbench.
- Automations idle: leave Navin open and confirm the job is linked to a chat (see [`../automations.md`](../automations.md)).

## Related docs

- [`build-a-personal-ai-agent.md`](./build-a-personal-ai-agent.md)
- [`../configuration.md`](../configuration.md)
- [`../start-without-technical-background.md`](../start-without-technical-background.md)
