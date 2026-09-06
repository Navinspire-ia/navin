# Marketing Agent OS - Overview

The **Marketing** module (sidebar → **Marketing**, route `#/marketing`) is a **local marketing desk**: harvest a live product site (or scan the bound project), lock Brand Memory, plan a campaign, write channel variants, produce brand and post images (video/audio when a provider is set), run visual QA, ingest metrics and measured SEO rankings, then let a wall-clock loop measure and improve winners.

The live book is Studio `#/marketing` and the `marketing` tool (same store as Tauri on Linux / Windows / macOS, `navin marketing`, and `python -m navin.marketing.desk_cli`). Do not invent traffic, CTR, spend, or a published post that is not in that store.

For **live paid-media accounts** (Google / Meta / TikTok / Reddit Ads MCP), use the dedicated **Ads** studio (`#/ads`). For live product demos and social video exports, use **Montage** (`#/montage`).

## How it works

1. Open **Marketing** in the sidebar (`#/marketing`).
2. If the product already has a URL, **Harvest the live site**. The desk pulls title, one-liner, headings, CTAs, colors, fonts, logo, OG image and social links, then fills brand, SEO, content and ads. Otherwise **Use current project** (workspace scan + Brand Memory).
3. **Approve** the campaign you want to run.
4. In Studio, **Generate brand kit** / **Generate post images** (`produce` with `pack=brand` or `pack=posts`). Generate one card with `creative_id`. Video and speech stay skipped until a provider is configured.
5. **Start loop** (daily / weekdays / weekend / week / month + hour) or **Launch product** (Markdown files under `launch/`). Pause or change the hours anytime. The gateway measures then improves on that calendar while Navin is up.
6. Heartbeat only reports winners (and competitor changes). It never publishes, never spends, never starts the loop.

```
/marketing
/marketing action=status
/campaign persist this launch on the marketing desk then brief LinkedIn + X
```

Do not create a chat cron that ticks or reviews KPIs. The desk loop is the autonomous cycle.

## Happy path

| Step | UI | Store action |
| --- | --- | --- |
| Harvest | Harvest the live site | `harvest` then `fill_from_site` (SEO, content, social, ads) |
| Understand | Use current project | `pipeline` (understand → position → research → plan → content → creative → launch kit) |
| Approve | Approve on Campaigns | `approve` |
| Produce | Generate brand kit / post images | `produce` (`pack` or `creative_id`) |
| Recurring | Start loop | `start` + saved schedule |
| One cycle | Run cycle | `tick` with `force=true` |
| Ship kit | Launch product | `launch` (Markdown files on disk) |

Armed means a product name or a brand company/product is set. Start loop stays disabled until the desk is armed.

## The `/marketing` command

| | |
| --- | --- |
| Command | `/marketing [launch\|pipeline\|loop]` |
| Lifecycle | Agent workflow (docs model route) |
| Skills preloaded | `marketing-strategist`, `growth-marketing`, `digital-marketing`, `email-marketing`, `marketing-analytics` (+ campaign / montage skills when `/campaign` or `/montage` is used) |
| Tool | `marketing` (same book as the Studio desk) |
| Output | `~/.navin/marketing/` JSON book + optional Track A `marketing-report-*` UI |

`/campaign` still produces chat deliverables (copy, visuals, reports). Persist brand, campaign, content and creatives with `marketing action=plan` / `content` / `creative` so the loop can measure them.

## Same book, four doors

| Door | How |
| --- | --- |
| Studio | `#/marketing` → HTTP `/api/marketing?action=` |
| Agent | `marketing` tool |
| CLI | `navin marketing` or `python -m navin.marketing.desk_cli` |
| Gateway | Supervisor `navin-marketing-loop` + heartbeat `watch` |

## What the machine does (and does not)

| Does | Does not |
| --- | --- |
| Harvest a live URL or understand the bound project | Invent live traffic, rankings or published posts |
| Write positioning, research, campaigns, channel copy | Auto-publish to LinkedIn, X, Meta, or email |
| Produce brand / post images (video/audio when a provider is set) | Spend ad budget |
| Score winners from ingested metrics (`by_content`) and clone variants | Invent SEO positions (only `ingest_ranking`) |
| Alert (WebUI + optional Telegram / email / WhatsApp) once per winner | Hunt the public web on every heartbeat |
| Recover a stuck cycle, skip overlap, retry with backoff | Block the gateway if a cycle hangs |

Harvested images and produced assets live under `~/.navin/marketing/assets/` and are served by `/api/marketing/file`. Visual QA lists **desk creatives** plus workspace shots. Montage still owns live demo exports (`#/montage`).

## Related

- [Desk UI](./desk.md)
- [Growth loop](./loop.md)
- [Heartbeat](./heartbeat.md)
- [Desktop (Tauri)](./desktop.md)
- [Actions](./actions.md)
- [AI layer](./ai.md)
- Desk loop contract: [desk-loop](../../studio/desk-loop.md)
- Ads: [navin_ads](../../navin_ads/en/README.md)
- Montage: [navin_montage](../../navin_montage/en/README.md)
