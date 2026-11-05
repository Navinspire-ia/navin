---
name: slo-error-budget
description: Define, compute and review service level indicators, SLO targets and error budgets. Expresses availability, latency, correctness, freshness and throughput SLIs as good over total events, computes budget consumed and remaining over rolling or calendar windows, burn rates over 1h, 6h, 1d and 3d, and evaluates the Google SRE multi-window burn-rate alerts. Generates Prometheus recording and alert rules, reads and writes OpenSLO v1 and Sloth specs, maps to Pyrra, Nobl9, Datadog SLOs and Grafana SLO, and produces error budget policies and SLO review reports. Use when the user asks to "define an SLO for this service", "how much error budget is left", "set up burn rate alerts", "generate Prometheus rules for my SLO", "convert this to OpenSLO", or when a reliability review or release freeze decision needs numbers. In French "calculer le budget d'erreur", "definir des SLO", "alertes de burn rate".
metadata: {"navin":{"emoji":"🎯","category":"devops"}}
---

# SLO and Error Budget

## Overview

This skill turns raw good and bad event counts into service level indicators,
error budgets, burn rates and alert decisions, and writes the artifacts that
make them operational: Prometheus rules, OpenSLO or Sloth specs, an error
budget policy and a review report. The default posture is read-only and
evidence-based: query the metrics, compute from exported buckets, show the
formulas and the series used, and never load alert rules, change an SLO
definition or a monitor on a target system without the user's explicit
approval.

## When it applies

- A team needs its first SLOs for a service, or wants to check whether an
  existing target matches reality.
- An on-call engineer or a release manager asks how much error budget is
  left and whether the current incident is burning it fast.
- Alerting must move from static thresholds to burn-rate alerts, or existing
  burn-rate alerts page too much or too late.
- SLO definitions must be expressed in OpenSLO, Sloth, Pyrra, Nobl9, Datadog
  or Grafana SLO, or migrated between them.
- A monthly SLO review or an error budget policy needs a report with the
  numbers and the decisions.

Hand over to `observability-agent` for the incident investigation itself and
to `sre-incident-responder` for incident command.

## Engines and sources

### Formulas

- SLI as a ratio: `good / total` over the window. Each SLI type is defined by
  what counts as good and what counts as total; the table in
  `references/slo-review-template.md` gives the standard choices for
  availability, latency, correctness, freshness and throughput.
- Error budget `B = 1 - target`. Budget consumed `C = (1 - SLI) / B`; remaining
  `1 - C`. For 99.9% over 30 days the budget is 43.2 minutes of full outage.
- Burn rate over a span `BR(w) = error_ratio(w) / B`. Time to exhaustion at a
  constant burn is `window / BR`.
- Multi-window alerts (Google SRE workbook): page at 14.4x over 1h with a 5m
  short window (2% of the budget in one hour, exhaustion in 50 hours), page
  at 6x over 6h with 30m (5%, 5 days), ticket at 3x over 1d with 2h (10%,
  10 days), ticket at 1x over 3d with 6h (10%, 30 days). Both windows must
  exceed the threshold. Derivations, detection times and low-traffic
  adjustments: `references/burn-rate-alerts.md`.

### Prometheus

- Recording rules `slo:sli_error:ratio_rate<window>` for 5m, 30m, 1h, 2h,
  6h, 1d, 3d, built as
  `1 - sum(rate(good[w])) / sum(rate(total[w]))`; `slo:error_budget:remaining`
  from `increase(...[30d])`.
- Alert rules compare the long and short window series to
  `factor * budget`. Validate with `promtool check rules` and
  `promtool test rules`.
- Latency SLIs use `http_request_duration_seconds_bucket{le="0.3"}` over
  `_count`; the threshold must be an existing bucket boundary.
- Historical buckets for offline computation come from
  `GET /api/v1/query_range?query=<good or total increase>&start=&end=&step=300`
  on Prometheus, Thanos or Mimir, or from Grafana through the `connectors`
  tool.

### Spec and platform formats

