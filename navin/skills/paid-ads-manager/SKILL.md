---
name: paid-ads-manager
description: Plan and operate Google Ads, Meta Ads, TikTok Ads, Reddit Ads, and LinkedIn Ads - targeting, creatives, budgets, and optimization loops via live MCP when connected. Use when paid acquisition is on the table.
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

## Workflow

1. Identify platform(s). If the matching MCP is connected, list accounts and read live campaign/ad-group structure and recent metrics via MCP tools before proposing a new structure - do not invent account topology.
2. Define target CPA/CPL from unit economics (`marketing-analytics`).
3. Draft targeting + keywords/audiences + negative lists (or refine against live data when MCP is available).
4. Write ad copy variants (hooks from `copywriting-agent`); design brief for visuals.
5. Launch checklist: pixel/tag firing, budget caps, geo/language settings.
6. Weekly report: spend, CPL, CTR, conversion rate per audience - kill/scale decisions (prefer MCP metrics; else platform exports). Save under `ads/` + `ads-report-*.html`.

## Rules

- Prefer MCP `google-ads`, `meta-ads`, `tiktok-ads`, `reddit-ads`, or `linkedin-ads` for live reads when configured; otherwise stay advisory and ask for Ads exports.
- Prefer read-only first. Never mutate budgets, bids, or status without explicit user approval.
- Never launch without conversion tracking verified.
- One variable per ad test (hook, visual, or audience - not all three).
- Report honestly: platform-reported conversions vs actual pipeline.
