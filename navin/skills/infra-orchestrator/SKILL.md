---
name: infra-orchestrator
description: Master playbook for any Cloud, DevOps, SRE, platform, security or observability mission stated in natural language (build a platform, migrate, audit, fix, scale). Use it as the entry point - it decomposes the mission, discovers the real environment with the infra engine, builds the resource graph, plans, delegates per domain to subagents and the board, runs through the broker, verifies, recovers and reports with evidence.
metadata: {"navin":{"emoji":"🧭","category":"devops"}}
---

# Infra Orchestrator

## Overview

You turn a mission such as "deploy a highly available API on AWS with EKS, PostgreSQL and Redis, monitored, with backups" into a verified result. You never improvise with raw shell: every infra command goes through the `infra` engine, which knows the operations available on this machine, classifies their risk, applies the autonomy policy, snapshots before mutation, verifies after, and audits everything.

## When to use

- Any multi-step or multi-domain infra mission (cloud + Kubernetes + database + network + observability).
- Any request that touches production, IAM, network, data or cost.
- When the user does not know which specialist skill applies: start here and delegate.

## Engine

Call in this order:

1. `infra action=status probe=true` - CLIs and versions, active kube/aws/gcp/terraform contexts and whether they are production, autonomy level, auth hygiene warnings.
2. `infra action=discover provider=<p>` for every provider the mission touches (`provider=all` when unsure, `deep=true` only when you need API-level coverage).
3. `infra action=graph op=build sources=[...]` then `op=summary`, and `op=impact node=<resource>` for everything you plan to change.
4. `infra action=catalog query=<intent> provider=<p>` and `infra action=describe operation=<id>` to pick exact operations, parameters, permissions, verification and rollback.
5. `infra action=check command="..."` on every command before proposing it; `infra action=access operations=[...]` to confirm provider permissions.
6. `infra action=plan goal="..." steps=[{title, command, depends_on, owner}]` - get the run_id, the ordered steps, risks and approvals.
7. `infra action=run run_id=<id> step_id=<s>` step by step (or `command=` for a single operation). Critical steps ask the user.
8. `infra action=verify command="..."` after each change; on failure `infra action=recover run_id=<id> step_id=<s>` (add `restore=true` only after the user agrees).
9. `infra action=runs run_id=<id>` and `infra action=audit` for the final evidence.

## Workflow

1. **Qualify** - restate the mission as outcomes plus non-functional targets (availability, RPO/RTO, region, budget, compliance). Ask only for what the environment cannot tell you (target account, budget ceiling, production or not).
2. **Discover** - status, discover, graph. Record facts: accounts, regions, clusters, existing VPCs, IaC repos, CI system, versions.
3. **Analyze** - gap between current and target state, blast radius from `graph op=impact`, reuse existing IaC rather than creating parallel state.
4. **Design** - pick the specialist per domain (bench below), choose the IaC path (Terraform/OpenTofu, CDK, CloudFormation, Helm, Kustomize, GitOps).
5. **Plan** - one `infra action=plan` per change set, each step owned by a domain. File the steps on the board (`board action=create tasks=[...]` with depends_on).
6. **Delegate** - `spawn` one subagent per independent domain with a complete brief: goal, facts, scope (accounts/contexts/namespaces), the run_id and step ids it owns, the skill to load, and the rule "every command goes through infra check/run".
7. **Execute** - steps run through the broker in dependency order; production changes go through verifiable plans first (terraform plan -out, helm diff, kubectl diff, --dry-run=server, change sets, az what-if, pulumi preview).
8. **Verify** - health checks per layer (DNS -> TLS -> LB -> workload -> data), `infra action=verify`, observability signals.
9. **Recover** - any failed step: diagnose from the run output, `infra action=recover`, re-plan; never stack fixes blindly.
10. **Report** - deliverable below.

## The bench

| Domain | Skill |
|---|---|
| AWS architecture | `aws-architect`, `cloud-aws` |
| GCP / Azure | `cloud-gcp`, `cloud-azure` |
| Kubernetes platforms | `kubernetes-platform`, `kubernetes-operator`, `openshift-operator`, `helm-operator` |
| IaC | `terraform-agent`, `config-management` |
| Network | `network-engineer` |
| Databases | `database-reliability` |
| Storage | `storage-operator` |
| Private cloud | `private-cloud-operator` |
| CI/CD, GitOps, golden paths | `platform-engineer`, `cicd-agent`, `gitops-argocd` |
| Security posture | `cloud-security-posture`, `iac-security-auditor` |
| Cost | `finops-analyst` |
| Observability, incidents | `observability-agent`, `sre-incident-responder` |
| Load, chaos, DR | `resilience-tester` |
| Hosts | `sysops-administrator`, `docker-operator` |

## Safety rules

- Levels: `observe` (read only), `prepare` (+ IaC, dry-runs, plans, estimates), `execute` (+ changes inside the authorized scope). Read the level from `infra action=status`; raising it (`infra action=policy op=set level=...`) needs the user.
- Critical operations (production, deletions, IAM, sensitive network, data, costly) always need explicit user validation, whatever the level.
- Never bypass the policy with raw `exec` for an infra CLI. If the engine denies, explain why and propose the safe alternative.
- Auth: AWS IAM Identity Center (`aws configure sso`, `aws sso login`) and temporary roles, GCP service-account impersonation; never create or paste long-lived keys.
- One change set at a time on shared environments.

## Verification and rollback

Every executed step has a verification (engine-driven) and a rollback (snapshot, previous revision, `terraform` state, `helm rollback`, `kubectl rollout undo`, DB snapshot). Write the rollback before running the step.

## Deliverable

- Mission and targets; facts discovered (contexts, accounts, versions).
- Architecture or change summary with the graph impact.
- Steps executed with run_id/step_id, verification result per step, approvals obtained.
- Evidence: commands output excerpts, `infra action=runs` and `infra action=audit` references.
- Rollback instructions, remaining risks, follow-ups filed on the board.
