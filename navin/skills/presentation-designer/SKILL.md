---
name: presentation-designer
description: Design the story, structure, and slide-by-slide plan of presentations — pitch decks, client presentations, reports. Use before generating any deck.
metadata: {"navin":{"emoji":"🎬","category":"documents"}}
---

# Presentation Designer

## Overview

Design the argument before the slides. Output: a validated slide plan that `pptx-generator` turns into a file.

## Story structures

| Deck type | Structure |
|-----------|-----------|
| Sales/pitch | leur situation → problème chiffré → vision du résultat → notre approche → preuve (cas) → offre → next step |
| Executive report | conclusion d'abord → 3 messages clés → données à l'appui → décisions demandées |
| Project update | statut en 1 slide (🟢🟡🔴) → réalisations → risques → décisions nécessaires → plan |
| Training | pourquoi ça compte → concept → démo/exemple → pratique → récap |

## Slide plan format (the deliverable)

```markdown
## Deck: <titre> — <audience>, <durée>, <objectif>
| # | Message du slide (phrase complète) | Contenu | Visuel |
|---|-----------------------------------|---------|--------|
| 1 | "Le coût de X vous coûte 2M/an" | 1 chiffre géant | big number |
| 2 | ... | 3 bullets | photo/chart |
```

The message column is the test: each is a full assertion (not a topic). Read only that column top to bottom — if the story convinces, the deck will.

## Design rules

- 1 idée/slide; le titre EST le message ("CA +18%" pas "Résultats financiers")
- 10/20/30 discipline for pitches: ~10 slides, 20 min, ≥30pt fonts
- Data → chart with the takeaway in the title; details → annexe slides
- Assertion-evidence beats bullet lists

## Workflow

1. Clarify: audience, decision sought, duration, context (projected? read alone? sent by email = more text allowed).
2. Draft the message column first; validate the story with the user.
3. Complete content + visual specs per slide.
4. Hand to `pptx-generator` (brand template via `template-manager`); review the rendered deck against the plan.

## Rules

- No deck generation before the plan is validated — slides are expensive to redo.
- Annexes catch everything cut from the main flow.
