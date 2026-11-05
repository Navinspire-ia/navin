# SLO review template and error budget policy

## Error budget policy (fill the blanks and get it signed by product and engineering)

| Budget remaining (rolling window) | State | Mandatory actions | Who decides exceptions |
|---|---|---|---|
| above 50% | green | normal release cadence; reliability work from the backlog as planned | team lead |
| 25% to 50% | yellow | every release needs a rollback plan and canary; one engineer on reliability items from the last review | team lead |
| 0% to 25% | orange | feature freeze on the affected service except fixes that lower risk; postmortems within 5 working days for every incident of the window; weekly review | engineering manager with product |
| exhausted (0% or below) | red | full freeze until the budget is positive again or 2 weeks have passed; all hands on reliability items; executive update | director with product |
| exhausted twice in 90 days | escalated | SLO or architecture review: either the target is wrong or the system is; decision recorded | director |

Additional clauses:

- A page-level burn alert (14.4x over 1h or 6x over 6h) counts as an incident
  and requires an incident commander even when the budget is still green.
- Budget consumed by a planned maintenance window counts unless a status
  correction was declared before the window started.
- Budget consumed by a dependency's outage counts; the postmortem assigns the
  action to the owning team or to this team (add a fallback).
- Changing the target or the SLI definition is a policy change: it requires
  the same signatures as the policy and takes effect at the next calendar
  window, never retroactively.

## Monthly SLO review agenda (45 minutes)

1. Status board (5 min): one row per SLO with SLI, budget remaining, trend
   against last month, alerts fired (page and ticket counts).
2. Budget consumers (15 min): top incidents and their share of the budget;
   slow burns without an incident (deploy regressions, dependency
   degradation, traffic mix changes).
3. Alert quality (10 min): pages that were not actionable, burns that were
   missed, time to detect versus the expected detection time for each rule.
4. SLO health (10 min): is the SLI still measuring what users feel? traffic
   below the noise floor? new critical journeys without an SLO? targets
   consistently far above or below reality?
5. Decisions (5 min): policy state per SLO, approved exceptions, owners and
   dates for every action.

## Report tables

Status board:

| SLO | Service | Target | Window | SLI | Budget remaining | Trend | Pages | Tickets | State |
|---|---|---:|---|---:|---:|---|---:|---:|---|

Budget consumers:

| Incident or period | Start (UTC) | Duration | Bad events | Budget share | Root cause | Postmortem | Action owner |
|---|---|---:|---:|---:|---|---|---|

Alert quality:

| Alert | Fired | Actionable | Detection time (observed) | Detection time (expected) | Change proposed |
|---|---:|---:|---:|---:|---|

Decisions:

| # | Decision | SLO | Owner | Due | Needs approval |
|---|---|---|---|---|---|

## Writing the SLI as good/total

| Type | Good event | Total event | Source |
|---|---|---|---|
| availability | request with status not 5xx (and not timed out at the load balancer) | every request | `http_requests_total`, ALB `HTTPCode_Target_5XX_Count` versus `RequestCount` |
| latency | request served under the threshold | every request (or every successful request, state which) | histogram `_bucket{le=...}` versus `_count` |
| correctness | job output passed its validation, response matched the golden answer | every checked output | data quality assertions, canary comparisons |
| freshness | dataset or feed updated within its cadence at the time of the check | every check (one per minute) | `time() - last_update_timestamp < cadence` evaluated per minute |
| throughput | minute where processed rate met the committed rate | every minute | `rate(events_processed_total[1m]) >= committed` |
| durability | object readable with the right checksum | every object or sample verified | background scrubber counters |

State the exclusions explicitly (health checks, synthetic traffic, client
errors 4xx, requests from internal tools) and keep them in the PromQL
selector so the dashboard and the alert agree.

## Choosing target and window

- Start from measured reality: compute the SLI over the last 30 or 90 days
  with `scripts/error_budget.py` and pick a target slightly below the
  observed level, then tighten after two review cycles.
- Rolling 28 days keeps the same number of weekdays in every window; rolling
  30 days matches monthly reporting; calendar months make the budget reset
  visible to product and are easier to explain but allow a bad first week
  to freeze the whole month.
- Each additional 9 in the target divides the budget by 10: 99.9% on 30
  days is 43.2 minutes of full outage; 99.95% is 21.6 minutes; 99.99% is
  4.3 minutes, below the detection plus response time of most teams.