- OpenSLO v1: `apiVersion: openslo/v1`, `kind: SLO`, `spec.service`,
  `spec.indicator.spec.ratioMetric.good|total` or `thresholdMetric`,
  `spec.objectives[].target` (0..1), `spec.timeWindow[].duration` and
  `isRolling`, `spec.budgetingMethod`, `spec.alertPolicies`.
- Sloth: `version: prometheus/v1`, `slos[].objective` in percent,
  `sli.events.error_query` and `total_query` with `{{.window}}`,
  `alerting.page_alert` and `ticket_alert`; `sloth validate -i slo.yml`,
  `sloth generate -i slo.yml -o rules.yml`.
- Pyrra `ServiceLevelObjective` CRD, Nobl9 `sloctl`, Datadog
  `POST /api/v1/slo` and `GET /api/v1/slo/{id}/history`, Grafana SLO plugin
  API. Field tables and examples: `references/openslo-sloth.md`.

### Navin integration

- Prometheus, Alertmanager, Sloth and Pyrra running in Kubernetes are read
  through the `infra` tool at observe level (`kubectl get prometheusrule`,
  `kubectl get servicelevelobjectives`); applying generated rules is a
  prepare then execute operation that waits for approval.
- Datadog and Grafana SLOs, Slack delivery of the report, GitHub pull request
  with the rules: use the `connectors` tool.
- `DD_API_KEY`, `DD_APP_KEY`, `GRAFANA_TOKEN`, `NOBL9_CLIENT_SECRET` stay in
  environment variables; report presence only.
- `promtool`, `sloth`, `oslo` missing: install with the `toolbelt` skill.
- Large `query_range` exports and rule files are searched with the `grep`
  tool before being read.

## Workflow

