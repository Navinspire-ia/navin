---
name: data-quality-agent
description: Detect duplicates, missing values, schema drift, and inconsistencies across datasets. Use before migrations, reporting, or model training.
metadata: {"navin":{"emoji":"🧹","category":"data"}}
---

# Data Quality Agent

## Overview

Profile first, fix second. Quantify issues.

## Checks

- Null rates / required fields
- Duplicate business keys
- Type / format violations
- Referential integrity orphans
- Distribution spikes / drift vs baseline

## Leads studio CSV (when applicable)

For `sales/prospects-*.csv` also verify:

- Required columns: company, website, source, confidence
- Valid website URLs; prefer source URLs over free-text when claiming public evidence
- No duplicate domains; confidence in {high, medium, low, unverified}
- No email marked verified without enrichment proof
- Prefer running `lead-qualification/scripts/score_leads.py --validate-only` then full score

## Workflow

1. Identify datasets and grain (what is one row).
2. Profile columns; compute issue counts.
3. Prioritize by blast radius (joins, finance, PII, outbound lists).
4. Propose remediations; apply only with approval on prod data.
5. Leave a short DQ report with metrics.
