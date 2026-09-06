---
name: pricing-assistant
description: Build pricing scenarios - options, margins, discount policies, and negotiation floors - for offers and deals. Use before pricing any proposal or negotiation.
metadata: {"navin":{"emoji":"🏷️","category":"sales"}}
---

# Pricing Assistant

## Overview

Price from value and costs, not from fear. Prepare scenarios BEFORE the negotiation so concessions are planned, not improvised.

## Pricing worksheet

```markdown
## Pricing - <deal>
### Costs & floor
- Delivery cost estimate (days × loaded rate + externals)
- Walk-away floor (below this we decline)
### Value anchor
- Client's cost of problem / value of outcome (chiffré)
- Reference prices: competitors, client's alternatives
### Scenarios
| Option | Scope | Price | Margin | Notes |
| Essential | ... | ... | ...% | |
| Recommended | ... | ... | ...% | |
| Premium | ... | ... | ...% | |
### Negotiation plan
- Expected asks & planned responses
- Concession ladder: each give tied to a get
```

## Discount doctrine

- Never a naked discount - trade for: longer commitment, case study rights, prepayment, reduced scope, volume
- Concessions shrink (10% → 4% → 1%) to signal the floor is near
- "C'est trop cher" → `objection-handler` AER first; price moves last

## Workflow

1. Gather: scope, delivery estimate, client context (`account-research`), budget signals (`discovery-call-assistant`).
2. Fill the worksheet; user validates costs, floor, and margins.
3. Feed scenarios into `sales-proposal-writer`; brief the negotiator with the plan.
4. Post-deal: log final price vs list - the discount history informs the next pricing round.

## Rules

- The floor is set cold, before the meeting, and holds.
- Every number shown to a client is user-validated (`human-approval`).
- Multi-currency deals (DZD/EUR/SAR/USD): state currency, rate assumptions, and validity.
