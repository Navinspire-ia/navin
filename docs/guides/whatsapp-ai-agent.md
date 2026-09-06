# Build a WhatsApp AI Agent with Navin

This guide connects Navin to WhatsApp through the WhatsApp channel. The channel links as a WhatsApp device and uses the same Navin agent runtime, tools, memory, and project as the desktop chat.

## What this guide builds

- WhatsApp channel support enabled in Navin
- a linked WhatsApp device session
- one pairing-approved WhatsApp sender

## Prerequisites

- Navin desktop app able to reply in local chat
- A WhatsApp account that can link a new device
- A machine that can keep Navin open

## Enable WhatsApp in Settings

1. Open **Settings → Channels → WhatsApp**.
2. Enable the channel and complete the link / QR flow when the panel offers it (scan from WhatsApp → Settings → Linked Devices).
3. Keep group policy on **mention** for a first deployment.
4. Leave allowlists empty for pairing-only mode on private chats (recommended).
5. Save, restart when prompted, and keep Navin open.

## Test a message

1. Send the bot a private WhatsApp message. It should return a pairing code.
2. Approve it in Navin, or with `/pairing approve ABCD-EFGH` from a trusted chat.
3. Send the message again after approval. The reply should use the same model and project as desktop chat.

## Security notes

- Treat the WhatsApp session as account access - protect the machine that runs Navin.
- Prefer pairing-only mode for first setup. Add an allowlist only when intentional.
- Keep group policy as mention-only before adding the bot to groups.
- Avoid allowing everyone unless the bot is intentionally public or isolated.

## Troubleshooting

- QR linking fails: reopen the WhatsApp panel in Settings and start the link flow again.
- Migrating from an older bridge setup: clear obsolete bridge fields in the channel panel if shown, then re-link.
- Sender appears as a LID instead of a phone number: let Navin learn the mapping at runtime, or use LID mappings in advanced channel settings when available.
- First private message returns a pairing code: approve before testing normal replies.

## Next

- [Secure local AI agent](./secure-local-ai-agent.md)
