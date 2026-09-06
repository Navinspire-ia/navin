---
name: freelance-rate-card
description: Build or defend a freelance daily rate (TJM) from the Career profile, market, and mission scope. Use for freelance pricing, rate cards, and counter-offers.
metadata: {"navin":{"emoji":"📐","category":"careers","default_for":"career"}}
---

# Freelance Rate Card

Price the mission, not a vanity TJM. The Career profile `min_rate` is the floor unless the user changes it.

## Inputs

1. `career action=status` - titles, countries, stack, min_rate, currency.
2. Mission: duration, remote vs on-site, seniority, stack, buyer type (ESN, end client, scale-up).
3. Public market signals via `web_search` (Malt, official rate surveys). Cite sources. No scrape of closed portals.

## Rate card

| Scenario | TJM | When |
|----------|-----|------|
| Floor | profile min_rate | Short, vague, or high friction |
| Standard | floor + 10-20% | Clear scope, known stack |
| Premium | standard + 15-25% | Rare stack, rush, or staff-plus |

Add extras: travel days, extra hours, IP, notice. Never discount below floor without an explicit user yes.

## Rules

- Track stays `freelance`. Do not convert a TJM into a fake CDI salary without saying so.
- Store the agreed rate on the opportunity notes after `career action=apply` or stage=offer.
- Pair with `salary-negotiator` only when the buyer asks for a package, not a day rate.
