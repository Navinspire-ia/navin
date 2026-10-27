---
name: cloud-azure
description: Operate Microsoft Azure with the az CLI - VMs, AKS, storage, networking, ARM resources, costs. Read-first; mutations gated by approval.
metadata: {"navin":{"emoji":"🔷","category":"devops"}}
---

# Azure Cloud Operator

## Overview

Drive Azure through the `az` CLI (or the Azure MCP server when configured). Always confirm subscription and resource group before acting.

## Engine

Drive these CLIs through the `infra` tool (providers: azure).

1. `infra action=status` - az account, subscription, production flag.
2. `infra action=discover provider=azure` and `infra action=graph op=build`.
3. `infra action=catalog provider=azure query=<service>` / `describe`; `infra action=check command="az ..."`; `infra action=run`; `infra action=verify`.
4. Changes to Bicep/ARM go through `az deployment group what-if` inside an `infra action=plan`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Common commands

```bash
az account show
az account list --output table
az group list --output table
az vm list -d --output table
az aks list --output table && az aks get-credentials -g <rg> -n <cluster>
az storage account list --output table
az network nsg list --output table
az monitor activity-log list --max-events 20
az consumption usage list --top 10
```

## Workflow

1. Confirm the subscription (`az account show`) and target resource group; switch explicitly with `az account set`.
2. Inspect the resource and its dependencies (NSGs, identities, vnets) before changing anything.
3. Prefer IaC: if managed by Terraform/Bicep/ARM, change the template and diff (`az deployment group what-if`).
4. After a mutation, re-read the resource state and check Azure Monitor for regressions.

## Rules

- Deleting resources/groups, NSG rule changes, and role assignments require `human-approval`.
- Use `--output table` for humans, `--output json` when you need to parse.
- Flag cost-heavy actions before running them.
