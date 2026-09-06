---
name: sales-proposal-writer
description: Generate sales offers, quotes, and commercial proposals with pricing options and decision-ready framing. Use when a qualified prospect asks "send me something".
metadata: {"navin":{"emoji":"📑","category":"sales"}}
---

# Sales Proposal Writer

## Overview

Sales-stage sibling of `proposal-writer`: faster, more commercial, built to close. The proposal restates their pain, quantifies the value, and makes the decision easy.

## Proposal grades

| Grade | When | Format |
|-------|------|--------|
| Quote | simple, price-driven ask | 1 page: scope, price, terms, validity |
| Standard proposal | qualified opportunity | 3-6 pages: summary, need, solution, pricing options, next steps |
| Strategic offer | large/competitive deal | full `proposal-writer` treatment + battlecard positioning |

## Pricing presentation

- **Three options** when possible (essential / recommended / premium) - the middle sells
- Anchor on value: state the cost of the problem before the price of the solution
- Price per outcome or phase, not a raw day-rate dump
- Validity date + clear payment terms; discounts only against something (volume, testimonial, longer commitment - via `pricing-assistant`)

## Workflow

1. Inputs: discovery notes (`discovery-call-assistant`), qualification score, competitor context (`objection-handler` battlecards).
2. Draft the executive summary in THEIR words - their pain, their number, their deadline.
3. Build options; sanity-check margins with the user.
4. Handle known objections in-line (security? integration? references?).
5. Produce: `docx-generator`/`pdf-generator` on the brand template; log in pipeline (`crm-update-agent`).
6. Follow-up plan attached: send date, relance J+3 and J+8 (`meeting-followup` / `cron`).

## Rules

- No proposal without a prior conversation - "send me a doc" from a stranger gets a qualification call proposal instead.
- Every price validated by the user before sending (`human-approval`).
- Track win/loss per proposal structure to improve the template.
