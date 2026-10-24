---
name: model-migration
description: Migrates an application's code, configuration and prompts from one model generation to a newer one, for any provider (OpenAI, Gemini, Mistral, open-weight models, frontier model families). Finds every model id, updates ids and request parameters (reasoning effort, thinking budget, max tokens, temperature limits, deprecated fields), then tunes prompts for models that follow instructions more literally, and compares outputs before and after. Use when the user asks to "migrate to the new model", "upgrade the model version", "switch our app to the latest model", "update model ids in the codebase", "the new model overuses tools" or "our prompts behave differently after the upgrade".
metadata: {"navin":{"category":"development"}}
---

# Model Migration

Move an application to a newer model generation in one controlled pass: code and
configuration first, prompts second, and a measured comparison at the end. The
workflow is provider-neutral. The provider-specific details (parameter names,
limits, retired fields) live in `references/parameters.md`, and the prompt
snippets live in `references/prompt-snippets.md`.

## Principles

- **Change the minimum by default.** A migration updates ids and parameters.
  Prompt rewrites happen only when the user asks for them or a regression is
  observed. A newer model often needs fewer instructions, not more.
- **Never guess an id.** Read the provider's current model list (docs page,
  `GET /v1/models`, SDK constant, or the gateway catalog) with `web_fetch` or
  the provider CLI. Model ids change often and a plausible-looking id that does
  not exist fails only at runtime.
- **Same family, same tier.** Map a small fast model to the new small fast
  model, a large reasoning model to the new large reasoning model. Do not
  silently move a cheap high-volume path onto an expensive tier. Ask when the
  mapping is not one-to-one.
- **Keep a baseline.** Capture outputs of the old model before touching
  anything, so the result can be compared rather than judged by impression.

## Workflow

### 1. Inventory

Locate every place the model is chosen or configured. Search code, config,
infrastructure and tests:

- literal ids: `grep` for the provider prefixes and version patterns, for
  example `gpt-`, `o[0-9]-`, `gemini-`, `mistral-`, `codestral`, `llama`,
  `qwen`, `deepseek`, plus the family names used in the codebase;
- indirect selection: environment variables (`MODEL`, `LLM_MODEL`,
  `*_MODEL_ID`), YAML/JSON/TOML config, feature flags, database seeds,
  Terraform/Helm values, CI secrets documentation;
- parameters next to each call: `max_tokens`, `max_output_tokens`,
  `temperature`, `top_p`, `reasoning_effort`, thinking or reasoning budget
  fields, beta headers, tool choice, response format, stop sequences;
- prompts: system prompts, templates, few-shot files, agent instructions.

Present the inventory as a table: location, current id or parameter, proposed
value, risk. Keep pinned dated snapshots (ids with a date suffix) pinned to the
new dated snapshot; keep floating aliases floating unless the user wants pins.

### 2. Choose the target

For each current id, propose the target id from the provider's live list.
When a real choice exists (two candidate tiers, alias versus pinned snapshot,
migrate everything versus one endpoint first), ask with `ask_user`, offering
2 to 4 options and marking the recommended one. Leave models the user did not
name untouched, and say so in the summary (for example a small classification
model that is outside the scope of the upgrade).

### 3. Baseline

Before editing, record the behaviour of the current model:

- run the existing test suite and any eval script (`test_run`);
- if there is no eval, build a small one: 10 to 30 representative inputs taken
  from tests, fixtures or logs, including edge cases and one or two inputs that
  used to fail;
- store inputs and outputs in a file (JSONL or CSV) with token usage and
  latency when the API returns them.

### 4. Update ids and parameters

Apply the changes with `apply_patch`. Review each request against
`references/parameters.md`:

- **Reasoning controls.** Newer generations often expose an effort level
  (low / medium / high) and/or a thinking token budget. Decide the effort level
  first, then the budget. Default for a quality-sensitive path: high effort.
  For cost or latency-sensitive paths: medium. For simple high-volume calls:
  low or reasoning disabled.
