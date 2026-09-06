---
name: model-router
description: Recommend which model or provider preset to use based on cost, latency, context size, and task difficulty. Use when the user asks which model to pick or when work is mismatched to the current model.
metadata: {"navin":{"emoji":"🔀","category":"intelligence"}}
---

# Model Router

## Overview

Help choose the right model configuration for the job. You cannot always switch models mid-turn yourself - recommend Settings → Models / Providers presets when a change is needed.

## Decision factors

| Factor | Prefer |
|--------|--------|
| Simple edits, chatty Q&A | Fast / cheap model |
| Large codebase reasoning, hard bugs | Stronger reasoning model |
| Huge context / many files | Model with large context window |
| Vision / images | Vision-capable model |
| Strict JSON / tool-heavy loops | Models known-good with tools |
| On-prem / privacy | Local (Ollama / vLLM / LM Studio) |

## Task-based routing (`modelRoutes`)

Navin maps **roles** to **named presets** in Settings → Models → Task routing (`modelRoutes` in config). Values are preset keys (e.g. `primary`, `economy`), not free-form labels.

### Automatic (preferred)

Workflow slash commands pick the routed preset for that turn:

| Role | Commands (examples) |
|------|---------------------|
| `plan` | `/blueprint`, `/board` |
| `dev` | `/forge`, `/mobile`, `/ops` |
| `deep` | `/risklens` |
| `security` | `/fortify`, `/probe`, `/pentest`, … |
| `review` | `/inspect`, `/turbo` |
| `docs` | `/studio`, `/seo`, `/leads`, `/campaign`, `/atlas`, `/report` |
| `search` | `/scrape` |
| `fast` | `/pulse` (+ Code editor assist) |

So `/forge` uses the `dev` route automatically - the user does not need `/pilot` first.

### Manual (`/pilot`)

| Task | Suggested preset profile |
|------|--------------------------|
| `search` | fast, cheap, large context (web synthesis) |
| `plan` | strongest reasoning model |
| `review` / `security` | high-precision reasoning, low temperature |
| `dev` | best coding model, tool-reliable |
| `fast` | minimal latency for quick edits |
| `deep` | maximum capability regardless of cost |

`/pilot <task>` switches the session preset in memory. Recommend it for free-form chat when the phase changes without a workflow command, and `/checkpoint save` before switching mid-task.

## Workflow

1. Classify the task (shallow / standard / deep) and modality (text, code, vision).
2. Check the **current** model from context / Settings snapshot if available.
3. Recommend:
   - stay on current model, or
   - run the matching workflow command so auto-routing applies, or
   - switch via `/model <preset>` / `/pilot <task>`, or
   - create the missing preset and assign it under **Settings → Models → Task routing**.
4. Explain **why** in one short paragraph (cost vs quality vs latency).
5. If the user must configure a route: point them to **Settings → Models → Task routing** and continue with best effort on the current model until they save.

## Anti-patterns

- Blindly recommending the most expensive model
- Suggesting a provider that is not configured
- Telling the user to `/pilot` before `/forge` / `/blueprint` when Task routing is already configured (those workflows auto-route)
- Changing approach mid-task without saying the model is the bottleneck
