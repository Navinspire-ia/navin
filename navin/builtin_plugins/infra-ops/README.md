# Infra Ops Plugin

A Cloud, DevOps, SRE and platform engineering bench driven by the `infra` engine.

## Overview

The plugin groups the expert infra skills. Every skill drives the `infra` tool instead of raw shell: `status` and `discover` first, `graph` for impact, `check` on every command, `plan` for multi-step changes, `run` through the execution broker (policy gate, snapshot, verification, audit), `verify` and `recover`.

Entry point: the `infra-orchestrator` skill (also loaded by `/ops`). It decomposes a natural-language mission, delegates per domain to subagents and the board, and closes with an evidence report.

## Examples

```
/ops deploy a highly available API on AWS: EKS, RDS PostgreSQL, Redis, ALB, TLS, backups
/ops audit the security posture of the prod cluster, read-only
/ops why did the AWS bill grow last month
```

Behind each example the skill calls, for instance:

```
infra action=status probe=true
infra action=discover provider=aws
infra action=graph op=impact node=<vpc>
infra action=plan goal="..." steps=[...]
infra action=run run_id=<id> step_id=<s>
```

## Safety

- Autonomy levels: observe, prepare, execute. Raising the level needs the user.
- Critical operations (production, deletions, IAM, sensitive network, data, costly) always need explicit user validation.
- Temporary credentials only (AWS IAM Identity Center, GCP impersonation).

## Plugin contents

| Kind | Items |
|---|---|
| Skills | infra-orchestrator, ops-supervisor, aws-architect, cloud-aws, cloud-gcp, cloud-azure, kubernetes-platform, kubernetes-operator, openshift-operator, helm-operator, docker-operator, terraform-agent, config-management, network-engineer, database-reliability, storage-operator, private-cloud-operator, platform-engineer, cicd-agent, gitops-argocd, cloud-security-posture, iac-security-auditor, finops-analyst, observability-agent, sre-incident-responder, resilience-tester, sysops-administrator |

## Version

1.0.0
