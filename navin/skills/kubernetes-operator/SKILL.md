---
name: kubernetes-operator
description: Inspect pods, logs, services, ingress, and deployments with kubectl. Use for cluster debugging and safe read-mostly operations.
metadata: {"navin":{"emoji":"☸️","category":"devops","requires":{"bins":["kubectl"]}}}
---

# Kubernetes Operator

## Overview

Debug Kubernetes with read-first habits. Mutating prod requires approval.

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
