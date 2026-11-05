---
name: ai-llm-telemetry
description: Observe LLM applications and agents from their telemetry. Reads Opik (Comet) traces and spans, LiteLLM proxy spend logs, budgets and key limits, and OpenTelemetry GenAI spans (gen_ai.* attributes) to compute cost per model, team, user and day, token percentiles, cache hit ratio, tool call frequency, failure rate and latency, time to first token, error and retry rates, cost anomalies and runaway tool loops. Bundled scripts aggregate JSON or JSONL exports offline. Use when the user asks to "how much did our LLM calls cost", "which team burns the most tokens", "audit the agent tool calls", "find runaway loops in traces", "set up Opik with LiteLLM", "is the LLM cache working", or when an AI feature bill, latency or error rate needs an explanation. In French "cout des appels LLM", "analyser les traces Opik", "audit des tool calls de l'agent".
metadata: {"navin":{"emoji":"🧠","category":"devops"}}
---

# AI and LLM Telemetry

## Overview

This skill turns the telemetry emitted by LLM applications (traces, spans, spend
logs, GenAI metrics) into answers about cost, usage, latency, tool behaviour and
privacy. It knows three sources well: Opik from Comet for traces and
evaluations, the LiteLLM proxy for spend, budgets and routing, and the
OpenTelemetry GenAI semantic conventions used by vendor-neutral
instrumentations. The default posture is read-only and evidence-based: query
and export, compute from the data, cite request ids, trace ids and timestamps,
and never change a budget, a key limit, a callback or a retention setting on a
target system without the user's explicit approval.

## When it applies

- A bill from OpenAI, Anthropic, Bedrock or Azure OpenAI is higher than
  expected and the user wants to know which model, team, user, key or day is
  responsible.
- An agent behaves strangely: too many tool calls, repeated identical calls,
  long chains, high failure rate on one tool, slow time to first token.
- A team wants an observability setup for a new LLM feature: Opik project,
  LiteLLM callbacks, OTel collector pipeline, budgets and alerts.
- A compliance or privacy review asks what prompt content is stored, where,
  for how long, and whether personal data is redacted.
- Evaluation results (feedback scores, experiments on datasets) must be
  correlated with production traces.

Hand over to `observability-agent` when the problem is infrastructure latency
unrelated to model calls, and to `finops-analyst` when the question is cloud
spend outside LLM providers.

## Engines and sources

### Opik (Comet)

- Instrumentation: `@opik.track` on functions, `type` in `llm`, `tool`,
  `general`, `guardrail`; integrations for OpenAI, Anthropic, Bedrock,
  LangChain, LlamaIndex, CrewAI, OpenAI Agents. Configuration through
  `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`, `OPIK_URL_OVERRIDE`.
- REST: `GET /api/v1/private/traces?project_name=<p>&page=1&size=500&filters=<json>`,
  `GET /api/v1/private/spans?project_name=<p>&type=tool&trace_id=<id>`,
  `POST /api/v1/private/traces/search` and `/spans/search` with
  `last_retrieved_id` cursor, `GET /api/v1/private/traces/stats` for
  aggregates. Responses are `{page, size, total, content: [...]}`.
- Key fields: `usage.prompt_tokens`, `usage.completion_tokens`,
  `usage.total_tokens`, `total_estimated_cost`, `model`, `provider`,
  `error_info`, `thread_id`, `feedback_scores[].name` and `.value`,
  `tags`, `metadata`.
- Full field list, filter grammar and the Langfuse, Arize Phoenix and Helicone
  equivalents: `references/opik-api.md`.

### LiteLLM proxy

- `GET /spend/logs?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` with rows
  `request_id`, `call_type`, `api_key` (hashed), `spend`, `total_tokens`,
  `prompt_tokens`, `completion_tokens`, `startTime`, `endTime`,
  `completionStartTime`, `model`, `user`, `team_id`, `end_user`, `metadata`,
  `cache_hit`, `request_tags`.
- `GET /global/spend/report?start_date=&end_date=&group_by=team|customer|api_key`,
  `GET /global/spend/tags`, `GET /spend/tags`, `GET /global/activity`.
- `GET /key/info?key=`, `GET /team/info?team_id=`, `GET /user/info?user_id=`
  return `spend`, `max_budget`, `budget_duration`, `budget_reset_at`,
  `soft_budget`, `rpm_limit`, `tpm_limit`, `model_max_budget`.
- `GET /model/info` for `input_cost_per_token` and `output_cost_per_token`.
- `config.yaml`: `litellm_settings.success_callback: ["opik", "langfuse",
  "otel"]`, `failure_callback`, `turn_off_message_logging`, `cache`,
  `max_budget`, `budget_duration`; `environment_variables.OPIK_API_KEY`,
  `OPIK_PROJECT_NAME`.
