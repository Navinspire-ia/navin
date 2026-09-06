# Build a Discord AI Agent with Navin

This guide connects Navin to Discord so a Discord user or server channel can talk to your self-hosted AI agent through the Navin desktop app.

## What this guide builds

- a Discord bot application
- Message Content intent enabled
- the Discord channel enabled in Navin Settings
- one direct message or mention test

## Prerequisites

- Navin desktop app able to reply in local chat
- Access to the Discord Developer Portal
- A Discord server where you can invite a bot

## Enable Discord in Settings

1. Create a Discord application, add a bot, copy the token, and enable **MESSAGE CONTENT INTENT** in the bot settings.
2. Invite the bot with permissions to read history and send messages.
3. In Navin, open **Settings → Channels → Discord**.
4. Paste the bot token, enable the channel, and keep group policy on **mention** for first deployment.
5. Optionally limit allowed server channels in the panel.
6. Leave allowlists empty for pairing-only mode (recommended). A new user should DM the bot first, get a pairing code, and be approved before using the bot in servers.
7. Save, restart when prompted, and keep Navin open.

## Test a message

1. Send the bot a DM first. It should return a pairing code.
2. Approve it in Navin, or with `/pairing approve ABCD-EFGH` from a trusted chat.
3. After approval, mention it in an allowed server channel:

```text
@your-bot Hello from Discord
```

## Security notes

- Keep group policy as mention-only for first deployment.
- Limit allowed channels where the bot should operate.
- Prefer pairing-only mode for user access; add a static allowlist only when intentional.
- Avoid open group behavior in busy channels until session routing is clear.
- Review tool access before inviting the bot into shared servers.

## Troubleshooting

- No messages arrive: confirm Message Content intent is enabled and Navin is open.
- DM returns a pairing code: approve it before testing normal replies.
- Server messages ignored: check pairing approval, allowed channels, and whether the bot was mentioned.
- Bot cannot reply: confirm invite permissions and channel overrides.

## Next

- [AI Agent Memory](./ai-agent-memory.md)
- [Configure MCP tools](./configure-mcp-tools.md)
