---
name: disaster-recovery-planner
description: Plan, run and evaluate disaster recovery (PRA) and business continuity (PCA) exercises and validate RPO and RTO targets against evidence. Builds the business impact analysis (MTPD, RTO, RPO, MBCO), picks the recovery strategy (backup and restore, pilot light, warm standby, active-active), writes the exercise plan with injects and success criteria, computes achieved RTO and RPO from drill timestamps, and maps evidence to DORA art. 11, 12 and 24 to 26, ISO 22301, NIS2 and ANSSI guides, using AWS Backup, Elastic Disaster Recovery, Route 53 ARC, Aurora Global Database, Azure Site Recovery, Velero and database PITR. Use when the user asks to "plan a DR exercise", "run a failover test", "check our RTO and RPO", "write the business impact analysis", or must prove backups restore within target. In French "preparer un exercice PRA", "tester le plan de continuite", "valider nos RTO et RPO".
metadata: {"navin":{"emoji":"🛟","category":"devops"}}
---

# Disaster Recovery Planner

## Overview

Design, run and assess disaster recovery (PRA, plan de reprise d'activite) and business continuity (PCA, plan de continuite d'activite) exercises, and turn their results into measured RTO and RPO figures, findings with owners, and the evidence pack that DORA, NIS2, ISO 22301 and internal audit ask for. The default posture is read-only and evidence-based: read backup catalogues, replication status, runbooks and incident timelines, compute achieved objectives from recorded timestamps, draft plans and reports. Any action on a target (promoting a replica, triggering a Velero restore, rotating a Route 53 ARC routing control, starting an Elastic Disaster Recovery drill) is written as a proposed command with its rollback and runs only after the user approves it in the exercise window.

## When it applies

- A regulated entity (bank, insurer, payment institution, NIS2 essential or important entity) must show yearly continuity testing with switchover scenarios.
- A team wants to know whether the stated RTO of 1 hour and RPO of 15 minutes are real, or has never restored a backup.
- A DR plan or runbook exists on paper and must be exercised: walkthrough, tabletop, simulation, component or full failover, backup restore test, chaos game day.
- A real incident happened and the after-action review must measure achieved RTO and RPO and feed the plan.
- A cloud architecture decision depends on the RTO/RPO band: pilot light versus warm standby versus active-active, and the cost of each.
- A BIA (business impact analysis) must be written or refreshed before an audit.

Out of scope: live incident command during a real outage (`sre-incident-responder`), backup software operation on its own (`backup-rollback`, `storage-operator`), penetration and threat-led testing (`penetration-tester`).

## Engines and sources

Cloud and Kubernetes commands run through the `infra` tool: level `observe` for every read below, `prepare` to render a change plan or dry run, `execute` only for the approved exercise step. Ticketing, chat and documentation go through the `connectors` tool (Jira, Confluence, Notion, Slack, GitHub, Gmail) when a connector exists. Install a missing CLI with the `toolbelt` skill. Credentials come from the environment (`AWS_PROFILE`, `AZURE_SUBSCRIPTION_ID`, `CLOUDSDK_CORE_PROJECT`, `KUBECONFIG`, `PGPASSWORD`) and are never printed.

