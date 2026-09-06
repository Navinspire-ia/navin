# Marketing AI layer

Three loops on Studio `#/marketing`. Do not mix them.

| Loop | Who runs it | Allowed mutations |
| --- | --- | --- |
| Heartbeat | Gateway then a short LLM turn | `status` / `snapshot` / `watch` only |
| Desk loop | Supervisor `navin-marketing-loop` + Studio Start / Pause / Cycle | `maybe_tick`, no chat required |
| Chat agent | `/marketing`, `/campaign`, `/montage` + `marketing` tool | Full desk writes, except on a heartbeat turn |

## Tool `marketing`

Same book as Studio. Call `action=status` before any claim.

Read-only: `status`, `snapshot`, `watch`.

Writes: `brand`, `settings`, `understand`, `position`, `research`, `competitor`, `plan`, `approve`, `content`, `creative`, `vision`, `metrics`, `improve`, `launch`, `pipeline`, `start`, `stop`, `schedule`, `tick`.

On heartbeat, writes return `ToolResult.error`. The HTTP API refuses the same set.

`tick` defaults `force=true` when the tool is called without `force`. Prefer the saved schedule for recurring work.

## Slash commands

| Command | Model route | Must persist on the desk |
| --- | --- | --- |
| `/marketing` | `docs` | Yes. Status first. Start / stop / schedule for the loop. No chat cron. |
| `/campaign` | `docs` | Yes. `action=plan` then `content` and `creative`. Copy and images may also land under `marketing/` in the project. |
| `/montage` | `docs` | Assets under `marketing/montage/`. Loop seed must call `marketing action=start`, not a Monday chat cron. |

Palette line for `/marketing`: understand, plan, create, measure, improve. Not publish.

`studio-expert-contract` applies to `/marketing`, `/campaign`, `/seo`, and `/leads`.

## Skills

Preloaded on `product_module=marketing`:

- `marketing-strategist` (wired to the `marketing` tool and the desk loop)
- `growth-marketing` (experiments on the desk, never a chat cron)
- `digital-marketing`
- `email-marketing`
- `marketing-analytics` (prefer `marketing action=metrics` for the book)

`/campaign` and `/montage` add campaign-manager, ad-creative-generator, product-visuals, montage-studio, and the expert contract.

## Routing

| Key | Value |
| --- | --- |
| `STUDIO_OWNED_TOOLS["marketing"]` | `marketing` |
| View `#/montage` | product module `marketing` (owns `/campaign` + `/montage`) |
| Heartbeat denied tools | `scrape`, `browser`, `cron`, `web_search`, `trading` (marketing writes still gated by the tool) |
| Sandbox | `~/.navin/marketing` is writable |

## Session loop seed

`createSeed.marketing` and `createSeed.montage` tell the agent to start the **desk** loop (`marketing action=start`). They must not create a chat cron that ticks KPIs or re-records demos every Monday.

## Honesty rules for the model

- Never invent traffic, CTR, ROAS, or a published post.
- Never mark an email verified without API proof (Ads / Leads connectors, not this desk).
- Never spend ad budget from Marketing. Hand paid accounts to `#/ads`.
- Images and video: call `generate_image` / `generate_video` / `montage`, then Visual QA.
- No em dash characters in copy, reports, or UI.

See [Skills](./skills.md) and [Actions](./actions.md).
