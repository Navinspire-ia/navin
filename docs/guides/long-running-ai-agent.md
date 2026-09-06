# How to Run a Long-Running AI Agent with Navin

Navin can keep agent work alive across turns through sustained goals, persistent chat sessions, scheduled automations, and heartbeat checks. The desktop app is one way to host that; for true 24/7 operation, install the gateway as a system service and the bot keeps working after you close the app or log off.

## What you will use

- a working local agent in the Navin app
- a persistent chat session
- a long-running goal or automation
- Navin left running for background delivery

## When to use this

Use this when the task is not a one-shot answer: project work, recurring checks, scheduled summaries, file maintenance, multi-step research, or quiet watch loops.

## Setup

1. Open Navin and confirm **Settings → Providers** / **Models**.
2. Open the project and chat where work should continue.
3. Leave Navin running while you need goals or schedules to continue.

## Minimal working example

From chat, start a sustained goal:

```text
/goal Review this project, identify missing tests, and propose the smallest next fix.
```

For scheduled or recurring runs, ask from the same chat so Navin links the automation to the correct session and project. Details: [`../automations.md`](../automations.md).

For quiet checks that should only notify when something matters, edit `HEARTBEAT.md` in the project and keep the gateway running.

## Run 24/7 without keeping the app open

The gateway is the process that hosts channels, automations, heartbeat, and missions. It does not need the desktop app or a browser tab: install it as a system service and it starts at login and survives the app being closed.

```bash
navin gateway install-service    # systemd user unit (Linux), LaunchAgent (macOS), autostart (Windows)
navin gateway status             # confirm it is running
navin gateway logs --tail 100    # inspect recent activity
navin gateway uninstall-service  # remove the service
```

Notes:

- `navin gateway --background` starts it once without installing anything; `restart` and `stop` manage it.
- On a Linux server, run `loginctl enable-linger $USER` so the systemd user unit also runs when no session is open.
- For guaranteed 24/7, host it on a machine that stays on: a mini-PC, a VPS, or Docker - see [Deploy the gateway](./deploy-navin-gateway.md).
- Once the service runs, hand work over from any connected channel (WebUI, Telegram, email, ...): `/mission <goal>` for durable multi-step missions with checkpoints and resume, `/cruise <task>` for autopilot builds, chat-created cron automations for schedules, and `HEARTBEAT.md` for quiet watch loops.

## Production notes

- Keep the gateway running (service or `--background`) for chat apps, automations, and heartbeat; the desktop app itself can be closed.
- Use stable chat sessions for work that should preserve context.
- Keep goals bounded and explicit about done-ness.
- Review **Automations** in the app before relying on a schedule.

## Security notes

- Treat long-running goals as delegated work with real tool access.
- Restrict projects and shell execution before scheduling unattended tasks.
- Keep chat channel access narrow so unknown users cannot create goals or automations.

## Troubleshooting

- Goal appears stuck: inspect the active chat session and pause/resume from the UI when available.
- Automation does not run: confirm it is linked to a chat, enabled, and that the gateway is running (`navin gateway status`).
- Heartbeat silent: check `HEARTBEAT.md` and heartbeat settings; silence is often intentional when nothing actionable appeared.

## Related docs

- [Automations](../automations.md)
- [Memory](../memory.md)
- [Self-hosted AI agent](./self-hosted-ai-agent.md)
