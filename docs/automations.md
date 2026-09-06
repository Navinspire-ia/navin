# Automations

<!-- Meta description: Create, run, and manage Navin scheduled automations and heartbeat-backed background checks from the desktop app. -->

Automations are agent turns that run later in a linked chat. Use them when Navin should do work without you actively typing: reminders, recurring checks, nightly summaries, or quiet workspace watch loops.

Create automations from the chat session where the result should appear. That keeps the right history, project, and reply target.

Everything below happens inside the **Navin desktop app** (Windows `.exe`, macOS `.dmg`, or Linux AppImage/deb/rpm/pacman). Keep Navin open for scheduled work to run.

## Choose an automation type

| Type | Starts from | Best for | How to create |
|---|---|---|---|
| Scheduled automation | Time, interval, or cron-style schedule | Recurring reminders, scheduled summaries, one-time future tasks | Ask Navin in the target chat to schedule it |
| Heartbeat | Protected system schedule | Quiet recurring checks that should only report useful results | Edit `HEARTBEAT.md` in the project |
| Advanced wake-up (optional) | Something outside Navin | Rare integrations that need to wake a specific chat later | Ask in chat with `/trigger`, or contact support for advanced integrations |

Scheduled automations and heartbeat cover most personal and project use. Heartbeat is system-managed and protected from normal automation edits.

## Before you create one

1. Open Navin and leave it running while you need background delivery.
2. Open the chat (or channel thread) where results should appear.
3. Confirm a provider and model under **Settings → Providers** and **Settings → Models**.

An automation without a linked chat cannot be enabled or run from the Automations view, because Navin would not know where to deliver the turn.

## Scheduled automations

Ask Navin from the target chat:

```text
Every weekday at 9am, check open pull requests and summarize blockers here.
```

or:

```text
Tomorrow at 4pm, remind me to send the release notes.
```

Schedules can be intervals, calendar-style expressions, or one-time future times. You can include a timezone such as `America/Vancouver`; otherwise Navin uses the runtime default.

Scheduled automations normally deliver the result back to the session where they were created. Use them when every run should produce a visible reminder or report.

For background checks that should stay quiet unless something useful appears, use heartbeat instead.

## Heartbeat

Heartbeat is for recurring project checks that should usually stay quiet. It reads `HEARTBEAT.md` in the active project, runs the listed tasks, and sends only useful or actionable results to the most recently active chat target.

Use heartbeat for checks such as "watch this project for important failures" or "periodically inspect this workspace and only tell me when action is needed."

Heartbeat is enabled by default while Navin is running. Timing options live under agent / gateway heartbeat settings in **Settings** (see also the advanced notes in [`configuration.md`](./configuration.md)).

Career and Trading desks have their own loop (calendar hunt/cycle) plus a silent heartbeat `watch`. That is not a chat cron. Contract: [desk loop](./studio/desk-loop.md).

## Advanced wake-up (`/trigger`)

Some workflows need an outside event to wake a specific chat later. In the target chat, you can create a named wake-up with the composer action:

```text
/trigger PR review
```

Navin links that name to the current session. Advanced integrations outside the app can wake that session later. Everyday users can skip this path. If you need a custom integration, ask support or check later advanced docs - this page does not require terminal recipes.

## Manage automations

Open the **Automations** view in the app to:

- filter by all, active, paused, needs-attention, or system jobs;
- search by task name, message, linked chat, schedule, or status;
- sort by next run, last run, updated time, or name;
- run scheduled automations now;
- pause or resume, rename, or delete user-created automations;
- inspect protected system automations without changing them.

## Delivery and reliability

Automation delivery is local to your machine and project. Scheduled jobs use the same project as the chat where they were created.

Keep Navin open (or restarted) for due work to fire. If a linked session is already mid-turn, a wake-up waits until the session is idle instead of interrupting the active turn.

## Common patterns

Nightly report - ask from the target chat:

```text
Every night at 9pm, review today's project changes and summarize anything I should handle tomorrow.
```

Quiet watch - add a short task list to `HEARTBEAT.md` in the project, then leave Navin running.

One-time reminder:

```text
Tomorrow at 4pm, remind me to send the release notes.
```

## Troubleshooting

- Automation does not run: confirm Navin is open, the automation is enabled, and it was created from a linked chat.
- No delivery target: recreate the automation from the chat where you want replies.
- Heartbeat too noisy or too quiet: edit `HEARTBEAT.md` and adjust heartbeat interval in Settings.
- To edit, pause, resume, rename, delete, or inspect automations, use the Automations view.

## Related docs

- [`guides/long-running-ai-agent.md`](./guides/long-running-ai-agent.md) - leave Navin running for sustained work
- [`configuration.md`](./configuration.md) - Settings for providers, models, and heartbeat
- [`memory.md`](./memory.md) - Dream and durable project memory
- [`studio/desk-loop.md`](./studio/desk-loop.md) - Career and Trading: two clocks, never a chat cron
