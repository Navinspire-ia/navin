---
name: cloud-aws
description: Operate AWS with the aws CLI - EC2, EKS, S3, IAM, VPC, RDS, Lambda, costs. Read-first; mutations gated by approval.
metadata: {"navin":{"emoji":"🟠","category":"devops"}}
---

# AWS Cloud Operator

## Overview

Drive AWS through the `aws` CLI (or the AWS MCP servers when configured). Always confirm account and region before acting.

## Engine

Drive these CLIs through the `infra` tool (providers: aws).

1. `infra action=status` - AWS profile, SSO session, account, region, production flag.
2. `infra action=discover provider=aws` (`deep=true` adds Cloud Control types) and `infra action=graph op=build sources=[aws]`.
3. `infra action=catalog provider=aws query=<service>` / `describe operation=<id>` for exact parameters and IAM permissions; `infra action=access operations=[...]` simulates them.
4. `infra action=check command="aws ..."`, then `infra action=run command="aws ..."` (read commands run at observe), `infra action=verify`.
5. Multi-step changes: `infra action=plan`. Architecture work: `aws-architect`.

- Never run these CLIs through raw `exec`: the engine applies the autonomy policy (observe / prepare / execute), asks the user for critical operations (production, deletions, IAM, sensitive network, data, costly), snapshots before mutation, verifies after and audits.
- Multi-domain missions start from `infra-orchestrator`.

## Common commands

```bash
aws sts get-caller-identity
aws ec2 describe-instances --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType]' --output table
aws eks list-clusters && aws eks update-kubeconfig --name <cluster>
aws s3 ls
aws iam list-roles --max-items 20
aws logs tail <log-group> --since 1h
aws ce get-cost-and-usage --time-period Start=<d1>,End=<d2> --granularity MONTHLY --metrics UnblendedCost
```

## Workflow

1. Identify account, region, and profile (`sts get-caller-identity`); never assume.
2. Inspect before changing: describe the resource and its dependencies (security groups, IAM policies, subnets).
3. Prefer IaC: if the resource is Terraform/CDK/CloudFormation-managed, change the code and plan/diff it, not the console state.
4. After a mutation, re-describe the resource and check CloudWatch/logs for regressions.

## Rules

- Terminating instances, deleting buckets/data, and any IAM or security-group change require `human-approval`.
- Never print or store credentials; use profiles and env vars.
- Flag cost-heavy actions (large instances, cross-region transfer, NAT) before running them.
