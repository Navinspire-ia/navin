# Build an Email AI Agent with Navin

This guide turns Navin into an email AI agent that polls IMAP for accepted messages and replies through SMTP - configured from Settings in the desktop app.

## What this guide builds

- a dedicated mailbox for Navin
- IMAP and SMTP credentials in **Settings → Channels → Email**
- an allowed sender list
- Navin left open so it can poll and reply

## Prerequisites

- Navin desktop app able to reply in local chat
- A mailbox for the bot
- IMAP and SMTP access (for Gmail, use an app password rather than your account password)

## Enable Email in Settings

1. Open **Settings → Channels → Email**.
2. Enable the channel and grant mailbox consent when prompted.
3. Fill IMAP host/port/username/password and SMTP host/port/username/password.
4. Set the from-address and a narrow **allow from** list (your real email first).
5. Enable auto-reply when you want Navin to answer accepted mail.
6. Save, restart when prompted, and keep Navin open long enough for the poll interval.

Typical Gmail values: IMAP `imap.gmail.com:993`, SMTP `smtp.gmail.com:587`, plus an app password.

## Test a message

Send an email from an address in the allow list to the bot mailbox. Keep Navin open until the poll cycle receives it.

## Security notes

- Use a dedicated mailbox, not your primary personal inbox.
- Clear consent / disable the channel to fully stop mailbox access.
- Email does not use DM pairing. Keep the allow list narrow; allowing everyone accepts mail from anyone.
- Prefer Settings fields or OS secrets for mailbox passwords - do not commit them.
- Enable attachment types only when the agent needs them.

## Troubleshooting

- Login fails: confirm IMAP/SMTP access and app-password setup.
- Bot reads but does not reply: check auto-reply, SMTP settings, and allowed sender addresses.
- Attachments missing: review allowed attachment types and size limits in the channel panel.

## Next

- [Secure local AI agent](./secure-local-ai-agent.md)
- [AI Agent Memory](./ai-agent-memory.md)
