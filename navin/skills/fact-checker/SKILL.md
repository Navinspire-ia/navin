---
name: fact-checker
description: Verify claims, statistics, quotes, and citations before publication — with sources and confidence levels. Use before publishing anything containing factual assertions.
metadata: {"navin":{"emoji":"✅","category":"writing"}}
---

# Fact Checker

## Overview

Every factual claim in a publishable text gets verified, sourced, or removed. Reputation costs more than the 20 minutes of checking.

## What to check

- Numbers and statistics (and their date + scope)
- Quotes and attributions
- Names, titles, company facts
- Superlatives ("first", "biggest", "only") — usually wrong
- Legal/regulatory statements
- Technical claims (versions, capabilities, benchmarks)

## Verification ladder

| Confidence | Standard |
|-----------|----------|
| Verified | 2+ independent primary/authoritative sources agree |
| Single-source | one credible source — label it in the text ("selon X") |
| Unverifiable | rewrite as opinion, or cut |
| Contradicted | correct it; note the common misconception if useful |

## Workflow

1. Extract every checkable claim from the text into a list.
2. Verify each with `web_search`/`web_fetch`; prefer primary sources (official stats, original study, company filings) over articles citing articles.
3. Check dates: a 2019 stat presented as current is a fail.
4. Produce the fact-check table: `| Claim | Verdict | Source | Suggested fix |`.
5. Apply fixes or return the table to the author.

## Rules

- The original study beats the article about the study.
- If two credible sources conflict, present both — don't pick silently.
- AI-generated "facts" (including your own memory) are claims to verify, not sources.
