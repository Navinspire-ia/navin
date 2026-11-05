# Exercise types for DR (PRA) and BC (PCA) plans

PRA (plan de reprise d'activite, disaster recovery plan) covers the technical restoration of IT services after a disruption: failover, restore, rebuild. PCA (plan de continuite d'activite, business continuity plan) covers how the business keeps delivering its critical activities during the disruption, with or without IT: degraded procedures, relocation, manual workarounds, communication. A DR exercise proves RTO and RPO; a BC exercise proves the MBCO (minimum business continuity objective) can be delivered. ISO 22301 clause 8.5 and DORA article 25 both require a programme that covers both, with increasing realism over time.

## Type matrix

| Type | What happens | Production impact | Duration | Who attends | Cadence | Proves |
|---|---|---|---|---|---|---|
| Plan walkthrough | the owners read the plan step by step and check that contacts, accesses, runbooks and dependencies are still right | none | 1 to 2 h | plan owner, system owners, one observer | after every significant change, at least yearly | the plan is current and understandable |
| Tabletop | a facilitator narrates a scenario and releases injects; participants describe what they would do and decide | none | 2 to 4 h | crisis management team, incident commander, communications, business leads, legal, DPO when personal data is involved | twice a year; one per critical scenario (regional loss, ransomware, key supplier failure, loss of premises) | decision chain, roles, escalation thresholds, communication content |
| Simulation | technical teams execute the runbooks against a non-production or isolated copy (restore to a sandbox account, rebuild a cluster from code) | none on production | half a day | technical leads, observer, scribe | yearly per critical system | runbooks work, timing of each step, missing accesses |
| Component failover | one component is really failed over in production (database writer promotion, one availability zone drained, one region of a multi-region service) | limited, planned window | 1 to 3 h | on-call engineers, system owner, incident commander, observer | quarterly for tier 1 components | achieved RTO and RPO for that component, monitoring and alerting during failover |
| Full failover | production traffic is moved to the secondary site or region and run there for a defined period, then failed back | real; customers may notice | 4 h to several days | full incident organisation, business validators, communications, service desk, management sponsor | yearly for critical or important functions (DORA art. 25 expects advanced testing on them) | end-to-end RTO/RPO, capacity of the secondary site, data reconciliation, fail-back |
| Backup restore test | a backup is restored to an isolated target and the data is validated by the business (row counts, checksums, sample transactions) | none | 1 to 4 h per system | DBA or storage engineer, data owner, observer | quarterly per backup policy; every system at least yearly; after each change of backup tooling | backups are restorable, restore duration, recovery point actually available, immutability and offline copy |
| Chaos game day | faults are injected under controlled conditions (AWS FIS, Chaos Mesh, Litmus, Gremlin) to observe automatic recovery | controlled, with a stop button | 2 to 4 h | engineers of the service, SRE, observer | quarterly | self-healing, alert quality, blast radius, assumptions about dependencies |

## Choosing the type

- No exercise in the last 12 months, or a plan rewritten: walkthrough, then tabletop.
- Tabletop done, runbooks never executed: simulation on a copy, then component failover.
- RTO/RPO never measured on production: component failover with timestamps recorded, then full failover.
- Backups exist but were never restored: backup restore test first; it is the cheapest exercise with the highest finding rate.
- Regulated entity (DORA, NIS2 essential entity): yearly full failover or equivalent advanced test on each critical function, documented with evidence; the tabletop alone does not meet article 25.

## Realism ladder

Each level adds a condition that the previous one assumed away:

1. Announced, daytime, all experts available.
2. Announced date, unannounced scenario.
3. Key person unavailable (the primary DBA is "on holiday"; the runbook must work for the backup).
4. Out of hours, on-call only.
5. Unannounced to the responders (announced to management and the service desk).
6. Combined scenario: regional loss plus a supplier outage, or ransomware with backups encrypted and a restore from the immutable or offline copy.

## Scenarios that must appear in the programme

| Scenario | Exercise type suited | Specific points to test |
|---|---|---|
| Loss of the primary region or data centre | full failover, simulation | DNS switch, data promotion, capacity of the standby, identity provider reachable from the secondary site |
| Ransomware | tabletop plus backup restore from immutable copy | detection to isolation time, clean-room rebuild, backup catalogue integrity, decision on paying (never), regulator and law-enforcement notification |
| Loss of a critical ICT third party (SaaS, payment provider, cloud) | tabletop, simulation of degraded mode | DORA art. 28 exit strategy, manual workaround capacity, contract clauses on RTO |
| Corruption or deletion of data | backup restore test with PITR | point-in-time recovery to one minute before the corruption, reconciliation of transactions after that point |
| Loss of premises or of the workforce | BC tabletop | remote work capacity, call tree, MBCO delivery with reduced staff |
| Identity provider or PKI failure | component failover | break-glass accounts, certificate renewal, access to the DR console without the IdP |

## Measuring during the exercise

The observer records UTC timestamps for: alert received, incident declared, decision to fail over, restore or failover started, service restored (technical), service validated (business), rollback complete. For data, record the last consistent recovery point before the switch: Aurora `aws rds describe-global-clusters` replica lag, PostgreSQL `SELECT now() - pg_last_xact_replay_timestamp()` on the standby, MySQL `SHOW REPLICA STATUS` `Seconds_Behind_Source`, backup catalogue completion time of the last successful job. Run `rpo_rto_check.py` on these values; never estimate the RTO from memory after the fact.

## Common findings

- Runbook references a tag or a hostname that no longer exists.
- Secondary site has 20 % of the capacity and no documented scale-up path.
- Secrets or KMS keys are regional; the standby cannot decrypt.
- Monitoring and the status page depend on the failed region.
- Backups restore, but the application needs a schema migration that lives only in CI.
- Nobody owns the decision to fail over, so it takes 45 minutes to find an approver.
- Fail-back was never planned; the exercise ends with production running on the secondary site.
