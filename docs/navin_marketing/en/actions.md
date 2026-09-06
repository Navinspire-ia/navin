# Marketing actions - 17 cards

Studio `#/marketing` has two surfaces and one book. Prefer the desk tool for anything the loop must measure.

## Desk tool / HTTP / CLI

`POST /api/marketing?action=` or `marketing` tool or `navin marketing <action>`.

Heartbeat may only run the first three.

| Action | Role |
| --- | --- |
| `status` / `snapshot` | Full desk: brand, product, campaigns, content, creatives, analytics, loop (`peek_loop`), journal, KPIs, armed |
| `watch` | Silent winner / competitor pass. Deduped. Optional notify |
| `brand` | Brand Memory (company, tone, audience, colors, ...) |
| `settings` | `execution_mode`, `auto_publish` (stored, never auto-publishes), `winner_multiple`, optional notify channels |
| `understand` / `product` | Scan workspace or accept a product brief. A live `site` URL still harvests. |
| `harvest` | Fetch a live URL, parse title / one-liner / headings / CTAs / colors / fonts / images / social links, apply to brand + creatives, then `fill_from_site` |
| `position` | Positioning + ICP from the product |
| `research` | Competitive map. Accepts injected `hits`. Does not invent live traffic |
| `competitor` | Upsert a named competitor |
| `plan` / `campaign` | 30-day (or `days`) campaign for a signup target. Upserts a matching draft / planned row |
| `approve` | Approve campaign `id`, then fill content + creatives |
| `content` | Channel variants (harvest headings / CTAs + brand). Upserts per channel + campaign. Optional `hook` |
| `creative` | Image / video / audio / banner briefs (`status=brief`) |
| `produce` / `generate` | Write real assets. Optional `pack=brand` / `pack=posts` or `creative_id`. Skips video / speech without a provider |
| `seo` | Build keywords / pages from harvest + research. Optional measured ranking (`keyword`, `url`, `position`) via `ingest_ranking` |
| `vision` | Human PASS / WARN / BLOCK on a creative `id` |
| `metrics` / `analytics` | Ingest traffic, leads, signups, revenue, `by_content` |
| `improve` | Scoreboard, winners, variants |
| `launch` | Launch kit: JSON plus Markdown files under `launch/` |
| `pipeline` | Understand → position → research → plan → content → creative → launch |
| `start` | Arm the growth loop. Optional `schedule`, `run_now`, `tz` |
| `stop` | Pause |
| `schedule` | Change hours |
| `tick` | One cycle (`force` default true on HTTP) |

Unknown actions return `MarketingError`. Invalid `days` / `score` return 400.

## Chat cards (`/campaign`)

Each card still seeds `/campaign` with a precise spec. Persist the result with `marketing action=plan` / `content` / `creative` when the loop should own it.

### Creative

| Action | Delivers |
| --- | --- |
| Product images | Studio packshot, lifestyle, social variants via image tools. Visual QA after generation. |
| Ad video | 15-30s script, storyboard, voiceover, generated video when a provider is set. |
| Open Montage studio | Bridge to `#/montage`. Never auto-publish. |
| Social media visuals | Square / vertical / landscape set. |
| Brand kit | Logo directions, palette, type, voice. |

### Design

| Action | Delivers |
| --- | --- |
| Poster / key visual | 4:5 hero plus 9:16 and 1:1 crops, gated by `visual_qa`. |
| Display banner pack | Master declined into six ad-network sizes. |
| 3D product page | Interactive orbit page with a still fallback. |
| Carousel design | 5-7 slide 1080x1350 narrative. |

### Content

| Action | Delivers |
| --- | --- |
| Social posts | LinkedIn, X, Instagram, TikTok script: hooks, hashtags, CTA. |
| Blog article | Outline, body, meta, promo snippets. |
| Email sequence | 5 emails with A/B subjects. |
| Landing page copy | Hero, benefits, proof, FAQ, CTA. Super render uses `ui-ux-pro-max` (designed 3D, not wallpaper). |

### Strategy

| Action | Delivers |
| --- | --- |
| 360 campaign | Message house, channel plan, then every deliverable. |
| Customer persona | ICP with sourced claims. |
| 30-day content plan | Weekly themes mapped to funnel stages. |
| Competitor analysis | Public pages only. Store names with `marketing action=competitor`. |

Never invent ROAS / CPC. Label estimates. Paid-account reads go to `#/ads`.
