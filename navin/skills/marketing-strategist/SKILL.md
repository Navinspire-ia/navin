---
name: marketing-strategist
description: Build positioning, ICP, offer, channel mix, and an execution plan. Use when marketing lacks direction or before investing in campaigns.
metadata: {"navin":{"emoji":"🧭","category":"marketing","default_for":"marketing"}}
---

# Marketing Strategist

Strategy answers: who we serve, why they choose us, where we reach them. Everything else is execution.

The Marketing module (`product_module=marketing`) uses Settings → Models → Task routing (`docs`), with or without the `/marketing` slash. Switch with `/pilot <task>` when the turn needs Planning, Medium, or Complex.

The live book is Studio `#/marketing` and the `marketing` tool. Do not invent traffic, CTR, spend, or a published post that is not in that store. Do not call `trading`, `career`, `tenders` or `leads` from this module.

## Wiring

- Tool: `marketing` (same store as Studio `#/marketing`, Tauri, HTTP `/api/marketing`, `navin marketing`, and `python -m navin.marketing.desk_cli`).
- Skills preloaded: this file plus `growth-marketing`, `digital-marketing`, `email-marketing`, `marketing-analytics`.
- Desk loop: Studio Start loop measures then improves (`marketing action=start` / `schedule` / `tick`) on the saved wall-clock calendar while the gateway is up. That is the autonomous growth loop. Never publish. Never spend ad budget.
- Heartbeat: the gateway already ran `marketing action=watch`. Silent when `watch.count` is 0. Never understand, publish, start, schedule or tick from heartbeat.
- Sandbox: `~/.navin/marketing` is writable. Never write config.json or the machine key.

## Strategy framework

1. **ICP** - industry, size, geography, role, trigger events (use `customer-persona-builder`)
2. **Positioning** - for [ICP] who [pain], [product] is the [category] that [key differentiator], unlike [alternative]
3. **Offer** - what exactly is sold, packaging, pricing logic, risk reversal
4. **Channels** - pick 2-3 where the ICP already is; justify each
5. **Messages** - 3 core messages mapped to the top 3 pains
6. **Plan** - 90-day roadmap with owners, budget, KPIs

## Workflow

1. `marketing action=status` before any claim. That call returns the local book: brand, product, positioning, campaigns, content, creatives, analytics, loop. Treat it as the only source of truth.
2. If Brand Memory is empty, write it with `marketing action=brand` (company, tone, audience). Do not invent a logo or forbidden words the user did not give.
3. Understand the bound project with `marketing action=understand` or `action=pipeline` (workspace = the project root). Then `position`, `research`, `plan`, `content`, `creative`, `launch`.
4. Research the market with `market-research` and `competitor-intelligence` findings, then store competitors with `marketing action=competitor` when the user names them.
5. Approve a campaign with `marketing action=approve` after the user confirms. Then write variants and creative briefs.
6. Recurring: start the desk loop with `marketing action=start` and a JSON schedule (kind/hour/minute). Pause with `stop`. Change hours with `schedule`. Do not create a chat cron that ticks or reviews KPIs.
7. Recurring silence: heartbeat already ran `marketing action=watch`. Report only when count > 0 or the prompt includes a Marketing digest.

## Deliverable

One-page strategy doc: ICP, positioning statement, offer, channels, messages, 90-day plan, KPIs. Persist facts on the desk; do not leave them only in chat.

## Rules

- Positioning must be falsifiable - if the opposite is absurd, it is not positioning.
- No plan without a named owner and a date per action.
- Never invent traffic, CTR or published posts.
