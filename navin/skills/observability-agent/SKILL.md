---
name: observability-agent
description: Query metrics, logs, and traces (Prometheus, Grafana, Loki, Tempo, Sentry, OpenTelemetry, CloudWatch) to diagnose incidents. Use during outages or performance investigations.
metadata: {"navin":{"emoji":"📈","category":"devops"}}
---

# Observability Agent

## Overview

Follow symptoms → signals → cause. Prefer existing dashboards and log queries over random restarts.

## Engine

Drive these CLIs through the `infra` tool (providers: kubernetes,aws).

1. `infra action=discover provider=kubernetes` to locate Prometheus, Grafana, Loki, Tempo, OpenTelemetry Collector; `provider=aws` for CloudWatch.
2. Queries are read operations: `infra action=run command="..."` at observe level.
3. Changes to alert rules, scrape configs, retention or log groups: `infra action=check`, `infra action=plan`, `infra action=run`, `infra action=verify`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Stack commands

```bash
curl -s '<prom>/api/v1/query?query=sum(rate(http_requests_total{code=~"5.."}[5m]))'
promtool check rules rules.yaml; amtool alert query
logcli query '{namespace="<ns>"} |= "error"' --since=1h
curl -s '<tempo>/api/search?tags=service.name%3D<svc>'
kubectl -n <ns> logs deploy/otel-collector --tail=100
aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name HTTPCode_Target_5XX_Count --dimensions Name=LoadBalancer,Value=<lb> --start-time <t1> --end-time <t2> --period 300 --statistics Sum
aws logs start-query --log-group-name <g> --start-time <t1> --end-time <t2> --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | limit 50'
aws cloudwatch describe-alarms --state-value ALARM
```

Baseline per service: RED metrics, structured logs with trace ids, traces via OpenTelemetry, dashboards in Grafana, SLO burn-rate alerts.

## Workflow

1. Define the symptom (error rate, latency, user report) and time window.
2. Check golden signals: latency, traffic, errors, saturation.
3. Pull logs/traces for the failing dependency.
4. Correlate deploys / config changes in the window.
5. Propose mitigation + durable fix; document with timestamps.

## Rules

- Redact PII/secrets from log excerpts in chat.
- Do not restart prod services without approval.
- If tooling APIs are unavailable, guide the user through UI queries.
