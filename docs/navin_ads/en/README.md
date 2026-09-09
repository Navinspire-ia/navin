# Ads module - Overview

The **Ads** module (sidebar → **Other modules → Ads**, route `#/ads`) is a **senior paid-media desk**: Google Ads, Microsoft Ads, Meta Ads, TikTok Ads, Reddit Ads, and LinkedIn Ads. Every number comes from the built-in `ads` engine (your Ads Manager exports or MCP report rows), never from the model. Creative production (videos, social visuals, brand kits) stays in the **Marketing** studio (`#/marketing`).

## How it works

1. Open **Ads** under Other modules.
2. Click a card: **Audit from exports** (attach your CSV/XLSX), **Wasted spend & negatives**, **Approve & apply changes**, or a platform card for Google / Microsoft / Meta / TikTok / Reddit / LinkedIn (overview, structure, or optimization) - see [Actions](./actions.md) and [Skills](./skills.md).
3. The chat opens with `/ads` and the card prompt. Fill account / KPI, then send.
4. The agent runs the `ads` engine (and connected MCP tools when available), saves under `ads/` + `ads-report-*.html`, and queues changes in `ads/changes.jsonl` until you approve them.

Direct usage:

```
/ads Google Ads overview for customer 1234567890
/ads Meta weekly optimization - ad account act_…
```

## The `/ads` command

| | |
| --- | --- |
| Command | `/ads [platform\|account\|brief]` |
| Lifecycle | Agent workflow |
| Skills preloaded | `studio-expert-contract`, `critic-reviewer`, `paid-ads-manager`, `marketing-analytics`, `conversion-rate-optimization`, `copywriting-agent`, `ad-creative-generator` |
| Board | Tracked run |
| Output | Files under `ads/` + `ads-report-*.html` + expert gate |

## The `ads` engine (real analysis)

The `ads` tool is deterministic and only available in this module:

| Action | What it does |
| --- | --- |
| `ingest` | Reads Google, Microsoft, Meta, LinkedIn, TikTok or Reddit exports (CSV / TSV / XLSX, UTF-16 Google downloads, French number formats, report preambles) and MCP JSON rows; detects the platform and maps the columns. |
| `analyze` / `pipeline` | CTR, CPC, CPM, CPA, CVR, ROAS and spend share per campaign, ad group, ad, keyword and search term; period and monthly projection. |
| findings | `zero_conversion_spend`, `high_cpa`, `search_term_waste`, `low_ctr`, `low_quality_score`, `creative_fatigue`, `budget_pacing` (with `monthly_budget`), `impression_share_limited`, `spend_concentration`, `scale_winner`, `tracking_missing`. Each one carries the exported numbers as evidence. |
| `score` | Health score 0-100: severity penalties plus one point per percent of spend without conversions. |
| `report` | Deterministic JSON / Markdown / HTML report (KPIs, campaigns, findings, proposed changes, data gaps). |
| `changes` | Approval queue: list, `status=approved`, `rejected`, `applied`. Approved changes come with an MCP execution plan (operation + read-back check). |
| `export_changes` | `csv` (all actions), `google_editor` (negatives + pauses for Google Ads Editor), `microsoft_bulk` (Microsoft Advertising bulk sheet). |

Rules the desk follows: numbers only from exports or MCP rows; missing columns are reported as `data_gaps`; nothing is paused, re-budgeted or negated without an approved change id. Thresholds live under `tools.ads.thresholds` in the config.

## Connect platform MCPs

Open **Settings → MCP**, then install the presets you need (category **ads**):

### Google Ads (`google-ads`)

- `pipx` + official google-ads-mcp
- Env: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` (optional MCC: `GOOGLE_ADS_LOGIN_CUSTOMER_ID`)
- Docs: https://github.com/googleads/google-ads-mcp

### Microsoft Ads (`microsoft-ads`)

- `npx -y @cesteral/msads-mcp` (Microsoft Advertising API v13)
- Env: `MSADS_ACCESS_TOKEN`, `MSADS_DEVELOPER_TOKEN`, `MSADS_CUSTOMER_ID`, `MSADS_ACCOUNT_ID`
- Docs: https://www.npmjs.com/package/@cesteral/msads-mcp

### Meta Ads (`meta-ads`)

- Remote hosted MCP: `https://mcp.facebook.com/ads`
- Field: `Authorization` header as `Bearer <user access token>` (scopes include ads_read / ads_management)
- Docs: [Meta Ads MCP get started](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-mcp-server/ads-mcp-server-get-started)
- Note: some accounts are still on Meta's phased MCP rollout.

### TikTok Ads (`tiktok-ads`)

- Community PyPI server via `uvx tiktok-ads-mcp` (official [TikTok Agentic Hub / MCP](https://ads.tiktok.com/help/article/about-tiktok-for-business-agentic-hub-and-mcp-server?lang=en) is announced; public paste-URL not self-serve yet)
- Env: `TIKTOK_APP_ID`, `TIKTOK_SECRET`, `TIKTOK_ACCESS_TOKEN`

### Reddit Ads (`reddit-ads`)

- `npx -y mcp-server-reddit-ads`
- Env: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN` (default `REDDIT_ADS_WRITE_TIER=read`)
- Docs: https://github.com/camlowe/mcp-server-reddit-ads

### LinkedIn Ads (`linkedin-ads`)

- Preset `linkedin-ads` (`npx -y @cesteral/linkedin-mcp`) + Marketing API token
- Env: `LINKEDIN_ACCESS_TOKEN` (optional `LINKEDIN_API_VERSION`)
- Docs: https://www.npmjs.com/package/@cesteral/linkedin-mcp

Cron/loop jobs reuse the same gateway env when configured.

## Tips

- Start with **Audit from exports** when you have no MCP: the engine reads what Ads Manager exports.
- Connect MCP before asking for live metrics.
- Prefer read-only; never approve spend mutations casually.
- Chain with Marketing for creatives, then Ads for structure and optimization.
