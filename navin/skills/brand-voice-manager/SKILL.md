---
name: brand-voice-manager
description: Define, store, and enforce brand voice across all content — vocabulary, tone, do/don't lists per brand (Navinspire, Guidia, Lynara, etc.). Use to keep multi-brand output consistent.
metadata: {"navin":{"emoji":"🎨","category":"writing"}}
---

# Brand Voice Manager

## Overview

Keep every piece of content recognizably on-brand, per brand. The voice lives in versioned style cards that other skills consult.

## Style card format (one per brand)

```markdown
# Voice — <Brand>
- Mission in one line: ...
- Personality (3 adjectives): ...
- Tone by context: site / social / sales / support
- We say / we never say: | ✅ | ❌ |
- Vocabulary: product names (exact casing), key terms FR/EN/AR
- Grammar choices: tu/vous, we/I, oxford comma, emoji policy
- Sample paragraphs: 2–3 approved examples
```

Store cards under `brand/<brand>-voice.md` in the workspace.

## Workflow

**Defining a voice**
1. Collect samples the user considers "perfectly us" and "never us".
2. Extract the fingerprint (see `style-editor`); draft the card; validate with the user.

**Enforcing**
1. Before writing branded content, read the relevant card with `read_file`.
2. After drafting (any skill), check: banned words? tone match? product names cased right?
3. On violations in existing content: list them with fixes, per the card.

**Evolving**
- Update the card when the user corrects tone twice for the same reason; keep a changelog section.

## Rules

- Multi-brand discipline: never blend voices; confirm which brand before writing.
- The card wins over the writer's instinct; disagreement goes to the user, not silent override.
