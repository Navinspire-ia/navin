# OpenSLO v1 and Sloth

## OpenSLO v1

Spec: `https://github.com/OpenSLO/OpenSLO`, `apiVersion: openslo/v1`. Kinds:
`DataSource`, `SLI`, `SLO`, `AlertPolicy`, `AlertCondition`,
`AlertNotificationTarget`, `Service`.

### SLO

```yaml
apiVersion: openslo/v1
kind: SLO
metadata:
  name: checkout-availability
  displayName: Checkout API availability
  labels:
    team: payments
spec:
  description: Successful (non-5xx) checkout requests
  service: checkout-api
  indicator:
    metadata:
      name: checkout-success-ratio
    spec:
      ratioMetric:
        counter: true
        good:
          metricSource:
            type: Prometheus
            spec:
              query: sum(rate(http_requests_total{job="checkout-api",code!~"5.."}[{{.window}}]))
        total:
          metricSource:
            type: Prometheus
            spec:
              query: sum(rate(http_requests_total{job="checkout-api"}[{{.window}}]))
  timeWindow:
    - duration: 30d
      isRolling: true
  budgetingMethod: Occurrences
  objectives:
    - displayName: 99.9 percent
      target: 0.999
  alertPolicies:
    - alertPolicyRef: fast-burn
    - alertPolicyRef: slow-burn
```

Field notes:

| Field | Values | Notes |
|---|---|---|
| `spec.service` | string | must match a `Service` kind name when one exists |
| `spec.indicator` or `spec.indicatorRef` | inline SLI or name of a `kind: SLI` | one of the two |
| `indicator.spec.ratioMetric` | `good` + `total`, or `bad` + `total`; `counter: true` when the metric is a counter; optional `rawType: success|failure` with `raw` | good/total events |
| `indicator.spec.thresholdMetric` | `metricSource` returning a value compared to `objectives[].op` and `value` | used with `objectives[].op: lte|gte|lt|gt` and `objectives[].value` |
| `timeWindow[].duration` | `30d`, `28d`, `7d`, `1h` | exactly one entry |
| `timeWindow[].isRolling` | `true` or `false` | `false` needs `calendar.startTime` (`2026-01-01 00:00:00`) and `calendar.timeZone` (`Europe/Paris`) |
| `budgetingMethod` | `Occurrences`, `Timeslices`, `RatioTimeslices` | Timeslices needs `objectives[].timeSliceTarget` (0..1) and `timeSliceWindow` (`1m`) |
| `objectives[].target` | 0..1 | `0.999`, not `99.9` |
| `alertPolicies[]` | inline `AlertPolicy` or `alertPolicyRef` | |

### AlertPolicy and AlertCondition

```yaml
apiVersion: openslo/v1
kind: AlertPolicy
metadata:
  name: fast-burn
spec:
  alertWhenBreaching: true
  alertWhenResolved: true
  conditions:
    - conditionRef: burn-14x-1h
  notificationTargets:
    - targetRef: pagerduty-payments
---
apiVersion: openslo/v1
kind: AlertCondition
metadata:
  name: burn-14x-1h
spec:
  severity: page
  condition:
    kind: burnrate
    op: gt
    threshold: 14.4
    lookbackWindow: 1h
    alertAfter: 5m
```

`AlertNotificationTarget` has `spec.target` (`pagerduty`, `slack`, `email`,
`webhook`) and `spec.description`. Converters: `oslo validate -f slo.yaml`
(the reference CLI), `sloth generate` accepts OpenSLO with
`--plugins` for metric sources, Nobl9 imports OpenSLO through `sloctl`.

## Sloth

Sloth generates Prometheus recording and alert rules from its own spec or
from OpenSLO, with the four workbook windows built in.

```yaml
# slo.yml (Sloth spec prometheus/v1)
version: prometheus/v1
service: checkout-api
labels:
  team: payments
slos:
  - name: availability
    objective: 99.9
    description: Non-5xx checkout requests
    labels:
      tier: "1"
    sli:
      events:
        error_query: sum(rate(http_requests_total{job="checkout-api",code=~"5.."}[{{.window}}]))
        total_query: sum(rate(http_requests_total{job="checkout-api"}[{{.window}}]))
    alerting:
      name: CheckoutAvailabilityBurn
      labels:
        category: availability
      annotations:
        runbook: https://runbooks.example.internal/slo/checkout-availability
      page_alert:
        labels:
          severity: page
      ticket_alert:
        labels:
          severity: ticket
  - name: latency
    objective: 99
    sli:
      events:
        error_query: |
          sum(rate(http_request_duration_seconds_count{job="checkout-api"}[{{.window}}]))
          - sum(rate(http_request_duration_seconds_bucket{job="checkout-api",le="0.3"}[{{.window}}]))
        total_query: sum(rate(http_request_duration_seconds_count{job="checkout-api"}[{{.window}}]))
    alerting:
      name: CheckoutLatencyBurn
      page_alert:
        disable: false
      ticket_alert:
        disable: true
```

