---
name: contract-extractor
description: Extract structured data from contracts - parties, dates, amounts, obligations, deadlines, and renewal terms - into usable tables. Use to digest contract stacks.
metadata: {"navin":{"emoji":"🗄️","category":"documents"}}
---

# Contract Extractor

## Overview

Turn contract PDFs into a structured register: who, what, how much, until when, renewing when. Extraction, not legal judgment (that's `contract-reviewer`).

## Extraction schema

```markdown
| Field | Example |
|-------|---------|
| Parties (+ roles) | Navinspire (prestataire) / Client X (client) |
| Type | prestation / NDA / licence / bail / cadre |
| Signature date / effective date | ... |
| Duration & end date | 12 mois → 2026-09-30 |
| Renewal | tacite, préavis 3 mois → deadline dénonciation 2026-06-30 |
| Amounts | montant, devise, échéancier, indexation |
| Payment terms | 30j fin de mois, pénalités |
| Key obligations (per party) | livrables, SLA |
| Termination conditions | ... |
| Governing law / jurisdiction | ... |
| Special clauses flags | exclusivité, non-concurrence, caution |
```

## Workflow

1. Ingest: text-layer PDFs via `pdf-ocr-extractor` pipeline (pdfplumber; OCR fallback for scans).
2. Extract per the schema; quote the source text + page for every extracted value (auditability).
3. Uncertain reads (bad scan, ambiguous clause) → mark `⚠ à vérifier` - never guess an amount or date.
4. Batch mode: one row per contract into a register (`spreadsheet-analyst` for the xlsx).
5. High-value output: the **deadline calendar** - renewals, préavis, expiries → `cron` reminders at J-90/J-30.

## Register format

```markdown
| Contrat | Contrepartie | Montant | Fin | Préavis deadline | Alerte |
```

## Rules

- Every value traceable to the page it came from.
- Confidentiality: contracts stay in the workspace; nothing quoted externally.
- Interpretation questions → route to `contract-reviewer` explicitly.
