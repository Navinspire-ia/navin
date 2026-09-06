---
name: contract-reviewer
description: Review commercial contracts - flag risky clauses, missing protections, and negotiation points. Use before signing or sending any agreement. Not a substitute for a lawyer.
metadata: {"navin":{"emoji":"📜","category":"sales"}}
---

# Contract Reviewer

## Overview

Systematic first-pass review of commercial agreements: what's risky, what's missing, what to negotiate. Final review of significant contracts belongs to a qualified lawyer - say so every time.

## Clause checklist

| Clause | Check for |
|--------|-----------|
| Scope / SOW | precise deliverables, change-request process defined |
| Payment | terms, currency, late penalties, milestones vs completion |
| Liability | cap (ideally ≤ contract value), no unlimited liability, no broad indemnities |
| IP | who owns deliverables, pre-existing IP carve-out, license-back |
| Termination | exit rights for both sides, notice period, kill fees |
| Confidentiality/NDA | mutual, reasonable duration, standard carve-outs |
| Data | GDPR/local law compliance, hosting location, subprocessors |
| Non-compete/exclusivity | scope, duration, geography - proportionate? |
| Penalties/SLA | achievable? cap on penalties? force majeure? |
| Law & disputes | governing law, jurisdiction/arbitration (critical cross-border DZ/Gulf/EU) |

## Workflow

1. Ingest the contract (`pdf-ocr-extractor` for scans); identify type and our side.
2. Review clause by clause; classify findings: 🔴 blocker / 🟡 negotiate / 🟢 acceptable / ⚪ missing clause to add.
3. For each 🔴/🟡: quote the exact text, explain the risk in one sentence, propose alternative wording.
4. Deliver the review memo + a markup list; recommend lawyer review for anything high-stakes.

## Review memo format

```markdown
## Contract review - <name> (<our role>)
Verdict: sign / negotiate first / lawyer required
### 🔴 Blockers
### 🟡 Negotiation points (with proposed wording)
### ⚪ Missing protections
```

## Rules

- Always state this is analysis, not legal advice.
- Quote exact clause text - never paraphrase a risk.
- Cross-border contracts always get the "lawyer required" flag.
