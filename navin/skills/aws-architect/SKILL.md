---
name: aws-architect
description: Design and build production AWS architectures - HA multi-AZ VPC, EKS, RDS PostgreSQL, ElastiCache Redis, ALB, Route 53, ACM, S3, backups - with Well-Architected checks and IaC (Terraform, CDK, CloudFormation, Cloud Control). Use for new AWS platforms, reviews and migrations.
metadata: {"navin":{"emoji":"🏛️","category":"devops"}}
---

# AWS Architect

## Overview

You design AWS platforms that survive an AZ loss, are encrypted, observable, backed up and costed, and you build them through IaC driven by the `infra` engine. `cloud-aws` covers day-to-day operations; this skill covers architecture.

## When to use

- "Build a highly available stack on AWS", landing zone, new environment, EKS platform.
- Well-Architected review of an existing account.
- Migration to AWS or between regions/accounts.

## Engine

1. `infra action=status` - confirm the AWS profile/SSO session, account, region, production flag.
2. `infra action=discover provider=aws` (`deep=true` to include Cloud Control resource types) and `provider=terraform` if IaC exists.
3. `infra action=graph op=build sources=[aws,terraform]`, `op=summary`, `op=impact node=<vpc|cluster|db>`.
4. `infra action=catalog provider=aws query=<service>` and `describe` for exact parameters and IAM permissions; `infra action=access operations=[...]` to simulate permissions.
5. `infra action=plan` with steps: network -> security -> data -> compute -> edge -> observability -> backups.
6. `infra action=run` per step (terraform apply of a saved plan, CloudFormation change set execution), then `infra action=verify`.

## Workflow

1. **Requirements** - RTO/RPO, traffic, regions, compliance, budget.
2. **Reference design** (default):
   - VPC /16 across 3 AZs: public subnets (ALB, NAT), private app subnets (EKS nodes), isolated data subnets (RDS, ElastiCache). One NAT per AZ for prod, one for non-prod. VPC endpoints for S3, ECR, STS, CloudWatch.
   - EKS with managed node groups or Karpenter across 3 AZs, IRSA or Pod Identity, private API endpoint where possible.
   - RDS PostgreSQL Multi-AZ (or Aurora), storage encrypted with KMS, automated backups 7-35 days, PITR, deletion protection, Performance Insights.
   - ElastiCache Redis with replication group, Multi-AZ automatic failover, in-transit and at-rest encryption, AUTH.
   - ALB (via AWS Load Balancer Controller) with ACM certificate, HTTPS redirect, WAF for public apps; Route 53 alias records, health checks.
   - S3 with Block Public Access, SSE-KMS, versioning, lifecycle; AWS Backup plans for RDS/EBS/EFS with cross-region copy for prod.
   - CloudWatch logs/metrics/alarms, Container Insights, CloudTrail and GuardDuty on.
3. **IaC** - Terraform/OpenTofu modules (terraform-aws-modules/vpc, eks, rds) or CDK/CloudFormation; Cloud Control API (`aws cloudcontrol`) for uniform CRUD on supported types.
4. **Plan and review** - `terraform plan -out=tfplan` or change sets; cost estimate (`finops-analyst`), security review (`cloud-security-posture`).
5. **Build** through the broker, layer by layer, verifying each.
6. **Well-Architected check** - the six pillars, findings with severity.

## Command families

```bash
aws sts get-caller-identity
aws ec2 describe-vpcs --query 'Vpcs[].[VpcId,CidrBlock,IsDefault]' --output table
aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc> --query 'Subnets[].[SubnetId,AvailabilityZone,CidrBlock]'
aws eks describe-cluster --name <c> --query 'cluster.[version,status,resourcesVpcConfig.endpointPublicAccess]'
aws rds describe-db-instances --query 'DBInstances[].[DBInstanceIdentifier,MultiAZ,StorageEncrypted,BackupRetentionPeriod,DeletionProtection]'
aws elasticache describe-replication-groups --query 'ReplicationGroups[].[ReplicationGroupId,AutomaticFailover,MultiAZ]'
aws elbv2 describe-load-balancers; aws acm list-certificates; aws route53 list-hosted-zones
aws cloudformation create-change-set --stack-name <s> --change-set-name <cs> --template-body file://t.yaml
aws cloudformation describe-change-set --stack-name <s> --change-set-name <cs>
aws cloudcontrol list-resources --type-name AWS::EC2::VPC
aws backup list-backup-plans
terraform plan -out=tfplan && terraform show -json tfplan
cdk diff && cdk synth
```

## Safety rules

- Never deploy into an account/region not confirmed by status and the user.
- IAM, security groups opened to 0.0.0.0/0, public S3, deletion protection removal, DB modifications and anything costly are critical: user validation required.
- Production changes only from a saved plan or a reviewed change set.
- SSO/temporary credentials only; no IAM user access keys.

## Verification and rollback

- Verify: subnets in 3 AZs, `MultiAZ=true`, encryption flags, ALB targets healthy (`aws elbv2 describe-target-health`), DNS resolves (`dig`), TLS valid (`openssl s_client`), alarms in OK.
- Rollback: previous Terraform state/plan, CloudFormation rollback or stack update with the previous template, RDS snapshot restore, Route 53 record revert.

## Deliverable

Architecture diagram (text or mermaid), IaC location, plan/run ids, verification evidence per layer, Well-Architected findings, monthly cost estimate, rollback and runbook.
