---
name: lead-prospector
description: Expert-level prospect hunting — find companies, people, roles, and verified contact context from open web sources. Use for any "find me leads / people / companies" request.
metadata: {"navin":{"emoji":"🎯","category":"sales"}}
---

# Lead Prospector

## Overview

Operate like a top-tier SDR research desk: turn a target definition into a clean, deduplicated, fully sourced list of companies and decision-makers, with the context needed to open a conversation. Never invent data — every field has a source or is marked unknown.

## Search playbook

### Companies

| Technique | How |
|-----------|-----|
| Directory sweep | `web_search` "{sector} companies {geography}", industry directories, chambers of commerce, awards lists, "top X {sector}" roundups |
| Registry lookups | official company registries (e.g. societe.com/Pappers FR, Companies House UK, OpenCorporates) for legal data, size, incorporation date |
| Ecosystem mining | competitors' customer logos and case studies, partner pages, marketplace vendor lists, event exhibitor/sponsor lists |
| Tech footprint | job postings and site source hints revealing the stack (careers pages, BuiltWith-style clues in HTML) |
| Lookalike expansion | take the 3 best current clients → search their competitors and peers |

### People

| Technique | How |
|-----------|-----|
| Role search | `web_search` "{company} {role}" site:linkedin.com/in — collect name, title, profile URL (do not scrape logged-in content) |
| Team pages | fetch the company's /about, /team, /leadership pages |
| Press & talks | press releases, podcast/conference speaker bios, webinar panels — decision-makers show up with titles |
| Authorship | blog post bylines, whitepaper authors, patent filings, GitHub orgs for technical buyers |

### Contact context

- Email patterns: infer from public sources (press contacts, legal pages, published emails) → note the pattern (first.last@) with confidence level; never fabricate an address as verified.
- Phone/switchboard: official site contact/legal pages only.
- Always record: source URL + date collected per datum.

## Output format

Deliver as a CSV or markdown table saved to the workspace (`sales/prospects-<date>.csv`):

`company, website, size, sector, country, signal/trigger, person, role, profile_url, contact_hint, source, confidence`

Follow with the 5 best-fit leads and why, plus a suggested opening angle per lead.

## Rules

- Public sources only; respect robots and terms — no login-walled scraping.
- Dedupe aggressively (same domain = same company).
- Mark every unverified field `unverified`; never pad the list with guesses.
- Quality beats volume: 25 verified beats 200 noisy rows. Ask for the target volume if unclear.
