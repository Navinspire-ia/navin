---
name: cloud-security-posture
description: Audit and harden cloud and Kubernetes security posture - IAM, RBAC, KMS, Vault, cert-manager, OPA/Gatekeeper, Kyverno, Trivy, Falco, CIS benchmarks. Read-only audit first, then gated remediation. Use for posture reviews, compliance checks and hardening.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Cloud Security Posture

## Overview

Two phases, never mixed: an evidence-based read-only audit, then remediation through gated plans. IAM, secrets and policy changes are critical and need the user.

## When to use

- Security or compliance review of an AWS/GCP/Azure account or a cluster.
- Least-privilege work, key rotation, encryption gaps, admission policies.
- After an incident or before a production launch.

## Engine

1. `infra action=status` - auth hygiene warnings (long-lived keys, root usage, broad kube contexts).
2. `infra action=discover provider=aws|gcp|azure|kubernetes`, `infra action=graph op=build` to find public exposure paths.
3. `infra action=catalog risk=read query=<iam|kms|policy>` for audit operations; run them with `infra action=run` (read-only, allowed at observe).
4. Remediation: `infra action=check` each command, `infra action=plan`, user validation, `infra action=run`, `infra action=verify`.

## Workflow

1. **Identity** - who has admin, unused roles, keys older than 90 days, MFA, cross-account trust, wildcard policies; Kubernetes cluster-admin bindings, default service accounts with tokens.
2. **Data** - encryption at rest (KMS keys, rotation), public buckets, snapshot sharing, TLS everywhere.
3. **Secrets** - Vault/Secrets Manager/Key Vault usage, no secrets in env files, CI variables or manifests.
4. **Workloads** - image vulnerabilities (Trivy), privileged pods, hostPath, runAsRoot, admission policies (Gatekeeper/Kyverno), runtime detection (Falco).
5. **Benchmarks** - CIS (kube-bench, Prowler, Security Hub, Defender for Cloud, Security Command Center).
6. **Report** findings by severity with evidence, then propose remediation plans.

## Command families

```bash
aws iam get-account-summary; aws iam generate-credential-report && aws iam get-credential-report
aws iam list-attached-role-policies --role-name <r>; aws accessanalyzer list-findings --analyzer-arn <a>
aws kms list-keys; aws kms get-key-rotation-status --key-id <k>
aws s3api get-public-access-block --bucket <b>; aws securityhub get-findings --max-items 50
prowler aws --severity critical high
gcloud projects get-iam-policy <p>; gcloud iam service-accounts keys list --iam-account <sa>
az role assignment list --all -o table; az security assessment list
kubectl get clusterrolebindings -o wide; kubectl auth can-i --list --as system:serviceaccount:<ns>:<sa>
kube-bench run; trivy image <img>; trivy k8s --report summary cluster; trivy config .
kubectl get constraints; kubectl get clusterpolicy; kubectl logs -n falco -l app.kubernetes.io/name=falco --tail=100
vault status; vault policy list; vault audit list
kubectl get certificates -A
```

## Safety rules

- The audit phase is read-only. Never rotate, revoke or delete during the audit.
- IAM/RBAC changes, key disabling, policy enforcement (Gatekeeper/Kyverno `deny`/`Enforce`) are critical: user validation, and start policies in audit/warn mode.
- Never print secret values; reference them by path.

## Verification and rollback

- Verify each remediation with the same audit command that found it.
- Rollback: saved previous policy JSON / RoleBinding YAML, policy back to audit mode, re-enable a key during its grace window.

## Deliverable

Findings table (severity, resource, evidence, fix, effort), remediation plans with run ids, before/after evidence, residual risks.
