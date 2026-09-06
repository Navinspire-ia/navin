# How to Build a Personal AI Agent with Navin

This guide builds a personal AI agent you run locally in the Navin desktop app, then optionally connect to chat apps, memory, tools, and automations.

## What you will build

- Navin installed as a desktop app
- one working model provider
- one successful chat reply
- a project you can keep using

## When to use this

Use this when you want a personal AI agent that you control rather than a hosted chat-only interface. Navin is useful when the agent needs local project access, tool calls, session history, memory, scheduled work, or chat app delivery.

## Install

1. Open [navin.live/download](https://navin.live/download).
2. Download the installer for Windows, macOS, or Linux.
3. Install and open **Navin**.
4. Complete the setup wizard (language, model access, first action).

If terminals and config files are new to you, also read [`../start-without-technical-background.md`](../start-without-technical-background.md).

## First working chat

1. Open **Settings → Providers** and add an API key, or finish local Ollama setup under **Settings → Providers → Ollama**.
2. Open **Settings → Models**, add a configuration, and set it **Active**.
3. Open a project (demo workspace or your own folder).
4. Send a short message in chat, for example:

```text
Hello! Summarize what you can help me with in this project.
```

## Next steps in the app

- **Memory** - keep durable facts with Dream (`/dream` in chat). See [`ai-agent-memory.md`](./ai-agent-memory.md).
- **Channels** - Telegram, Discord, Slack, and more under **Settings → Channels**. See [`chat-app-ai-agent.md`](./chat-app-ai-agent.md).
- **Automations** - ask for schedules in chat; leave Navin open. See [`../automations.md`](../automations.md).
- **MCP / Apps** - external tools. See [`configure-mcp-tools.md`](./configure-mcp-tools.md).
- **Models** - named presets, task routing, fallbacks under **Settings → Models**. See [`../configuration.md`](../configuration.md).

## Production notes

- Keep one project per personal or client context.
- Use model configurations when you want stable names for fast, deep, local, or fallback models.
- Leave Navin running for chat apps and scheduled automations.
- Prefer Settings over hand-editing files for everyday changes.

## Security notes

- Do not paste API keys into shared chats or public docs; use Settings fields.
- Prefer chat app pairing for first setup. Keep allowlists narrow.
- Enable workspace restriction before exposing file or shell tools to other users.
- Use a separate project for experiments that can modify files.

## Troubleshooting

- No reply: open **Settings → Providers** and **Settings → Models**, confirm a configuration is Active, then retry chat.
- Wrong files: check the project picker in the workbench.
- Channel silent: keep Navin open and review **Settings → Channels**.

## Related docs

- [`../start-without-technical-background.md`](../start-without-technical-background.md)
- [`ai-agent-webui.md`](./ai-agent-webui.md)
- [`self-hosted-ai-agent.md`](./self-hosted-ai-agent.md)
