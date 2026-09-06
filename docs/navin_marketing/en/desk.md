# Marketing desk UI

Studio `#/marketing` is the Marketing Agent OS. It talks to `/api/marketing` (same store as the `marketing` tool). Chat stays optional.

## Stack

| Layer | What you get |
| --- | --- |
| Fluent UI | Buttons, fields, MessageBar, ProgressIndicator, theme |
| framer-motion | Pane transitions |
| three + R3F + drei | `MarketingScene`: heat follows winners / armed, motion follows the live loop. Designed 3D, not wallpaper. Orbit controls. Pauses when reduced-motion or off-screen. |

## Header

| Control | Action | Test id |
| --- | --- | --- |
| Refresh | `snapshot` | - |
| Chat | Toggle the composer | - |
| Start loop | Opens the schedule panel, then `start` | `marketing-start-loop` |
| Pause loop | `stop` | `marketing-pause-loop` |
| Schedule | `schedule` (hours only, does not enable) | `marketing-schedule-loop` |
| Run cycle | `tick` with `force=true` | `marketing-tick-loop` |

Start loop is disabled until the desk is armed. While a request is in flight, controls show `busy`.

If the first snapshot fails, the desk shows the error and **Try again**. While the first load runs, a ProgressIndicator is shown.

When the loop is enabled the desk refreshes every 12 seconds.

## Panes

| Pane | What you do |
| --- | --- |
| Overview | KPIs, loop status, harvest snapshot (one-liner, headings, CTAs, social links), Use current project, Harvest the live site, Launch product |
| Brand | Company, tone, audience, harvested colors / fonts / logo → `brand` |
| Product | Understand / position / research (market, trends, keywords) |
| Campaigns | Plan 30 days, Approve (upsert, no duplicate campaigns) |
| Content / Social | Write channel variants (upsert per channel + campaign) |
| Studio | **Generate brand kit** (`pack=brand`), **Generate post images** (`pack=posts`), **Generate this** (`creative_id`) |
| Visual QA | Desk creatives + workspace shots (`visual_qa`, reports, human override) |
| SEO | Keywords from harvest / research + **Ingest measured ranking** (never invented) |
| Ads | Spend hint, harvested / produced creative previews, seed `/ads` (never spend without a human click) |
| Analytics | Traffic / leads / signups / revenue + per-content views / clicks / conversions → `metrics` |
| Competitors | Add a named competitor |
| Launch | Build launch kit (Markdown files under `launch/`) |
| Journal | Loop, watch and alert lines. Empty state if the book is new. |

Nav icons are Fluent icons. Copy is i18n (`studio.marketing.*`, `studio.marketingQA.*`) in English and French.

## Planning panel

`TradingLoopSchedulePanel` with Marketing i18n keys:

- kind: daily, weekdays, weekend, weekly, monthly
- hour / minute, weekday, day of month
- timezone = browser IANA zone
- optional **Also run a cycle now** (`run_now`)

## Visual QA

The QA pane is `MarketingQA`. It lists workspace images, runs the gate, and records an audited human override. The machine verdict stays on disk. French copy lives under `studio.marketingQA`.

## What is not this desk

| Route | Role |
| --- | --- |
| `#/ads` | Live paid-media MCP |
| `#/seo` | Full SEO studio |
| `#/montage` | Live demo + social video exports |

Those studios do not replace the Marketing book. Ads spend and Montage exports stay propose-or-human-click.
