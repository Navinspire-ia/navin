# Separate Projects and Channel Setups

The Navin desktop app is one application. Separate work is handled with **projects**, **Settings**, and optional **channel** bots - not by launching multiple CLI processes.

## One app, many projects

Open Navin once. Use the project / workspace picker to switch folders. Each project keeps its own:

- chat sessions and history
- memory files (`USER.md`, `MEMORY.md`, Dream state)
- automations and heartbeat tasks tied to that project
- file tree, terminals, and Dev workbench scope

Switching projects changes where the agent reads and writes. Prefer one project per trust boundary (personal vs work, client A vs client B).

## Separate channel bots

To talk to Navin from Telegram, Discord, Slack, and similar apps:

1. Open **Settings → Channels**.
2. Enable the platform you need and paste its token or complete its login flow.
3. Keep Navin open so messages can arrive and replies can send.
4. Prefer pairing for first DMs; keep group policies narrow until you trust the setup.

You can enable more than one channel in the same app. Use different bot tokens when you want distinct public identities (for example a personal bot and a team bot). Channel credentials live in Settings for that Navin install.

If two bots must stay fully isolated (different memory, different tools, different people), use separate projects and review which project is active before testing each bot.

## Models and providers per role

Under **Settings → Providers** and **Settings → Models** you can:

- add more than one provider key
- create named model configurations (presets)
- set task routing so fast work and deep work use different models
- set fallback models if a provider rate-limits

You do not need a second Navin install to use a cheaper model for summaries and a stronger model for coding.

## When you might want two installs

Most people never need this. Consider a second desktop install only if your OS account isolation requires it (for example a locked-down work laptop profile vs a personal machine). Everyday separation is: different projects, different channel bots, different model presets - all inside one Navin app.

## Common setups

| Goal | Approach in the app |
|---|---|
| Personal vs client work | Two projects; switch with the project picker |
| Telegram + Discord | Enable both under **Settings → Channels**; keep Navin open |
| Fast vs deep models | **Settings → Models** presets + task routing |
| Quiet background checks | `HEARTBEAT.md` in the project + Automations view |
| Test risky tools safely | A separate experimental project with stricter Settings |

## Related docs

- [`configuration.md`](./configuration.md) - Providers, Models, task routing
- [`automations.md`](./automations.md) - Scheduled work while Navin stays open
- [`guides/chat-app-ai-agent.md`](./guides/chat-app-ai-agent.md) - Channels from Settings
- [`start-without-technical-background.md`](./start-without-technical-background.md) - First launch wizard
