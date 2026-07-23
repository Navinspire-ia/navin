---
name: objection-handler
description: Build and use objection response playbooks — price, security, integration, competition, status quo. Use to prepare answers and battlecards for sales conversations.
metadata: {"navin":{"emoji":"🛡️","category":"sales"}}
---

# Objection Handler

## Overview

Objections are requests for reassurance, rarely rejections. Prepare structured responses per objection family, grounded in real proof.

## Response framework (AER)

**Acknowledge** (never argue) → **Explore** ("qu'est-ce qui vous fait dire ça ?") → **Respond** with proof → confirm.

## Objection families

| Family | Real question | Response assets |
|--------|---------------|-----------------|
| Price ("trop cher") | "quelle valeur pour ce prix ?" | ROI calc, cost-of-inaction, options (`pricing-assistant`) |
| Security/data | "puis-je vous faire confiance ?" | architecture note, hosting location, compliance, références |
| Integration | "ça va casser mon existant ?" | integration map, phased plan, pilot offer |
| Competition ("X fait pareil") | "pourquoi vous ?" | battlecard: honest diff, where we win, where they win |
| Status quo ("on gère en interne") | "le changement vaut-il l'effort ?" | cost of current state chiffré, migration path |
| Timing ("plus tard") | "pas prioritaire" | trigger tie-back, cost of delay, low-commitment first step |

## Battlecards

Per competitor, kept in `sales/battlecards/<name>.md` (fed by `competitor-intelligence`): their pitch, real strengths, weaknesses (from reviews), landmine questions to plant, our winning story.

## Workflow

1. Collect objections actually heard (`discovery-call-assistant` notes) — build the playbook from reality.
2. Draft AER responses with the user; attach proof per response.
3. Before key meetings: brief with the 3 most likely objections + responses.
4. After losses: log which objection killed it → strengthen that response or the product story.

## Rules

- Honesty wins: concede real limitations and reframe — bullshit detected = deal dead.
- Price objections are never answered with an instant discount.
