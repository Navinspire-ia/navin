# Chat Apps for Self-Hosted AI Agents

Connect navin to Telegram, Discord, Slack, Email, Mattermost, and
other chat platforms. This page is the full chat-channel reference. If you want
a focused setup path for one platform, start with a guide:

| Platform | Guide |
|---|---|
| Telegram | [Build a Telegram AI Agent with navin](./guides/telegram-ai-agent.md) |
| Discord | [Build a Discord AI Agent with navin](./guides/discord-ai-agent.md) |
| Slack | [Build a Slack AI Agent with navin](./guides/slack-ai-agent.md) |
| WhatsApp | [Build a WhatsApp AI Agent with navin](./guides/whatsapp-ai-agent.md) |
| Email | [Build an Email AI Agent with navin](./guides/email-ai-agent.md) |
| Mattermost | [Build a Mattermost AI Agent with navin](./guides/mattermost-ai-agent.md) |

Want to build your own channel? See the [Channel Plugin Guide](./channel-plugin-guide.md).

Before configuring a chat app, make sure the local CLI path works:

```bash
navin agent -m "Hello!"
```

If that fails, fix installation, config, provider, or model setup first with [`quick-start.md`](./quick-start.md), [`providers.md`](./providers.md), and [`troubleshooting.md`](./troubleshooting.md). Chat apps require `navin gateway` to stay running after the channel is configured.

## Recommended Setup in the WebUI

For normal local setup, let the WebUI write and validate the channel config:

1. Run `navin webui`.
2. Open **Settings → Channels**.
3. Search for the platform and open its setup panel.
4. Follow the credential fields or QR flow. The screen tells you which platform-side token, permission, account, or URL it needs.
5. Let navin install the optional channel support when prompted.
6. Restart from the WebUI if it reports that a restart is required.
7. Send a private test message. If the channel returns a pairing code, approve the pending request in the WebUI and send the message again.

