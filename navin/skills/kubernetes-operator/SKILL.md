---
name: kubernetes-operator
description: Inspect pods, logs, services, ingress, and deployments with kubectl. Use for cluster debugging and safe read-mostly operations.
metadata: {"navin":{"emoji":"☸️","category":"devops"}}
---

# Kubernetes Operator

## Overview

Debug Kubernetes with read-first habits. Mutating prod requires approval.

## Engine

Drive these CLIs through the `infra` tool (providers: kubernetes).

1. `infra action=status` - current context and whether it is production.
2. `infra action=discover provider=kubernetes context=<ctx>` (api-resources with CRDs).
3. `infra action=graph op=impact node=<deployment|service>` before a change.
4. `infra action=check command="kubectl ..."`; `infra action=access operations=[...]` runs `kubectl auth can-i`.
5. `infra action=run command="kubectl ..."` (prefer `kubectl diff` / `--dry-run=server` first), then `infra action=verify` (rollout status). Cluster build and upgrades: `kubernetes-platform`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Common commands

```bash
kubectl config current-context
kubectl get pods -A
kubectl describe pod <name> -n <ns>
kubectl logs <pod> -n <ns> --tail=200
kubectl get svc,ingress -n <ns>
kubectl get events -n <ns> --sort-by=.lastTimestamp
```

## Workflow

1. Confirm context/namespace (never assume prod).
2. Locate unhealthy resources (CrashLoop, Pending, ImagePull).
3. Correlate describe + logs + events.
4. Propose a fix; apply only with approval on shared clusters.

## Rules

- `kubectl delete`, scale-to-zero, and apply on prod → `human-approval`.
- Prefer dry-run: `kubectl apply --dry-run=client -o yaml`.
