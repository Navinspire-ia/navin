---
name: invoice-reader
description: Extract invoice data — vendor, amounts, taxes, dates, line items — from PDFs and scans into structured records, with validation checks. Use for invoice processing and expense tracking.
metadata: {"navin":{"emoji":"🧾","category":"documents"}}
---

# Invoice Reader

## Overview

Read invoices (PDF/scan/photo) into clean records, validate the math, and flag anomalies. Built for batches.

## Extraction schema

```markdown
| Field | Notes |
|-------|-------|
| Vendor (name, address, tax IDs) | FR: SIREN/TVA; DZ: NIF/RC/AI |
| Invoice number & date | duplicate-check key |
| Due date / payment terms | |
| Currency | |
| Line items | description, qty, unit price, total |
| Subtotal HT / VAT per rate / Total TTC | |
| Payment details (IBAN) | ⚠ see fraud rules |
```

## Validation checks (every invoice)

1. **Math**: Σ lines = subtotal; subtotal + VAT = total; VAT = rate × base (rounding tolerance)
2. **Duplicates**: same vendor + number, or same amount + date pattern
3. **Anomalies**: amount ≫ vendor's history, new IBAN for a known vendor (🚨 classic fraud — always flag), missing tax IDs, suspicious rounding

## Workflow

1. Ingest via the `pdf-ocr-extractor` pipeline (pdfplumber → OCR fallback for scans/photos).
2. Extract per schema; every field carries a confidence note; low-confidence values marked `⚠ à vérifier`.
3. Run validations; produce the record + anomaly flags.
4. Batch: registre in xlsx via `spreadsheet-analyst` — one row per invoice + status column (ok / à vérifier / anomalie).
5. Optional: due-date reminders (`cron`) for payables.

## Register format

```markdown
| # | Fournisseur | N° | Date | HT | TVA | TTC | Échéance | Statut |
```

## Rules

- Never auto-approve a payment — extraction and flags only; decisions are human.
- IBAN changes are always escalated, never silently recorded.
- Keep source files linked to records for audit.
