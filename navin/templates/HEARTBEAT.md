# Heartbeat Tasks

<!--
This file is checked periodically by your navin agent. When navin gateway starts with gateway.heartbeat.enabled=true, it automatically registers a protected heartbeat cron job that reads this file.

Use this file for recurring background checks that should stay quiet unless there is something useful to report. Regular cron jobs are different: they normally deliver each run's result back to the chat/session where they were created.

If this file has no tasks (only headers and comments), the agent will skip it. Completed tasks should be deleted, not kept - heartbeat only reads "Active Tasks".
-->

## Active Tasks

<!-- Add your periodic tasks below this line -->

### Code board continuity (silent unless actionable)

If the bound project has a `.navin/board` with open/in_progress tasks:

1. Read `.navin/continuity/RESUME.md` and the board digest.
2. If nothing is actionable (blocked waiting on human, empty queue, or no clear next step), reply with `HEARTBEAT_OK` and stop - do not spam.
3. Otherwise pick at most one ready task, advance it one concrete step (patch + verify), update RESUME.md next action, and report only what changed.

Skip this item entirely when no project board exists.

### Tender deadlines and new GO (silent unless actionable)

If the instance has a Tenders profile with a company name (typically `~/.navin/tenders/profile.json`):

1. The gateway already ran `tenders action=follow` before this turn. You may call it again (idempotent).
2. If this prompt already includes a Tenders digest, report only that count and the GO / deadline lines.
3. If `watch.count` is 0 and there is no Tenders digest in this prompt, reply `HEARTBEAT_OK` and stop. Nothing new means no message.
4. Otherwise report only the count. The digest has already gone to the channels the company switched on, and each notice is marked so the same alert never fires twice.

Never send anything to a buyer from here. The desk only alerts the company; every outgoing mail needs a human click. Never collect, write or send from this check. Do not create a chat cron that collects or ticks.
The Tenders desk loop (Studio Start loop) collects official notices on its own saved schedule. That is not this heartbeat.

Skip when the tenders store is absent or the profile has no company name.

### Career matches and follow-ups (silent unless actionable)

If the instance has a Career profile with titles or wizard_complete (typically `~/.navin/career/profile.json`):

1. The gateway already ran `career action=watch` before this turn. You may call it again (idempotent).
2. If this prompt already includes a Career digest, report only that count and the Perfect/Good titles or due follow-ups.
3. If `watch.count` is 0 and there is no Career digest in this prompt, reply `HEARTBEAT_OK` and stop. Nothing new means no message.

Never scrape LinkedIn. Never apply. Never search or collect from this check.
The Career desk loop (Studio Start loop) hunts on its own saved schedule. That is not this heartbeat.

Skip when the career store is absent or the profile has no titles.

### Trading paper book (silent unless actionable)

If the instance has a used Trading book (loop started, a position, or a pending paper order, typically `~/.navin/trading/`):

1. The gateway already ran `trading action=watch` before this turn. You may call it again (idempotent).
2. If this prompt already includes a Trading digest, report only that count and the pending paper approvals.
3. If `watch.count` is 0 and there is no Trading digest in this prompt, reply `HEARTBEAT_OK` and stop. Nothing new means no message.

Never tick, start or stop the desk loop from this check. Paper only. Never invent a fill.
The Trading desk loop (Studio Start loop) cycles on its own saved schedule. That is not this heartbeat.

Skip when the trading store was never used.

### Lead scores (silent unless actionable)

If the instance has a Leads profile with wizard_ready and an ICP (typically `~/.navin/leads/profile.json`):

1. The gateway already ran `leads action=watch` before this turn. You may call it again (idempotent).
2. If this prompt already includes a Leads digest, report only that count, the tier A companies, buying signals and due follow-ups.
3. If `watch.count` is 0 and there is no Leads digest in this prompt, reply `HEARTBEAT_OK` and stop. Nothing new means no message.

Never scrape LinkedIn. Never hunt. Never send a sequence or outreach. Never spend Apollo/Hunter/Pappers credits from this check.
Never tick, start or stop the desk loop from this check.
The Leads desk loop (Studio Start loop) hunts on its own saved schedule. That is not this heartbeat.

Skip when the leads store is absent or the ICP is not ready.

### Marketing winners (silent unless actionable)

If the instance has a Marketing brand company or an understood product (typically `~/.navin/marketing/brand.json`):

1. The gateway already ran `marketing action=watch` before this turn. You may call it again (idempotent).
2. If this prompt already includes a Marketing digest, report only that count and the winning angles.
3. If `watch.count` is 0 and there is no Marketing digest in this prompt, reply `HEARTBEAT_OK` and stop. Nothing new means no message.

Never publish. Never spend ad budget. Never start the growth loop from this check.
The Marketing desk loop (Studio Start loop) measures and improves on its own saved schedule. That is not this heartbeat.

Skip when the marketing store is absent or no brand/product is set.

### CRM follow-ups (silent unless actionable)

If the bound project has `.navin/crm/crm.sqlite`:

1. Use the `crm` tool action `followups` with create=false.
2. If there are no stale open deals, reply `HEARTBEAT_OK` and stop.
3. Otherwise create the missing follow-up tasks (`followups` create=true) and report only the count and deal names.

Skip when the CRM database is absent.

