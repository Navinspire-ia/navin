# Opik (Comet) tracing: concepts, SDK and REST API

## Concepts

| Object | Meaning | Key fields |
|---|---|---|
| Project | a bucket of traces, `OPIK_PROJECT_NAME` (default `Default Project`) | `id`, `name`, `workspace` |
| Trace | one end-to-end request through the application | `id`, `project_name`, `name`, `start_time`, `end_time`, `input`, `output`, `metadata`, `tags`, `usage`, `total_estimated_cost`, `thread_id`, `error_info`, `feedback_scores` |
| Span | one step inside a trace, nested with `parent_span_id` | `id`, `trace_id`, `parent_span_id`, `name`, `type`, `start_time`, `end_time`, `input`, `output`, `metadata`, `model`, `provider`, `usage`, `total_estimated_cost`, `error_info`, `tags` |
| Thread | a conversation grouping traces by `thread_id` | `id`, `first_message`, `last_message`, `number_of_messages` |
| Feedback score | a numeric or categorical score on a trace or span | `name`, `value`, `category_name`, `reason`, `source` (`sdk`, `ui`, `online_scoring`) |
| Dataset | evaluation inputs with expected outputs | `id`, `name`, items with `input`, `expected_output`, `metadata` |
| Experiment | one evaluation run of a dataset | `id`, `dataset_name`, `feedback_scores`, per item `experiment_items` |

Span `type` values: `llm` (a model call, carries `model`, `provider`, `usage`),
`tool` (a tool or function execution), `general` (any other step: retrieval,
parsing, agent loop), `guardrail` (safety check). Only `llm` spans have cost;
trace `total_estimated_cost` is the sum of its `llm` spans.

`usage` is normalised to `prompt_tokens`, `completion_tokens`, `total_tokens`
plus provider-specific keys kept as `original_usage.*` (for example
`original_usage.prompt_tokens_details.cached_tokens` for OpenAI prompt caching,
`original_usage.cache_read_input_tokens` for Anthropic). Cost is computed from
Opik's price table when `model` and `provider` are set; unknown models give
`total_estimated_cost: null`, not 0.

## SDK

```python
import opik
from opik import track, opik_context

opik.configure(use_local=False)  # reads OPIK_API_KEY, OPIK_WORKSPACE, OPIK_URL_OVERRIDE

@track(project_name="support-agent", type="tool", tags=["prod"])
def get_order(order_id: str) -> dict:
    ...

@track(type="llm")
def call_model(messages: list[dict]) -> str:
    ...
    opik_context.update_current_span(
        model="gpt-4o", provider="openai",
        usage={"prompt_tokens": 812, "completion_tokens": 133, "total_tokens": 945},
    )
    return "..."

@track(name="handle_ticket")
def handle(ticket: dict) -> str:
    opik_context.update_current_trace(thread_id=ticket["conversation_id"], tags=["tier1"])
    return call_model([...])
```

- Nested `@track` calls produce nested spans automatically; the outermost one
  creates the trace.
- Integrations: `opik.integrations.openai.track_openai(client)`,
  `track_anthropic`, `track_bedrock`, `track_litellm` (SDK side), LangChain
  `OpikTracer`, LlamaIndex, DSPy, Haystack, CrewAI, OpenAI Agents.
- Redaction before sending: `opik.configure(...)` plus `@track(capture_input=False,
  capture_output=False)` or a custom `opik.Opik(...)` client with a filter.
- Environment: `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`,
  `OPIK_URL_OVERRIDE` (`https://www.comet.com/opik/api` for cloud,
  `http://localhost:5173/api` for the default self-hosted stack),
  `OPIK_TRACK_DISABLE=true` to turn tracing off.

## REST API

Base: `$OPIK_URL_OVERRIDE` (ends with `/api`). Headers on Comet cloud:
`authorization: $OPIK_API_KEY` (raw key, no `Bearer`) and
`Comet-Workspace: $OPIK_WORKSPACE`. Self-hosted open source needs no auth by
default.