If your installed stable release does not show **Settings → Channels**, continue with the [manual setup pattern](#manual-setup-pattern) below or install current source.

Optional package installation is available to a same-machine WebUI by default. Remote browser clients cannot change the Python environment unless an administrator explicitly enables that capability. Run `navin plugins enable <channel>` locally when the guided install is unavailable.

The sections below explain what each chat platform requires and provide manual config for deployments that manage `config.json` directly.

> [!NOTE]
> If you are upgrading from a version where chat app SDKs were installed by default,
> install the channel extra in the same Python environment before enabling or
> restarting that channel:
>
> ```bash
> navin plugins enable <channel>
> ```
>
> Replace `<channel>` with names such as `telegram`, `slack`, `matrix`, or `msteams`.
> To turn a channel off later, run `navin plugins disable <channel>`.
> navin keeps the saved settings, but stops loading that channel after the
> next restart.

## Manual Setup Pattern

Most examples below are snippets to merge into `~/.navin/config.json`. When a snippet includes `allowFrom`, it is showing a static allowlist. For pairing-based access on supported channels, omit `allowFrom`; Slack and Mattermost also need `dm.policy` set to `"allowlist"` for DMs to issue pairing codes.

Every chat app uses the same shape:

1. Create or prepare the bot/account in the chat platform.
2. Copy the token, secret, QR login state, webhook URL, or account ID that platform gives you.
3. Merge that platform's JSON snippet into `~/.navin/config.json`.
4. Prefer pairing for DM-capable channels: omit `allowFrom`, let the first DM receive a pairing code, then approve it with `/pairing approve <code>`.
5. For channels without pairing, such as Email, keep access narrow with `allowFrom` or the platform-specific allow list.
6. Check that navin can see the configured channel:

```bash
navin channels status
```

7. Start the gateway and leave that terminal running:

```bash
navin gateway
```

8. Send a test DM. If the bot returns a pairing code, approve it and send the message again. In group chats, follow that channel's `groupPolicy` behavior: many channels default to mention-only, while Matrix and WhatsApp default to open group replies.

If `navin channels status` does not show the channel as enabled, the config snippet is in the wrong place, the channel name is misspelled, or the config file you edited is not the one navin is reading. If the channel is enabled but messages do not arrive, run `navin gateway --verbose` and compare the platform-side credentials, event permissions, and allow lists.

> `allowFrom: ["*"]` bypasses pairing and allows anyone who can reach that channel to talk to the bot. Use it only when that is intentional, or temporarily while testing in a private sandbox.

| Channel | What you need |
|---------|---------------|
| **Telegram** | Bot token from @BotFather |
| **Discord** | Bot token + Message Content intent |
| **WhatsApp** | QR code scan (`navin channels login whatsapp`) |
| **Slack** | Bot token + App-Level token |
| **Matrix** | Homeserver URL + Access token |
| **Email** | IMAP/SMTP credentials |
| **Microsoft Teams** | App ID + App Password + public HTTPS endpoint |
| **Signal** | signal-cli daemon + phone number |

<details>
<summary><b>Telegram</b></summary>

**Install the optional channel dependency**

```bash
navin plugins enable telegram
```

**1. Create a bot**
- Open Telegram, search `@BotFather`
- Send `/newbot`, follow prompts
- Copy the token

**2. Configure**

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> You can find your **User ID** in Telegram settings. It is shown as `@yourUserId`. Copy this value **without the `@` symbol** and paste it into the config file.
>
> `richMessages` defaults to `false`. Set it to `true` only if your Telegram client supports Bot API 10.1 rich messages and you want richer markdown rendering; keep it disabled for Telegram Web, which may show unsupported-message errors for rich messages.


**3. Run**

```bash
navin gateway
```

**Webhook mode (optional)**

Telegram uses long polling by default. To receive updates through a webhook, expose a public HTTPS URL that forwards to navin's local listener and set `mode` to `webhook`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "mode": "webhook",
      "webhookUrl": "https://example.com/telegram",
      "webhookListenHost": "127.0.0.1",
      "webhookListenPort": 8081,
      "webhookPath": "/telegram",
      "webhookSecretToken": "CHANGE_ME_RANDOM_SECRET",
      "webhookMaxConnections": 4,
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> `webhookSecretToken` is required in webhook mode. Do not expose the local webhook listener directly to the public internet without a reverse proxy or tunnel in front of it. TLS/Host policy is handled by your proxy; navin only listens on `webhookListenHost:webhookListenPort` and validates Telegram's webhook secret token. `webhookMaxConnections` defaults to `4`; navin still serializes Telegram updates per conversation before forwarding them to the agent.
>
> `webhookUrl` is the public HTTPS URL registered with Telegram. `webhookPath` is the local path navin listens on. They often use the same path, but may differ when a reverse proxy or tunnel rewrites the request path.

</details>

</details>

<details>
<summary><b>Discord</b></summary>

**1. Create a bot**
- Go to https://discord.com/developers/applications
- Create an application → Bot → Add Bot
- Copy the bot token

**2. Enable intents**
- In the Bot settings, enable **MESSAGE CONTENT INTENT**
- (Optional) Enable **SERVER MEMBERS INTENT** if you plan to use allow lists based on member data

**3. Get your User ID**
- Discord Settings → Advanced → enable **Developer Mode**
- Right-click your avatar → **Copy User ID**

**4. Configure**

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "allowChannels": [],
      "groupPolicy": "mention",
      "streaming": true
    }
  }
}
```

> `groupPolicy` controls how the bot responds in group channels:
> - `"mention"` (default) — Only respond when @mentioned
> - `"open"` — Respond to all messages
> DMs always respond when the sender is in `allowFrom`.
> - If you set group policy to open create new threads as private threads and then @ the bot into it. Otherwise the thread itself and the channel in which you spawned it will spawn a bot session.
> `allowChannels` restricts the bot to specific Discord channel IDs. Empty (default) means respond in every channel the bot can see. Example: `["1234567890", "0987654321"]`. The filter applies after `allowFrom`, so both must pass. Discord threads under an allowed parent channel are also allowed; for Forum channels, allowing the parent Forum channel allows all threads/posts in that forum.
> `streaming` defaults to `true`. Disable it only if you explicitly want non-streaming replies.

**5. Invite the bot**
- OAuth2 → URL Generator
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Read Message History`
- Open the generated invite URL and add the bot to your server

**6. Run**

```bash
navin gateway
```

</details>

