---
name: brand-voice-manager
description: Define, store, and enforce brand voice across all content - vocabulary, tone, do/don't lists per brand. Use to keep multi-brand output consistent.
metadata: {"navin":{"emoji":"🎨","category":"writing"}}
---

# Brand Voice Manager

Keep every piece recognizably on-brand. Voice lives in versioned style cards that other skills consult before writing.

## When to use

- Defining or updating a brand voice
- Enforcing consistency across campaign assets

## When not to use

- One-off neutral docs with no brand
- Blending multiple brands in one piece

## Style card format

```markdown
# Voice - <Brand>
- Mission in one line
- Personality (3 adjectives)
- Tone by context: site / social / sales / support
- We say / we never say table
- Vocabulary: product names (exact casing), key terms FR/EN/AR
- Grammar: tu/vous, we/I, emoji policy
- Sample paragraphs: 2-3 approved examples
- Changelog
```

Store under `brand/<brand>-voice.md`.

## Workflow

**Define**
1. Collect "perfectly us" and "never us" samples.
2. Draft the card; validate with the user.

**Enforce**
1. `read_file` the card before branded writing.
2. After drafting: banned words? tone? casing?
3. List violations with fixes when auditing existing content.

**Evolve**
- Update when the user corrects the same tone issue twice; append changelog.

## Rules

- Confirm which brand before writing.
- The card wins over writer instinct; escalate disagreements to the user.
- Marketing studio runs should load the card early and keep it across assets.

## Anti-patterns

- Silent voice drift across a campaign pack
- Mixing two brands' vocabulary
