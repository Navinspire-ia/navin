---
name: model-router
description: Recommend which model or provider preset to use based on cost, latency, context size, and task difficulty. Use when the user asks which model to pick or when work is mismatched to the current model.
metadata: {"navin":{"emoji":"🔀","category":"intelligence"}}
---

# Model Router

## Overview

Help choose the right model configuration for the job. You cannot always switch models mid-turn yourself — recommend Settings → Models / Providers presets when a change is needed.

## Decision factors

| Factor | Prefer |
|--------|--------|
| Simple edits, chatty Q&A | Fast / cheap model |
| Large codebase reasoning, hard bugs | Stronger reasoning model |
| Huge context / many files | Model with large context window |
| Vision / images | Vision-capable model |
| Strict JSON / tool-heavy loops | Models known-good with tools |
| On-prem / privacy | Local (Ollama / vLLM / LM Studio) |

## Task-based routing (/pilot)

Navin supports per-task model presets. If the user creates presets named `search`, `plan`, `review`, `security`, `dev`, `fast`, `deep`, or `docs` in Settings → Models, the `/pilot <task>` command switches to the matching preset instantly:

| Task | Suggested preset profile |
|------|--------------------------|
| `search` | fast, cheap, large context (web synthesis) |
| `plan` | strongest reasoning model |
| `review` / `security` | high-precision reasoning, low temperature |
| `dev` | best coding model, tool-reliable |
| `fast` | minimal latency for quick edits |
| `deep` | maximum capability regardless of cost |

Recommend `/pilot` when the user's workflow phase changes (e.g. moving from planning to implementation), and `/checkpoint save` before switching mid-task so the state is recoverable.

## Workflow

1. Classify the task (shallow / standard / deep) and modality (text, code, vision).
2. Check the **current** model from context / Settings snapshot if available.
3. Recommend:
   - stay on current model, or
   - switch via `/model <preset>` or `/pilot <task>` when a matching preset exists, or
   - create the missing preset in **Settings → Models**.
4. Explain **why** in one short paragraph (cost vs quality vs latency).
5. If the user must switch: point them to **Settings → Models** (or Providers) and continue with best effort on the current model until they switch.

## Anti-patterns

- Blindly recommending the most expensive model
- Suggesting a provider that is not configured
- Changing approach mid-task without saying the model is the bottleneck
