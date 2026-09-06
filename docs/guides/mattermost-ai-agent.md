# Build a Mattermost AI Agent with Navin

This guide connects Navin to Mattermost through the built-in Mattermost channel, using the desktop app Settings panel.

## What this guide builds

- a Mattermost bot account or token
- the Mattermost channel enabled in Navin
- mention-only group behavior for first deployment
- one pairing-approved DM or mention test

## Prerequisites

- Navin desktop app able to reply in local chat
- A Mattermost server URL
- A bot token or personal access token for the bot account

## Enable Mattermost in Settings

1. Open **Settings → Channels → Mattermost**.
2. Enter the server URL, token, and team id.
3. Keep group policy on **mention** for the first test.
4. Prefer allowlist / pairing for DMs so new DM senders receive a pairing code.
5. Enable reply-in-thread if you want threaded replies.
6. Save, restart when prompted, and keep Navin open.

## Test a message

1. DM the bot account. It should return a pairing code.
2. Approve it in Navin, or with `/pairing approve ABCD-EFGH` from a trusted chat.
3. DM the bot again, or mention it in a channel where the bot has access:

```text
@navin Hello from Mattermost
```

## Security notes

- Keep the Mattermost token in Settings; do not paste it into shared docs.
- Keep DM allowlist / pairing on when you want approval before access.
- Use mention-only group behavior before opening the bot to busy channels.
- Review file and shell tools before inviting broad channel access.

## Troubleshooting

- Settings say server URL and token are required: fill both fields (and team id) in the channel panel.
- DMs ignored: review DM policy and pairing approval state.
- Channel messages ignored: confirm the bot is mentioned and belongs to the team/channel.
- Thread replies surprising: review reply-in-thread and include-thread-context options in Settings.

## Next

- [Long-running AI Agent](./long-running-ai-agent.md)