- Details: `references/litellm-spend-api.md`.

### OpenTelemetry GenAI conventions

- Spans `chat <model>`, `embeddings <model>`, `execute_tool <tool>`,
  `invoke_agent <agent>`; attributes `gen_ai.system` (or
  `gen_ai.provider.name`), `gen_ai.operation.name`, `gen_ai.request.model`,
  `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`,
  `gen_ai.tool.name`, `gen_ai.tool.call.id`, `error.type`.
- Content events (`gen_ai.content.prompt`, `gen_ai.user.message`, ...) exist
  only when `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
- Metrics `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`,
  `gen_ai.server.time_to_first_token`.
- Query in Tempo with TraceQL, in Prometheus with PromQL, in Datadog or
  Grafana through the `connectors` tool when the connector is configured.
- Attribute tables, PromQL and collector redaction: `references/otel-genai-conventions.md`.

### Navin integration

- Kubernetes or cloud CLIs (reading the proxy config map, collector logs,
  secrets metadata) go through the `infra` tool at observe level; prepare
  and execute levels are reserved for changes the user approved.
- Datadog, Grafana, Slack, GitHub, Notion: use the `connectors` tool for
  dashboards, alert history and delivery of the report.
- Tokens (`OPIK_API_KEY`, `LITELLM_MASTER_KEY`, `LANGFUSE_SECRET_KEY`) come
  from environment variables; print only their presence, never their value.
- A missing CLI (`curl`, `jq`, `promtool`) is installed with the `toolbelt`
  skill.
- Exports above a few megabytes are searched with the `grep` tool
  (`"error_info"`, a `request_id`, a `trace_id`) before any `read_file`.

## Workflow

1. Frame the question. Record the period (dates in UTC), the scope (project,
   team, key, model) and the metric asked for (cost, tokens, latency, tool
   failures, loops). Name the source of truth: LiteLLM for money when the
   proxy is in front of every provider, Opik or OTel for behaviour.
2. Check access without printing secrets: `env | grep -c OPIK_API_KEY`,
   `GET /health/liveliness` on the proxy, `GET /api/v1/private/projects` on
   Opik. Stop and ask when a source is missing; do not guess numbers.
3. Export the data to files in the workspace, one file per source and day
   range: `spend_logs_<from>_<to>.json`, `opik_spans_<project>_<date>.json`
   (keep the raw `{content: [...]}` page). Paginate until `page * size >=
   total`. For OTel, export OTLP JSON from the collector `file` exporter or
   Tempo `GET /api/traces/<id>`.
4. Compute spend:
   `python3 scripts/spend_report.py examples/spend_logs.json --budget 5`
   (replace the sample by the export). The report gives cost and tokens per
   model, team, user and day, p50/p95 tokens per request, cache hit ratio,
   the top 10 requests and daily anomalies by median absolute deviation.
   Exit 1 means the budget is exceeded or a day is anomalous: open the
   flagged day and list its top requests before drawing conclusions.
5. Reconcile with the proxy's own view: `GET /global/spend/report` for the
   same range must match the script's totals within rounding. A mismatch
   points to rows with `spend: 0` for an unknown model (check
   `GET /model/info` prices) or to cache hits counted differently.
6. Audit agent behaviour:
   `python3 scripts/tool_calls_audit.py examples/opik_spans.json --fail-on-loops`
   for an Opik span page, or
   `python3 scripts/tool_calls_audit.py examples/otel_genai_spans.json --json`
   for OTLP JSON. The audit gives per-tool call count, failure rate, latency
   p50/p95, repeated identical calls per trace (default threshold 3) and the
   longest chains. For each loop, open the trace and read the `error_info`
   or `status.message` of the repeated span: a loop is almost always the
   model retrying a failing tool with the same arguments.
7. Derive the remaining metrics from the same exports: time to first token
   = `completionStartTime - startTime` on streamed LiteLLM rows or
   `gen_ai.server.time_to_first_token`; error rate = rows with a non-2xx
   `status` or spans with `error_info` divided by all rows; retry rate =
   extra CLIENT spans under one parent; cost per 1000 requests per team.
8. Check privacy: confirm whether prompt bodies are stored (`input` and
   `output` on Opik spans, `gen_ai.*.message` events, LiteLLM
   `turn_off_message_logging`), whether personal data appears in them
   (grep for emails, phone numbers, ids), what the retention is (Opik cloud
   plan, self-hosted ClickHouse TTL, Tempo retention). Report gaps as
   findings; propose the redaction config but apply nothing unprompted.
9. Write the deliverable, cite request ids, trace ids and timestamps, and
   propose budget or limit changes as a separate approval list with the
   exact LiteLLM call (`POST /key/update`, `POST /team/update`) for each.

## Rules

- `spend` in LiteLLM is computed at request time from the price table; a
  price update does not rewrite old rows. Recompute from tokens and
  `/model/info` when the user disputes a figure.
- Rows with `cache_hit: "True"` cost 0 and keep their tokens: compute the
  cache hit ratio on rows, not on spend, and report the saved amount as
  tokens times the model price.
- `api_key` in spend logs is a SHA-256 hash. Map it to an alias with
  `GET /key/info?key=<hash>` (returns `key_alias`), never ask for raw keys.
- Opik `total_estimated_cost` is `null` for models missing from its price
  table; treat null as unknown, not as free, and list those models.
- Opik `type` is set by the instrumentation; frameworks sometimes log tools
  as `general`. When the audit finds no tool spans, filter on `name` prefixes
  (`tool:`, `function:`) and report the mapping used.
- OTel `execute_tool` spans are children of the agent span, not of the
  `chat` span that requested them; link them through `gen_ai.tool.call.id`
  matching the assistant message `tool_calls[].id`.
- `gen_ai.response.finish_reasons = ["length"]` on many spans means
  `max_tokens` truncation, usually followed by a retry: count it as a cost
  driver.
- The MAD anomaly detector needs at least three days; with fewer, say so
  instead of flagging nothing silently.
- A budget breach returned by the script (exit 1) is a finding, not a
  failure of the run.
- Never export or paste prompt bodies into the report; quote at most the
  tool name and a hash of the arguments. Redact emails and ids in any
  excerpt.
- Timezones: LiteLLM stores `startTime` in UTC; Opik `start_time` is UTC
  with `Z`. Convert the user's local dates before filtering.
- Changes to budgets, `rpm_limit`, `tpm_limit`, callbacks or retention are
  prepared as commands and executed only after the user approves each one.

## Deliverable

Produce a Markdown report with these sections and tables:

1. Scope: period, sources, projects, keys, counts of rows and traces.
2. Spend summary: table `| Dimension | Key | Requests | Tokens | Spend (USD) | Share |`
   for model, team and user; table `| Day | Requests | Tokens | Spend (USD) | Anomaly |`.
3. Top requests: `| Request id | Model | Team | User | Tokens | Spend (USD) | Start (UTC) |`.
4. Tool behaviour: `| Tool | Calls | Failures | Failure rate | p50 ms | p95 ms |`
   and `| Trace id | Tool | Repeats | Arguments hash | Likely cause |`.
5. Latency and errors: `| Metric | Value | Source | Note |` for time to first
   token p50/p95, error rate, retry rate, cache hit ratio.
6. Privacy: `| Store | Prompt bodies kept | Personal data seen | Retention | Gap |`.
7. Findings and proposed actions: `| # | Finding | Evidence (id, timestamp) | Proposed action | Needs approval |`.

