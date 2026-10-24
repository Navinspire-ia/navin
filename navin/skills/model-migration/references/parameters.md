# Request parameters across providers

Use this file during step 4 of the migration. Names below are typical at the
time of writing. Always confirm against the provider's current API reference
with `web_fetch`: field names, limits and defaults change between generations.

## Checklist per call site

| Concern | What to check | Typical action |
|---|---|---|
| Model id | exists in the live model list | replace, keep pin style |
| Reasoning effort | supported values (low / medium / high, sometimes minimal) | set explicitly |
| Thinking budget | field name, min and max, counts against output | set after effort |
| Output limit | field name, ceiling of the new model | raise to budget + answer |
| Sampling | temperature / top_p / penalties accepted? | remove when refused |
| Beta flags | still needed, still supported | remove obsolete, comment dropped ones |
| System role | `system` versus `developer` or a separate field | follow the SDK |
| Tools | call format, parallel calls, tool choice values | update schema if changed |
| Structured output | JSON mode versus schema mode | prefer schema mode if available |
| SDK version | minimum version for the new model or field | bump and type check |

## Effort versus thinking budget

The two settings answer different questions:

- **Effort** says how eagerly the model spends tokens overall: reasoning,
  visible text and tool calls.
- **Thinking budget** caps how many tokens can go to hidden reasoning.

They are independent. High effort with no thinking still produces longer,
more thorough answers. High effort with a fixed budget lets the model work
hard but stops reasoning at the cap. Decide in this order:

1. effort level from the path's needs (quality: high, cost or latency: medium,
   simple high-volume: low);
2. thinking budget, if the provider has one, sized to the task;
3. output limit large enough to hold the budget plus the longest expected
   answer.

## Request examples

OpenAI-style chat request with a reasoning model:

```python
response = client.chat.completions.create(
    model=NEW_MODEL_ID,               # from config, never hardcoded in many places
    reasoning_effort="high",          # was absent on the previous generation
    max_completion_tokens=8000,       # renamed output limit, covers reasoning too
    # temperature removed: refused by this reasoning model
    messages=messages,
)
```

Gemini-style request with a thinking budget:

```python
response = client.models.generate_content(
    model=NEW_MODEL_ID,
    contents=prompt,
    config={
        "thinking_config": {"thinking_budget": 4096},
        "max_output_tokens": 12000,
    },
)
```

Frontier model family with effort and extended thinking (generic shape):

```json
{
  "model": "NEW_MODEL_ID",
  "max_tokens": 16000,
  "effort": "high",
  "thinking": {"type": "enabled", "budget_tokens": 8000},
  "messages": []
}
```

Open-weight model behind an OpenAI-compatible local server:

```bash
# The served name is chosen by the operator: update both sides.
grep -rn "served-model-name\|--model" deploy/ docker-compose*.yml
curl -s http://127.0.0.1:8000/v1/models
```

## Dropped capabilities

When a beta or capability is not yet available on the new model, remove it and
leave a short comment at the call site:

```python
# Extended context beta removed: not available on NEW_MODEL_ID yet (migration 2026-xx).
```

## Cost and limits

- Compare price per input and output token, and remember that reasoning
  tokens are billed as output.
- Check rate limits and quotas of the new id: they are often lower at launch.
- Record usage from the baseline and the new run to show the real difference.