| Method and path | Purpose | Parameters |
|---|---|---|
| `GET /api/v1/private/projects` | list projects | `page`, `size`, `name` |
| `GET /api/v1/private/traces` | list traces of a project | `project_name` or `project_id` (one is required), `page` (1-based), `size` (max 1000), `filters` (JSON, URL-encoded), `truncate=true` to cut long input/output |
| `GET /api/v1/private/traces/{id}` | one trace with `feedback_scores` | |
| `POST /api/v1/private/traces/search` | same as list, filters in the body | `{"project_name": "...", "filters": [...], "limit": 500, "last_retrieved_id": "..."}` for cursor pagination |
| `GET /api/v1/private/traces/stats` | aggregates (count, usage, cost, duration percentiles) | `project_name`, `filters` |
| `GET /api/v1/private/spans` | list spans | `project_name` or `project_id`, `trace_id`, `type` (`llm`, `tool`, `general`), `page`, `size`, `filters`, `truncate` |
| `POST /api/v1/private/spans/search` | spans with cursor pagination | body like traces search |
| `GET /api/v1/private/spans/stats` | span aggregates | `project_name`, `type` |
| `PUT /api/v1/private/traces/{id}/feedback-scores` | add scores | body `{"scores": [{"name": "helpfulness", "value": 0.8, "source": "sdk"}]}` |
| `GET /api/v1/private/datasets` and `/datasets/{id}/items` | evaluation datasets | `page`, `size` |
| `GET /api/v1/private/experiments` | experiments with aggregated scores | `datasetId`, `page`, `size` |
| `POST /api/v1/private/traces/batch`, `POST /api/v1/private/spans/batch` | ingest traces and spans (what the SDK uses) | body `{"traces": [...]}` or `{"spans": [...]}` |

List responses: `{"page": 1, "size": 100, "total": 1234, "content": [...]}`.
Iterate while `page * size < total`. `scripts/tool_calls_audit.py` reads the
`content` list directly, so a page can be saved as-is.

### Filters

A filter is `{"field": "...", "operator": "...", "value": "...", "key": "..."}`.

| Field (traces) | Field (spans) | Operators |
|---|---|---|
| `name`, `input`, `output`, `metadata` (with `key`), `tags`, `thread_id` | same plus `model`, `provider`, `type` | `=`, `!=`, `contains`, `not_contains`, `starts_with`, `ends_with` |
| `start_time`, `end_time` | same | `=`, `>`, `<`, `>=`, `<=` (ISO 8601) |
| `usage.total_tokens`, `usage.prompt_tokens`, `usage.completion_tokens`, `total_estimated_cost`, `duration` | same | `=`, `!=`, `>`, `<`, `>=`, `<=` |
| `feedback_scores` (with `key` = score name) | same | numeric operators |
| `error_info` | same | `is_empty`, `is_not_empty` |

Examples:

```bash
# failed tool spans in the last day
curl -sG "$OPIK_URL_OVERRIDE/api/v1/private/spans" \
  -H "authorization: $OPIK_API_KEY" -H "Comet-Workspace: $OPIK_WORKSPACE" \
  --data-urlencode 'project_name=support-agent' --data-urlencode 'type=tool' --data-urlencode 'size=500' \
  --data-urlencode 'filters=[{"field":"error_info","operator":"is_not_empty","value":""},{"field":"start_time","operator":">=","value":"2026-03-05T00:00:00Z"}]' \
  > spans_failed_tools.json

# traces above 0.05 USD
--data-urlencode 'filters=[{"field":"total_estimated_cost","operator":">","value":"0.05"}]'
```

### Online evaluation and rules

`GET /api/v1/private/automations/evaluators?project_id=` lists LLM-as-a-judge
rules (`type: llm_as_judge`, `sampling_rate`, `code.model`, `code.messages`,
`code.schema`). Scores they produce appear in `feedback_scores` with `source:
online_scoring`. Count them per day to check that sampling works.

## Alternatives, mapped

| Product | Trace list | Spend | Notes |
|---|---|---|---|
| Langfuse | `GET /api/public/traces?fromTimestamp=&limit=&page=`, `GET /api/public/observations?type=GENERATION` | `calculatedTotalCost`, `usage.input`, `usage.output` per observation | basic auth with `LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY`; `GET /api/public/metrics/daily` for cost per day and model |
| Arize Phoenix | GraphQL at `/graphql` and `px.Client().get_spans_dataframe()`; OpenInference attributes `llm.token_count.prompt`, `llm.token_count.completion`, `tool.name`, `openinference.span.kind` (`LLM`, `TOOL`, `CHAIN`, `AGENT`) | cost via `llm.cost.total` when configured | accepts OTLP; self-hosted at `:6006` |
| Helicone | `POST /v1/request/query` with `filter`, `limit`, `offset`; fields `costUSD`, `prompt_tokens`, `completion_tokens`, `model`, `properties` | included | proxy mode adds `Helicone-Property-*` headers for user and team |