When the user wants an HTML report, render the same content with the
`studio-html-report` skill. Deliver through the `connectors` tool (Slack,
Notion, GitHub issue) when asked.

## Resources

- `scripts/spend_report.py`: LiteLLM `/spend/logs` export (JSON array,
  `{data: [...]}` or JSONL) to cost and tokens per model, team, user and day,
  top requests, cache hit ratio, token percentiles and MAD anomalies;
  `--budget` sets the exit 1 threshold, `--json` for machine output. Run it
  in step 4.
- `scripts/tool_calls_audit.py`: Opik span page or OTLP GenAI JSON to
  per-tool frequency, failure rate, latency p50/p95, repeated identical
  calls and longest chains; `--loop-threshold`, `--fail-on-loops`, `--json`.
  Run it in step 6.
- `examples/spend_logs.json`: seven days of synthetic LiteLLM spend rows with
  one anomalous day and cache hits, for testing the spend script.
- `examples/opik_spans.json`: an Opik `/spans` page with `tool` and `llm`
  spans, including a six-repeat loop on `get_order`.
- `examples/otel_genai_spans.json`: OTLP JSON with `execute_tool` and `chat`
  spans following the GenAI conventions.
- `references/litellm-spend-api.md`: spend log fields, aggregate endpoints,
  key, team and user budget fields, callback configuration and alerting.
  Open it before querying the proxy or proposing a budget change.
- `references/opik-api.md`: Opik objects, SDK usage, REST endpoints, filter
  grammar, online evaluation rules, and the Langfuse, Phoenix and Helicone
  equivalents. Open it before exporting traces.
- `references/otel-genai-conventions.md`: span names, attributes, content
  capture opt-in, metrics, PromQL and collector redaction. Open it when the
  data comes from an OTel pipeline or when designing instrumentation.