<details>
<summary><b>Matrix (Element)</b></summary>

Enable Matrix support first:

```bash
navin plugins enable matrix
```

> [!NOTE]
> Matrix encryption is disabled by default on Windows because `matrix-nio[e2e]` depends on `python-olm`, which has no pre-built Windows wheel. Use macOS, Linux, or WSL2 if you need Matrix E2EE.

**1. Create/choose a Matrix account**

- Create or reuse a Matrix account on your homeserver (for example `matrix.org`).
- Confirm you can log in with Element.

**2. Get credentials**

- You need:
  - `userId` (example: `@navin:matrix.org`)
  - `password`

(Note: `accessToken` and `deviceId` are still supported for legacy reasons, but for reliable encryption, password login is recommended instead. If the `password` is provided, `accessToken` and `deviceId` will be ignored.)

**3. Configure**

```json
{
  "channels": {
    "matrix": {
      "enabled": true,
      "homeserver": "https://matrix.org",
      "userId": "@navin:matrix.org",
      "password": "mypasswordhere",
      "e2eeEnabled": true,
      "sasVerification": true,
      "allowFrom": ["@your_user:matrix.org"],
      "groupPolicy": "open",
      "groupAllowFrom": [],
      "allowRoomMentions": false,
      "maxMediaBytes": 20971520
    }
  }
}
```

> Keep a persistent `matrix-store` — encrypted session state is lost if these change across restarts.

| Option | Description |
|--------|-------------|
| `allowFrom` | User IDs allowed to interact. Empty denies all; use `["*"]` to allow everyone. |
| `groupPolicy` | `open` (default), `mention`, or `allowlist`. |
| `groupAllowFrom` | Room allowlist (used when policy is `allowlist`). |
| `allowRoomMentions` | Accept `@room` mentions in mention mode. |
| `e2eeEnabled` | E2EE support (default `true`). Set `false` for plaintext-only. |
| `sasVerification` | Auto-complete SAS device verification requests from allowed users (default `false`). Useful for Element X, which does not expose manual trust for third-party devices. |
| `maxMediaBytes` | Max attachment size (default `20MB`). Set `0` to block all media. |




**4. Run**

```bash
navin gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires the WhatsApp optional dependencies:

```bash
navin plugins enable whatsapp
```

**1. Link device with QR**

```bash
navin channels login whatsapp
# Scan QR with WhatsApp → Settings → Linked Devices
```

**2. Configure**

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["1234567890"]
    }
  }
}
```

Optional session database path:

```json
{
  "channels": {
    "whatsapp": {
      "databasePath": "~/.navin/whatsapp-auth/neonize.db"
    }
  }
}
```

**Migrating from the old bridge**

- Remove `bridgeUrl` and `bridgeToken`; WhatsApp no longer runs a local Node.js bridge.
- Re-run `navin channels login whatsapp`; old Baileys bridge auth data is not reused by neonize.
- Update `allowFrom` entries to the WhatsApp sender ID without a leading `+`.

**3. Run**

```bash
navin gateway
```

**Optional: static LID mappings**

