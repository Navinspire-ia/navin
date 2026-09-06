---
name: iac-security-auditor
description: Audit infrastructure-as-code and runtime config - Dockerfiles, Kubernetes manifests, Terraform, CI/CD pipelines, and cloud settings for misconfigurations and hardening gaps. Use for /bastion, deploy reviews, or container/cloud hardening.
metadata: {"navin":{"emoji":"🏰","category":"security"}}
---

# Infrastructure & IaC Security Auditor

## Overview

Review how the system is built, shipped, and deployed. Misconfigured infrastructure is exploited as often as vulnerable code. Cite the exact file and directive; rate by blast radius.

## What to inspect

**Containers (Dockerfile / compose)**
- Runs as `root` (no `USER`), `latest` base tags, secrets baked into layers, `ADD` from URLs, mounted `docker.sock`, missing healthchecks, oversized attack surface (dev tools in prod image).

**Kubernetes**
- `privileged: true`, `hostNetwork`/`hostPID`, missing `securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`, dropped capabilities), no resource limits, secrets as env vars, wide RBAC (`cluster-admin`, `*` verbs), no NetworkPolicy.

**Terraform / cloud**
- Public S3/buckets/blobs, `0.0.0.0/0` security groups, unencrypted volumes/DBs, IAM `*:*` policies, disabled logging/audit, public database endpoints, hardcoded credentials in `.tf` or state.

**CI/CD**
- Secrets echoed in logs, untrusted PR workflows with write tokens, unpinned action versions (`@main`), `pull_request_target` misuse, artifact/cache poisoning.

## Workflow

1. Locate every infra file (Dockerfile, `*.tf`, `k8s/*.yaml`, `.github/workflows/*`, compose files).
2. Prefer real scanners when available (`trivy config`, `checkov`, `tfsec`, `kube-linter`, `hadolint`); pattern-review what they miss.
3. For each finding: `[SEVERITY] file:directive` - misconfiguration, what it exposes, and the hardened setting.
4. Separate exploitable-now from defense-in-depth. Prioritize anything publicly reachable or granting broad privilege.
5. Offer a hardened snippet per fix, and a least-privilege baseline for IAM/RBAC.

## Anti-patterns

- Flagging a non-root dev container as if it were production
- Recommending settings the platform does not support
- Ignoring CI/CD - the pipeline is part of the attack surface
