---
name: conversion-rate-optimization
description: Optimize landing pages, forms, CTAs, and funnels - diagnosis, hypotheses, and test plans. Use when traffic exists but conversions lag.
metadata: {"navin":{"emoji":"🎚️","category":"marketing"}}
---

# Conversion Rate Optimization (CRO)

## Overview

CRO = removing friction and adding clarity where visitors already are. Diagnose before touching anything.

## Landing page audit grid

| Element | Question |
|---------|----------|
| Headline | does a stranger get the value in 5 seconds? |
| Subhead / hero | who it's for + what changes for them |
| Proof | logos, numbers, testimonials, cases near the claim they support |
| CTA | one primary action, visible without scrolling, specific label |
| Friction | form fields count, unclear pricing, dead ends |
| Objections | are the top 3 objections answered on the page? |
| Speed / mobile | loads fast, works on phone |

## Workflow

1. Get data: where users drop (analytics), what they say (session notes, chat logs).
2. Audit the page with the grid; fetch it with `web_fetch` for structure.
3. Rank issues: high traffic × high friction first.
4. Write hypotheses (`growth-marketing` experiment format) - copy changes usually beat design changes.
5. Propose the test: A/B if traffic allows, before/after with a holdout period otherwise.
6. Rewrite copy via `copywriting-agent`; measure and iterate.

## Rules

- Clarity beats persuasion tricks; specificity beats superlatives.
- Never test tiny changes on tiny traffic - you'll never reach significance.
- The form asks only for what sales actually uses.
