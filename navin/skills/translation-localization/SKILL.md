---
name: translation-localization
description: Translate and localize between French, English, and Arabic - adapting idioms, register, formats, and cultural context, not just words. Use for any cross-language content.
metadata: {"navin":{"emoji":"🌐","category":"writing"}}
---

# Translation & Localization

## Overview

Translate meaning and intent, not words. Localize formats, examples, and register for the target culture.

## Localization checklist

| Element | Adapt |
|---------|-------|
| Register | FR vouvoiement, AR honorifics, EN directness |
| Dates/numbers | 21/07/2026 vs July 21, 2026; decimal comma vs point |
| Currency | DZD / EUR / SAR / USD with context |
| Idioms | equivalent expression, never literal |
| Examples/references | local companies, laws, institutions |
| Legal terms | jurisdiction-correct equivalents (flag, don't guess) |
| Layout | Arabic RTL implications for docs/UI |

## Variants awareness

- French: France vs Algeria/Maghreb business usage
- Arabic: MSA (فصحى) for documents; note when Gulf vs Maghreb dialect matters for marketing
- English: US vs UK spelling, pick one and hold it

## Workflow

1. Clarify: target audience, variant, purpose (legal? marketing? technical?), glossary/brand terms that stay untranslated.
2. Translate in full passes (not sentence-by-sentence) to keep flow.
3. Localize formats and examples per the checklist.
4. Back-check: re-read the target text alone - does it read as native?
5. For documents: preserve formatting via `docx-generator` round-trip; for the WebUI/i18n files, follow the project's key structure.

## Rules

- Proper nouns, product names, and quoted legal text stay as-is unless instructed.
- Uncertain legal/technical terms get flagged with the original in parentheses.
- Marketing copy is transcreated (rewritten for effect), not translated literally.
