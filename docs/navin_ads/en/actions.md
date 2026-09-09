# Ads actions - 21 cards

Each card sends `/ads` with a brief. The three engine cards work from your Ads Manager exports; the platform cards use the matching MCP under **Settings → MCP** (or exports as fallback). All numbers go through the `ads` engine.

## Analysis engine (exports & MCP data)

| Action | Delivers |
| --- | --- |
| Audit from exports | `ads` action=pipeline on attached CSV/XLSX (campaign + search terms reports): KPIs, wasted spend, high CPA, low CTR, fatigue, pacing, health score, `ads/ads-report.html`, proposed changes in `ads/changes.jsonl`. |
| Wasted spend & negatives | Search terms and keywords that spend without converting, monthly projection, exact-match negative proposals, `google_editor` / `microsoft_bulk` export once approved. |
| Approve & apply changes | Change queue review: approve / reject by id, bulk export or MCP execution plan with read-back verification, `applied` status. |

## Google Ads

| Action | Delivers |
| --- | --- |
| Account overview | Accessible customers, live campaign/ad-group map, last-7-day metrics via MCP `google-ads` (or export fallback). Saved under `ads/google/` + `ads-report-*.html`. |
| Campaign structure | Search/PMax structure from live reads: campaign → ad group → keywords/themes, negatives, budgets, tracking checklist. |
| Weekly optimization | Kill/scale report from recent metrics: winners, losers, search-term negatives, budget shifts - no mutations without approval. |

## Microsoft Ads

| Action | Delivers |
| --- | --- |
| Account overview | Accounts, campaigns/ad groups and a 7-day report via MCP `microsoft-ads` (or exports through the engine). Saved under `ads/microsoft/` + `ads-report-*.html`. |
| Campaign structure | Search structure (import from Google or native), negatives, budgets, UET tracking checklist; new entities stay paused. |
| Weekly optimization | Engine findings on MCP reports or exports: wasted spend, negatives, quality score, budget shifts; approved changes via `microsoft_bulk` export or MCP write tools. |

## Meta Ads

| Action | Delivers |
| --- | --- |
| Account overview | Ad accounts, campaigns/ad sets, recent performance via MCP `meta-ads` (`https://mcp.facebook.com/ads`). |
| Campaign structure | Audience → ad set → creative plan, placements, pixel/CAPI checklist; new entities stay paused unless you ask to activate. |
| Creative & budget loop | Fatigue, high-CPA sets, and budget reallocation candidates with evidence only. |

## TikTok Ads

| Action | Delivers |
| --- | --- |
| Advertiser overview | Authorized advertisers, campaigns, and reports via MCP `tiktok-ads` (community server until official hosted MCP is self-serve). |
| Campaign structure | Campaign → ad group → ads for short-form, Spark/In-Feed notes, pixel/events checklist. |
| Performance loop | CPA/CTR cut-and-scale and creative rotation recommendations. |

## Reddit Ads

| Action | Delivers |
| --- | --- |
| Account overview | Accounts, campaigns, ad groups, last-7-day performance via MCP `reddit-ads` (read tier by default). |
| Community targeting plan | Subreddit/interest structure using MCP search tools when available. |
| Weekly optimization | Pause/scale and creative swap recommendations; write tier stays read unless you explicitly raise it. |

## LinkedIn Ads

| Action | Delivers |
| --- | --- |
| Account overview | Ad accounts, campaign groups, last-7-day metrics via MCP `linkedin-ads` or Campaign Manager export. Saved under `ads/linkedin/` + `ads-report-*.html`. |
| Title / industry structure | Campaign group → campaign → creative with tight B2B ICP (title, industry, seniority). New entities stay paused unless you ask to activate. |
| Weekly B2B optimization | Kill/scale from CPL and audience demographics; read-only first, no spend mutations without approval. |
