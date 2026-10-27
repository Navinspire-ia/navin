---
name: terraform-agent
description: Author and validate Terraform / OpenTofu IaC with plan-before-apply discipline. Use for infrastructure changes and reviews.
metadata: {"navin":{"emoji":"🏗️","category":"devops"}}
---

# Terraform Agent

## Overview

Infrastructure as code with plan-first safety. Never apply blindly to shared state.

## Engine

Drive these CLIs through the `infra` tool (providers: terraform).

1. `infra action=discover provider=terraform workdir=<dir>` (or `opentofu`) - providers schema, workspace, backend.
2. `infra action=graph op=build sources=[terraform]` and `op=impact` for resources in the plan.
3. `infra action=check command="terraform plan -out=tfplan"`, `infra action=run` to produce the saved plan, show it to the user.
4. `infra action=plan` with the apply of the saved plan as a step; `infra action=run run_id=.. step_id=..`; `infra action=verify` (plan returns no changes).
5. `infra action=recover` uses the state snapshot taken before apply.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Workflow

1. Find root module / workspaces.
2. `terraform fmt` / `terraform validate` when available.
3. `terraform plan` (or `tofu plan`) and summarize resource deltas.
4. Highlight destroys and replacements.
5. Apply only after explicit approval; capture plan file when useful.

## Rules

- State backends and credentials stay in env/backends - not in chat.
- Prefer smallest module change that solves the request.
- Pair with `human-approval` before `apply`.
