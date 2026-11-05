# After-action report (AAR) template

Write the report within 10 working days of the exercise, while the log is fresh. One report per exercise; one finding per row; every finding has an owner and a due date. Keep the tone factual: what was planned, what happened, what the gap is.

---

# After-action report: <exercise name>

| Field | Value |
|---|---|
| Exercise type | <walkthrough, tabletop, simulation, component failover, full failover, backup restore, chaos game day> |
| Date and window | <YYYY-MM-DD HH:MM to HH:MM UTC> |
| Environment | <production, pre-production, isolated restore account> |
| Exercise director | <name> |
| Incident commander | <name> |
| Participants | <count> (list in appendix A) |
| Regulatory drivers | <DORA art. 11(6), art. 25; ISO 22301 8.5; NIS2 art. 21(2)(c)> |
| Overall result | <PASS / PASS WITH FINDINGS / FAIL> |
| Report author and date | <name, YYYY-MM-DD> |
| Approved by | <management sponsor, date> |

## 1. Executive summary

Three to five sentences: scenario, what was proved, the main gap, the decision requested from management (budget, priority, policy change).

## 2. Objectives and outcome

| # | Objective | Met | Evidence |
|---|---|---|---|
| 1 | <objective from the plan> | yes / partly / no | <log line, screenshot id, script output> |

## 3. RTO / RPO results

Paste the table produced by `rpo_rto_check.py`:

| System | Tier | RTO target | RTO achieved | RTO margin | RPO target | RPO achieved | RPO source | Result |
|---|---|---|---|---|---|---|---|---|

Phase breakdown (decision, preparation, restore, validation) per system, from the same script. Comment on where the time went: decision latency, waiting for an approver, slow restore, long validation.

## 4. Timeline

| UTC | T+ (min) | Event | Planned (inject) or observed | Actor | Note |
|---|---|---|---|---|---|
| 09:00 | 0 | Alert received | inject 1 | monitoring | |
| 09:04 | 4 | SEV1 declared | observed | on-call | incident_declared |

Mark the five measurement timestamps explicitly in the Note column: incident_declared, failover_decided, restore_started, service_restored, service_validated.

## 5. What worked

- <fact, with the evidence reference>

## 6. Findings

Severity: **critical** (the RTO or RPO of a tier 1 system would be breached in a real event, or the plan could not be executed), **major** (a step failed and needed improvisation, or a control required by regulation is missing), **minor** (inefficiency, documentation gap).

| ID | Severity | Finding | Impact | Root cause | Corrective action | Owner | Due date | Status | Regulation or clause |
|---|---|---|---|---|---|---|---|---|---|
| F-01 | critical | Failover decision took 38 min; no designated approver on the on-call rota | RTO budget consumed before any technical action | decision authority not delegated | add the failover decision to the incident commander mandate; publish approver rota | Head of operations | <date> | open | DORA art. 11(2), ISO 22301 8.4 |
| F-02 | major | KMS key for orders-db is single-region; standby could not decrypt until a key was replicated by hand | restore delayed 14 min | key created before the multi-region policy | migrate to a multi-region key; add check to the quarterly review | DBA lead | <date> | open | DORA art. 12(3) |
| F-03 | minor | Runbook step 7 referenced a decommissioned hostname | 3 min lost | runbook not updated after migration | fix runbook; add runbook review to change checklist | Platform lead | <date> | closed | ISO 22301 8.6 |

## 7. Deviations from the plan

| Planned | What happened | Why | Consequence |
|---|---|---|---|

Include any abort, pause, assistance given by the exercise director, or inject skipped.

## 8. Business impact (full failover and component failover only)

| Metric | Value | Source |
|---|---|---|
| Customer-facing error rate during the switch | <%> | dashboard export |
| Transactions reconciled after fail-back | <count, amount> | reconciliation report |
| Support tickets attributable to the exercise | <count> | service desk |
| Cost of the exercise (people hours, cloud) | <value> | finance |

## 9. Plan, runbook and BIA updates

| Document | Change | Owner | Done |
|---|---|---|---|
| DR plan v<N> | <section, change> | | |
| Runbook <name> | | | |
| BIA system table | achieved RTO/RPO and last exercise date updated | | |

## 10. Next exercise

| Field | Value |
|---|---|
| Proposed type | <next step on the realism ladder> |
| Scenario | |
| Target quarter | |
| Prerequisites | <findings that must be closed first> |

## Appendix A: participants

| Name | Role in exercise | Team | Present |
|---|---|---|---|

## Appendix B: evidence index

| ID | Type | Location | Timestamp | Description |
|---|---|---|---|---|
| E-01 | log | <path or ticket> | | observer log |
| E-02 | screenshot | | | failover command output |
| E-03 | script output | | | rpo_rto_check.py Markdown and JSON |
| E-04 | dashboard export | | | error rate and replication lag |
| E-05 | communication draft | | | status page and regulator notification drafts |

---

## Distribution and follow-up

- Send the report to the management sponsor, risk or compliance, internal audit, and every finding owner. Through the `connectors` tool when Jira, Confluence, Notion or Slack connectors exist: one Jira issue per finding with the AAR id in the summary, the report page in the DR space, a summary in the DR channel.
- Review open findings monthly until closed; the closure evidence goes into the next report.
- The report is itself evidence for DORA art. 11(6) and 11(7), ISO 22301 8.5 and 10. Keep it for at least five years or the retention period the supervisor requires.