Commands:

```bash
sloth validate -i slo.yml
sloth generate -i slo.yml -o rules.yml
sloth generate -i slo.yml -o rules.yml --slo-period-windows-path ./windows   # custom window set
sloth generate -i slo.yml -o rules.yml --default-slo-period 28d
promtool check rules rules.yml
```

Generated series:

| Series | Meaning |
|---|---|
| `slo:sli_error:ratio_rate5m`, `30m`, `1h`, `2h`, `6h`, `1d`, `3d`, `30d` | error ratio per window, labels `sloth_service`, `sloth_slo`, `sloth_id`, `sloth_window` |
| `slo:objective:ratio` | target as 0..1 |
| `slo:error_budget:ratio` | `1 - objective` |
| `slo:time_period:days` | window length |
| `slo:current_burn_rate:ratio` | `sli_error:ratio_rate5m / error_budget:ratio` |
| `slo:period_burn_rate:ratio` | `sli_error:ratio_rate30d / error_budget:ratio` |
| `slo:period_error_budget_remaining:ratio` | `1 - period_burn_rate` |
| `sloth_slo_info` | metadata: `sloth_version`, `sloth_mode`, `sloth_spec`, `sloth_objective` |

Sloth's Kubernetes mode watches `PrometheusServiceLevel` CRDs
(`sloth.slok.dev/v1`) and writes `PrometheusRule` objects for the Prometheus
Operator; same spec under `spec.service` and `spec.slos`.

## Pyrra

CRD `pyrra.dev/v1alpha1`, `kind: ServiceLevelObjective`:

```yaml
spec:
  target: "99.9"
  window: 4w
  indicator:
    ratio:
      errors:
        metric: http_requests_total{job="checkout-api",code=~"5.."}
      total:
        metric: http_requests_total{job="checkout-api"}
  alerting:
    burnrates: true
    absent: true
```

`indicator.latency` with `success.metric` (`..._bucket{le="0.3"}`) and
`total.metric`, or `indicator.boolGauge`. Pyrra computes its windows from
`spec.window` (for 4w: 1h/5m 14x, 6h/30m 7x, 1d/2h 2x, 4d/6h 1x).

## Nobl9

`sloctl apply -f slo.yaml` with `apiVersion: n9/v1alpha`, `kind: SLO`,
`spec.budgetingMethod: Occurrences|Timeslices`, `spec.objectives[].target`,
`spec.timeWindows[].unit: Day, count: 30, isRolling: true`,
`spec.indicator.metricSource.name`; Nobl9 also exposes `GET
/api/slo-status/v1/slos` with `budget.remaining`, `budget.burnRate`.

## Datadog SLOs API

- `POST /api/v1/slo` with `type: metric` (`query.numerator`,
  `query.denominator`), `type: monitor` (`monitor_ids`), or `type: time_slice`;
  `thresholds[] {timeframe: 7d|30d|90d, target: 99.9, warning: 99.95}`.
- `GET /api/v1/slo/{slo_id}/history?from_ts=&to_ts=` returns `data.overall.sli_value`,
  `data.overall.error_budget_remaining.<timeframe>`, `data.series`.
- `GET /api/v1/slo?query=&tags_query=`, `GET /api/v1/slo/{id}/corrections` for
  status corrections (maintenance windows).
- Burn-rate monitor: `type: slo alert`, query
  `burn_rate("<slo_id>").over("30d").long_window("1h").short_window("5m") > 14.4`.

Use the Datadog connector through the `connectors` tool rather than raw HTTP
when it is configured; `DD_API_KEY` and `DD_APP_KEY` stay in the environment.

## Grafana SLO

Grafana Cloud SLO API: `GET /api/plugins/grafana-slo-app/resources/v1/slo`,
`POST` with `{"name", "description", "query": {"type": "ratio", "ratio":
{"successMetric": "...", "totalMetric": "...", "groupByLabels": []}},
"objectives": [{"value": 0.999, "window": "30d"}], "alerting": {"fastBurn":
{...}, "slowBurn": {...}}}`. Recording rules land as `grafana_slo_sli_*`,
`grafana_slo_error_budget_remaining`.