- **Token limits.** Reasoning tokens usually count against the output limit.
  Raise `max_tokens` (or the provider's renamed field) so that budget plus the
  visible answer still fit. Check the new context window and output ceiling.
- **Sampling restrictions.** Some reasoning models reject or ignore
  `temperature`, `top_p` or penalties, or accept only the default value.
  Remove them where they are refused instead of sending values that error.
- **Renamed and retired fields.** Replace renamed fields (for example an old
  `max_tokens` replaced by a completion-specific name) and remove beta flags or
  headers that the new model does not support. Leave a one-line comment where a
  capability was dropped (for example an extended context beta not yet
  available on the new model) so nobody re-adds it blindly.
- **System role and tools.** Check whether the new model expects a different
  role name for system instructions, a new tool-call format or a new structured
  output option, and update the client or SDK version if required.
- **SDK version.** Bump the client library when the new model or field needs
  it (`pip index versions`, `npm view <pkg> version`), and run the type check.

Keep the change readable: one commit-sized diff for ids and parameters,
separate from prompt changes.

### 5. Re-run and compare

Run the same baseline set on the new model and compare side by side:
correctness, format compliance (JSON validity, schema fields), length, tool
calls per task, refusals, latency and cost. Report regressions with the input
that triggers them. Only then move to prompt changes, and only for the
problems actually observed.

### 6. Prompt adjustments (on request or on observed regression)

Newer generations tend to follow the system prompt more literally and react
more strongly to emphasis. Instructions written to push an older, less
attentive model can overshoot. Apply the matching fix, each described with a
ready-to-adapt snippet in `references/prompt-snippets.md`:

1. **Tools called too often.** Old prompts shout (`CRITICAL`, `You MUST`,
   `ALWAYS`, `NEVER skip`, `REQUIRED`) to force tool use. Soften only the
   tool-triggering lines: `You MUST use X` becomes `Use X when ...`, and add the
   condition under which the tool is useful. Leave emphasis that protects
   safety or format rules alone.
2. **Tools called too rarely or answers without looking.** Add an explicit
   trigger: when to read files, search or call a tool before answering, and the
   instruction to inspect the relevant code before proposing a fix.
3. **Explicit scope.** Literal models do exactly what is written. If the old
   model inferred "and also fix the tests", the new one may not. State the
   expected scope and deliverables directly.
4. **Overengineering.** Extra files, abstractions, configuration options and
   unrequested features: add the minimal-change guard.
5. **Generic frontend output.** Bland layouts and default fonts: add the
   frontend aesthetics guard.
6. **Verbosity drift.** Answers longer or shorter than before, or missing
   progress summaries after tool calls: state the expected length and format.
7. **Reasoning word sensitivity.** When reasoning is off, some models treat
   the word "think" as a request to reason at length. Replace it with
   "consider", "evaluate" or "check" in prompts sent without a reasoning
   budget.

Integrate snippets, do not append them blindly: match the existing structure
of the prompt (sections, XML-style tags such as `<tool_usage>` or
`<code_guidelines>`, Markdown headings), put each rule next to related rules,
and remove the older instruction it replaces so the prompt does not contradict
itself. Re-run the comparison after each prompt change.

### 7. Summarize

Report:

- every file changed, with old and new id or parameter;
- parameters removed and why (unsupported, renamed, retired);
- models deliberately left unchanged;
- before/after results of the baseline set, with regressions and fixes;
- prompt snippets applied, and where;
- open risks (cost change, rate limits, quotas, features not yet available).

Close by offering to tune prompts further if new behaviour issues show up.

## Provider notes in brief

- **OpenAI-style APIs:** reasoning models take an effort setting and may refuse
  sampling parameters; output limits can use a completion-specific field name.
- **Gemini-style APIs:** thinking is configured by a budget or level inside the
  generation config; a budget of zero may disable it on some tiers.
- **Mistral and open-weight models:** ids carry dates or version suffixes;
  local servers (vLLM, Ollama, llama.cpp) expose the new weights under a name
  chosen by the operator, so check the server config as well as the code.
- **Frontier model families with extended thinking:** effort and thinking
  budget are independent; set effort first, then the budget, and raise the
  output limit to cover both.

The details, with request examples, are in `references/parameters.md`. Always
confirm against the provider's current documentation, which wins over this
skill.

## Common pitfalls

- **Partial migration.** One call site updated, another still on the old id
  through an environment default or a test fixture. Re-run the inventory
  search after editing; the only remaining old ids must be intentional.
- **Silent truncation.** A reasoning budget raised without raising the output
  limit returns empty or cut answers. Check the stop reason in the comparison.
- **Over-tuned prompts.** Adding every snippet "just in case" produces
  contradictions. Apply one snippet per observed problem.
- **Tests asserting exact wording.** Snapshot tests on model text break on any
  model change. Switch them to structural checks (schema, key facts) and say
  so in the summary rather than updating snapshots blindly.
- **Cached ids.** Prompt caches, embeddings and fine-tuned derivatives are tied
  to a model. Embedding models in particular need a re-index, not a simple id
  swap: flag them and leave them out of scope unless the user agrees.

## Safety

- Do not commit API keys while editing config. Keys stay in environment
  variables or the secret store.
- Do not change production model ids without the user's confirmation when a
  rollout or feature flag exists: propose the flag-based rollout instead.
- Keep the old id reachable (config value, flag) until the comparison passes,
  so rollback is a configuration change.
