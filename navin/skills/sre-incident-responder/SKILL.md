---
name: sre-incident-responder
description: Triage incidents like an SRE - correlate logs, metrics, events and traces, find the root cause, propose remediation, and write the postmortem. Uses k8sgpt when available.
metadata: {"navin":{"emoji":"🚨","category":"devops"}}
---

# SRE Incident Responder

## Overview

Diagnose first, mutate last. Establish the blast radius, timeline, and root cause from evidence before touching anything.

## Engine

Drive these CLIs through the `infra` tool (providers: kubernetes,aws).

1. `infra action=status` then `infra action=graph op=build` and `op=impact node=<failing service>` to see dependencies and blast radius.
2. Evidence gathering is read-only: `infra action=run command="kubectl ..."` / cloud describe commands.
3. Mitigations (rollback, scale, failover, restart): `infra action=check`, user validation when critical, `infra action=run`, `infra action=verify`; `infra action=recover run_id=..` for failed changes.
4. `infra action=audit` gives the timeline of changes for the postmortem.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Signal sources

Cross-platform (kubectl, k8sgpt, and docker work identically on Linux, macOS, and Windows):

```bash
kubectl get events -A --sort-by=.lastTimestamp
kubectl logs <pod> -n <ns> --previous --tail=200
kubectl top pods -n <ns>
k8sgpt analyze --explain            # if k8sgpt is installed
docker logs --tail 200 <container>
```

Host logs per OS:

```bash
journalctl -u <service> --since "1 hour ago"                      # Linux
log show --last 1h --predicate 'process == "<service>"'           # macOS
```

```powershell
Get-WinEvent -LogName Application -MaxEvents 100 |
  Where-Object LevelDisplayName -in 'Error','Critical'            # Windows
```

Also use Grafana/Prometheus MCP servers when configured for dashboards, alert state, and metric queries.

## Workflow

1. Scope: what is broken, since when, for whom. Pin the first bad timestamp.
2. Correlate across layers: recent deploys/config changes first (most incidents are changes), then resources (OOM, CPU throttling, disk), then dependencies (DB, DNS, upstream APIs), then infra.
3. State the root cause with the evidence chain, not just the symptom.
4. Propose remediation in two parts: immediate mitigation (rollback, restart, scale) and durable fix (code/config/capacity).
5. Apply mitigation only with approval; verify recovery against the original symptom.
6. Write a short postmortem: timeline, root cause, impact, actions, follow-ups. Save it under `ops/incidents/`.

## Rules

- Rollbacks and restarts on prod require `human-approval`.
- Never delete evidence (logs, crashed pods) before capturing it.
- If the cause cannot be proven, list the ranked hypotheses and the test for each; do not guess-remediate.
