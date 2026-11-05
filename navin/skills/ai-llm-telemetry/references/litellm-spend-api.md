# LiteLLM proxy: spend, budgets and callbacks

All endpoints live on the proxy base URL (default `http://localhost:4000`) and
require the header `Authorization: Bearer $LITELLM_MASTER_KEY` (or a key with
the right role). Read the token from the environment, never paste it in chat.

## GET /spend/logs

Returns one row per request from the `LiteLLM_SpendLogs` table.

| Query parameter | Meaning |
|---|---|
| `api_key` | hashed key (the `token` column), filters one virtual key |
| `user_id` | filters on `user` |
| `request_id` | one request |
| `start_date`, `end_date` | ISO dates, `YYYY-MM-DD`; both required for a range |

Row fields (table `LiteLLM_SpendLogs`):

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | provider id when available (`chatcmpl-...`) |
| `call_type` | string | `completion`, `acompletion`, `embedding`, `aembedding`, `image_generation`, `pass_through_endpoint` |
| `api_key` | string | SHA-256 hash of the virtual key, never the raw key |
| `spend` | float | USD, computed from `/model/info` prices or `litellm.model_cost` |
| `total_tokens`, `prompt_tokens`, `completion_tokens` | int | usage returned by the provider |
| `startTime`, `endTime` | ISO 8601 | `endTime - startTime` is the proxy-side latency |
| `completionStartTime` | ISO 8601 | first streamed chunk, gives time to first token |
| `model` | string | the public model name of the request |
| `model_group`, `custom_llm_provider` | string | router group and provider (`openai`, `anthropic`, `bedrock`) |
| `api_base` | string | upstream URL |
| `user` | string | `user` field of the OpenAI request, or the key owner |
| `team_id` | string | team of the virtual key |
| `end_user` | string | `end_user_id`/`user` header for customer-level spend |
| `metadata` | JSON | `user_api_key_alias`, `user_api_key_team_alias`, `spend_logs_metadata`, tags |
| `cache_hit` | string/bool | `"True"` when served from the LiteLLM cache (spend is 0) |
| `request_tags` | list | tags from `metadata.tags` on the request |
| `requester_ip_address` | string | only when `general_settings.log_requester_ip: true` |

An export from the UI or `GET /spend/logs?start_date=...&end_date=...` is a
JSON array; a response from `GET /spend/logs/ui` wraps it in `{"data": [...],
"total": N, "page": 1, "page_size": 50}`. `scripts/spend_report.py` accepts both
shapes and JSONL.

Large deployments: `GET /spend/logs` without a date range loads the whole
table. Query day by day, or enable `general_settings.disable_spend_logs: true`
and read spend from the Opik or Langfuse callback instead.

## GET /global/spend/report

Aggregated spend, grouped.

| Parameter | Values |
|---|---|
| `start_date`, `end_date` | `YYYY-MM-DD` |
| `group_by` | `team` (default), `customer`, `api_key` |
| `api_key` | restrict to one key hash |
| `internal_user_id` | restrict to one user |
| `team_id` | restrict to one team |
| `customer_id` | restrict to one `end_user` |

Response (`group_by=team`): list of `{"group_by_day": "2026-03-01", "teams":
[{"team_name": "...", "total_spend": 1.23, "metadata": [{"model": "gpt-4o",
"spend": 0.9, "total_tokens": 12000, "api_key": "<hash>"}]}]}`.
`group_by=customer` returns `customers` with `customer`, `total_spend`,
`metadata`.

Other aggregate endpoints:

- `GET /global/spend/tags?start_date=&end_date=&tags=a,b`: spend per request
  tag (`individual_request_tag`, `total_spend`, `log_count`).
- `GET /spend/tags`: same data, older route.
- `GET /global/spend/models?limit=10`, `GET /global/spend/keys?limit=10`,
  `GET /global/spend/end_users`: top models, keys and customers.
