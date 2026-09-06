# How to Connect an AI Agent to Chat Apps with Navin

Navin can reply as a self-hosted chatbot or AI agent in Telegram, Discord, Slack, WhatsApp, Email, Mattermost, and other chat apps. Messages arrive while the desktop app is open; Navin runs the agent and sends replies back to the same channel.

## What you will build

- a working local agent in Navin
- one enabled chat channel in Settings
- a pairing-based approval flow or a narrow static allowlist

## When to use this

Use chat apps when the agent should live where users already communicate: private DMs, team channels, group chats, email threads, or bot workspaces.

## Before you add a channel

1. Open Navin and complete provider setup under **Settings → Providers** and **Settings → Models**.
2. Send a short message in the desktop chat to confirm replies work.
3. Keep Navin open while you test the channel.

Then choose one platform guide for bot or account prerequisites:

- [Telegram AI agent](./telegram-ai-agent.md)
- [Discord AI agent](./discord-ai-agent.md)
- [Slack AI agent](./slack-ai-agent.md)
- [WhatsApp AI agent](./whatsapp-ai-agent.md)
- [Email AI agent](./email-ai-agent.md)
- [Mattermost AI agent](./mattermost-ai-agent.md)

## Connect a channel in Settings

1. Get the platform token, login state, or mailbox credentials from that platform.
2. Open **Settings → Channels** in Navin.
3. Choose the platform and open its setup panel.
4. Paste credentials or complete the QR / login flow; install optional support if the app prompts you.
5. Restart when Navin requests it.
6. Send a private test message from the chat app.
7. Approve the pairing request in Navin when a DM-capable channel asks for one.

If your installed release does not show **Settings → Channels**, use the in-app help for Chat Apps or update Navin.

## Production notes

- Leave Navin running for always-on chat apps.
- Use mention-only group policies before opening a bot to busy channels.
- Enable one channel at a time while debugging.
- Prefer DMs for first tests; pairing only works in DMs, and group chats add permissions and routing behavior.

## Security notes

- Prefer pairing or explicit allowlists; do not allow everyone unless the bot is intentionally public or isolated.
- Rotate bot tokens if they are pasted into logs or shared files.
- Review file, shell, and web tool access before inviting other users.

## Troubleshooting

- Channel missing in Settings: enable the platform panel again or restart Navin after optional channel support installs.
- First DM returns a pairing code: approve the pending request in Navin (or with `/pairing approve <code>` from an already trusted chat).
- Messages do not arrive: confirm credentials, that Navin is open, and that allow lists / group policies match your test.
- Group replies unexpected: review that channel's group policy in Settings.

## Related docs

- [`secure-local-ai-agent.md`](./secure-local-ai-agent.md)
- [`../automations.md`](../automations.md)
- [`long-running-ai-agent.md`](./long-running-ai-agent.md)
