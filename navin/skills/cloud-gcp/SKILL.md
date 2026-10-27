---
name: cloud-gcp
description: Operate Google Cloud with gcloud - GCE, GKE, storage, networking, IAM, logs, costs. Read-first; mutations gated by approval.
metadata: {"navin":{"emoji":"🌈","category":"devops"}}
---

# GCP Cloud Operator

## Overview

Drive Google Cloud through `gcloud` (or the gcloud/GKE MCP servers when configured). Always confirm project and zone/region before acting.

## Engine

Drive these CLIs through the `infra` tool (providers: gcp).

1. `infra action=status` - gcloud account, project, impersonated service account, production flag.
2. `infra action=discover provider=gcp` (`deep=true` adds Google API Discovery) and `infra action=graph op=build`.
3. `infra action=catalog provider=gcp query=<service>` / `describe`; `infra action=check command="gcloud ..."`; `infra action=run`; `infra action=verify`.
4. Auth: `infra action=policy op=set auth={gcp_impersonate: <sa>}` (with the user) instead of service account keys.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Common commands

```bash
gcloud config list
gcloud projects list
gcloud compute instances list
gcloud container clusters list
gcloud container clusters get-credentials <cluster> --region <region>
gcloud storage ls
gcloud logging read 'severity>=ERROR' --limit 20 --freshness 1h
gcloud iam service-accounts list
gcloud billing accounts list
```

## Workflow

1. Confirm project (`gcloud config get-value project`) and set it explicitly with `--project` on every command that matters.
2. Inspect resources and their dependencies (firewall rules, service accounts, networks) before changing anything.
3. Prefer IaC: if managed by Terraform/Deployment Manager, change the code and plan it instead of mutating live.
4. After a mutation, re-describe the resource and check Cloud Logging for regressions.

## Rules

- Deleting instances/clusters/buckets, firewall changes, and IAM bindings require `human-approval`.
- Use `--format=json` when parsing output programmatically.
- Flag cost-heavy actions before running them.
