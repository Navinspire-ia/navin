---
name: observability-agent
description: Query metrics, logs, and traces (Prometheus, Grafana, Loki, Sentry, OpenTelemetry) to diagnose incidents. Use during outages or performance investigations.
metadata: {"navin":{"emoji":"📈","category":"devops"}}
---

# Observability Agent

## Overview

Follow symptoms → signals → cause. Prefer existing dashboards and log queries over random restarts.

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