| Need | Command (observe) | Exercise step (execute, approval required) |
|---|---|---|
| AWS Backup recovery points | `aws backup list-recovery-points-by-backup-vault --backup-vault-name <vault> --by-resource-arn <arn>`; `aws backup describe-backup-job --backup-job-id <id>` | `aws backup start-restore-job --recovery-point-arn <arn> --metadata file://restore.json --iam-role-arn <role>`; `aws backup start-restore-job` into an isolated account for art. 12(5) |
| AWS Elastic Disaster Recovery | `aws drs describe-source-servers`; `aws drs describe-replication-configuration-templates`; lag in `dataReplicationInfo.lagDuration` | `aws drs start-recovery --source-servers sourceServerID=<id> --is-drill` (drill flag keeps the source untouched) |
| Aurora Global Database | `aws rds describe-global-clusters --global-cluster-identifier <id>` (`GlobalClusterMembers[].IsWriter`), CloudWatch `AuroraGlobalDBReplicationLag` (ms) | `aws rds switchover-global-cluster --global-cluster-identifier <id> --target-db-cluster-identifier <arn>` (planned, zero data loss) or `aws rds failover-global-cluster --allow-data-loss` (unplanned) |
| Route 53 failover and ARC | `aws route53 get-health-check-status --health-check-id <id>`; `aws route53-recovery-cluster get-routing-control-state --routing-control-arn <arn> --region <cluster-region>`; `aws route53-recovery-readiness get-readiness-check-status --readiness-check-name <name>` | `aws route53-recovery-cluster update-routing-control-states --update-routing-control-state-entries '[{"RoutingControlArn":"<primary>","RoutingControlState":"Off"},{"RoutingControlArn":"<secondary>","RoutingControlState":"On"}]'` (call one of the five cluster endpoints) |
| Azure Site Recovery and Backup | `az backup job list --resource-group <rg> --vault-name <vault> --output table`; `az backup recoverypoint list --resource-group <rg> --vault-name <vault> --container-name <c> --item-name <i>`; ASR replication health in `az resource show` on `replicationProtectedItems` or the portal RPO column | `az backup restore restore-disks ...`; ASR test failover from recovery plan (`Start-AzRecoveryServicesAsrTestFailoverJob` in PowerShell, portal otherwise) |
| GCP Backup and DR | `gcloud backup-dr backup-vaults list`; `gcloud backup-dr backups list --backup-vault <vault> --data-source <ds> --location <loc>`; `gcloud sql backups list --instance <inst>` | `gcloud backup-dr backups restore ...`; `gcloud sql backups restore <id> --restore-instance <clone>` into a clone, never the source |
| Kubernetes (Velero) | `velero backup get`; `velero backup describe <name> --details`; `velero schedule get`; `velero backup-location get` | `velero restore create --from-backup <name> --namespace-mappings prod:dr-test --wait`; `velero restore describe <name>` for the warnings count |
| etcd | `kubectl -n kube-system exec etcd-<node> -- etcdctl endpoint status --write-out=table`; `etcdutl snapshot status snapshot.db --write-out=table` | `etcdutl snapshot restore snapshot.db --data-dir /var/lib/etcd-restore` on a rebuilt control plane |
| PostgreSQL | standby lag: `SELECT now() - pg_last_xact_replay_timestamp() AS replay_lag;` on the replica; `SELECT client_addr, write_lag, flush_lag, replay_lag FROM pg_stat_replication;` on the primary; PITR coverage: `aws rds describe-db-instances --query 'DBInstances[].LatestRestorableTime'` | `aws rds restore-db-instance-to-point-in-time --restore-time <ts> --target-db-instance-identifier <clone>`; `pg_basebackup` plus `recovery_target_time` on self-managed |
| MySQL | `SHOW REPLICA STATUS\G` (`Seconds_Behind_Source`, `Replica_SQL_Running`) | `mysqlbinlog --start-datetime ... | mysql` replay on a clone |
| Timeline sources | PagerDuty, Opsgenie or Slack incident channel exports, CloudTrail (`aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=FailoverGlobalCluster`), Jira ticket history via `connectors` | - |

Large exports (CloudTrail, Velero describe with details, backup job lists) are written to files and searched with the `grep` tool before any part is read.

## Workflow