Modern WhatsApp can deliver a sender's LID instead of their phone number. navin
learns LID to phone mappings at runtime when both identifiers are present, but you
can also seed mappings up front so the phone number resolves from the
very first message:

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["1234567890"],
      "lidMappings": { "123456789012345": "1234567890" }
    }
  }
}
```

</details>

<details>
<summary><b>Slack</b></summary>

Uses **Socket Mode** — no public URL required.

**Install the optional channel dependency**

```bash
navin plugins enable slack
```

**1. Create a Slack app**
- Go to [Slack API](https://api.slack.com/apps) → **Create New App** → "From scratch"
- Pick a name and select your workspace

**2. Configure the app**
- **Socket Mode**: Toggle ON → Generate an **App-Level Token** with `connections:write` scope → copy it (`xapp-...`)
- **OAuth & Permissions**: Add bot scopes: `chat:write`, `reactions:write`, `app_mentions:read`, `files:read`, `files:write`, `channels:history`, `groups:history`, `im:history`, `mpim:history`
- **Event Subscriptions**: Toggle ON → Subscribe to bot events: `message.im`, `message.channels`, `app_mention` → Save Changes
- **App Home**: Scroll to **Show Tabs** → Enable **Messages Tab** → Check **"Allow users to send Slash commands and messages from the messages tab"**
- **Install App**: Click **Install to Workspace** → Authorize → copy the **Bot Token** (`xoxb-...`)

> `files:read` is required to read files users send to navin. `files:write` is required for navin to send images, videos, and other file uploads. If you add either scope later, reinstall the Slack app to the workspace and restart navin so it uses the updated bot token.

**3. Configure navin**

```json
{
  "channels": {
    "slack": {
      "enabled": true,
      "botToken": "xoxb-...",
      "appToken": "xapp-...",
      "allowFrom": ["YOUR_SLACK_USER_ID"],
      "groupPolicy": "mention"
    }
  }
}
```

**4. Run**

```bash
navin gateway
```

DM the bot directly or @mention it in a channel — it should respond!

> [!TIP]
> - `groupPolicy`: `"mention"` (default — respond only when @mentioned), `"open"` (respond to all channel messages), or `"allowlist"` (restrict to specific channels via `groupAllowFrom`).
> - `groupAllowFrom`: channel IDs the bot may respond in when `groupPolicy` is `"allowlist"`.
> - `groupRequireMention`: when `true` and `groupPolicy` is `"allowlist"`, the bot only replies to channels in `groupAllowFrom` **and** only when @mentioned (instead of every message). No effect for `"mention"`/`"open"`. Use this to scope the bot to approved channels while keeping mention-only behavior.
> - DM policy defaults to open. Set `"dm": {"enabled": false}` to disable DMs.

</details>

<details>
<summary><b>Email</b></summary>

Give navin its own email account. It polls **IMAP** for incoming mail and replies via **SMTP** — like a personal email assistant.

**1. Get credentials (Gmail example)**
- Create a dedicated Gmail account for your bot (e.g. `my-navin@gmail.com`)
- Enable 2-Step Verification → Create an [App Password](https://myaccount.google.com/apppasswords)
- Use this app password for both IMAP and SMTP

**2. Configure**

> - `consentGranted` must be `true` to allow mailbox access. This is a safety gate — set `false` to fully disable.
> - `allowFrom`: Add your email address. Use `["*"]` to accept emails from anyone.
> - `smtpUseTls` and `smtpUseSsl` default to `true` / `false` respectively, which is correct for Gmail (port 587 + STARTTLS). No need to set them explicitly.
> - Set `"autoReplyEnabled": false` if you only want to read/analyze emails without sending automatic replies.
> - `postAction`: Optional post-processing for processed emails: `"delete"` or `"move"` (default `null`).
>   This runs only after an accepted email is successfully delivered to the AI pipeline.
> - `postActionMoveMailbox`: Destination mailbox used when `postAction` is `"move"` (for example `"Processed"` or `"[Gmail]/Trash"`).
> - `postActionIgnoreSkipped`: If `true` (default), skipped emails are ignored for post-action and not moved/deleted.
> - `postActionExpunge`: When `true`, the channel allows a full-mailbox `EXPUNGE` fallback if UID-scoped expunge is unavailable or fails (default `false`). Enable only on very old IMAP servers that lack modern UIDPLUS support. Note that this fallback will expunge **all** messages marked as deleted in the mailbox, including ones not handled by the agent. Leaving this off is safe for all modern IMAP servers.
> - `allowedAttachmentTypes`: Save inbound attachments matching these MIME types — `["*"]` for all, e.g. `["application/pdf", "image/*"]` (default `[]` = disabled).
> - `maxAttachmentSize`: Max size per attachment in bytes (default `2000000` / 2MB).
> - `maxAttachmentsPerEmail`: Max attachments to save per email (default `5`).

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-navin@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-navin@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-navin@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"],
      "postAction": "move",
      "postActionMoveMailbox": "[Gmail]/Trash",
      "postActionIgnoreSkipped": true,
      "postActionExpunge": false,
      "allowedAttachmentTypes": ["application/pdf", "image/*"]
    }
  }
}
```


**3. Run**

```bash
navin gateway
```

</details>

<details>
<summary><b>Microsoft Teams</b> (MVP — DM only)</summary>

