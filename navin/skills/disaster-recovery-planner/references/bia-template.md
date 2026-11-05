# Business impact analysis (BIA) template

The BIA fixes, per business process and then per supporting system, how long and how much data loss the organisation can tolerate. It is the input of every DR and BC decision: strategy choice, exercise priorities, cloud architecture. Without a signed BIA an RTO is an opinion. Required by ISO 22301 clause 8.2.2, DORA article 11(2) (ICT business continuity policy based on an analysis of the impact of severe business disruptions), NIS2 article 21(2)(c) through risk management.

## Vocabulary

| Term | Definition | Unit | Typical source |
|---|---|---|---|
| Criticality tier | ranking of the process for the organisation (tier 1 vital, tier 2 important, tier 3 deferrable, tier 4 non-essential) | tier | management decision |
| MTPD (maximum tolerable period of disruption) | the time after which the damage from the loss of the process becomes unacceptable (financial, legal, reputational, safety) | hours or days | business owner, legal, finance |
| RTO (recovery time objective) | the target time to restore the process or system to the agreed level after the disruption is declared; must be shorter than the MTPD, with a margin for detection and decision | minutes or hours | derived from MTPD |
| RPO (recovery point objective) | the maximum acceptable age of the data at the recovery point, that is the data loss the business accepts | minutes or hours | business owner |
| MBCO (minimum business continuity objective) | the minimum level of service or product the organisation must deliver during the disruption (for example 50 % of order volume, payments only, no refunds) | percentage or list of functions | business owner |
| WRT (work recovery time) | time after technical restoration to catch up on the backlog, reconcile data and return to normal operation; MTPD >= RTO + WRT | hours | operations |
| Dependencies | upstream systems, suppliers, people, sites and data without which the process cannot run | list | architecture repository, contracts |
| Peak periods | times when the impact is highest (month end, payroll, sales campaign) | calendar | business owner |

## Strategy by RTO / RPO band

| Strategy | Typical RTO | Typical RPO | Relative cost | How it works | Cloud building blocks |
|---|---|---|---|---|---|
| Backup and restore | 4 h to 48 h | 1 h to 24 h | lowest | data and infrastructure as code are restored in a new environment after the event | AWS Backup with cross-region copy, Azure Backup vault, GCP Backup and DR; Velero for Kubernetes |
| Pilot light | 30 min to 4 h | minutes to 1 h | low | data replicated continuously, core infrastructure present but switched off or minimal; scaled up at failover | Aurora global database or cross-region read replica, AMIs or images replicated, infra provisioned by Terraform at failover; Azure Site Recovery; AWS Elastic Disaster Recovery (continuous block replication, launch on demand) |
| Warm standby | 5 min to 30 min | seconds to minutes | medium | a scaled-down but fully functional copy runs permanently; scale up and switch traffic | multi-region EKS or AKS at reduced capacity, Route 53 failover records with health checks, Route 53 ARC routing controls, Azure Front Door priority routing |
| Multi-site active-active | near zero (seconds) | near zero | highest | all sites serve traffic; failure removes one site from routing | DynamoDB global tables, Spanner, CockroachDB, Cosmos DB multi-region writes; global load balancing; conflict resolution designed in |

An RPO under one minute requires synchronous or near-synchronous replication; a backup-based strategy cannot deliver it. An RTO under 15 minutes requires automation of the switch; a manual approval step alone usually consumes the budget.

## Process-level table

| Process | Owner | Tier | MTPD | RTO | RPO | MBCO | Peak periods | Legal or contractual driver | Manual workaround |
|---|---|---|---|---|---|---|---|---|---|
| Card payment acceptance | Head of payments | 1 | 4 h | 1 h | 5 min | authorisations only, no refunds | Black Friday, 24 December | PSD2 availability, scheme rules, DORA critical function | paper authorisation list for up to 2 h |
| Order fulfilment | COO | 2 | 24 h | 8 h | 1 h | 50 % volume, priority customers | month end | customer SLAs | spreadsheet picking lists |
| Payroll | HR director | 2 | 5 days | 2 days | 24 h | pay last month's amounts | 25th to 30th | labour law | bank file from previous month |
| Marketing analytics | CMO | 4 | 30 days | 10 days | 7 days | none | - | - | - |

## System-level table

Derived from the process table: a system inherits the strictest RTO/RPO of the processes it supports, minus the time the process needs after the system is back (WRT).

| System | Supports processes | Tier | RTO | RPO | Strategy | Secondary site | Dependencies | Owner | Last exercise | Achieved RTO / RPO |
|---|---|---|---|---|---|---|---|---|---|---|
| orders-db (Aurora PostgreSQL) | payments, fulfilment | 1 | 60 min | 5 min | warm standby, Aurora global database | eu-central-1 | KMS multi-region key, IAM | DBA lead | 2026-03-14 | 31 min / 0.7 min |
| payments-api (EKS) | payments | 1 | 60 min | 15 min | warm standby 20 % | eu-central-1 | orders-db, PSP, IdP | platform lead | 2026-03-14 | 58 min / 5 min |
| identity provider | all | 1 | 30 min | 15 min | active-active (vendor) | vendor | break-glass accounts | security lead | 2025-11-02 | 12 min / 0 |
| reporting-warehouse | analytics | 3 | 4 h | 1 h | backup and restore | same region, other account | orders-db exports | data lead | 2026-03-14 | 290 min / 180 min (breach) |

## Dependency questions per system

1. Which identity, DNS, secrets, certificate and monitoring services does the recovery itself depend on, and are they available when the primary site is gone?
2. Which external parties (PSP, SaaS, cloud) are in the path, what RTO do their contracts promise, and has it been tested (DORA art. 28 to 30 for ICT third parties)?
3. Who must be reachable to approve the failover, and who is the alternate?
4. Where is the runbook stored, and is that store reachable without the primary site and without the IdP?
5. What data flows into this system after the recovery point and how is it reconciled (queues, payment notifications, partner files)?

## Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Business owner | | | RTO/RPO/MBCO accepted |
| CIO or CTO | | | strategy and cost accepted |
| Risk or compliance | | | regulatory drivers mapped |
| Management body (DORA art. 5) | | | ICT business continuity policy approved |

Review the BIA yearly and after every major change (new product, migration, new critical supplier). Record the review date; auditors ask for it.
