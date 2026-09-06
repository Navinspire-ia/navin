# Navin AGI

Navin is an **AGI**. The product name on the site is **Navin AGI**.

A harness is the loop that runs a model with tools, memory, permissions and channels until the job is done. It is not a chat tab in the browser. It is not autocomplete in an editor.

## What the harness does

1. Take a goal (chat, board mission, channel message, heartbeat, slash command).
2. Call a model (local, BYOK or managed).
3. Use tools: files, shell, git, web, scrape, MCP, cron, studios.
4. Keep memory and session context.
5. Ask you before risky steps (Git brakes, approvals).
6. Repeat until the slice is done, then stop for review.

## What sits on the harness

- **Code** - Vision 360, plan, review, security, debug, board autonomy
- **Channels** - WhatsApp, Telegram, Slack, email and more
- **Studios** - scraping, leads, marketing, SEO, documents, meeting, media
- **Heartbeat** - quiet recurring checks
- **Plugins / skills / MCP** - extend the same loop

## Local-first

The harness runs on your machine (Windows, macOS, Linux). Chats and files stay on disk. You choose local models, your own keys, or optional managed models.

## Related

- [Architecture](./architecture.md) - AgentLoop, AgentRunner, tools
- [Quick start](./quick-start.md)
- [Board Autonomy](./navin_dev/en/board-autonomy.md)
