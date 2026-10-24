---
description: Migrate this app's code, config and prompts to a newer model generation, with a before/after comparison
argument-hint: "[target model or provider] [paths]"
---

Migrate the application in this workspace to a newer model generation.

Request: $ARGUMENTS

Load the `model-migration` skill and follow its workflow in order:

1. Inventory every model id and request parameter (code, config, env,
   infrastructure, tests, prompts). Show the table before editing.
2. Read the provider's current model list with `web_fetch` and propose one
   target per current id, same tier. When the mapping is a real choice, ask
   with `ask_user` (2 to 4 options, recommended one first).
3. Record a baseline on the current model (tests plus a small eval set).
4. Update ids and parameters with `apply_patch`, using
   `references/parameters.md` of the skill: effort, thinking budget, output
   limit, sampling restrictions, retired fields, SDK version.
5. Re-run the baseline and compare.
6. Apply prompt snippets only if the user asked for them or a regression
   appeared in step 5.
7. Summarize: files changed, old and new values, removed parameters, models
   left unchanged, before/after results, open risks.

Never commit, and never write API keys into files.