- `GET /global/activity?start_date=&end_date=`: requests and tokens per day
  (`daily_data[].api_requests`, `total_tokens`).

## Keys, teams, users and budgets

`GET /key/info?key=<raw or hashed key>` returns `info`:

| Field | Meaning |
|---|---|
| `key_name` | masked display name `sk-...abcd` |
| `key_alias`, `team_id`, `user_id` | ownership |
| `spend` | lifetime or current period spend in USD |
| `max_budget` | USD cap, request refused with HTTP 400 `ExceededBudget` once reached |
| `budget_duration` | `30s`, `30m`, `30h`, `30d`, `1mo`; `budget_reset_at` tells when `spend` resets |
| `soft_budget` | alert-only threshold (webhook, Slack) |
| `rpm_limit`, `tpm_limit` | requests and tokens per minute for the key |
| `max_parallel_requests` | concurrency cap |
| `model_max_budget` | `{"gpt-4o": {"budget_limit": 10, "time_period": "1d"}}` per-model caps |
| `models` | allowed model list, empty means all |
| `metadata` | free JSON, including `tags` |
| `expires` | ISO expiry |

The same budget fields exist on `POST /team/new`, `GET /team/info?team_id=`,
`POST /user/new`, `GET /user/info?user_id=` and `POST /customer/new` (for
`end_user`), with `max_budget`, `budget_duration`, `rpm_limit`, `tpm_limit`,
and on teams `team_member_budget` and `model_rpm_limit` / `model_tpm_limit`.

Precedence when a request comes in: key budget, then team budget, then user
budget, then global `max_budget` in `litellm_settings`. The first exhausted one
rejects the call.

`GET /model/info` lists deployments with `model_info.input_cost_per_token`,
`model_info.output_cost_per_token`, `model_info.max_input_tokens`,
`model_info.max_output_tokens`, `model_info.mode`. Use these to recompute
`spend` when a row looks wrong (price overrides live in `model_list[].model_info`).

## Callbacks in config.yaml

```yaml
litellm_settings:
  success_callback: ["opik", "langfuse", "otel"]
  failure_callback: ["opik", "otel"]
  cache: true
  cache_params:
    type: redis
  max_budget: 500
  budget_duration: 30d
  turn_off_message_logging: true   # keep prompt and completion bodies out of callbacks

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  alerting: ["slack"]
  alerting_threshold: 300

environment_variables:
  OPIK_API_KEY: os.environ/OPIK_API_KEY
  OPIK_WORKSPACE: os.environ/OPIK_WORKSPACE
  OPIK_PROJECT_NAME: litellm-proxy
  OPIK_URL_OVERRIDE: https://www.comet.com/opik/api   # self-hosted: http://opik-backend:8080/api
  LANGFUSE_PUBLIC_KEY: os.environ/LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY: os.environ/LANGFUSE_SECRET_KEY
  OTEL_EXPORTER: otlp_http
  OTEL_ENDPOINT: http://otel-collector:4318/v1/traces
  OTEL_HEADERS: Authorization=Bearer%20os.environ/OTEL_TOKEN
```

- `turn_off_message_logging: true` or per-request `metadata.no-log: true`
  stops prompt bodies from reaching Opik, Langfuse and OTel while keeping
  usage and cost.
- Per-request routing of Opik metadata: `metadata.opik = {"project_name":
  "...", "tags": [...], "thread_id": "..."}` on the chat completion call.
- Redaction of specific fields: `litellm_settings.redact_user_api_key_info:
  true` strips key aliases from callback payloads.

## Alerts

`general_settings.alerting: ["slack"]` with `SLACK_WEBHOOK_URL` sends budget
alerts at 50%, 75%, 85%, 95% and 100% of `max_budget` (`alert_types:
["budget_alerts", "spend_reports", "llm_exceptions", "llm_too_slow"]`).
`spend_reports` posts a weekly and monthly spend per team. Verify alert
delivery with `GET /health/services?service=slack`.
