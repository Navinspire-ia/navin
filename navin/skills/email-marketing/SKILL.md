---
name: email-marketing
description: Design newsletters, nurture sequences, and lifecycle emails - structure, cadence, deliverability, and measurement. Use for any recurring or automated email program.
metadata: {"navin":{"emoji":"💌","category":"marketing"}}
---

# Email Marketing

## Overview

Email is the highest-ROI owned channel when the list is clean and the sequences are intentional.

## Core programs

| Program | Trigger | Goal |
|---------|---------|------|
| Welcome sequence | signup | activate + set expectations (3-5 emails) |
| Nurture | lead not sales-ready | educate until buying trigger (weekly-ish) |
| Newsletter | calendar | stay top of mind with real value |
| Re-engagement | 90d inactive | win back or clean the list |
| Post-sale | purchase/onboarding | adoption, reviews, referrals |

## Email anatomy

- Subject: ≤50 chars, curiosity or clear benefit - write 5 options
- Preheader: complements, doesn't repeat the subject
- Body: one idea, one CTA; short paragraphs; personal tone
- CTA: one primary button/link (repeat it, don't compete with it)

## Workflow

1. Map the lifecycle; pick the missing program with the highest impact.
2. Write sequence outline (email count, trigger, delay, goal per email).
3. Draft with `email-writer`/`copywriting-agent`; keep brand voice.
4. Deliverability basics: SPF/DKIM/DMARC configured, clean list, easy unsubscribe.
5. Measure: open (direction only), CTR, replies, conversions; iterate subject/CTA.

## Rules

- Consent-based lists only; respect unsubscribe instantly (legal + deliverability).
- Sequences pause automatically when the lead replies or converts.
- Plain, human emails usually beat heavy HTML for B2B.
