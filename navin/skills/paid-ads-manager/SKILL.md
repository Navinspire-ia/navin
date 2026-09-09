---
name: paid-ads-manager
description: Plan and operate Google Ads, Microsoft Ads, Meta Ads, TikTok Ads, Reddit Ads, and LinkedIn Ads - real analysis of exports / MCP rows with the `ads` engine, targeting, creatives, budgets, and approval-gated optimization loops. Use when paid acquisition is on the table.
metadata: {"navin":{"emoji":"💰","category":"ads"}}
---

# Paid Ads Manager

## Overview

Paid ads amplify a working message - they don't fix a broken offer. Structure > hacks.
Prefer the **Ads studio** (`#/ads`, slash `/ads`) and Settings → MCP presets over inventing account data.

## Platform picker

| Platform | MCP preset | Best for | Minimum viable budget |
|----------|------------|----------|----------------------|
| Google Search / PMax | `google-ads` | existing demand | modest, intent-driven |
| Microsoft Search | `microsoft-ads` | cheaper search demand, Google import | modest, intent-driven |
| Meta (FB/IG) | `meta-ads` (`https://mcp.facebook.com/ads`) | B2C, local, retargeting | low, creative-hungry |
| TikTok | `tiktok-ads` | short-form, creative tests | creative-hungry |
| Reddit | `reddit-ads` | community / interest targeting | tight ICP |
| LinkedIn | `linkedin-ads` | B2B title/industry | high CPC - tight ICP only |

## Campaign structure

1. **Offer + landing page first** - validate with `conversion-rate-optimization`
2. Campaign → ad set/group per audience → 2-4 ad variants
3. Tracking before launch: conversion events, UTM, thank-you page
4. Budget: 70% proven / 30% test
5. Optimization cadence: no changes for the learning phase, then weekly cuts of losers

## The `ads` engine (numbers come from here)

- `ads action=pipeline paths=<exports>` (CSV/XLSX attached to the chat or under `ads/imports/`; campaign report + search terms report) or `data=<MCP report rows as JSON>`: per campaign / ad group / ad / keyword / search term KPIs, findings with evidence, health score, `ads/ads-report.html`, proposals in `ads/changes.jsonl`.
- Findings: `zero_conversion_spend`, `high_cpa`, `search_term_waste`, `low_ctr`, `low_quality_score`, `creative_fatigue`, `budget_pacing` (pass `monthly_budget`), `impression_share_limited`, `spend_concentration`, `scale_winner`, `tracking_missing`.
- `ads action=changes` lists the queue; `status=approved ids=...` records the user's decision and returns an MCP execution plan; `action=export_changes format=csv|google_editor|microsoft_bulk` writes the editor import file.
- Keep `data_gaps` visible (missing conversions column, undated export). Never type a CPA or ROAS that the engine did not compute.

## Workflow

1. Identify platform(s). If the matching MCP is connected, list accounts and read live campaign/ad-group structure and recent reports via MCP tools, then feed the rows to the `ads` engine before proposing a new structure - do not invent account topology.
2. Define target CPA/CPL from unit economics (`marketing-analytics`).
3. Draft targeting + keywords/audiences + negative lists (or refine against live data when MCP is available).
4. Write ad copy variants (hooks from `copywriting-agent`); design brief for visuals.
5. Launch checklist: pixel/tag firing, budget caps, geo/language settings.
6. Weekly report from the engine findings: spend, CPL, CTR, conversion rate per audience - kill/scale decisions (MCP rows or platform exports through `ads`). Save under `ads/` + `ads-report-*.html`.
7. Changes: propose (engine), let the user approve by id, then export (`google_editor` / `microsoft_bulk` / `csv`) or execute the approved MCP plan and verify each read-back; mark `applied`.

## Rules

- Prefer MCP `google-ads`, `microsoft-ads`, `meta-ads`, `tiktok-ads`, `reddit-ads`, or `linkedin-ads` for live reads when configured; otherwise ask for Ads Manager exports and run the `ads` engine on them.
- Prefer read-only first. Never mutate budgets, bids, or status without an approved change id (`ads action=changes status=approved`).
- Never launch without conversion tracking verified.
- One variable per ad test (hook, visual, or audience - not all three).
- Report honestly: platform-reported conversions vs actual pipeline.
