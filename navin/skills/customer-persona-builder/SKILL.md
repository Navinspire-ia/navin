---
name: customer-persona-builder
description: Build ICP and buyer personas - pains, goals, objections, triggers, and watering holes - from real evidence. Use before messaging, targeting, or content planning.
metadata: {"navin":{"emoji":"👤","category":"marketing"}}
---

# Customer Persona Builder

A persona is only useful if built from evidence (calls, reviews, won/lost deals) - not pure imagination. Flag assumptions explicitly.

## When to use

- Before campaigns, messaging, or lead ICP definition
- Refreshing personas after new sales learning

## When not to use

- Inventing demographics with no research when user has customer data available (ask for it first)

## ICP vs persona

- **ICP** (company): industry, size, geography, tech, triggers, disqualifiers
- **Persona** (person): role, goals, pains, objections, power, direct sources

## Persona template

```markdown
## Persona: <name> - <role>
- Context: company type, team, KPIs
- Trigger: why they look NOW
- Pains (ranked)
- Objections: price / risk / integration / status quo
- Decision role: champion / economic buyer / blocker
- Watering holes: where they read/ask/trust
- Words they use: verbatim quotes (sourced)
- Messaging map: pain → message → proof
```

## Workflow

1. Gather evidence: best customers, lost deals, competitor reviews, real buyer profiles.
2. Research with `web_search` / `entity-research` for role pains and communities.
3. Draft 2-3 personas max (primary, secondary, blocker).
4. Extract messaging map per persona for `copywriting-agent` / `campaign-manager`.
5. Store `marketing/personas.md` (and feed Leads ICP when relevant).

## Rules

- Every pain needs a source or is marked assumption.
- Refresh after ~10 sales conversations or quarterly.
- Do not invent quotes.

## Anti-patterns

- 8 fluffy personas nobody uses
- Demographics without jobs-to-be-done