1. **Frame the request.** Determine whether the need is a BIA, an exercise, a post-incident measurement, or an evidence pack for a regulation. Identify the regulatory drivers (DORA article 11(6) yearly test with switchover, art. 25 scenario-based tests on critical functions, NIS2 art. 21(2)(c), ISO 22301 8.5) from `references/dora-iso-mapping.md`; they set the minimum exercise type and cadence.
2. **Build or read the BIA.** Fill `references/bia-template.md`: processes with owner, tier, MTPD, RTO, RPO, MBCO, peak periods, manual workaround; then systems inheriting the strictest objectives, with strategy, secondary site, dependencies, last exercise and achieved figures. Challenge any RTO equal to its MTPD (no detection and decision margin) and any RPO under one minute served by a backup strategy.
3. **Inventory the real capability.** Through `infra observe`, read the backup catalogue (last successful recovery point per system, retention, cross-region or offline copy, immutability), replication status and lag, DNS failover configuration, the secondary site capacity, and the runbooks. Record the current recovery point age per system: it is the RPO achievable today, before any exercise.
4. **Choose the exercise type** with `references/exercise-types.md`: walkthrough or tabletop when the plan was never exercised; backup restore test when backups were never restored; component failover for a measured RTO on one system; full failover when a regulation requires switchover of a critical function. Climb the realism ladder one step per exercise.
5. **Write the exercise plan.** Prepare the scenario JSON (name, type, scope, objectives, systems with targets and owners, injects with offsets, roles, communication, drivers) and run `python3 scripts/exercise_plan.py scenario.json --out plan.md`. The output carries the inject timeline with clock times, the roles table, the success criteria derived from each system's RTO and RPO, abort and rollback criteria, the communication plan and the evidence checklist. Have the exercise director, the incident commander and the business owner sign it.
6. **Prepare the write steps.** For every inject that changes a target, produce the exact command (table above), its precondition check, its rollback command, and the observer's timestamp to record. Run `infra prepare` where a dry run exists (`velero restore create ... --dry-run`, `aws drs start-recovery --is-drill`, ASR test failover). Confirm the restore target is isolated from the source (separate account, subscription or namespace; DORA art. 12(5)).
7. **Run the exercise.** The observer logs UTC timestamps: incident_declared, failover_decided, restore_started, service_restored, service_validated, plus the last consistent recovery point (backup completion time or replica lag read just before the switch). Each write step executes through `infra execute` only after the user confirms the inject in the window. Stop on any abort criterion.
8. **Measure.** Put the timestamps in a drill log JSON and run `python3 scripts/rpo_rto_check.py drill.json` (exit 1 on any breach). Read the phase breakdown: decision latency and validation time are the usual budget consumers, not the restore itself.
9. **Write the after-action report** from `references/after-action-report.md`: objectives met, RTO/RPO table, timeline, findings with severity, root cause, owner and due date, plan and BIA updates, next exercise. Create one ticket per finding through `connectors` when Jira or GitHub is connected.
10. **Assemble the evidence pack** for the regulation rows that apply: signed plan, log, script outputs, screenshots or command outputs, communication drafts, the report, and the management acknowledgement. Deliver the Markdown report below; use `studio-html-report` when the user wants an HTML report.

## Rules

- RTO starts at incident declaration (or detection when the organisation defines it so), not at the start of the restore; RTO ends at business validation, not when the process is up. Both conventions are written in the BIA and used consistently.
- RPO is measured from the last consistent recovery point to the moment of the disruption: backup completion time, or replica lag read before the switch. A backup that is "daily at 02:00" gives an RPO of up to 24 hours, whatever the policy document says.
- Aurora `switchover-global-cluster` is a planned, zero-RPO operation and proves the runbook, not the RPO under failure; the unplanned `failover-global-cluster --allow-data-loss` is the one that measures real data loss.
- `aws drs start-recovery --is-drill` and ASR test failover do not touch the source servers; they prove RTO, not fail-back. Fail-back is a separate step to plan and time.
- A Velero restore completes with status `Completed` even with warnings; read `velero restore describe` warnings and `errors` counts, and validate the application, not the object count.
- Restore into the production account or the source cluster is not a test: DORA art. 12(5) requires a segregated target; it also avoids overwriting the data you are trying to protect.
- Route 53 ARC routing control updates must be sent to the cluster endpoints in their own regions and work when the control plane region is down; the regular `route53` API is not the failover path.
- A tabletop alone does not satisfy DORA art. 11(6): the text requires switchover between primary and redundant infrastructure to be tested. Say so when the only evidence is a tabletop.
- Exercise communications carry the prefix EXERCISE; regulator notifications (DORA art. 19, NIS2 art. 23) are completed as drafts and never sent during an exercise.
- The observer takes no action; the exercise director may stop but not help. Assistance given is recorded as a deviation and usually as a finding.
- Breached targets are reported as breaches with the margin, never rounded into a pass; the fix is a change in the plan, the architecture or the BIA, decided by the owner in the after-action report.
- Keep timestamps in UTC with the source (log line, ticket event) next to them; two clocks that disagree are a finding.

