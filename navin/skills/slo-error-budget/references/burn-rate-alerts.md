# Burn-rate alerts: thresholds, derivations, time to exhaustion

## Definitions

- Target `T` (for example 0.999). Error budget `B = 1 - T` (0.001 = 0.1%).
- Error ratio over a span `E(w) = bad(w) / total(w)`.
- Burn rate over a span `BR(w) = E(w) / B`. Burn 1 consumes exactly the
  budget over the full SLO window; burn 2 consumes it in half the window.
- Budget consumed over the window `C = E(window) / B`; remaining `1 - C`.
- Time to exhaustion at a constant burn `BR`: `window / BR`.

For a 30 day window: `BR = 1` exhausts in 30 days, `BR = 14.4` in 50 hours,
`BR = 6` in 5 days, `BR = 3` in 10 days. For 28 days replace 30 by 28 (the
workbook numbers below assume 30 days; scale `C` proportionally for 28).

## Workbook thresholds (Google SRE Workbook, chapter 5)

| Severity | Long window | Short window | Burn rate | Budget consumed by the long window at that burn | Time to exhaustion (30 d) |
|---|---|---|---:|---:|---:|
| page | 1h | 5m | 14.4 | 2% | 50 h |
| page | 6h | 30m | 6 | 5% | 5 d |
| ticket | 1d | 2h | 3 | 10% | 10 d |
| ticket | 3d | 6h | 1 | 10% | 30 d |

Derivation of the burn factor from the budget share:
`BR = share * window / long_window`. For 2% over 1h on 30 days:
`0.02 * 720h / 1h = 14.4`. For 5% over 6h: `0.05 * 720 / 6 = 6`. For 10% over
1d: `0.10 * 720 / 24 = 3`. For 10% over 3d: `0.10 * 720 / 72 = 1`.

The short window is one twelfth of the long window. It must be above the
threshold as well so the alert resolves as soon as the burn stops, instead of
staying red until the long window drains (detection time stays set by the
long window; reset time is set by the short one).

## Detection time

For a sudden error ratio `E` the long window crosses the threshold after
`long_window * (BR_threshold * B) / E`. Full outage (`E = 1`) with `B = 0.001`:
1h window at 14.4x fires after `60 min * 0.0144 = 0.86 min`; 6h window at 6x
after `360 * 0.006 = 2.2 min`; 1d window at 3x after `1440 * 0.003 = 4.3 min`.
At 1% errors (`E = 0.01`) the 1h page fires after 86 minutes, which is why
the 6h/6x rule exists: it fires after 216 minutes and the 1d/3x ticket after
7.2 hours, catching slow burns the fast rule never reaches.

## Alert expression (PromQL)

```promql
(
  slo:sli_error:ratio_rate1h{sloth_slo="checkout-availability"} > (14.4 * 0.001)
  and
  slo:sli_error:ratio_rate5m{sloth_slo="checkout-availability"} > (14.4 * 0.001)
)
or
(
  slo:sli_error:ratio_rate6h{sloth_slo="checkout-availability"} > (6 * 0.001)
  and
  slo:sli_error:ratio_rate30m{sloth_slo="checkout-availability"} > (6 * 0.001)
)
```

Recording rule behind it, one per window:

```promql
# slo:sli_error:ratio_rate1h
1 - (
  sum(rate(http_requests_total{job="checkout-api",code!~"5.."}[1h]))
  /
  sum(rate(http_requests_total{job="checkout-api"}[1h]))
)
```

Budget remaining over the window:

```promql
1 - (
  (1 - sum(increase(http_requests_total{job="checkout-api",code!~"5.."}[30d]))
       / sum(increase(http_requests_total{job="checkout-api"}[30d])))
  / 0.001
)
```

`scripts/burn_rate_rules.py` emits exactly these shapes. Validate with
`promtool check rules rules.yml` and test with `promtool test rules`.

## Low-traffic services

With fewer than about 1 request per second the 5m window is noisy: one error
in 100 requests is a 10x burn. Options, in order of preference:

1. Lengthen the short windows (5m to 15m, 30m to 1h) and accept slower reset.
2. Aggregate several services or regions into one SLO.
3. Add a minimum-traffic guard: `and sum(rate(total[1h])) > 0.5`.
4. Generate synthetic traffic (blackbox probes) so the denominator is never
   empty.

Never treat an empty denominator as 100% good: PromQL returns no sample and
the alert silently stops evaluating; add an `absent()` alert on the recording
rule.

## Latency SLIs

Good events are requests under the threshold:
`sum(rate(http_request_duration_seconds_bucket{le="0.3"}[1h]))` over
`sum(rate(http_request_duration_seconds_count[1h]))`. Pick a `le` bucket that
exists in the histogram; the SLO threshold cannot fall between buckets. For
native histograms use `histogram_fraction(0, 0.3, rate(...[1h]))`.

## Multi-burn-rate in other tools

| Tool | Where the windows are set |
|---|---|
| Sloth | `alerting.page_alert` and `alerting.ticket_alert` generate the four rules; `disable: true` removes one |
| Pyrra | `ServiceLevelObjective` CRD, `spec.alerting.burnrates: true`, windows derived from `spec.window` |
| Datadog | `POST /api/v1/monitor` type `slo alert`, query `burn_rate("<slo_id>").over("1h").long_window("1h").short_window("5m") > 14.4` |
| Grafana SLO | plugin creates `grafana_slo_*` recording rules and burn-rate alerts with the same 1h/5m, 6h/30m, 1d/2h, 3d/6h pairs |
| Nobl9 | `AlertPolicy` with `conditions[].measurement: averageBurnRate`, `value`, `alertingWindow`, `lastsFor` |
| Honeycomb | Burn alerts with `exhaustion_minutes` (fires when the budget would be gone in N minutes at the current rate) |
