# model-migration

Move an application to a newer model generation without surprises. Works for
OpenAI, Gemini, Mistral, open-weight models behind a local server and other
frontier model families.

## What the skill does

1. Finds every model id and request parameter in code, config and
   infrastructure.
2. Proposes target ids from the provider's live model list (never from
   memory), same tier for each path.
3. Records a baseline on the current model.
4. Updates ids and parameters: reasoning effort, thinking budget, output
   limit, sampling restrictions, renamed or retired fields, SDK version.
5. Re-runs the baseline and compares quality, format, length, tool calls,
   latency and cost.
6. Applies prompt snippets only for the regressions seen: softer tool
   triggers, explicit scope, overengineering guard, frontend aesthetics guard,
   verbosity rules, reasoning word sensitivity.

## Example

```text
/migrate-model gemini src/
```

Or in plain words:

```text
Migrate our backend to the newest model generation and tell me what changed.
```

Typical parameter change made by the skill:

```python
response = client.chat.completions.create(
    model=settings.model_id,
    reasoning_effort="high",
    max_completion_tokens=8000,
    messages=messages,
)
```

References shipped with the skill: `references/parameters.md` and
`references/prompt-snippets.md`.
