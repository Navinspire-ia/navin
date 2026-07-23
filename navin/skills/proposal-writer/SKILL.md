---
name: proposal-writer
description: Prepare commercial proposals and client responses — structure, pricing presentation, and persuasive framing. Use for quotes, offers, and project proposals.
metadata: {"navin":{"emoji":"📄","category":"writing"}}
---

# Proposal Writer

## Overview

A proposal is a decision document: restate their problem better than they did, then make saying yes easy.

## Proposal structure

1. **Executive summary** — their situation, the outcome you deliver, investment range (1 page)
2. **Understanding of the need** — their words, their stakes (proves you listened)
3. **Proposed solution** — phases, deliverables, what's in/out of scope
4. **Methodology & timeline** — how, when, milestones
5. **Team & references** — who does the work, similar projects (`case-study-writer` output)
6. **Investment** — options (good/better/best when possible), payment terms
7. **Next steps** — one clear action with a date
8. Annexes — technical details, CVs, legal

## Workflow

1. Collect: discovery notes (`discovery-call-assistant`), scope, constraints, budget signals, decision process.
2. Draft the executive summary first — if it doesn't convince alone, fix it before writing 20 pages.
3. Price presentation: anchor with value delivered, not cost breakdown; options beat single take-it-or-leave-it.
4. Address known objections preemptively (`objection-handler` input).
5. Produce the document: `docx-generator` or `pdf-generator`, branded template via `template-manager`.
6. Validity date + follow-up plan (`meeting-followup`).

## Rules

- Scope boundaries explicit — "not included" prevents disputes.
- No jargon the client didn't use first; mirror their vocabulary.
- Human validation before sending anything priced.
