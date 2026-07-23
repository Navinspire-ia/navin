---
name: data-migration-agent
description: Plan and execute source→target data migrations with mapping, transforms, reconciliation, and cutover checks. Use for CRM/ERP/DB moves.
metadata: {"navin":{"emoji":"🚚","category":"data"}}
---

# Data Migration Agent

## Overview

Migrations succeed on reconciliation, not on “export finished”.

## Workflow

1. Inventory source entities and volumes.
2. Build mapping table (source field → target field → transform).
3. Trial on a sample; measure error rates.
4. Full load with idempotent keys when possible.
5. Reconcile counts + checksums + spot business records.
6. Cutover checklist + rollback (`backup-rollback`).

## Deliverables

- Mapping sheet
- Transform notes
- Reconciliation report
- Cutover / rollback plan