1. Define the SLI with the user: service, user journey, good event, total
   event, exclusions (health checks, 4xx, synthetic traffic). Write the two
   PromQL selectors (or the equivalent query in the user's platform) and
   confirm they exist with a `query` call at observe level.
2. Export history: run `query_range` for `increase(good[5m])` and
   `increase(total[5m])` over at least the SLO window (30 days at a 5m step
   is 8640 buckets) and write `timestamp,good,total` CSV. Thirty days of
   history are required for the budget figure to be complete; with less,
   the script marks the result as partial.
3. Compute the current state:
   `python3 scripts/error_budget.py examples/sli_buckets.csv --target 99.9 --window-days 30`
   (replace the sample by the export). The output gives SLI, bad versus
   allowed events, budget consumed and remaining, burn rates over 1h, 6h,
   1d and 3d, and the four alert evaluations with the budget share and time
   to exhaustion each one implies. Exit 1 means the budget is exhausted or a
   page-level burn is active; treat it as a finding and continue.
   Add `--now <iso>` to evaluate a past moment (what the alerts would have
   said during an incident) and `--json` to feed a dashboard.
4. Choose target and window from the measured SLI: a target just below the
   observed level for the first cycle, rolling 28 or 30 days unless the
   organisation reports by calendar month. Record the budget in minutes so
   product understands it.
5. Generate the rules:
   `python3 scripts/burn_rate_rules.py --spec examples/slo_spec.json --out rules.yml`
   or pass `--name`, `--target`, `--good`, `--total`, `--service`,
   `--labels`. Validate with `promtool check rules rules.yml`. When the team
   runs Sloth or Pyrra, write the spec from `references/openslo-sloth.md`
   instead and let the tool generate; keep one source of truth.
6. Tune for traffic: if `total` over 5m is below about 300 events, widen the
   short windows or add a minimum-traffic guard as described in
   `references/burn-rate-alerts.md`; add an `absent()` alert on the recording
   rule.
7. Write the error budget policy from the template (thresholds at 50%, 25%,
   0%, consequences, who decides) and agree it with product; the policy is
   what makes the budget actionable.
8. Review: for a monthly review, run step 3 for every SLO, fill the status
   board, list the incidents with their budget share (bad events of the
   incident divided by allowed bad events), check alert quality against the
   expected detection times, and record decisions with owners.
9. Deliver the report and the artifacts (rules file, spec, policy) and list
   every change to a live system as an approval item with the exact command
   (`kubectl apply -f rules.yml`, `POST /api/v1/slo`, Sloth CRD).

## Rules

- Targets are written as percent in Sloth (`99.9`) and as ratio in OpenSLO
  (`0.999`); converting between them is the most common mistake.
- Both windows of a burn-rate alert must exceed the threshold; alerting on
  the long window alone stays red for up to the full window after recovery.
- The workbook factors assume a 30 day window: for 28 days the same factors
  mean 2.14% and 5.36% of budget instead of 2% and 5%; state the assumption.
- An empty denominator yields no sample in PromQL: the alert silently stops.
  Always pair burn-rate alerts with an `absent()` alert.
- Latency thresholds must coincide with a histogram bucket boundary; a
  threshold of 250 ms with buckets at 100 and 300 ms is not measurable.
- Freshness and throughput SLIs are time-slice based (one check per minute);
  say so, because a minute of total silence counts as one bad event, not as
  zero events.
- `increase()` and `rate()` extrapolate at series boundaries; a counter
  reset inside the window inflates errors for one scrape. Use recording
  rules, not ad hoc queries, for the official numbers.
- Budget figures from `error_budget.py` are exact over the exported buckets;
  Prometheus figures differ slightly because of extrapolation. Report which
  one is quoted.
- Status corrections (maintenance windows) only apply when declared before
  the window; never remove incident minutes from the budget after the fact
  without a policy clause allowing it.
- Never apply rules, create SLO objects or modify monitors without approval;
  prepare the artifact, show the diff, wait.

## Deliverable

Produce a Markdown report with:

1. SLO definition: `| SLO | Service | SLI type | Good events | Total events | Target | Window | Budget (minutes) |`.
2. Current state: `| Metric | Value |` for SLI, bad versus allowed events,
   budget consumed and remaining, evaluation time.
3. Burn rates: `| Window | Burn rate | Threshold | Alert | Active | Time to exhaustion at threshold |`.
4. Alert rules: the generated YAML, with the `promtool check rules` result.
5. Error budget policy: `| Budget remaining | State | Mandatory actions | Who decides |`.
6. Review tables when applicable: status board, budget consumers, alert
   quality, decisions, as laid out in `references/slo-review-template.md`.
7. Proposed changes: `| # | Change | Target system | Command or API call | Rollback | Needs approval |`.

When the user wants an HTML report, render the same content with the
`studio-html-report` skill. Post to Slack, Notion or a GitHub pull request
through the `connectors` tool when asked.

## Resources

- `scripts/error_budget.py`: CSV or JSON buckets (`timestamp,good,total`)
  plus `--target` and `--window-days` to SLI, budget consumed and remaining,
  burn rates over 1h, 6h, 1d, 3d, and the four multi-window alert
  evaluations with time to exhaustion; `--now`, `--json`; exit 1 when the
  budget is exhausted or a page alert is active. Steps 3 and 8.
- `scripts/burn_rate_rules.py`: SLO name, target, good and total PromQL
  selectors (flags or `--spec` JSON) to Prometheus recording rules
  `slo:sli_error:ratio_rate<w>`, `slo:error_budget:remaining` and the four
  burn-rate alerts, as YAML on stdout or `--out`; `--json` prints a summary.
  Step 5.
- `examples/sli_buckets.csv`: 30 days of 5 minute buckets for a 99.9%
  service with one past incident and an ongoing fast burn, for testing the
  budget script.
- `examples/slo_spec.json`: the `--spec` input format for the rules script
  (name, service, target, window_days, good, total, labels).
- `references/burn-rate-alerts.md`: thresholds, derivation of the factors,
  detection times, PromQL shapes, low-traffic adjustments, and how Sloth,
  Pyrra, Datadog, Grafana, Nobl9 and Honeycomb express the same alerts. Open
  it in steps 5 and 6.
- `references/openslo-sloth.md`: OpenSLO v1 fields with a full SLO and
  AlertPolicy, Sloth spec and generated series, Pyrra CRD, Nobl9, Datadog
  SLOs API and Grafana SLO. Open it when writing or converting a spec.
- `references/slo-review-template.md`: error budget policy table, monthly
  review agenda, report tables, SLI type to good/total mapping, and guidance
  on target and window choice. Open it in steps 1, 4, 7 and 8.
