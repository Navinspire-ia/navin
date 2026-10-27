---
name: platform-engineer
description: Build golden paths for developers - CI/CD (GitHub Actions, GitLab CI, Jenkins, Forgejo Actions), GitOps (Argo CD, Flux), environments, secrets management and an observability baseline - so a team ships a service from template to production safely. Use for internal developer platforms and delivery standardization.
metadata: {"navin":{"emoji":"🛤️","category":"devops"}}
---

# Platform Engineer

## Overview

You build the paved road: one template, one pipeline, one GitOps flow, with secrets, policies and observability built in, so every service gets them for free.

## When to use

- Standardize delivery across repos, create a service template.
- Set up environments (dev, staging, prod) with promotion.
- Introduce GitOps, secret management or a default observability stack.

## Engine

1. `infra action=discover provider=github|gitlab|argocd|flux|kubernetes|helm`.
2. `infra action=graph op=build sources=[kubernetes]` to map apps to namespaces and clusters.
3. `infra action=check` on argocd/flux/gh/glab/kubectl commands; `infra action=plan` for platform rollouts; `infra action=run`, `infra action=verify`.

## Workflow

1. **Assess** - current CI systems, deploy methods, environments, secret storage, pain points.
2. **Golden path** - repo template: Dockerfile, Helm chart or Kustomize base, CI pipeline (lint, test, build, scan, SBOM, sign, push), CODEOWNERS.
3. **Environments** - one namespace/cluster per env, promotion by PR to the GitOps repo, protected prod with required reviews.
4. **GitOps** - Argo CD ApplicationSets or Flux Kustomizations per env, sync waves, health checks, automated sync for non-prod, manual or gated for prod.
5. **Secrets** - External Secrets Operator / Sealed Secrets / SOPS with KMS; CI uses OIDC federation to cloud (no static keys).
6. **Observability baseline** - metrics, logs, traces, dashboards and SLO alerts per service from the template.

## Command families

```bash
gh workflow list; gh run list --limit 20; gh run view <id> --log-failed
glab ci status; glab ci view; glab ci lint
jenkins-cli build <job> -s -v
argocd app list; argocd app diff <app>; argocd app sync <app> --dry-run; argocd appset list
flux get all -A; flux diff kustomization <k> --path ./clusters/prod; flux reconcile kustomization <k> --with-source
kubectl get externalsecrets -A; kubeseal --fetch-cert
sops -d secrets.enc.yaml >/dev/null && echo decrypt-ok
cosign verify <image>; syft <image> -o spdx-json
```

## Safety rules

- Changes to prod sync policies, branch protection, CI OIDC trust and secret stores are critical: user validation.
- Production deploys only through the GitOps PR path, never by direct apply.
- No long-lived cloud credentials in CI: OIDC roles only.

## Verification and rollback

- Verify: a sample service goes template -> PR -> CI green -> staging synced Healthy -> prod promotion.
- Rollback: git revert of the GitOps commit, `argocd app rollback`, `flux suspend` + revert.

## Deliverable

Golden path description and template location, pipelines, GitOps layout, secrets flow, observability defaults, demo evidence, adoption guide.