> Direct-message text in/out, tenant-aware OAuth, conversation reference persistence.
> Uses a public HTTPS webhook — no WebSocket; you need a tunnel or reverse proxy.

**1. Enable Microsoft Teams support**

```bash
navin plugins enable msteams
```

**2. Create a Teams / Azure bot app registration**

Create or reuse a Microsoft Teams / Azure bot app registration. Set the bot messaging endpoint to a public HTTPS URL ending in `/api/messages`.

**3. Configure**

```json
{
  "channels": {
    "msteams": {
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "appPassword": "YOUR_APP_SECRET",
      "tenantId": "YOUR_TENANT_ID",
      "host": "0.0.0.0",
      "port": 3978,
      "path": "/api/messages",
      "allowFrom": ["*"],
      "replyInThread": true,
      "mentionOnlyResponse": "Hi — what can I help with?",
      "validateInboundAuth": true,
      "refTtlDays": 30,
      "pruneWebChatRefs": true,
      "pruneNonPersonalRefs": true,
      "refTouchIntervalS": 300
    }
  }
}
```

> - `replyInThread: true` replies to the triggering Teams activity when a stored `activity_id` is available.
> - `mentionOnlyResponse` controls what Navin receives when a user sends only a bot mention (`<at>Navin</at>`). Set to `""` to ignore mention-only messages.
> - `validateInboundAuth: true` enables inbound Bot Framework bearer-token validation (signature, issuer, audience, lifetime, `serviceUrl`). This is the safe default for public deployments. Only set it to `false` for local development or tightly controlled testing.
> - `refTtlDays` (default `30`) controls how old stored conversation refs can be before they are pruned.
> - `pruneWebChatRefs` (default `true`) drops refs with `webchat.botframework.com` service URLs.
> - `pruneNonPersonalRefs` (default `true`) drops refs whose `conversation_type` is not `personal`.
> - `refTouchIntervalS` (default `300`) throttles how often successful sends refresh `updated_at` for active refs.

**4. Run**

```bash
navin gateway
```

</details>

<details>
<summary><b>Signal</b></summary>

Uses **signal-cli** daemon in HTTP mode — receive messages via SSE, send via JSON-RPC.

**1. Install signal-cli**

Install [signal-cli](https://github.com/AsamK/signal-cli) and register a phone number:

```bash
signal-cli -u +1234567890 register
signal-cli -u +1234567890 verify <CODE>
```

Start the daemon:

```bash
signal-cli -a +1234567890 daemon --http localhost:8080
```

**2. Configure**

```json
{
  "channels": {
    "signal": {
      "enabled": true,
      "phoneNumber": "+1234567890",
      "daemonHost": "localhost",
      "daemonPort": 8080,
      "dm": {
        "enabled": true,
        "policy": "open"
      },
      "group": {
        "enabled": true,
        "policy": "open",
        "requireMention": true
      }
    }
  }
}
```

> - `phoneNumber`: Your registered Signal phone number.
> - `daemonHost` / `daemonPort`: Where signal-cli daemon is listening (default `localhost:8080`).
> - `dm.policy`: `"open"` (anyone can DM) or `"allowlist"` (only listed numbers/UUIDs). When `"allowlist"`, unlisted DM senders receive a pairing code.
> - `dm.allowFrom`: List of allowed phone numbers or UUIDs (used when policy is `"allowlist"`).
> - `group.policy`: `"open"` (all groups) or `"allowlist"` (only listed group IDs).
> - `group.requireMention`: When `true` (default), the bot only responds in groups when @mentioned.
> - `group.allowFrom`: List of allowed group IDs (used when group policy is `"allowlist"`).
> - `attachmentsDir`: Override the directory where signal-cli stores inbound attachments. Defaults to `~/.local/share/signal-cli/attachments` (the Linux default). Set this if signal-cli runs with a custom `XDG_DATA_HOME` or on macOS/Windows.
> - `groupMessageBufferSize`: Number of recent group messages kept for context (default `20`, must be > 0).

**3. Run**

```bash
navin gateway
```

> [!TIP]
> The channel automatically reconnects to the signal-cli daemon with exponential backoff if the connection drops.
> Markdown in bot replies is automatically converted to Signal text styles (bold, italic, code, etc.).

</details>
