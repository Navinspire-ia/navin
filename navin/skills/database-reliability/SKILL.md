---
name: database-reliability
description: Run databases reliably - PostgreSQL, MySQL, Redis, MongoDB, Kafka, Amazon RDS/Aurora, Cloud SQL, Azure Database - HA and failover, backups, PITR, restore drills, replication, performance and safe schema migrations. Use for any database design, incident, backup or migration.
metadata: {"navin":{"emoji":"🗄️","category":"devops"}}
---

# Database Reliability

## Overview

Data is the one thing you cannot redeploy. You prove backups by restoring them, change schemas with reversible migrations, and treat every write or failover as critical.

## When to use

- Design HA for a database, configure backups and PITR, run a restore drill.
- Replication lag, connection exhaustion, slow queries, disk full.
- Version upgrades and schema/data migrations.

## Engine

1. `infra action=discover provider=aws|gcp|azure|kubernetes` and `infra action=graph op=impact node=<db>` to see every consumer.
2. `infra action=catalog query=<rds|cloudsql|snapshot>` and `describe` for backup/restore operations and their verification.
3. Every change: `infra action=check` (data operations are critical), `infra action=plan` with a snapshot step first, `infra action=run`, `infra action=verify`; `infra action=recover` for restores.

## Workflow

1. **State** - engine, version, topology, replicas, backup retention, last successful backup, PITR window.
2. **HA** - Multi-AZ / regional instance / Patroni / replica set / Redis Sentinel or cluster / Kafka replication factor 3 and min.insync.replicas 2.
3. **Backups** - automated + logical (pg_dump) where needed, encryption, cross-region copy, retention matching RPO.
4. **Restore drill** - restore to a new instance, run integrity checks, measure RTO, delete the drill instance.
5. **Migrations** - expand/contract pattern, backward-compatible steps, tested on a restored copy, lock-aware (CREATE INDEX CONCURRENTLY).
6. **Upgrades** - snapshot, test on a clone, blue/green (RDS Blue/Green deployments) or replica promotion.

## Command families

```bash
psql -c 'select version()'; psql -c 'select * from pg_stat_replication'; psql -c 'select * from pg_stat_activity where state <> $$idle$$'
pg_dump -Fc -d <db> -f db.dump; pg_restore -l db.dump; pg_restore -d <newdb> db.dump
mysql -e 'SHOW REPLICA STATUS\G'; mysqldump --single-transaction <db>
redis-cli INFO replication; redis-cli INFO memory; redis-cli --latency
mongosh --eval 'rs.status()'; mongodump --uri <uri>
kafka-topics.sh --bootstrap-server <b> --describe; kafka-consumer-groups.sh --bootstrap-server <b> --describe --all-groups
aws rds describe-db-instances; aws rds describe-db-snapshots --db-instance-identifier <id>
aws rds create-db-snapshot --db-instance-identifier <id> --db-snapshot-identifier <id>-pre-change
aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier <id> --target-db-instance-identifier <id>-drill --restore-time <ts>
gcloud sql instances describe <i>; gcloud sql backups list --instance <i>
az postgres flexible-server show -g <rg> -n <n>
```

## Safety rules

- Writes, DROP/TRUNCATE, failovers, parameter changes needing reboot, restores over an existing DB and version upgrades are critical: user validation.
- Snapshot before any change. Never restore over the source: restore to a new target and switch.
- Never print connection strings with passwords.

## Verification and rollback

- Verify: replication healthy, lag near zero, application error rate, row counts/checksums after migrations.
- Rollback: down migration, snapshot/PITR restore to a new instance and endpoint switch, replica promotion.

## Deliverable

Topology, backup/PITR status with last tested restore and measured RTO, changes with run ids, verification evidence, rollback.
