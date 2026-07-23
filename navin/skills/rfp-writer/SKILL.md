---
name: rfp-writer
description: Respond to RFPs, RFQs, tenders, and cahiers des charges — compliance matrix, requirement mapping, and structured answers. Use for formal procurement responses.
metadata: {"navin":{"emoji":"🏛️","category":"writing"}}
---

# RFP/RFQ Writer

## Overview

Tenders are won on compliance first, differentiation second. Miss one mandatory requirement and the best offer is eliminated.

## Method

### 1. Deconstruct the RFP
- Extract every requirement into a **compliance matrix**: `| # | Requirement | Mandatory? | Our answer | Evidence | Page |`
- Flag: deadlines, format rules, page limits, required certificates, submission method
- List admin documents needed (registres, attestations, références — critical in DZ/Gulf public tenders)

### 2. Bid/no-bid check
Score: can we deliver? do we have references? is price competitive? do we know the buyer? — advise honestly.

### 3. Write the response
- Follow *their* structure and numbering exactly — evaluators score against a grid
- Answer each requirement explicitly ("Compliant — here's how…"), never by omission
- Differentiators woven into answers, not in a separate brochure section
- Reuse vetted content blocks (past responses, `case-study-writer` outputs) — adapted, not pasted

### 4. Review
- Compliance matrix 100% green before polish
- Independent read: `critic-reviewer` for gaps
- Format check: page limits, fonts, signatures, annexes order

## Workflow tools

Extract requirements from PDFs with `pdf-ocr-extractor`; produce final docs with `docx-generator`/`pdf-generator`; track deadlines with `cron` reminders; watch new tenders with `tender-monitor`.

## Rules

- Never claim a certification or reference you don't have.
- Deadline math includes submission logistics (physical copies, platforms).