## Deliverable

A Markdown report (HTML with `studio-html-report` on request) with these sections:

**Summary**: exercise type, date, scope, overall result (PASS, PASS WITH FINDINGS, FAIL), regulatory drivers covered.

**BIA extract** (from `references/bia-template.md`):

| System | Tier | MTPD | RTO target | RPO target | MBCO | Strategy | Secondary site | Owner |
|---|---|---|---|---|---|---|---|---|

**Current capability** (observe step):

| System | Last recovery point (UTC) | Age at check | Replica lag | Copy location | Immutable or offline copy | Runbook version |
|---|---|---|---|---|---|---|

**Achieved RTO / RPO** (from `scripts/rpo_rto_check.py`):

| System | Tier | RTO target | RTO achieved | RTO margin | RPO target | RPO achieved | RPO source | Result |
|---|---|---|---|---|---|---|---|---|

**Phase breakdown**:

| System | Decision | Preparation | Restore | Validation |
|---|---|---|---|---|

**Findings**:

| ID | Severity | Finding | Root cause | Corrective action | Owner | Due date | Regulation or clause |
|---|---|---|---|---|---|---|---|

**Regulatory evidence map** (from `references/dora-iso-mapping.md`):

| Requirement | Article or clause | Evidence item | Status |
|---|---|---|---|

**Proposed exercise steps** (write operations, none executed without approval):

| Inject | Command | Precondition | Rollback | Timestamp to record |
|---|---|---|---|---|

Evidence files kept in the workspace: `plan.md`, `drill.json`, the Markdown and JSON outputs of `rpo_rto_check.py`, exported logs and screenshots, communication drafts, `after-action-report.md`.

## Resources

- `scripts/rpo_rto_check.py` - achieved RTO and RPO per system from a drill log JSON, pass or fail with margin, phase breakdown, `--json`, `--warn-margin-pct`; exit 1 on any breach. Test run: `python3 scripts/rpo_rto_check.py examples/drill-log-regional-failover.json` (exits 1: the reporting warehouse breaches both targets).
- `scripts/exercise_plan.py` - complete Markdown exercise plan from a scenario JSON: header, objectives, systems, roles, inject timeline with clock times, timestamps to record, success criteria, abort criteria, communication plan, evidence checklist; `--out`, `--json`. Test run: `python3 scripts/exercise_plan.py examples/scenario-regional-failover.json --out plan.md`.
- `examples/drill-log-regional-failover.json` - drill log of a regional failover with three systems (Aurora global database, EKS warm standby, backup-and-restore warehouse); shows both RPO sources.
- `examples/scenario-regional-failover.json` - full failover scenario with nine injects, roles and regulatory drivers; copy it as the starting point for a new plan.
- `references/exercise-types.md` - type matrix (impact, duration, attendees, cadence, what it proves), choice rules, realism ladder, mandatory scenarios, measurement sources, common findings. Open it at step 4.
- `references/bia-template.md` - vocabulary (MTPD, RTO, RPO, MBCO, WRT), strategy bands with typical RTO/RPO and cloud building blocks, process and system tables, dependency questions, sign-off. Open it at step 2.
- `references/dora-iso-mapping.md` - DORA art. 5, 11, 12, 14, 17 to 19, 24 to 30 and RTS 2024/1774, NIS2 art. 21 and 23, ISO 22301 clauses 8 to 10, ISO/IEC 27031, ANSSI and EBA guides, with the evidence to produce for each. Open it at steps 1 and 10.
- `references/after-action-report.md` - report template with findings severity scale, timeline, business impact, plan updates, evidence index and distribution rules. Open it at step 9.
