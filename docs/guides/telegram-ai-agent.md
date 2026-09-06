# Build a Telegram AI Agent with Navin

This guide connects Navin to Telegram so a paired Telegram user can message a self-hosted AI agent backed by your normal Navin Settings, tools, memory, and project.

## What this guide builds

- a Telegram bot created through BotFather
- the Telegram channel enabled in Navin Settings
- one pairing-approved Telegram account

## Prerequisites

- Navin desktop app installed and able to reply in local chat
- A Telegram account
- A bot token from `@BotFather`

## Enable Telegram in Settings

1. Open Navin and confirm **Settings → Providers** / **Models** work with a short chat.
2. Open **Settings → Channels → Telegram**.
3. Paste the BotFather token and enable the channel.
4. Leave allowlists empty for pairing-only mode (recommended first). The first DM from a new user gets a pairing code instead of agent access.
5. Save and restart when Navin prompts you.
6. Keep Navin open while you test.

Telegram uses long polling by default in the desktop app. Start there for the first test.

## Test a message

1. Open Telegram, DM the bot, and send:

```text
Hello from Telegram
```

2. The bot should reply with a pairing code.
3. Approve it in Navin (Channels / pairing UI), or from an already trusted chat with `/pairing approve ABCD-EFGH`.
4. Send the message again after approval. The reply should use the same model and project as your desktop chat.

## Security notes

- Prefer pairing-only mode for first setup. Add an allowlist only when you want a static list instead of code approval.
- Do not allow everyone unless the bot is isolated or intentionally public.
- Rotate the BotFather token if it is pasted into logs or shared files.
- Review tool access before adding group chats or more users.

## Troubleshooting

- Channel not listed: reopen **Settings → Channels**, enable Telegram again, restart if prompted.
- Messages do not arrive: confirm the bot token and that Navin is still open.
- First DM returns a pairing code: that is expected - approve before testing normal agent replies.
- Telegram Web shows unsupported rich messages: keep rich messages disabled in the channel panel if available.

## Next

- [AI Agent Memory](./ai-agent-memory.md)
- [Long-running AI Agent](./long-running-ai-agent.md)
- [Configure MCP tools](./configure-mcp-tools.md)
