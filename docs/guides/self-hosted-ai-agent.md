# How to Keep a Self-Hosted AI Agent with Navin

This guide frames Navin as a self-hosted AI agent that stays on **your computer**. The desktop app keeps chats, projects, memory, and provider keys local unless you choose otherwise.

## What you get

- Navin running as a local desktop app under your control
- model providers configured in Settings
- optional chat apps and automations while the app stays open
- data that lives in your projects on disk

## When to use this

Use this path when you want local ownership of the agent process, project files, memory files, and provider keys - without sending everyday work to a hosted chat product.

## Keep Navin on your machine

1. Install Navin from [navin.live/download](https://navin.live/download).
2. Open the app and complete the setup wizard.
3. Add a provider under **Settings → Providers** (your own API key or a local model server such as Ollama).
4. Set an active model under **Settings → Models**.
5. Open a project folder you control and chat there.

Chats, files, and memory stay on your computer. An optional navin.live account syncs plan, license, and device activations only - see Privacy in [`../start-without-technical-background.md`](../start-without-technical-background.md).

## Stay available for channels and automations

Leave Navin running when you need:

- Telegram, Discord, Slack, and other channels (**Settings → Channels**)
- scheduled automations and heartbeat checks
- long-running goals in chat

You do not deploy a separate server for everyday personal use. The desktop app is the local runtime.

## Production notes

- Use one project per trust boundary.
- Prefer Settings for secrets; avoid pasting keys into shared documents.
- Review tool access (**Settings**) before inviting teammates through chat apps.
- For separate clients or bots, use separate projects and channel tokens - see [`../multiple-instances.md`](../multiple-instances.md).

## Security notes

- Keep Navin local unless you intentionally share access on a LAN.
- Prefer pairing for DM-capable chat apps; keep allowlists strict.
- Enable project / workspace restriction before broad file or shell tools.
- On Linux, prefer the shell sandbox when Settings offer it.

## Troubleshooting

- Provider errors: fix **Settings → Providers** / **Models**, then retry a short chat.
- Channels idle: confirm the channel is enabled and Navin is still open.
- Wrong memory or files: confirm the active project in the project picker.

## Related docs

- [`build-a-personal-ai-agent.md`](./build-a-personal-ai-agent.md)
- [`secure-local-ai-agent.md`](./secure-local-ai-agent.md)
- [`../multiple-instances.md`](../multiple-instances.md)
- [`long-running-ai-agent.md`](./long-running-ai-agent.md)
