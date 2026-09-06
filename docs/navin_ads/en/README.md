# Ads module - Overview

The **Ads** module (sidebar → **Other modules → Ads**, route `#/ads`) is a **senior paid-media desk**: Google Ads, Meta Ads, TikTok Ads, Reddit Ads, and LinkedIn Ads. Prefer live MCP reads over invented metrics. Creative production (videos, social visuals, brand kits) stays in the **Marketing** studio (`#/marketing`).

## How it works

1. Open **Ads** under Other modules.
2. Click a card for Google / Meta / TikTok / Reddit / LinkedIn (overview, structure, or optimization) - see [Actions](./actions.md) and [Skills](./skills.md).
3. The chat opens with `/ads` and the card prompt. Fill account / KPI, then send.
4. The agent uses connected MCP tools when available, saves under `ads/` + `ads-report-*.html`.

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

## Connect platform MCPs

Open **Settings → MCP**, then install the presets you need (category **ads**):

### Google Ads (`google-ads`)

- `pipx` + official google-ads-mcp
- Env: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` (optional MCC: `GOOGLE_ADS_LOGIN_CUSTOMER_ID`)
- Docs: https://github.com/googleads/google-ads-mcp

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

- Connect MCP before asking for live metrics.
- Prefer read-only; never approve spend mutations casually.
- Chain with Marketing for creatives, then Ads for structure and optimization.
