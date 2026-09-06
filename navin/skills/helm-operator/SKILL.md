---
name: helm-operator
description: Inspect, template, install, and upgrade Helm releases safely; author and lint charts. Use for anything chart- or release-related.
metadata: {"navin":{"emoji":"⎈","category":"devops","requires":{"bins":["helm"]}}}
---

# Helm Operator

## Overview

Manage Helm charts and releases with dry-run-first habits. Rendered output is inspected before anything reaches the cluster.

## Common commands

```bash
helm list -A
helm status <release> -n <ns>
helm get values <release> -n <ns>
helm history <release> -n <ns>
helm template <chart> -f values.yaml
helm upgrade --install <release> <chart> -n <ns> -f values.yaml --dry-run
helm lint <chart-dir>
helm diff upgrade <release> <chart> -f values.yaml   # if helm-diff plugin present
```

## Workflow

1. Identify the release, chart source (repo/OCI/local), and current values.
2. Render with `helm template` or `--dry-run` and review the manifests before applying.
3. Apply with `helm upgrade --install --atomic --timeout 5m` so failures roll back automatically.
4. Verify: `helm status`, rollout status of the workloads, then application health.
5. In a GitOps setup, change values files in git instead of running `helm upgrade` by hand.

## Rules

- `helm uninstall` and prod upgrades require `human-approval`.
- Pin chart versions; never deploy floating `latest` on shared clusters.
- Report the exact chart version and value overrides applied.
