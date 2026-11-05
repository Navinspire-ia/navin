# OpenTelemetry GenAI semantic conventions

Status: development (the conventions change between releases; pin
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` when the
instrumentation supports it and record the version in the report).

## Span names and operation names

| `gen_ai.operation.name` | Span name | Span kind | When |
|---|---|---|---|
| `chat` | `chat <gen_ai.request.model>` | CLIENT | chat completion request |
| `text_completion` | `text_completion <model>` | CLIENT | legacy completion |
| `embeddings` | `embeddings <model>` | CLIENT | embedding request |
| `generate_content` | `generate_content <model>` | CLIENT | multimodal (Gemini style) |
| `execute_tool` | `execute_tool <gen_ai.tool.name>` | INTERNAL | the application runs a tool the model asked for |
| `invoke_agent` | `invoke_agent <gen_ai.agent.name>` | CLIENT or INTERNAL | one agent turn |
| `create_agent` | `create_agent <gen_ai.agent.name>` | CLIENT | agent registration |

When the model is unknown the span name is the operation alone (`chat`).

## Request and response attributes

| Attribute | Type | Example | Requirement |
|---|---|---|---|
| `gen_ai.system` (older) / `gen_ai.provider.name` (newer) | string | `openai`, `anthropic`, `aws.bedrock`, `azure.ai.openai`, `gcp.vertex_ai`, `cohere`, `mistral_ai` | required |
| `gen_ai.operation.name` | string | `chat` | required |
| `gen_ai.request.model` | string | `gpt-4o` | conditionally required |
| `gen_ai.response.model` | string | `gpt-4o-2024-08-06` | recommended |
| `gen_ai.response.id` | string | `chatcmpl-abc123` | recommended |
| `gen_ai.response.finish_reasons` | string[] | `["stop"]`, `["tool_calls"]`, `["length"]`, `["content_filter"]` | recommended |
| `gen_ai.usage.input_tokens` | int | 812 | recommended |
| `gen_ai.usage.output_tokens` | int | 133 | recommended |
| `gen_ai.request.temperature`, `gen_ai.request.top_p`, `gen_ai.request.max_tokens`, `gen_ai.request.seed`, `gen_ai.request.frequency_penalty`, `gen_ai.request.presence_penalty`, `gen_ai.request.stop_sequences` | mixed | | recommended |
| `gen_ai.request.choice.count` | int | 1 | when > 1 |
| `gen_ai.output.type` | string | `text`, `json`, `image`, `speech` | recommended |
| `gen_ai.conversation.id` | string | thread or session id | recommended |
| `server.address`, `server.port` | string, int | `api.openai.com`, 443 | recommended |
| `error.type` | string | `timeout`, `429`, exception class | when the span fails |

Set the span status to `ERROR` and `error.type` when the provider returns a
non-2xx or the SDK raises. Retries are separate CLIENT spans under the same
parent: count them per parent to compute the retry rate.

## Tool attributes

| Attribute | Type | Example |
|---|---|---|
| `gen_ai.tool.name` | string | `get_order` |
| `gen_ai.tool.call.id` | string | `call_mszuSIzqtI65i1wAUOE8w5H4` (must match the id in the assistant message) |
| `gen_ai.tool.type` | string | `function`, `extension`, `datastore` |
| `gen_ai.tool.description` | string | from the tool schema |
| `gen_ai.tool.call.arguments` | string (JSON) | opt-in, may hold personal data |
| `gen_ai.tool.call.result` | string (JSON) | opt-in |

A model turn that requests tools ends with `gen_ai.response.finish_reasons =
["tool_calls"]`; the next `execute_tool` spans are children of the agent span,
not of the chat span. Loop detection groups `execute_tool` spans by trace,
`gen_ai.tool.name` and `gen_ai.tool.call.arguments`.

## Agent attributes

`gen_ai.agent.id`, `gen_ai.agent.name`, `gen_ai.agent.description`,
`gen_ai.data_source.id` (RAG source). Framework instrumentations (OpenAI
Agents SDK, LangGraph, CrewAI, Pydantic AI via Logfire, Semantic Kernel) emit
`invoke_agent` spans with these.

## Content capture: opt-in only

Prompt and completion bodies are never recorded by default. They appear only
when the operator sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`
(Python and Java instrumentations). Then the instrumentation emits:

- Events (older conventions): `gen_ai.content.prompt` with body `{"gen_ai.prompt":
  "..."}`, `gen_ai.content.completion` with `{"gen_ai.completion": "..."}`.
- Events (current conventions): `gen_ai.system.message`, `gen_ai.user.message`,
  `gen_ai.assistant.message`, `gen_ai.tool.message`, `gen_ai.choice`, each
  with `gen_ai.system` as attribute and the message JSON as body.
- Newest draft: span attributes `gen_ai.input.messages` and
  `gen_ai.output.messages` (JSON arrays with `role` and `parts`).

Treat any of these as personal data: redact before export (collector
`transform` or `redaction` processor), restrict retention, and document the
legal basis.

## Metrics

| Metric | Instrument | Unit | Attributes |
|---|---|---|---|
| `gen_ai.client.token.usage` | histogram | `{token}` | `gen_ai.token.type` = `input` or `output`, plus operation, provider, model |
| `gen_ai.client.operation.duration` | histogram | `s` | operation, provider, request and response model, `error.type` |
| `gen_ai.server.request.duration` | histogram | `s` | server side (model servers such as vLLM, TGI) |
| `gen_ai.server.time_to_first_token` | histogram | `s` | server side |
| `gen_ai.server.time_per_output_token` | histogram | `s` | server side |

Useful PromQL once the collector exports to Prometheus:

```promql
# input tokens per minute per model
sum by (gen_ai_request_model) (rate(gen_ai_client_token_usage_sum{gen_ai_token_type="input"}[5m])) * 60
# p95 request duration per provider
histogram_quantile(0.95, sum by (le, gen_ai_provider_name) (rate(gen_ai_client_operation_duration_bucket[5m])))
# error ratio
sum(rate(gen_ai_client_operation_duration_count{error_type!=""}[5m])) / sum(rate(gen_ai_client_operation_duration_count[5m]))
```

## Collector pipeline sketch

```yaml
processors:
  attributes/redact:
    actions:
      - key: gen_ai.tool.call.arguments
        action: delete
      - key: gen_ai.input.messages
        action: delete
  filter/drop_content_events:
    logs:
      log_record:
        - 'IsMatch(event_name, "gen_ai\\..*message") == true'
exporters:
  otlp/tempo:
    endpoint: tempo:4317
```

Search exported spans in Tempo with TraceQL: `{ span.gen_ai.operation.name =
"execute_tool" && status = error }` or `{ span.gen_ai.request.model = "gpt-4o"
&& span.gen_ai.usage.output_tokens > 2000 }`.
