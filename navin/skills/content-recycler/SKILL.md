---
name: content-recycler
description: Turn one long-form piece into LinkedIn posts, X threads, Instagram carousels, TikTok scripts, and emails. Use to multiply reach from existing content.
metadata: {"navin":{"emoji":"♻️","category":"marketing"}}
---

# Content Recycler

## Overview

One strong article contains 5–15 social assets. Extract the ideas and rewrite natively per platform — never copy-paste.

## Recycling map (from one article)

| Output | How |
|--------|-----|
| 3–5 LinkedIn posts | one insight each, new hook, personal angle |
| 1 X thread | the full argument, one idea per tweet, strong opener + payoff |
| 1 carousel (LinkedIn/IG) | 8–10 slides: hook → steps → CTA |
| 2–3 short video scripts | 30s each: hook, one point, punchline |
| 1 newsletter section | summary + "read more" link |
| Quote graphics | the 2–3 most quotable lines |

## Workflow

1. Ingest the source (URL via `web_fetch`, or file via `read_file`).
2. Extract the atomic ideas: claims, numbers, steps, stories, contrarian takes.
3. Rank ideas by hook potential.
4. Produce each output in its platform's native format (`social-media-manager` cheatsheet).
5. Deliver as a batch with suggested posting dates spread over 2–4 weeks.

## Rules

- Each derivative stands alone — no "as I wrote in my article" dependency.
- Rewrite hooks from scratch per platform; recycle ideas, not sentences.
- Keep the brand voice consistent (`brand-voice-manager`).
