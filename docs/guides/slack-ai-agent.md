# Build a Slack AI Agent with Navin

This guide connects Navin to Slack through Socket Mode. No public webhook URL is required for the first working setup.

## What this guide builds

- a Slack app with Socket Mode
- a bot token and app-level token
- the Slack channel enabled in Navin Settings
- a DM pairing flow and mention test from an approved Slack user

## Prerequisites

- Navin desktop app able to reply in local chat
- Permission to create a Slack app in a workspace

## Enable Slack in Settings

1. In Slack, create an app, enable Socket Mode, create an app-level token with `connections:write`, add bot scopes, subscribe to bot events, and install the app to your workspace.
2. In Navin, open **Settings → Channels → Slack**.
3. Paste the bot token (`xoxb-…`) and app-level token (`xapp-…`).
4. Keep group policy on **mention** for first deployment.
5. Prefer allowlist / pairing for DMs so new DM senders receive a pairing code.
6. Save, restart when prompted, and keep Navin open.

## Test a message

1. DM the Slack bot directly. It should return a pairing code.
2. Approve it in Navin, or with `/pairing approve ABCD-EFGH` from a trusted chat.
3. DM the bot again, or mention it in a channel:

```text
@navin Hello from Slack
```

## Security notes

- Keep group policy as mention-only unless the bot is intentionally listening to every channel message.
- Keep DM allowlist / pairing on when you want approval before access.
- Reinstall the Slack app after changing scopes.
- Keep bot and app tokens out of shared documents; paste them only into Settings.

## Troubleshooting

- Socket Mode fails: confirm the app-level token starts with `xapp-` and Navin is open.
- Bot cannot send files: add `files:write`, reinstall the Slack app, restart Navin.
- DM responds without pairing: check that DM policy is allowlist / pairing in Settings.
- Channel messages ignored: check event subscriptions and group policy.

## Next

- [Configure web search](./configure-web-search.md)
- [Long-running AI Agent](./long-running-ai-agent.md)
